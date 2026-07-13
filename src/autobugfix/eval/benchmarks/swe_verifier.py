from __future__ import annotations

import hmac
import hashlib
import json
import os
import shutil
import socket
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.swe_models import SWEInstance
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime, SWERuntimeError
from autobugfix.git_utils import git_common_dir
from autobugfix.models import RepoProfile, VerifierResult, utc_now
from autobugfix.worktree import diff_for_task

if TYPE_CHECKING:
    from autobugfix.eval.benchmarks.swe_codex import SWEExecutionLedger


VISIBLE_VERIFIER_COMMAND_ID = "swe-visible-v1"


def visible_command(language: str) -> str:
    normalized = language.strip().lower()
    commands = {
        "py": "python -m compileall -q .",
        "python": "python -m compileall -q .",
        "go": "go test ./...",
        "java": (
            "if [ -x ./gradlew ]; then ./gradlew test --no-daemon; "
            "elif [ -f pom.xml ]; then mvn -q test; else exit 2; fi"
        ),
        "js": "npm test --if-present && npm run build --if-present",
        "ts": "npm test --if-present && npm run build --if-present",
        "rust": "cargo test --workspace --no-fail-fast",
        "c": (
            "cmake -S . -B /tmp/autobugfix-build && "
            "cmake --build /tmp/autobugfix-build -j2 && "
            "ctest --test-dir /tmp/autobugfix-build --output-on-failure"
        ),
        "cpp": (
            "cmake -S . -B /tmp/autobugfix-build && "
            "cmake --build /tmp/autobugfix-build -j2 && "
            "ctest --test-dir /tmp/autobugfix-build --output-on-failure"
        ),
        "cs": "dotnet test --no-restore",
    }
    try:
        return commands[normalized]
    except KeyError as exc:
        raise SWERuntimeError(f"unsupported visible verifier language: {language}") from exc


def _bounded_log(path: Path, limit: int = 128 * 1024) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n... verifier output truncated ...\n" + text[-half:]


class SWEDockerVisibleVerifier:
    command_id = VISIBLE_VERIFIER_COMMAND_ID

    def __init__(
        self,
        runtime: SWERuntime,
        instance: SWEInstance,
        repo: RepoProfile,
        artifact_root: Path,
        image_id: str,
        allowed_worktree_roots: tuple[Path, ...] = (),
    ) -> None:
        self.runtime = runtime
        self.instance = instance
        self.repo = repo
        self.artifact_root = artifact_root.resolve()
        if not image_id.startswith("sha256:"):
            raise SWERuntimeError("visible verifier requires an immutable image ID")
        self.image_id = image_id
        if repo.worktree_root is None:
            raise SWERuntimeError("visible verifier requires a configured worktree root")
        self.worktree_root = repo.worktree_root.resolve()
        self.allowed_worktree_roots = tuple(
            dict.fromkeys(
                (self.worktree_root, *(path.resolve() for path in allowed_worktree_roots))
            )
        )
        self.common_dir = git_common_dir(repo.main_checkout)
        self._sequence = 0

    def _validate_worktree(self, worktree: Path) -> Path:
        resolved = worktree.resolve()
        if not any(resolved.is_relative_to(root) for root in self.allowed_worktree_roots):
            raise SWERuntimeError("visible verifier worktree is outside Execution ownership")
        if git_common_dir(resolved) != self.common_dir:
            raise SWERuntimeError("visible verifier worktree has foreign Git metadata")
        return resolved

    def patch_sha256(self, worktree: Path) -> str:
        checkout = self._validate_worktree(worktree)
        patch = diff_for_task(self.repo, checkout, "HEAD")
        return hashlib.sha256(patch.encode("utf-8")).hexdigest()

    def _docker_step(
        self,
        argv: list[str],
        root: Path,
        name: str,
        timeout_seconds: int,
    ):
        return run_command(
            argv,
            cwd=self.runtime.project_root,
            artifact_dir=root / name,
            name=name,
            timeout_seconds=timeout_seconds,
        )

    def run(
        self,
        worktree: Path,
        artifact_dir: Path,
        timeout_seconds: int,
    ) -> VerifierResult:
        del artifact_dir
        checkout = self._validate_worktree(worktree)
        self._sequence += 1
        root = self.artifact_root / f"attempt-{self._sequence:04d}"
        root.mkdir(parents=True, exist_ok=False)
        patch = diff_for_task(self.repo, checkout, "HEAD")
        patch_path = root / "candidate.patch"
        patch_path.write_text(patch, encoding="utf-8")
        docker = shutil.which("docker")
        if not docker:
            raise SWERuntimeError("docker executable is unavailable")
        inspect = self._docker_step(
            [docker, "image", "inspect", "--format", "{{.Id}}", self.image_id],
            root,
            "image-inspect",
            60,
        )
        if not inspect.passed:
            raise SWERuntimeError("visible verifier image is unavailable")
        image_id = Path(inspect.stdout_path).read_text(encoding="utf-8").strip()
        if image_id != self.image_id:
            raise SWERuntimeError("visible verifier image identity drift")

        container = f"autobugfix-visible-{uuid.uuid4().hex}"
        started_at = utc_now()
        try:
            create = self._docker_step(
                [
                    docker,
                    "create",
                    "--platform",
                    self.runtime.config.platform,
                    "--network",
                    "none",
                    "--memory",
                    self.runtime.config.memory_limit,
                    "--cpus",
                    str(self.runtime.config.cpu_limit),
                    "--pids-limit",
                    str(self.runtime.config.pids_limit),
                    "--name",
                    container,
                    "--entrypoint",
                    "sh",
                    self.image_id,
                    "-lc",
                    "while :; do sleep 3600; done",
                ],
                root,
                "container-create",
                120,
            )
            if not create.passed:
                raise SWERuntimeError("failed to create visible verifier container")
            start = self._docker_step(
                [docker, "start", container], root, "container-start", 120
            )
            if not start.passed:
                raise SWERuntimeError("failed to start visible verifier container")
            if patch:
                copy = self._docker_step(
                    [docker, "cp", str(patch_path), f"{container}:/tmp/autobugfix.patch"],
                    root,
                    "patch-copy",
                    120,
                )
                if not copy.passed:
                    raise SWERuntimeError("failed to copy patch into visible verifier")
                apply = self._docker_step(
                    [
                        docker,
                        "exec",
                        container,
                        "sh",
                        "-lc",
                        (
                            "cd /testbed; "
                            "if [ ! -d .git ]; then "
                            "g=$(find . -maxdepth 2 -mindepth 2 -type d -name .git -print -quit); "
                            "[ -n \"$g\" ] && cd \"${g%/.git}\"; fi; "
                            "git apply --binary --whitespace=nowarn /tmp/autobugfix.patch"
                        ),
                    ],
                    root,
                    "patch-apply",
                    min(timeout_seconds, 300),
                )
                if not apply.passed:
                    return VerifierResult(
                        command=self.command_id,
                        exit_code=int(apply.exit_code or 1),
                        stdout=_bounded_log(Path(apply.stdout_path)),
                        stderr=_bounded_log(Path(apply.stderr_path)),
                        started_at=started_at,
                        finished_at=utc_now(),
                        outcome="repair_failure",
                    )
            command = visible_command(self.instance.language)
            test = self._docker_step(
                [
                    docker,
                    "exec",
                    container,
                    "sh",
                    "-lc",
                    (
                        "cd /testbed; "
                        "if [ ! -d .git ]; then "
                        "g=$(find . -maxdepth 2 -mindepth 2 -type d -name .git -print -quit); "
                        "[ -n \"$g\" ] && cd \"${g%/.git}\"; fi; "
                        + command
                    ),
                ],
                root,
                "visible-command",
                timeout_seconds,
            )
            return VerifierResult(
                command=f"{self.command_id}: {command}",
                exit_code=int(test.exit_code if test.exit_code is not None else 124),
                stdout=_bounded_log(Path(test.stdout_path)),
                stderr=_bounded_log(Path(test.stderr_path)),
                started_at=started_at,
                finished_at=utc_now(),
                outcome=(
                    "passed" if test.exit_code == 0 else "repair_failure"
                ),
            )
        finally:
            self._docker_step(
                [docker, "rm", "-f", container],
                root,
                "container-remove",
                120,
            )


