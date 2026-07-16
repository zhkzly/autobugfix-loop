from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, Mapping

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_sdk import (
    CODEX_BROKER_SOCKET_ENV,
    CODEX_BROKER_TOKEN_ENV,
    CodexSDKBackend,
)
from autobugfix.models import CodexRequest, CodexResult, utc_now
from autobugfix.config import OPERATOR_CODEX_BROKER_ROLE_CONTRACTS, load_config
from autobugfix.operator.guard import effective_request
from autobugfix.operator.metrics import (
    OperatorMetricsError,
    baseline_for_request,
    compare_baseline,
    derive_metric_receipt,
)
from autobugfix.operator.models import digest_payload
from autobugfix.operator.policy import PolicyDecision, evaluate_policy
from autobugfix.operator.store import OperatorStore
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy


class OperatorValidationError(RuntimeError):
    pass


_SANDBOX_ENV_ALLOWLIST = (
    "CI",
    "FORCE_COLOR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "TERM",
    "TZ",
    "USER",
)
_HOME_AUTHORITY_DIRS = (
    ".aws",
    ".azure",
    ".codex",
    ".config/containers",
    ".config/gcloud",
    ".config/gh",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
)
_CODEX_BROKER_REQUEST_LIMIT = 4 * 1024 * 1024


