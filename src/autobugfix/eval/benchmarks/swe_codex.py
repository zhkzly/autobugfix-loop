from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Mapping

from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.config import load_config
from autobugfix.eval.benchmarks.models import record_with_digest
from autobugfix.eval.benchmarks.swe_runtime import SWERuntimeError
from autobugfix.git_utils import git_common_dir
from autobugfix.models import CodexResult, utc_now
from autobugfix.role_config import resolve_role
from autobugfix.worktree import diff_for_task


class SWEExecutionLedger:
    """Trusted ordering and budget record for one benchmark Execution run."""

    def __init__(self, max_attempts: int) -> None:
        if max_attempts < 1:
            raise SWERuntimeError("Execution ledger requires a positive attempt budget")
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._phase = "awaiting_writer"
        self._writer_calls = 0
        self._evaluator_calls = 0
        self._verifier_calls = 0
        self._patch_sha256 = hashlib.sha256(b"").hexdigest()
        self._events: list[dict[str, Any]] = []

    def begin_codex(self, role: str, patch_sha256: str) -> int:
        with self._lock:
            if patch_sha256 != self._patch_sha256:
                raise SWERuntimeError(
                    f"{role} observed a patch outside the trusted Execution sequence"
                )
            if role == "writer":
                if self._phase not in {
                    "awaiting_writer",
                    "verifier_repair_failure",
                    "evaluator_completed",
                }:
                    raise SWERuntimeError(
                        f"Writer call is invalid while ledger is {self._phase}"
                    )
                if self._writer_calls >= self.max_attempts:
                    raise SWERuntimeError("Writer attempt budget is exhausted")
                self._writer_calls += 1
                sequence = len(self._events) + 1
                self._phase = "writer_running"
            elif role == "evaluator":
                if self._phase != "verifier_passed":
                    raise SWERuntimeError(
                        f"Evaluator call is invalid while ledger is {self._phase}"
                    )
                if self._evaluator_calls >= self.max_attempts:
                    raise SWERuntimeError("Evaluator call budget is exhausted")
                self._evaluator_calls += 1
                sequence = len(self._events) + 1
                self._phase = "evaluator_running"
            else:
                raise SWERuntimeError(f"Codex role is not authorized: {role}")
            self._events.append(
                {
                    "sequence": sequence,
                    "kind": "codex_started",
                    "role": role,
                    "patch_sha256": patch_sha256,
                    "timestamp": utc_now(),
                }
            )
            return sequence

    def finish_codex(
        self,
        role: str,
        sequence: int,
        *,
        passed: bool,
        patch_sha256: str,
    ) -> None:
        with self._lock:
            expected = "writer_running" if role == "writer" else "evaluator_running"
            if self._phase != expected:
                raise SWERuntimeError("Codex completion does not match ledger state")
            self._events.append(
                {
                    "sequence": sequence,
                    "kind": "codex_finished",
                    "role": role,
                    "passed": passed,
                    "patch_sha256": patch_sha256,
                    "timestamp": utc_now(),
                }
            )
            if not passed:
                self._phase = "codex_failed"
            elif role == "writer":
                self._patch_sha256 = patch_sha256
                self._phase = "awaiting_verifier"
            else:
                if patch_sha256 != self._patch_sha256:
                    raise SWERuntimeError("read-only evaluator changed the candidate patch")
                self._phase = "evaluator_completed"

    def begin_verifier(self, patch_sha256: str) -> int:
        with self._lock:
            if patch_sha256 != self._patch_sha256:
                raise SWERuntimeError(
                    "Verifier observed a patch outside the trusted Writer transition"
                )
            if self._phase != "awaiting_verifier":
                raise SWERuntimeError(
                    f"Verifier call is invalid while ledger is {self._phase}"
                )
            if self._verifier_calls >= self.max_attempts:
                raise SWERuntimeError("Verifier call budget is exhausted")
            self._verifier_calls += 1
            sequence = len(self._events) + 1
            self._phase = "verifier_running"
            self._events.append(
                {
                    "sequence": sequence,
                    "kind": "verifier_started",
                    "patch_sha256": patch_sha256,
                    "timestamp": utc_now(),
                }
            )
            return sequence

    def finish_verifier(
        self,
        sequence: int,
        outcome: str,
        patch_sha256: str,
    ) -> None:
        with self._lock:
            if self._phase != "verifier_running":
                raise SWERuntimeError("Verifier completion does not match ledger state")
            if patch_sha256 != self._patch_sha256:
                raise SWERuntimeError("visible verifier changed the candidate patch")
            self._events.append(
                {
                    "sequence": sequence,
                    "kind": "verifier_finished",
                    "outcome": outcome,
                    "patch_sha256": patch_sha256,
                    "timestamp": utc_now(),
                }
            )
            if outcome == "passed":
                self._phase = "verifier_passed"
            elif outcome == "repair_failure":
                self._phase = "verifier_repair_failure"
            else:
                self._phase = "verifier_failed"

    def validate_terminal(self, final_patch_sha256: str) -> dict[str, Any]:
        with self._lock:
            if final_patch_sha256 != self._patch_sha256:
                raise SWERuntimeError(
                    "final patch differs from the last trusted Execution transition"
                )
            completed = self._phase == "evaluator_completed"
            exhausted = (
                self._phase == "verifier_repair_failure"
                and self._writer_calls == self.max_attempts
            )
            if not completed and not exhausted:
                raise SWERuntimeError(
                    f"Execution ledger has no valid terminal outcome: {self._phase}"
                )
            if not (
                self._writer_calls >= 1
                and self._writer_calls == self._verifier_calls
                and self._evaluator_calls <= self._verifier_calls
            ):
                raise SWERuntimeError("Execution ledger call counts are inconsistent")
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-execution-ledger-v2",
                "phase": self._phase,
                "max_attempts": self.max_attempts,
                "writer_calls": self._writer_calls,
                "verifier_calls": self._verifier_calls,
                "evaluator_calls": self._evaluator_calls,
                "patch_sha256": self._patch_sha256,
                "events": list(self._events),
            }
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()