class SWEVerifierServer(AbstractContextManager["SWEVerifierServer"]):
    _REQUEST_KEYS = frozenset(
        {"token", "command_id", "worktree", "timeout_seconds"}
    )

    def __init__(
        self,
        socket_path: Path | str,
        token: str,
        backend: SWEDockerVisibleVerifier,
        *,
        ledger: "SWEExecutionLedger | None" = None,
        max_timeout_seconds: int = 900,
    ) -> None:
        raw_address = str(socket_path)
        if raw_address.startswith("@"):
            raise SWERuntimeError(
                "verifier capability must use a filesystem Unix socket"
            )
        self.socket_path = Path(raw_address).resolve()
        self.socket_address = str(self.socket_path)
        self.token = token
        self.backend = backend
        self.ledger = ledger
        self.max_timeout_seconds = max_timeout_seconds
        if max_timeout_seconds < 1:
            raise SWERuntimeError("verifier maximum timeout must be positive")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    @staticmethod
    def _result(result: VerifierResult) -> dict[str, Any]:
        return {
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "outcome": result.outcome,
        }

    def _handle(self, connection: socket.socket) -> None:
        stream = connection.makefile("rwb")
        response: dict[str, Any]
        try:
            line = stream.readline()
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise SWERuntimeError("verifier request must be a mapping")
            unknown = set(request) - self._REQUEST_KEYS
            if unknown:
                raise SWERuntimeError(
                    "verifier request contains unauthorized fields: "
                    + ", ".join(sorted(unknown))
                )
            if not hmac.compare_digest(str(request.get("token") or ""), self.token):
                raise SWERuntimeError("verifier request token is invalid")
            if request.get("command_id") != self.backend.command_id:
                raise SWERuntimeError("verifier command ID is not authorized")
            timeout = int(request.get("timeout_seconds") or 0)
            if timeout < 1 or timeout > self.max_timeout_seconds:
                raise SWERuntimeError("verifier timeout is outside the trusted budget")
            worktree = Path(str(request.get("worktree") or ""))
            before_patch = (
                self.backend.patch_sha256(worktree)
                if self.ledger is not None
                else ""
            )
            sequence = self.ledger.begin_verifier(before_patch) if self.ledger else 0
            try:
                result = self.backend.run(
                    worktree,
                    getattr(self.backend, "artifact_root", Path.cwd()),
                    timeout,
                )
            except BaseException:
                if self.ledger is not None:
                    self.ledger.finish_verifier(
                        sequence,
                        "harness_error",
                        self.backend.patch_sha256(worktree),
                    )
                raise
            if self.ledger is not None:
                self.ledger.finish_verifier(
                    sequence,
                    result.outcome,
                    self.backend.patch_sha256(worktree),
                )
            response = {"result": self._result(result)}
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

    def __enter__(self) -> "SWEVerifierServer":
        self.socket_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.socket_path.exists():
            raise SWERuntimeError("verifier socket path already exists")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_address)
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