class _OperatorCodexServer(AbstractContextManager["_OperatorCodexServer"]):
    """Credential-owning Codex capability for one sandboxed profile command."""

    _REQUEST_KEYS = frozenset(
        {
            "token",
            "role",
            "prompt",
            "developer_instructions",
            "cwd",
            "control_root",
            "sandbox",
            "approval_mode",
            "model",
            "timeout_seconds",
        }
    )

    def __init__(
        self,
        socket_path: Path,
        token: str,
        *,
        allowed_roots: tuple[Path, ...],
        artifact_root: Path,
        runtime_root: Path,
        source_home: Path,
        model: str,
        required_role_sequence: tuple[str, ...],
        role_timeout_seconds: Mapping[str, int],
        backend: CodexBackend | None = None,
    ) -> None:
        self.socket_path = socket_path.resolve()
        self.token = token
        self.allowed_roots = tuple(path.resolve() for path in allowed_roots)
        self.artifact_root = artifact_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.source_home = source_home.resolve()
        self.model = model
        self.required_role_sequence = required_role_sequence
        self.role_timeout_seconds = dict(role_timeout_seconds)
        self.backend = backend or CodexSDKBackend(
            source_home=self.source_home,
            runtime_root=self.runtime_root,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._accepted_roles: list[str] = []

    def _inside_allowed_root(self, path: Path) -> bool:
        return any(path == root or path.is_relative_to(root) for root in self.allowed_roots)

    def _validate_request(self, request: Mapping[str, Any]) -> CodexRequest:
        unknown = set(request) - self._REQUEST_KEYS
        if unknown:
            raise OperatorValidationError(
                "Codex broker request contains unauthorized fields: "
                + ", ".join(sorted(unknown))
            )
        if not hmac.compare_digest(str(request.get("token") or ""), self.token):
            raise OperatorValidationError("Codex broker token is invalid")
        role = str(request.get("role") or "")
        if role not in OPERATOR_CODEX_BROKER_ROLE_CONTRACTS:
            raise OperatorValidationError(f"Codex broker role is not authorized: {role}")
        prompt = request.get("prompt")
        instructions = request.get("developer_instructions")
        if not isinstance(prompt, str) or not prompt.strip():
            raise OperatorValidationError("Codex broker prompt must be non-empty text")
        if not isinstance(instructions, str) or not instructions.strip():
            raise OperatorValidationError("Codex broker instructions must be non-empty text")
        if len(prompt.encode("utf-8")) > 2 * 1024 * 1024:
            raise OperatorValidationError("Codex broker prompt exceeds the trusted size limit")
        if len(instructions.encode("utf-8")) > 1024 * 1024:
            raise OperatorValidationError("Codex broker instructions exceed the trusted size limit")
        cwd = Path(str(request.get("cwd") or "")).resolve()
        control_root = Path(str(request.get("control_root") or "")).resolve()
        if not cwd.is_dir() or not self._inside_allowed_root(cwd):
            raise OperatorValidationError("Codex broker cwd is outside sandboxed profile state")
        if (
            not control_root.is_dir()
            or not self._inside_allowed_root(control_root)
            or not (control_root / ".autobugfix/config.yaml").is_file()
        ):
            raise OperatorValidationError("Codex broker control root is unauthorized")
        expected_sandbox, expected_approval = OPERATOR_CODEX_BROKER_ROLE_CONTRACTS[role]
        if request.get("sandbox") != expected_sandbox:
            raise OperatorValidationError("Codex broker sandbox differs from the role contract")
        if request.get("approval_mode") != expected_approval:
            raise OperatorValidationError("Codex broker approval mode differs from the role contract")
        if request.get("model") != self.model:
            raise OperatorValidationError("Codex broker model differs from the profile contract")
        raw_timeout = request.get("timeout_seconds")
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
            raise OperatorValidationError("Codex broker timeout must be an integer")
        timeout_seconds = raw_timeout
        if timeout_seconds < 1 or timeout_seconds > self.role_timeout_seconds[role]:
            raise OperatorValidationError("Codex broker timeout exceeds the profile contract")
        return CodexRequest(
            role=role,
            prompt=prompt,
            cwd=cwd,
            sandbox=expected_sandbox,
            model=self.model,
            timeout_seconds=timeout_seconds,
            developer_instructions=instructions,
            raw_log_path=Path(),
            stderr_log_path=Path(),
            approval_mode=expected_approval,
        )

    def _run_request(self, request: Mapping[str, Any]) -> tuple[CodexResult, dict[str, Any]]:
        codex_request = self._validate_request(request)
        sequence = len(self._accepted_roles) + 1
        if sequence > len(self.required_role_sequence):
            raise OperatorValidationError("Codex broker call budget is exhausted")
        expected_role = self.required_role_sequence[sequence - 1]
        if codex_request.role != expected_role:
            raise OperatorValidationError(
                "Codex broker role sequence mismatch: "
                f"expected {expected_role}, got {codex_request.role}"
            )
        self._accepted_roles.append(codex_request.role)
        call_root = self.artifact_root / f"call-{sequence:03d}-{codex_request.role}"
        call_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        call_root.chmod(0o700)
        codex_request.raw_log_path = call_root / "raw.jsonl"
        codex_request.stderr_log_path = call_root / "stderr.log"
        started_at = utc_now()
        result = self.backend.run(codex_request)
        receipt = {
            "schema": "autobugfix-operator-codex-call-v1",
            "sequence": sequence,
            "role": codex_request.role,
            "model": codex_request.model,
            "sandbox": codex_request.sandbox,
            "approval_mode": codex_request.approval_mode,
            "cwd": str(codex_request.cwd),
            "prompt_sha256": hashlib.sha256(codex_request.prompt.encode("utf-8")).hexdigest(),
            "result_sha256": hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            "exit_code": result.exit_code,
            "started_at": started_at,
            "finished_at": utc_now(),
        }
        receipt_path = call_root / "receipt.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        receipt_path.chmod(0o600)
        return result, receipt

    def _append_event(
        self,
        status: str,
        request: Mapping[str, Any] | None,
        *,
        error: str = "",
        receipt: Mapping[str, Any] | None = None,
    ) -> None:
        prompt = request.get("prompt") if request is not None else None
        instructions = request.get("developer_instructions") if request is not None else None
        event = {
            "schema": "autobugfix-operator-codex-event-v1",
            "timestamp": utc_now(),
            "status": status,
            "accepted_call_count": len(self._accepted_roles),
            "role": str(request.get("role") or "") if request is not None else "",
            "cwd": str(request.get("cwd") or "") if request is not None else "",
            "model": str(request.get("model") or "") if request is not None else "",
            "prompt_sha256": (
                hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                if isinstance(prompt, str)
                else None
            ),
            "instructions_sha256": (
                hashlib.sha256(instructions.encode("utf-8")).hexdigest()
                if isinstance(instructions, str)
                else None
            ),
            "error": error,
            "receipt": dict(receipt) if receipt is not None else None,
        }
        path = self.artifact_root / "broker-events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        path.chmod(0o600)

    def _handle(self, connection: socket.socket) -> None:
        with connection.makefile("rwb") as stream:
            request: Mapping[str, Any] | None = None
            try:
                line = stream.readline(_CODEX_BROKER_REQUEST_LIMIT + 1)
                if not line or len(line) > _CODEX_BROKER_REQUEST_LIMIT:
                    raise OperatorValidationError("Codex broker request is empty or oversized")
                request = json.loads(line)
                if not isinstance(request, Mapping):
                    raise OperatorValidationError("Codex broker request must be a mapping")
                result, receipt = self._run_request(request)
                self._append_event("completed", request, receipt=receipt)
                response = {
                    "result": {
                        "text": result.text,
                        "exit_code": result.exit_code,
                        "receipt": receipt,
                    }
                }
            except OperatorValidationError as exc:
                self._append_event("rejected", request, error=str(exc))
                response = {"error": str(exc)}
            except Exception as exc:
                self._append_event("failed", request, error=type(exc).__name__)
                response = {
                    "error": "trusted Codex execution failed; inspect host artifacts "
                    f"({type(exc).__name__})"
                }
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

    def __enter__(self) -> "_OperatorCodexServer":
        source_auth = self.source_home / "auth.json"
        has_environment_auth = bool(
            os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY")
        )
        if not has_environment_auth and (
            not source_auth.is_file() or source_auth.is_symlink()
        ):
            raise OperatorValidationError(
                "credentialed Operator profile requires host Codex authentication"
            )
        self.socket_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        self.artifact_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.artifact_root.chmod(0o700)
        if self.socket_path.exists():
            raise OperatorValidationError("Codex broker socket path already exists")
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
        thread_alive = False
        if self._thread is not None:
            self._thread.join(timeout=max(self.role_timeout_seconds.values()) + 60)
            thread_alive = self._thread.is_alive()
        self.socket_path.unlink(missing_ok=True)
        if not thread_alive:
            shutil.rmtree(self.runtime_root, ignore_errors=True)
        if thread_alive:
            raise OperatorValidationError("Codex broker did not stop after profile completion")

    def completion_error(self) -> str | None:
        observed = tuple(self._accepted_roles)
        if observed == self.required_role_sequence:
            return None
        return (
            "Codex broker role sequence incomplete: "
            f"expected {list(self.required_role_sequence)}, got {list(observed)}"
        )

    def artifact_paths(self) -> list[str]:
        if not self.artifact_root.is_dir():
            return []
        return [str(path) for path in sorted(self.artifact_root.rglob("*")) if path.is_file()]


def _host_authority_roots(host_environment: Mapping[str, str]) -> tuple[Path, ...]:
    roots = [Path("/run")]
    home = Path(host_environment.get("HOME") or str(Path.home())).expanduser()
    roots.extend(home / relative for relative in _HOME_AUTHORITY_DIRS)
    runner_temp = host_environment.get("RUNNER_TEMP")
    if runner_temp:
        roots.append(Path(runner_temp))
    for key in ("GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_STEP_SUMMARY"):
        value = host_environment.get(key)
        if value:
            roots.append(Path(value).parent)
    return tuple(roots)


def _prepare_bind_destination(candidate_root: Path, destination: Path) -> Path:
    lexical = Path(os.path.abspath(destination))
    try:
        relative = lexical.relative_to(candidate_root)
    except ValueError as exc:
        raise OperatorValidationError(
            f"read-only sandbox destination is outside candidate: {lexical}"
        ) from exc
    cursor = candidate_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise OperatorValidationError(
                f"read-only sandbox destination traverses a symlink: {cursor}"
            )
    if lexical.exists() and not lexical.is_dir():
        raise OperatorValidationError(
            f"read-only sandbox destination is not a directory: {lexical}"
        )
    lexical.mkdir(parents=True, exist_ok=True)
    return lexical


def _sandbox_directory_args(masked_root: Path, destination: Path) -> list[str]:
    """Recreate directory mountpoints hidden by a parent tmpfs."""
    try:
        relative = destination.relative_to(masked_root)
    except ValueError:
        return []
    args: list[str] = []
    cursor = masked_root
    for part in relative.parts:
        cursor /= part
        args.extend(["--dir", str(cursor)])
    return args


def _linked_worktree_common_git_dir(candidate_root: Path) -> Path | None:
    """Return trusted Git metadata needed by a linked worktree sandbox."""
    marker = candidate_root / ".git"
    if marker.is_dir() or not marker.exists():
        return None
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
        raise OperatorValidationError(
            "candidate .git marker is not a regular worktree pointer"
        )
    lines = marker.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise OperatorValidationError("candidate .git marker is not a valid worktree pointer")
    raw_git_dir = Path(lines[0].removeprefix("gitdir: "))
    git_dir = (
        raw_git_dir if raw_git_dir.is_absolute() else marker.parent / raw_git_dir
    ).resolve()
    common_marker = git_dir / "commondir"
    back_pointer = git_dir / "gitdir"
    if (
        not git_dir.is_dir()
        or common_marker.is_symlink()
        or not common_marker.is_file()
        or common_marker.stat().st_size > 4096
        or back_pointer.is_symlink()
        or not back_pointer.is_file()
        or back_pointer.stat().st_size > 4096
    ):
        raise OperatorValidationError("candidate linked-worktree metadata is incomplete")
    common_value = common_marker.read_text(encoding="utf-8").strip()
    if not common_value or "\n" in common_value:
        raise OperatorValidationError(
            "candidate linked-worktree common directory is invalid"
        )
    raw_common_dir = Path(common_value)
    common_dir = (
        raw_common_dir if raw_common_dir.is_absolute() else git_dir / raw_common_dir
    ).resolve()
    if not common_dir.is_dir() or git_dir.parent != common_dir / "worktrees":
        raise OperatorValidationError(
            "candidate .git pointer is not a standard linked worktree"
        )
    back_value = back_pointer.read_text(encoding="utf-8").strip()
    if not back_value or "\n" in back_value:
        raise OperatorValidationError("candidate linked-worktree back pointer is invalid")
    raw_back_pointer = Path(back_value)
    resolved_back_pointer = (
        raw_back_pointer
        if raw_back_pointer.is_absolute()
        else back_pointer.parent / raw_back_pointer
    ).resolve()
    if resolved_back_pointer != marker.resolve():
        raise OperatorValidationError("candidate linked-worktree back pointer does not match")
    return common_dir


def _format_argv(argv: list[Any], values: Mapping[str, str]) -> list[str]:
    return [str(item).format_map(values) for item in argv]


def _safe_log_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "command"


def _codex_broker_contract(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise OperatorValidationError("Codex broker profile contract must be a mapping")
    allowed_keys = {
        "enabled",
        "model",
        "required_role_sequence",
        "role_timeout_seconds",
    }
    unknown = set(raw) - allowed_keys
    if unknown:
        raise OperatorValidationError(
            "Codex broker profile contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    if raw.get("enabled") is not True:
        raise OperatorValidationError("Codex broker profile must set enabled: true")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise OperatorValidationError("Codex broker profile requires a model")
    raw_sequence = raw.get("required_role_sequence")
    if not isinstance(raw_sequence, list) or not raw_sequence:
        raise OperatorValidationError("Codex broker requires required_role_sequence")
    sequence = tuple(str(role) for role in raw_sequence)
    unknown_roles = set(sequence) - set(OPERATOR_CODEX_BROKER_ROLE_CONTRACTS)
    if unknown_roles:
        raise OperatorValidationError(
            "Codex broker profile contains unsupported roles: "
            + ", ".join(sorted(unknown_roles))
        )
    if len(sequence) > 32:
        raise OperatorValidationError("Codex broker role sequence cannot exceed 32 calls")
    raw_timeouts = raw.get("role_timeout_seconds")
    if not isinstance(raw_timeouts, Mapping):
        raise OperatorValidationError("Codex broker role_timeout_seconds must be a mapping")
    sequence_roles = set(sequence)
    timeout_roles = {str(role) for role in raw_timeouts}
    if timeout_roles != sequence_roles:
        raise OperatorValidationError(
            "Codex broker role_timeout_seconds must exactly cover sequence roles"
        )
    role_timeouts: dict[str, int] = {}
    for raw_role, raw_timeout in raw_timeouts.items():
        role = str(raw_role)
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
            raise OperatorValidationError(
                f"Codex broker timeout for {role} must be an integer"
            )
        if raw_timeout < 1 or raw_timeout > 1800:
            raise OperatorValidationError(
                f"Codex broker timeout for {role} must be between 1 and 1800"
            )
        role_timeouts[role] = raw_timeout
    return {
        "model": model,
        "required_role_sequence": sequence,
        "role_timeout_seconds": role_timeouts,
    }


def _run_command(
    candidate_root: Path,
    log_root: Path,
    name: str,
    argv: list[str],
    timeout_seconds: int | None,
    *,
    process_sandbox: str,
    require_process_sandbox: bool,
    network_access: bool,
    hidden_roots: tuple[Path, ...],
    writable_roots: tuple[Path, ...],
    read_only_binds: tuple[tuple[Path, Path], ...],
    codex_broker: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_root = candidate_root.resolve()
    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    host_environment = dict(os.environ)
    environment = {
        key: host_environment[key]
        for key in _SANDBOX_ENV_ALLOWLIST
        if key in host_environment
    }
    environment["HOME"] = "/tmp/autobugfix-home"
    environment["TMPDIR"] = "/tmp"
    environment["XDG_CACHE_HOME"] = "/tmp/autobugfix-home/.cache"
    environment["XDG_CONFIG_HOME"] = "/tmp/autobugfix-home/.config"
    environment["XDG_DATA_HOME"] = "/tmp/autobugfix-home/.local/share"
    environment["UV_NO_SYNC"] = "1"
    candidate_src = candidate_root / "src"
    python_path = environment.get("PYTHONPATH")
    project_python_path = os.pathsep.join((str(candidate_src), str(candidate_root)))
    environment["PYTHONPATH"] = (
        f"{project_python_path}{os.pathsep}{python_path}"
        if python_path
        else project_python_path
    )
    broker_context: AbstractContextManager[Any] = nullcontext()
    broker_server: _OperatorCodexServer | None = None
    capability_root: Path | None = None
    broker_contract = _codex_broker_contract(codex_broker)
    broker_enabled = broker_contract is not None
    sandboxed = False
    executed_argv = list(argv)
    if process_sandbox not in {"auto", "bubblewrap", "none"}:
        raise OperatorValidationError(f"unsupported process sandbox: {process_sandbox}")
    inherited_sandbox = host_environment.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap"
    bubblewrap = shutil.which("bwrap") if process_sandbox in {"auto", "bubblewrap"} else None
    if broker_enabled and (inherited_sandbox or bubblewrap is None):
        raise OperatorValidationError(
            "credentialed Codex broker requires a fresh Bubblewrap authority boundary"
        )
    if inherited_sandbox:
        sandboxed = True
        environment["AUTOBUGFIX_PROCESS_SANDBOX"] = "bubblewrap"
    elif bubblewrap:
        sandboxed = True
        candidate_venv = candidate_root / ".venv"
        host_home = Path(host_environment.get("HOME") or str(Path.home())).expanduser().resolve()
        wrapper = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            str(host_home),
            "--dir",
            "/tmp/autobugfix-home",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        if broker_enabled:
            assert broker_contract is not None
            allowed_roots = tuple(path.resolve() for path in writable_roots)
            if not allowed_roots:
                raise OperatorValidationError("Codex broker requires sandboxed writable state")
            capability_root = Path(
                tempfile.mkdtemp(prefix="autobugfix-operator-codex-", dir="/tmp")
            ).resolve()
            capability_root.chmod(0o700)
            socket_path = capability_root / "codex.sock"
            token = secrets.token_hex(32)
            broker_server = _OperatorCodexServer(
                socket_path,
                token,
                allowed_roots=allowed_roots,
                artifact_root=log_root / f"{name}.codex-calls",
                runtime_root=log_root / f".{name}.codex-runtime",
                source_home=Path(host_environment.get("HOME") or str(Path.home())) / ".codex",
                model=str(broker_contract["model"]),
                required_role_sequence=broker_contract["required_role_sequence"],
                role_timeout_seconds=broker_contract["role_timeout_seconds"],
            )
            broker_context = broker_server
            environment[CODEX_BROKER_SOCKET_ENV] = str(socket_path)
            environment[CODEX_BROKER_TOKEN_ENV] = token
            wrapper.extend(_sandbox_directory_args(Path("/tmp"), capability_root))
            wrapper.extend(["--ro-bind", str(capability_root), str(capability_root)])
        if not network_access:
            wrapper.append("--unshare-net")
        # Mount the broad candidate first. Authority masks and exact runtime
        # grants must remain visible as more-specific overlays.
        wrapper.extend(_sandbox_directory_args(host_home, candidate_root))
        wrapper.extend(["--bind", str(candidate_root), str(candidate_root)])
        masked_roots: set[Path] = set()
        for root in (*_host_authority_roots(host_environment), *hidden_roots):
            resolved = root.resolve()
            if resolved.exists() and resolved not in masked_roots:
                try:
                    resolved.relative_to(host_home)
                    continue
                except ValueError:
                    pass
                try:
                    candidate_root.relative_to(resolved)
                except ValueError:
                    wrapper.extend(["--tmpfs", str(resolved)])
                    masked_roots.add(resolved)
        common_git_dir = _linked_worktree_common_git_dir(candidate_root)
        if common_git_dir is not None:
            for masked_root in (host_home, Path("/tmp")):
                directory_args = _sandbox_directory_args(masked_root, common_git_dir)
                if directory_args:
                    wrapper.extend(directory_args)
                    break
            wrapper.extend(["--ro-bind", str(common_git_dir), str(common_git_dir)])
        for root in writable_roots:
            resolved = root.resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            wrapper.extend(_sandbox_directory_args(host_home, resolved))
            wrapper.extend(["--bind", str(resolved), str(resolved)])
        for source, destination in read_only_binds:
            source = source.resolve()
            if not source.exists():
                raise OperatorValidationError(f"read-only sandbox bind does not exist: {source}")
            destination = _prepare_bind_destination(candidate_root, destination)
            wrapper.extend(["--ro-bind", str(source), str(destination)])
            if destination == candidate_venv:
                # Console scripts retain their trusted-venv shebang. Re-expose
                # only that exact source path after masking HOME or /tmp.
                for masked_root in (host_home, Path("/tmp")):
                    try:
                        relative_source = source.relative_to(masked_root)
                    except ValueError:
                        continue
                    if not relative_source.parts:
                        raise OperatorValidationError(
                            "trusted runtime source cannot equal a masked authority root: "
                            f"{source}"
                        )
                    wrapper.extend(_sandbox_directory_args(masked_root, source))
                    wrapper.extend(["--ro-bind", str(source), str(source)])
                    break
                environment["VIRTUAL_ENV"] = str(destination)
                environment["UV_PROJECT_ENVIRONMENT"] = str(destination)
                runtime_python = source / "bin/python"
                runtime_link = runtime_python
                visited_links: set[Path] = set()
                while runtime_link.is_symlink() and runtime_link not in visited_links:
                    visited_links.add(runtime_link)
                    link_target = Path(os.readlink(runtime_link))
                    if not link_target.is_absolute():
                        link_target = runtime_link.parent / link_target
                    link_target = Path(os.path.abspath(link_target))
                    try:
                        link_target.relative_to(source)
                    except ValueError:
                        pass
                    else:
                        runtime_link = link_target
                        continue
                    runtime_source_prefix = link_target.resolve().parent.parent
                    runtime_destination_prefix = link_target.parent.parent
                    for masked_root in (host_home, Path("/tmp")):
                        directory_args = _sandbox_directory_args(
                            masked_root, runtime_destination_prefix
                        )
                        if not directory_args:
                            continue
                        wrapper.extend(
                            directory_args
                        )
                        wrapper.extend(
                            [
                                "--ro-bind",
                                str(runtime_source_prefix),
                                str(runtime_destination_prefix),
                            ]
                        )
                        break
                    break
        command_argv = list(argv)
        executable = shutil.which(command_argv[0], path=host_environment.get("PATH"))
        if executable:
            resolved_executable = Path(executable).resolve()
            try:
                resolved_executable.relative_to(host_home)
            except ValueError:
                pass
            else:
                executable_prefix = resolved_executable.parent.parent
                if (
                    resolved_executable.name.startswith("python")
                    and (executable_prefix / "lib").is_dir()
                ):
                    wrapper.extend(_sandbox_directory_args(host_home, executable_prefix))
                    wrapper.extend(
                        ["--ro-bind", str(executable_prefix), str(executable_prefix)]
                    )
                    command_argv[0] = str(resolved_executable)
                else:
                    sandbox_executable = (
                        Path("/tmp/autobugfix-bin") / resolved_executable.name
                    )
                    wrapper.extend(
                        [
                            "--dir",
                            str(sandbox_executable.parent),
                            "--ro-bind",
                            str(resolved_executable),
                            str(sandbox_executable),
                        ]
                    )
                    sandbox_bin = str(sandbox_executable.parent)
                    inherited_path = environment.get("PATH")
                    environment["PATH"] = (
                        f"{sandbox_bin}{os.pathsep}{inherited_path}"
                        if inherited_path
                        else sandbox_bin
                    )
                    command_argv[0] = str(sandbox_executable)
        environment["AUTOBUGFIX_PROCESS_SANDBOX"] = "bubblewrap"
        wrapper.extend(["--chdir", str(candidate_root), "--"])
        executed_argv = [*wrapper, *command_argv]
    elif require_process_sandbox:
        raise OperatorValidationError(
            "authoritative command execution requires Bubblewrap; install bwrap or configure a supported sandbox"
        )
    exit_code = 126
    stdout = ""
    stderr = ""
    try:
        with broker_context:
            try:
                result = subprocess.run(
                    executed_argv,
                    cwd=candidate_root,
                    text=True,
                    shell=False,
                    capture_output=True,
                    env=environment,
                    timeout=timeout_seconds,
                    check=False,
                )
                exit_code = result.returncode
                stdout = result.stdout
                stderr = result.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                exit_code = 124
                stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                stderr += f"\nvalidation command timed out after {timeout_seconds} seconds\n"
    except Exception as exc:
        stderr += (
            "\ntrusted validation command harness failed: "
            f"{type(exc).__name__}: {exc}\n"
        )
    finally:
        if capability_root is not None:
            shutil.rmtree(capability_root, ignore_errors=True)
    broker_artifacts: list[str] = []
    if broker_server is not None:
        broker_artifacts = broker_server.artifact_paths()
        completion_error = broker_server.completion_error()
        if exit_code == 0 and completion_error:
            exit_code = 125
            stderr += f"\n{completion_error}\n"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{name}.stdout.log"
    stderr_path = log_root / f"{name}.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "name": name,
        "argv": argv,
        "executed_argv": executed_argv,
        "sandbox": "bubblewrap" if sandboxed else "none",
        "sandbox_inherited": inherited_sandbox,
        "cwd": str(candidate_root),
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": time.monotonic() - started_monotonic,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "codex_call_artifacts": broker_artifacts,
    }


def run_validation_profiles(
    candidate_root: Path,
    record_root: Path,
    request_id: str,
    validation_id: str,
    decision: PolicyDecision,
    constitution: Mapping[str, Any],
    *,
    log_root_override: Path | None = None,
    process_sandbox: str = "auto",
    require_process_sandbox: bool = True,
    network_access: bool = False,
    hidden_roots: tuple[Path, ...] = (),
    writable_roots: tuple[Path, ...] = (),
    read_only_binds: tuple[tuple[Path, Path], ...] = (),
) -> list[dict[str, Any]]:
    profiles = constitution.get("validation_profiles") or {}
    values = {
        "base_sha": decision.base_sha,
        "head_sha": decision.head_sha,
        "request_id": request_id,
        "candidate_root": str(candidate_root),
    }
    results: list[dict[str, Any]] = []
    log_root = (
        log_root_override.resolve()
        if log_root_override is not None
        else record_root / ".autobugfix/operator/logs" / request_id / validation_id
    )
    for profile_name in decision.required_profiles:
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            raise OperatorValidationError(f"missing trusted validation profile: {profile_name}")
        default_timeout = int(profile.get("timeout_seconds", 300))
        commands = profile.get("commands") or []
        if not commands:
            raise OperatorValidationError(f"trusted validation profile has no commands: {profile_name}")
        results.extend(
            run_command_specs(
                candidate_root,
                log_root,
                commands,
                values=values,
                default_timeout_seconds=default_timeout,
                name_prefix=profile_name,
                process_sandbox=process_sandbox,
                require_process_sandbox=require_process_sandbox,
                network_access=network_access,
                hidden_roots=hidden_roots,
                writable_roots=writable_roots,
                read_only_binds=read_only_binds,
                codex_broker=None,
            )
        )
    return results


def run_command_specs(
    candidate_root: Path,
    log_root: Path,
    commands: list[Any],
    *,
    values: Mapping[str, str],
    default_timeout_seconds: int = 300,
    name_prefix: str = "command",
    process_sandbox: str = "auto",
    require_process_sandbox: bool = True,
    network_access: bool = False,
    hidden_roots: tuple[Path, ...] = (),
    writable_roots: tuple[Path, ...] = (),
    read_only_binds: tuple[tuple[Path, Path], ...] = (),
    codex_broker: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if codex_broker is not None and len(commands) != 1:
        raise OperatorValidationError(
            "credentialed Codex broker profiles require exactly one command"
        )
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(commands, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("argv"), list):
            raise OperatorValidationError(f"invalid configured command {name_prefix}[{index}]")
        display_name = str(raw.get("name") or f"{name_prefix}-{index}")
        log_name = f"{index:02d}-{_safe_log_name(display_name)}"
        argv = _format_argv(raw["argv"], values)
        result = _run_command(
            candidate_root,
            log_root,
            log_name,
            argv,
            int(raw.get("timeout_seconds", default_timeout_seconds)),
            process_sandbox=process_sandbox,
            require_process_sandbox=require_process_sandbox,
            network_access=network_access,
            hidden_roots=hidden_roots,
            writable_roots=writable_roots,
            read_only_binds=read_only_binds,
            codex_broker=codex_broker,
        )
        result["name"] = display_name
        results.append(result)
    return results


def validate_operator_request(
    project_root: Path | str,
    request_id: str,
    *,
    candidate_root: Path | str | None = None,
    trusted_ref: str | None = "origin/main",
    trusted_file: Path | None = None,
    bootstrap_policy: bool = False,
    run_profiles: bool = True,
    allowed_signers: Path | None = None,
    phase: str = "postflight",
    record: bool = True,
    trusted_policy: TrustedPolicy | None = None,
) -> dict[str, Any]:
    record_root = Path(project_root).resolve()
    candidate = Path(candidate_root or project_root).resolve()
    config = load_config(record_root)
    store = OperatorStore(
        record_root,
        state_root=config.operator.state.root,
        artifact_root=config.operator.artifacts.root,
        database_name=config.operator.state.database_name,
        lease_timeout_seconds=config.operator.state.lease_timeout_seconds,
    )
    request, scope_version = effective_request(
        store.read_request(request_id), store.read_scope_revisions(request_id)
    )
    approvals = store.read_approvals(request_id)
    policy = trusted_policy or load_trusted_policy(
        record_root,
        trusted_ref=trusted_ref,
        trusted_file=trusted_file,
        bootstrap=bootstrap_policy,
    )
    decision = evaluate_policy(
        candidate,
        request,
        approvals,
        constitution=policy.data,
        trusted_policy_source=policy.source,
        trusted_policy=policy.trusted,
        phase=phase,
        allowed_signers=allowed_signers,
        scope_version=scope_version,
    )
    validation_id = f"validation-{uuid.uuid4().hex[:12]}"
    command_results: list[dict[str, Any]] = []
    experiment_results: list[dict[str, Any]] = []
    regression: dict[str, Any] | None = None

    if not policy.trusted:
        decision.violations.append("bootstrap policy is local feedback only and cannot produce merge-ready authority")
        decision.allowed = False

    baseline_layers = {str(item) for item in policy.data.get("baseline_required_layers") or []}
    behavior_change = bool((set(decision.changed_layers) or request.declared_layers) & baseline_layers)
    if behavior_change and not request.performance_baseline:
        decision.violations.append("behavior-affecting change requires a trusted performance baseline")
        decision.allowed = False

    if decision.allowed and run_profiles:
        runtime_binds = (
            ((config.operator.verification.runtime_venv, candidate / ".venv"),)
            if config.operator.verification.runtime_venv
            and config.operator.verification.runtime_venv.is_dir()
            else ()
        )
        command_results = run_validation_profiles(
            candidate,
            record_root,
            request_id,
            validation_id,
            decision,
            policy.data,
            log_root_override=store.artifact_root / request_id / "standalone" / validation_id,
            process_sandbox=config.operator.verification.process_sandbox,
            require_process_sandbox=config.operator.verification.require_process_sandbox,
            network_access=config.operator.verification.network_access,
            hidden_roots=(store.root, store.artifact_root),
            read_only_binds=runtime_binds,
        )
        if any(not item["passed"] for item in command_results):
            decision.violations.append("one or more trusted validation profile commands failed")
            decision.allowed = False

    current_receipt: dict[str, Any] | None = None
    if decision.allowed and request.performance_baseline and run_profiles:
        try:
            baseline = baseline_for_request(
                record_root,
                request.performance_baseline,
                request.base_sha,
            )
            profile = baseline["profile_contract"]
            profile_values = {
                str(key): str(value)
                for key, value in (baseline.get("profile_values") or {}).items()
            }
            shadow_root = store.root / "standalone" / validation_id / "shadow"
            shadow_root.mkdir(parents=True, exist_ok=True)
            experiment_results = run_command_specs(
                candidate,
                store.artifact_root / request_id / "standalone" / validation_id / "experiment",
                list(profile.get("commands") or []),
                values={
                    "request_id": request_id,
                    "base_sha": decision.base_sha,
                    "head_sha": decision.head_sha,
                    "candidate_root": str(candidate),
                    "shadow_state_root": str(shadow_root),
                    **profile_values,
                },
                default_timeout_seconds=int(profile.get("timeout_seconds", 1800)),
                name_prefix=str(baseline["profile"]),
                process_sandbox=config.operator.verification.process_sandbox,
                require_process_sandbox=config.operator.verification.require_process_sandbox,
                network_access=bool(profile.get("network_access", False)),
                hidden_roots=(store.root, store.artifact_root),
                writable_roots=(shadow_root,),
                read_only_binds=runtime_binds,
                codex_broker=profile.get("codex_broker"),
            )
            current_receipt = derive_metric_receipt(
                source="trusted_admission_experiment",
                profile=str(baseline["profile"]),
                values=profile_values,
                base_sha=decision.base_sha,
                head_sha=decision.head_sha,
                patch_digest=decision.patch_digest,
                command_results=experiment_results,
                profile_contract=profile,
            )
            regression = compare_baseline(
                record_root,
                request.performance_baseline,
                current_receipt,
                policy.data.get("metrics") or {},
                request_base_sha=request.base_sha,
            )
        except OperatorMetricsError as exc:
            regression = {"ok": False, "failures": [str(exc)], "comparisons": {}}
        if not regression["ok"]:
            decision.violations.extend(str(item) for item in regression["failures"])
            decision.allowed = False

    payload = {
        "validation_id": validation_id,
        "request_id": request.request_id,
        "request_digest": request.request_digest,
        "candidate_root": str(candidate),
        "policy": decision.to_dict(),
        "command_results": command_results,
        "experiment_results": experiment_results,
        "regression": regression,
        "metric_receipt": current_receipt,
        "created_at": utc_now(),
    }
    report = {**payload, "validation_digest": digest_payload(payload)}
    if record:
        report["record_path"] = str(store.write_validation(request_id, validation_id, report))
    return report