class SWECodexServer(AbstractContextManager["SWECodexServer"]):
    """Credential-owning RPC boundary for an untrusted Autobugfix subject."""

    _REQUEST_KEYS = frozenset({"token", "role", "prompt", "cwd"})

    def __init__(
        self,
        socket_path: Path,
        token: str,
        *,
        control_root: Path,
        repo_id: str,
        main_checkout: Path,
        worktree_root: Path,
        artifact_root: Path,
        hidden_paths: tuple[Path, ...],
        model: str,
        ledger: SWEExecutionLedger,
        backend: CodexSDKBackend | None = None,
    ) -> None:
        self.socket_path = socket_path.resolve()
        self.token = token
        self.control_root = control_root.resolve()
        self.repo_id = repo_id
        self.main_checkout = main_checkout.resolve()
        self.worktree_root = worktree_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.hidden_paths = tuple(path.resolve() for path in hidden_paths)
        self.model = model
        self.ledger = ledger
        self.backend = backend or CodexSDKBackend()
        self.config = load_config(self.control_root)
        self.repo = self.config.repo(self.repo_id)
        self.common_dir = git_common_dir(self.main_checkout)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def _validate_request(self, request: Mapping[str, Any]) -> tuple[str, str, Path]:
        unknown = set(request) - self._REQUEST_KEYS
        if unknown:
            raise SWERuntimeError(
                "Codex broker request contains unauthorized fields: "
                + ", ".join(sorted(unknown))
            )
        if not hmac.compare_digest(str(request.get("token") or ""), self.token):
            raise SWERuntimeError("Codex broker request token is invalid")
        role = str(request.get("role") or "")
        if role not in {"writer", "evaluator"}:
            raise SWERuntimeError("Codex broker authorizes only writer and evaluator")
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SWERuntimeError("Codex broker prompt must be non-empty text")
        if len(prompt.encode("utf-8")) > 2 * 1024 * 1024:
            raise SWERuntimeError("Codex broker prompt exceeds the trusted size limit")
        cwd = Path(str(request.get("cwd") or "")).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(self.worktree_root):
            raise SWERuntimeError("Codex broker cwd is outside the task worktree root")
        if git_common_dir(cwd) != self.common_dir:
            raise SWERuntimeError("Codex broker cwd has foreign Git metadata")
        return role, prompt, cwd

    def _run(self, role: str, prompt: str, cwd: Path, sequence: int) -> CodexResult:
        resolved = resolve_role(self.config, role, repo_id=self.repo_id)
        if resolved.backend != "codex":
            raise SWERuntimeError("SWE production roles must use the Codex backend")
        if resolved.model != self.model:
            raise SWERuntimeError("resolved Codex role model differs from frozen protocol")
        call_root = self.artifact_root / f"call-{sequence:04d}-{role}"
        call_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        request = build_codex_request(
            self.control_root,
            role,
            prompt,
            cwd,
            None,
            None,
            None,
            call_root / "raw.jsonl",
            call_root / "stderr.log",
            repo_id=self.repo_id,
            resolved_role=resolved,
            hidden_paths=self.hidden_paths,
            expected_git_common_dir=self.common_dir,
        )
        if request.model != self.model:
            raise SWERuntimeError("trusted Codex request model differs from frozen protocol")
        started_at = utc_now()
        result = self.backend.run(request)
        receipt = record_with_digest(
            {
                "schema": "autobugfix-swe-codex-call-v1",
                "sequence": sequence,
                "role": role,
                "model": request.model,
                "sandbox": request.sandbox,
                "approval_mode": request.approval_mode,
                "cwd": str(cwd),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "result_sha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
                "exit_code": result.exit_code,
                "started_at": started_at,
                "finished_at": utc_now(),
            }
        )
        (call_root / "receipt.json").write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
        return result

    def _patch_sha256(self, cwd: Path) -> str:
        patch = diff_for_task(self.repo, cwd, "HEAD")
        return hashlib.sha256(patch.encode("utf-8")).hexdigest()

    def _handle(self, connection: socket.socket) -> None:
        stream = connection.makefile("rwb")
        response: dict[str, Any]
        role = ""
        sequence = 0
        try:
            line = stream.readline(2 * 1024 * 1024 + 4096)
            if not line or len(line) > 2 * 1024 * 1024 + 2048:
                raise SWERuntimeError("Codex broker request is empty or oversized")
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise SWERuntimeError("Codex broker request must be a mapping")
            role, prompt, cwd = self._validate_request(request)
            before_patch = self._patch_sha256(cwd)
            sequence = self.ledger.begin_codex(role, before_patch)
            try:
                result = self._run(role, prompt, cwd, sequence)
            except BaseException:
                self.ledger.finish_codex(
                    role,
                    sequence,
                    passed=False,
                    patch_sha256=self._patch_sha256(cwd),
                )
                raise
            self.ledger.finish_codex(
                role,
                sequence,
                passed=True,
                patch_sha256=self._patch_sha256(cwd),
            )
            response = {
                "result": {
                    "text": result.text,
                    "exit_code": result.exit_code,
                }
            }
        except BaseException as exc:
            response = {"error": f"{type(exc).__name__}: {exc}"}
        stream.write(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")
        stream.flush()

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                self._handle(connection)

    def __enter__(self) -> "SWECodexServer":
        self.socket_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.socket_path.exists():
            raise SWERuntimeError("Codex broker socket path already exists")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(1)
        server.settimeout(0.2)
        self._socket = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.socket_path.unlink(missing_ok=True)
