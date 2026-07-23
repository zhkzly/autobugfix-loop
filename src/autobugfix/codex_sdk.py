from __future__ import annotations

import hashlib
import importlib
import json
import os
import signal
import shutil
import subprocess
import sys
import traceback
import uuid
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, IO

from autobugfix.codex_backend import CodexBackend
from autobugfix.config import load_config
from autobugfix.credential_guard import (
    credential_markers,
    redact_credential_leaks,
    snapshot_regular_files,
)
from autobugfix.models import CodexRequest, CodexResult, utc_now


class CodexSDKError(RuntimeError):
    pass


_PREPARED_CODEX_HOME = "AUTOBUGFIX_PREPARED_CODEX_HOME"


def _resolver_mount_after_reset(
    *,
    resolver_path: Path = Path("/etc/resolv.conf"),
    reset_root: Path = Path("/run"),
) -> tuple[Path, Path] | None:
    """Return the exact resolver file to restore after a runtime-root reset.

    Some Linux hosts expose ``/etc/resolv.conf`` as a symlink into ``/run``.
    A sandbox may reset that runtime root to hide host authority state while
    still needing DNS for the Codex control connection. Only the resolved
    regular resolver file is eligible for a read-only remount.
    """

    try:
        source = resolver_path.resolve(strict=True)
        root = reset_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not source.is_file() or not source.is_relative_to(root):
        return None
    return source, source


def _make_descriptor_private(descriptor: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(descriptor, 0o600)


def private_text_writer(path: Path, mode: str = "w") -> IO[str]:
    """Open a UTF-8 artifact with private permissions from its first byte."""

    if mode not in {"a", "w", "x"}:
        raise ValueError(f"unsupported private text mode: {mode}")
    flags = os.O_WRONLY | os.O_CREAT
    if mode == "a":
        flags |= os.O_APPEND
    elif mode == "w":
        flags |= os.O_TRUNC
    else:
        flags |= os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _make_descriptor_private(descriptor)
        return os.fdopen(
            descriptor,
            "a" if mode == "a" else "w",
            encoding="utf-8",
        )
    except BaseException:
        os.close(descriptor)
        raise


def write_private_text(path: Path, content: str, *, exclusive: bool = False) -> None:
    with private_text_writer(path, "x" if exclusive else "w") as handle:
        handle.write(content)


def write_private_bytes(path: Path, content: bytes, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _make_descriptor_private(descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(by_alias=False))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _extract_text(result: Any) -> str:
    final_response = getattr(result, "final_response", None)
    if final_response is not None:
        text = _extract_text(final_response)
        if text:
            return text
    for attr in ("output_text", "text", "content", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    if isinstance(result, dict):
        for key in ("output_text", "text", "content", "message"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    items = getattr(result, "items", None)
    if isinstance(items, list):
        parts = [_extract_text(item) for item in items]
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    return str(result)


class CodexSDKBackend(CodexBackend):
    """Production adapter for the local preview Python Codex SDK.

    The current preview package is `openai-codex` and imports as
    `openai_codex`. It exposes `Codex(CodexConfig(...)).thread_start(...).run`.
    This adapter never invokes `codex exec` and never falls back to a fake
    backend for production CLI paths.
    """

    module_names = ("openai_codex",)

    def __init__(
        self,
        module_name: str | None = None,
        *,
        in_process: bool = False,
    ) -> None:
        self.module_name = module_name
        self.in_process = in_process

    def _load_module(self) -> Any:
        names = (self.module_name,) if self.module_name else self.module_names
        errors: list[str] = []
        for name in names:
            if not name:
                continue
            try:
                return importlib.import_module(name)
            except ImportError as exc:
                errors.append(f"{name}: {exc}")
        joined = "; ".join(errors) or "no module names configured"
        raise CodexSDKError(
            "Python Codex SDK is not installed or importable. Install the preview "
            f"`openai-codex` package in this uv environment. Tried: {joined}"
        )

    @staticmethod
    def _control_root(request: CodexRequest) -> Path:
        root = request.control_root.resolve()
        if not (root / ".autobugfix/config.yaml").is_file():
            raise CodexSDKError(
                "trusted Codex control root has no .autobugfix/config.yaml"
            )
        return root

    @staticmethod
    def worker_environment() -> dict[str, str]:
        allowed = {
            "CODEX_API_KEY",
            "HOME",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "LANG",
            "LOGNAME",
            "NO_PROXY",
            "OPENAI_API_KEY",
            "PATHEXT",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_FILE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USER",
            "USERPROFILE",
            "WINDIR",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in allowed or key.startswith("LC_")
        }
        environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        environment["TMPDIR"] = "/tmp"
        environment["TMP"] = "/tmp"
        environment["TEMP"] = "/tmp"
        return environment

    def _runtime_root(self, request: CodexRequest) -> Path:
        project_root = self._control_root(request)
        runtime = load_config(project_root).codex.role_runtime
        runtime_root = runtime.runtime_root
        if not runtime_root.is_absolute():
            runtime_root = project_root / runtime_root
        return runtime_root.resolve()

    def _trusted_worker_source(self, request: CodexRequest) -> Path:
        package = Path(__file__).resolve().parent
        entries: list[dict[str, str]] = []
        for path in sorted(package.rglob("*")):
            if path.is_symlink():
                raise CodexSDKError("trusted Codex worker source cannot contain symlinks")
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(package).as_posix()
            entries.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        digest = hashlib.sha256(
            json.dumps(entries, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        source_root = self._runtime_root(request) / "trusted-worker-sources" / digest
        destination = source_root / "autobugfix"
        marker = source_root / "manifest.json"
        if source_root.exists():
            try:
                observed = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CodexSDKError("trusted Codex worker source cache is corrupt") from exc
            if observed != {"digest": digest, "files": entries} or not destination.is_dir():
                raise CodexSDKError("trusted Codex worker source cache identity drift")
            return source_root
        source_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        shutil.copytree(
            package,
            destination,
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        write_private_text(
            marker,
            json.dumps({"digest": digest, "files": entries}, sort_keys=True),
            exclusive=True,
        )
        return source_root

    def _trusted_runtime_paths(self, request: CodexRequest) -> tuple[Path, ...]:
        base_prefix = Path(sys.base_prefix).resolve()
        candidates = [
            Path(sys.prefix).resolve(),
            base_prefix,
            base_prefix.parent,
            self._trusted_worker_source(request),
        ]
        return tuple(
            dict.fromkeys(
                path
                for path in candidates
                if path != Path("/") and path.exists()
            )
        )

    @staticmethod
    def _bwrap_destination_dirs(
        paths: list[Path],
        reset_roots: tuple[Path, ...] = (Path("/tmp"),),
    ) -> list[str]:
        directories: set[Path] = set()
        for path in paths:
            matching_roots = [
                root
                for root in reset_roots
                if path != root and path.is_relative_to(root)
            ]
            if not matching_roots:
                continue
            reset_root = max(matching_roots, key=lambda item: len(item.parts))
            current = path
            while current != reset_root:
                directories.add(current)
                current = current.parent
        argv: list[str] = []
        for directory in sorted(directories, key=lambda item: (len(item.parts), str(item))):
            argv.extend(("--dir", str(directory)))
        return argv

    def worker_launch_argv(
        self,
        request: CodexRequest,
        worker_argv: list[str],
        *,
        call_home: Path | None = None,
    ) -> list[str]:
        if os.name == "nt":
            if request.require_process_isolation:
                raise CodexSDKError(
                    "production Codex role requires Bubblewrap process isolation"
                )
            return worker_argv
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            if request.require_process_isolation:
                raise CodexSDKError(
                    "production Codex role requires Bubblewrap process isolation"
                )
            return worker_argv
        control_root = self._control_root(request)
        if (
            request.sandbox == "workspace-write"
            and request.cwd.resolve() == control_root
        ):
            raise CodexSDKError(
                "workspace-write Codex role cannot use the trusted control root"
            )
        runtime_root = self._runtime_root(request)
        runtime_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        isolated_home = (
            call_home.resolve()
            if call_home is not None
            else (runtime_root / "direct-sandbox").resolve()
        )
        if not isolated_home.is_relative_to(runtime_root):
            raise CodexSDKError("Codex call home is outside the role runtime")
        isolated_home.mkdir(parents=True, mode=0o700, exist_ok=True)
        readable = [
            path.resolve() for path in request.readable_paths if path.exists()
        ]
        extra_writable = [
            path.resolve() for path in request.writable_paths if path.exists()
        ]
        if len(readable) != len(request.readable_paths) or len(extra_writable) != len(
            request.writable_paths
        ):
            raise CodexSDKError("Codex Git metadata mount disappeared before launch")
        if any(
            not any(path == root or path.is_relative_to(root) for root in readable)
            for path in extra_writable
        ):
            raise CodexSDKError(
                "Codex writable metadata mount is outside its read-only authority root"
            )
        trusted_runtime = list(self._trusted_runtime_paths(request))
        role_readable = (
            [request.cwd.resolve()] if request.sandbox == "read-only" else []
        )
        role_writable = (
            [request.cwd.resolve()] if request.sandbox == "workspace-write" else []
        )
        writable = [*role_writable, isolated_home]
        mount_roots = [
            *trusted_runtime,
            *role_readable,
            *readable,
            *writable,
            *extra_writable,
        ]
        default_hidden = [Path("/run"), runtime_root]
        home = Path.home().resolve()
        for candidate in (
            Path("/home"),
            Path("/root"),
            Path("/media"),
            Path("/mnt"),
            Path("/srv"),
        ):
            if candidate.exists():
                default_hidden.append(candidate)
        for candidate in (
            Path("/var/lib/docker"),
        ):
            if candidate.exists():
                default_hidden.append(candidate)
        hidden = [
            path.resolve()
            for path in (*request.hidden_paths, *default_hidden)
            if path.exists() and path.resolve() != Path("/tmp")
        ]
        hidden = [
            path
            for path in hidden
            if not path.is_relative_to(Path("/tmp"))
            or control_root.is_relative_to(path)
            or path.is_relative_to(control_root)
        ]
        if control_root in hidden:
            raise CodexSDKError("Codex authority path must not equal control root")
        if any(path in hidden for path in readable):
            raise CodexSDKError("Codex readable metadata path is explicitly hidden")
        pre_control_hidden = [
            path for path in hidden if control_root.is_relative_to(path)
        ]
        remaining_hidden = [
            path for path in hidden if path not in pre_control_hidden
        ]
        authority_mount_roots = [control_root, *mount_roots]
        if any(path == mount_root for path in hidden for mount_root in mount_roots):
            raise CodexSDKError("Codex path cannot be both hidden and mounted")
        hidden_parents_of_mounts = [
            path
            for path in remaining_hidden
            if any(
                mount_root != path and mount_root.is_relative_to(path)
                for mount_root in authority_mount_roots
            )
        ]
        final_hidden = [
            path
            for path in remaining_hidden
            if path not in hidden_parents_of_mounts
            if any(
                path.is_relative_to(mount_root)
                for mount_root in authority_mount_roots
            )
        ]
        post_control_hidden = [
            path for path in remaining_hidden if path not in final_hidden
        ]
        resolver_mount = (
            _resolver_mount_after_reset()
            if Path("/run") in post_control_hidden
            else None
        )
        early_mount_roots = {
            mount_root
            for mount_root in mount_roots
            if mount_root != control_root
            if any(
                hidden_path.is_relative_to(mount_root)
                for hidden_path in hidden_parents_of_mounts
            )
            if not any(
                mount_root.is_relative_to(hidden_path)
                for hidden_path in hidden_parents_of_mounts
            )
        }
        late_mount_roots = [
            mount_root
            for mount_root in mount_roots
            if mount_root != control_root and mount_root not in early_mount_roots
        ]
        argv = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            *self._bwrap_destination_dirs(pre_control_hidden),
        ]
        for path in sorted(dict.fromkeys(pre_control_hidden), key=str):
            argv.extend(("--tmpfs", str(path)))
        argv.extend(
            self._bwrap_destination_dirs(
                [control_root, *early_mount_roots],
                (Path("/tmp"), *pre_control_hidden),
            )
        )
        argv.extend(("--ro-bind", str(control_root), str(control_root)))
        for path in sorted(
            dict.fromkeys(
                path
                for path in (*trusted_runtime, *role_readable, *readable)
                if path in early_mount_roots
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--ro-bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(
                path
                for path in readable
                if any(
                    path != writable_root and path.is_relative_to(writable_root)
                    for writable_root in writable
                )
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--ro-bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(
                path for path in writable if path in early_mount_roots
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(
                path for path in extra_writable if path in early_mount_roots
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(post_control_hidden),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--tmpfs", str(path)))
        if resolver_mount is not None:
            source, destination = resolver_mount
            # /etc/resolv.conf can resolve under /run. Restore only its target
            # after hiding /run so the SDK control client retains DNS without
            # exposing the rest of host runtime state to the role process.
            argv.extend(
                self._bwrap_destination_dirs(
                    [destination.parent],
                    tuple(dict.fromkeys(post_control_hidden)),
                )
            )
            argv.extend(("--ro-bind", str(source), str(destination)))
        argv.extend(
            self._bwrap_destination_dirs(
                late_mount_roots,
                tuple(dict.fromkeys(post_control_hidden)),
            )
        )
        for path in sorted(
            dict.fromkeys(
                path
                for path in (*trusted_runtime, *role_readable, *readable)
                if path in late_mount_roots
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--ro-bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(path for path in writable if path in late_mount_roots),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(
                path for path in extra_writable if path in late_mount_roots
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--bind", str(path), str(path)))
        for path in sorted(
            dict.fromkeys(
                path
                for path in trusted_runtime
                if any(
                    path != writable_root and path.is_relative_to(writable_root)
                    for writable_root in writable
                )
            ),
            key=lambda item: (len(item.parts), str(item)),
        ):
            argv.extend(("--ro-bind", str(path), str(path)))
        for path in sorted(dict.fromkeys(final_hidden), key=str):
            argv.extend(("--tmpfs", str(path)))
        if runtime_root in final_hidden:
            argv.extend(
                self._bwrap_destination_dirs(
                    [isolated_home],
                    (runtime_root,),
                )
            )
            argv.extend(("--bind", str(isolated_home), str(isolated_home)))
        argv.extend(("--chdir", str(control_root), "--", *worker_argv))
        return argv

    def _runtime_env(
        self,
        request: CodexRequest,
        *,
        allow_prepared: bool = True,
    ) -> dict[str, str]:
        project_root = self._control_root(request)
        cfg = load_config(project_root)
        runtime = cfg.codex.role_runtime
        if not runtime.enabled:
            raise CodexSDKError("isolated Codex role runtime is disabled")
        runtime_root = self._runtime_root(request)
        prepared_value = os.environ.get(_PREPARED_CODEX_HOME) if allow_prepared else None
        if prepared_value:
            prepared = Path(prepared_value).resolve()
            calls_root = (runtime_root / "calls").resolve()
            if (
                not prepared.is_relative_to(calls_root)
                or not prepared.is_dir()
                or not (prepared / "config.toml").is_file()
            ):
                raise CodexSDKError(
                    "prepared Codex home is outside the trusted role runtime"
                )
            environment = self.worker_environment()
            environment["CODEX_HOME"] = str(prepared)
            return environment
        path_digest = hashlib.sha256(
            str(request.raw_log_path.resolve()).encode("utf-8")
        ).hexdigest()[:12]
        call_id = f"{path_digest}-{uuid.uuid4().hex}"
        codex_home = runtime_root / "calls" / call_id
        codex_home.mkdir(parents=True, mode=0o700, exist_ok=False)
        codex_home.chmod(0o700)
        try:
            source_home = Path.home() / ".codex"
            for name in (
                "auth.json",
                "version.json",
                "installation_id",
                ".personality_migration",
            ):
                if name == "auth.json" and not runtime.bridge_auth:
                    continue
                source = source_home / name
                dest = codex_home / name
                if source.exists():
                    write_private_bytes(dest, source.read_bytes(), exclusive=True)
            values: dict[str, Any] = {
                "model_reasoning_effort": cfg.codex.reasoning_effort,
                "disable_response_storage": cfg.codex.disable_response_storage,
                "approval_policy": "never",
                "sandbox_mode": request.sandbox,
                "allow_login_shell": False,
                "web_search": "disabled",
            }
            if cfg.codex.service_tier is not None:
                values["service_tier"] = cfg.codex.service_tier

            def scalar(value: Any) -> str:
                if isinstance(value, bool):
                    return "true" if value else "false"
                if isinstance(value, str):
                    return json.dumps(value, ensure_ascii=True)
                return str(value)

            config_lines = [
                f"{key} = {scalar(value)}" for key, value in values.items()
            ]
            config_lines.extend(
                [
                    "",
                    "[features]",
                    "hooks = false",
                    "multi_agent = false",
                    "apps = false",
                    "browser_use = false",
                    "browser_use_external = false",
                    "computer_use = false",
                    "",
                    "[sandbox_workspace_write]",
                    "network_access = false",
                    "exclude_tmpdir_env_var = false",
                    "exclude_slash_tmp = false",
                    "",
                    "[shell_environment_policy]",
                    'inherit = "none"',
                    "ignore_default_excludes = false",
                    'include_only = ["PATH", "HOME", "LANG", "LC_*"]',
                    "set = { "
                    + f'PATH = {json.dumps(self.worker_environment()["PATH"])}, '
                    + f'HOME = {json.dumps(str(request.cwd.resolve()))}'
                    + " }",
                    "",
                    f"[projects.{json.dumps(str(request.cwd.resolve()))}]",
                    'trust_level = "trusted"',
                    "",
                ]
            )
            config_path = codex_home / "config.toml"
            temporary = config_path.with_suffix(".tmp")
            write_private_text(temporary, "\n".join(config_lines), exclusive=True)
            os.replace(temporary, config_path)
            env = self.worker_environment()
            env["CODEX_HOME"] = str(codex_home)
            env["PYTHONPATH"] = str(self._trusted_worker_source(request))
            return env
        except BaseException:
            shutil.rmtree(codex_home, ignore_errors=True)
            raise

    def prepare_worker_environment(self, request: CodexRequest) -> dict[str, str]:
        environment = self._runtime_env(request, allow_prepared=False)
        environment[_PREPARED_CODEX_HOME] = environment["CODEX_HOME"]
        return environment

    def _codex_bin(self, request: CodexRequest) -> str | None:
        project_root = self._control_root(request)
        configured = load_config(project_root).codex.role_runtime.codex_bin
        return str(configured) if configured is not None else None

    def _call_preview_sdk(self, module: Any, request: CodexRequest) -> Any:
        try:
            config = module.CodexConfig(
                cwd=str(request.cwd),
                env=self._runtime_env(request),
                codex_bin=self._codex_bin(request),
            )
        except TypeError as exc:
            raise CodexSDKError(
                "installed preview Codex SDK does not support the required "
                "isolated env/codex_bin configuration; install a compatible openai-codex preview"
            ) from exc
        client = module.Codex(config)
        try:
            sandbox = module.Sandbox(request.sandbox)
            approval_name = request.approval_mode or ("auto_review" if request.sandbox == "workspace-write" else "deny_all")
            approval = getattr(module.ApprovalMode, approval_name, approval_name)
            thread = client.thread_start(
                approval_mode=approval,
                cwd=str(request.cwd),
                developer_instructions=request.developer_instructions,
                model=request.model,
                sandbox=sandbox,
            )
            return thread.run(
                request.prompt,
                approval_mode=approval,
                cwd=str(request.cwd),
                model=request.model,
                sandbox=sandbox,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _call_legacy_sdk(self, module: Any, request: CodexRequest) -> Any:
        kwargs = {
            "prompt": request.prompt,
            "cwd": str(request.cwd),
            "sandbox": request.sandbox,
            "model": request.model,
            "timeout_seconds": request.timeout_seconds,
            "developer_instructions": request.developer_instructions,
        }
        if hasattr(module, "Client"):
            client = module.Client()
            if hasattr(client, "run"):
                return client.run(**kwargs)
            if hasattr(client, "responses") and hasattr(client.responses, "run"):
                return client.responses.run(**kwargs)
        if hasattr(module, "Codex"):
            client = module.Codex()
            if hasattr(client, "run"):
                return client.run(**kwargs)
        if hasattr(module, "run"):
            return module.run(**kwargs)
        raise CodexSDKError(
            "Python Codex SDK import succeeded, but no supported run API was found. "
            "Expected openai_codex Codex.thread_start().run(...), module.run(...), "
            "Client().run(...), Client().responses.run(...), or Codex().run(...)."
        )

    def _call_sdk(self, module: Any, request: CodexRequest) -> Any:
        if all(hasattr(module, name) for name in ("Codex", "CodexConfig", "Sandbox", "ApprovalMode")):
            return self._call_preview_sdk(module, request)
        if getattr(module, "__name__", None) == "openai_codex":
            raise CodexSDKError(
                "installed openai-codex preview does not expose the required "
                "Codex/CodexConfig/Sandbox/ApprovalMode API"
            )
        return self._call_legacy_sdk(module, request)

    @staticmethod
    def worker_request_payload(request: CodexRequest) -> dict[str, Any]:
        return {
            "role": request.role,
            "prompt": request.prompt,
            "cwd": str(request.cwd),
            "control_root": str(request.control_root),
            "sandbox": request.sandbox,
            "model": request.model,
            "timeout_seconds": request.timeout_seconds,
            "developer_instructions": request.developer_instructions,
            "raw_log_path": str(request.raw_log_path),
            "stderr_log_path": str(request.stderr_log_path),
            "approval_mode": request.approval_mode,
            "hidden_paths": [str(path) for path in request.hidden_paths],
            "readable_paths": [str(path) for path in request.readable_paths],
            "writable_paths": [str(path) for path in request.writable_paths],
            "require_process_isolation": request.require_process_isolation,
        }

    @staticmethod
    def _terminate_worker(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                return
            process.wait(timeout=10)

    @staticmethod
    def _write_worker_logs(
        stdout_path: Path,
        stderr_path: Path,
        stdout: str | None,
        stderr: str | None,
    ) -> None:
        write_private_text(stdout_path, stdout or "")
        write_private_text(stderr_path, stderr or "")

    @staticmethod
    def _scrub_worker_credentials(codex_home: Path) -> None:
        """Destroy the entire one-call home after allowlisted evidence is published."""

        if codex_home.is_symlink():
            codex_home.unlink(missing_ok=True)
        elif codex_home.exists():
            for credential in codex_home.rglob("auth.json"):
                if credential.is_symlink() or credential.is_file():
                    credential.unlink(missing_ok=True)
            shutil.rmtree(codex_home)
        if codex_home.exists() or codex_home.is_symlink():
            raise CodexSDKError("isolated Codex call home could not be destroyed")

    def _run_prepared_worker(
        self,
        request: CodexRequest,
        *,
        worker_environment: dict[str, str],
        request_path: Path,
        result_path: Path,
        worker_stdout: Path,
        worker_stderr: Path,
    ) -> CodexResult:
        project_root = self._control_root(request)
        argv = [
            sys.executable,
            "-m",
            "autobugfix.codex_sdk_worker",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        if self.module_name:
            argv.extend(("--module-name", self.module_name))
        process = subprocess.Popen(
            self.worker_launch_argv(
                request,
                argv,
                call_home=Path(worker_environment["CODEX_HOME"]),
            ),
            cwd=project_root,
            env=worker_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_worker(process)
            stdout, stderr = process.communicate()
            self._write_worker_logs(worker_stdout, worker_stderr, stdout, stderr)
            message = f"Codex SDK worker timed out after {request.timeout_seconds} seconds"
            with private_text_writer(request.raw_log_path, "a") as raw:
                raw.write(
                    json.dumps(
                        {
                            "kind": "codex_timeout",
                            "timestamp": utc_now(),
                            "error": message,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            with private_text_writer(request.stderr_log_path, "a") as error_log:
                error_log.write(message + "\n")
            raise CodexSDKError(message) from exc
        except BaseException:
            self._terminate_worker(process)
            stdout, stderr = process.communicate()
            self._write_worker_logs(worker_stdout, worker_stderr, stdout, stderr)
            raise
        self._write_worker_logs(worker_stdout, worker_stderr, stdout, stderr)
        if process.returncode != 0 or not result_path.is_file():
            message = (
                f"Codex SDK worker failed with exit {process.returncode}: "
                f"{(stderr or '').strip()}"
            )
            raise CodexSDKError(message)
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexSDKError("Codex SDK worker wrote an invalid result") from exc
        result_path.chmod(0o600)
        return CodexResult(
            text=str(data["text"]),
            raw=dict(data.get("raw") or {}),
            exit_code=int(data.get("exit_code", 0)),
        )

    def _run_worker(self, request: CodexRequest) -> CodexResult:
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.parent.chmod(0o700)
        request.stderr_log_path.parent.chmod(0o700)
        stem = request.raw_log_path.name
        worker_environment = self.prepare_worker_environment(request)
        codex_home = Path(worker_environment["CODEX_HOME"]).resolve()
        markers = credential_markers(codex_home / "auth.json", worker_environment)
        workspace_before = snapshot_regular_files(request.cwd.resolve())
        published: dict[Path, Path] = {}
        leak_paths: tuple[Path, ...] = ()
        try:
            private_root = codex_home / "broker-io"
            private_root.mkdir(mode=0o700, exist_ok=False)
            isolated_request = replace(
                request,
                raw_log_path=private_root / "raw.jsonl",
                stderr_log_path=private_root / "stderr.log",
            )
            request_path = private_root / "sdk-request.json"
            result_path = private_root / "sdk-result.json"
            worker_stdout = private_root / "worker.stdout.log"
            worker_stderr = private_root / "worker.stderr.log"
            write_private_text(
                request_path,
                json.dumps(
                    self.worker_request_payload(isolated_request), sort_keys=True
                ),
            )
            published = {
                isolated_request.raw_log_path: request.raw_log_path,
                isolated_request.stderr_log_path: request.stderr_log_path,
                request_path: request.raw_log_path.with_name(
                    f"{stem}.sdk-request.json"
                ),
                result_path: request.raw_log_path.with_name(
                    f"{stem}.sdk-result.json"
                ),
                worker_stdout: request.raw_log_path.with_name(
                    f"{stem}.worker.stdout.log"
                ),
                worker_stderr: request.raw_log_path.with_name(
                    f"{stem}.worker.stderr.log"
                ),
            }
            result = self._run_prepared_worker(
                isolated_request,
                worker_environment=worker_environment,
                request_path=request_path,
                result_path=result_path,
                worker_stdout=worker_stdout,
                worker_stderr=worker_stderr,
            )
            leak_paths = redact_credential_leaks(
                (request.cwd.resolve(), private_root),
                markers,
                baseline=workspace_before,
            )
            if leak_paths:
                raise CodexSDKError(
                    "Codex role attempted to publish bridged credential material; "
                    "affected files were redacted"
                )
            return result
        finally:
            try:
                if published and not leak_paths:
                    leak_paths = redact_credential_leaks(
                        (request.cwd.resolve(), *published.keys()),
                        markers,
                        baseline=workspace_before,
                    )
                for source, destination in published.items():
                    if source.is_file() and not source.is_symlink():
                        write_private_bytes(destination, source.read_bytes())
                if not request.raw_log_path.exists():
                    write_private_text(request.raw_log_path, "")
                if not request.stderr_log_path.exists():
                    write_private_text(request.stderr_log_path, "")
                if leak_paths:
                    with private_text_writer(request.stderr_log_path, "a") as error_log:
                        error_log.write(
                            "credential leakage guard redacted role-controlled output\n"
                        )
            finally:
                self._scrub_worker_credentials(codex_home)
            if leak_paths:
                raise CodexSDKError(
                    "Codex role attempted to publish bridged credential material; "
                    "affected files were redacted"
                )

    def _run_in_process(self, request: CodexRequest) -> CodexResult:
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        with private_text_writer(request.stderr_log_path, "a"):
            pass
        with private_text_writer(request.raw_log_path, "a") as raw:
            raw.write(json.dumps({"kind": "codex_request", "timestamp": utc_now(), "request": _jsonable(request)}, sort_keys=True) + "\n")
        try:
            module = self._load_module()
            result = self._call_sdk(module, request)
            text = _extract_text(result)
            raw_payload = _jsonable(result)
            module_name = getattr(module, "__name__", self.module_name or type(module).__name__)
            with private_text_writer(request.raw_log_path, "a") as raw:
                raw.write(
                    json.dumps(
                        {
                            "kind": "codex_response",
                            "timestamp": utc_now(),
                            "module": module_name,
                            "response": raw_payload,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            return CodexResult(text=text, raw={"module": module_name, "response": raw_payload})
        except BaseException as exc:
            write_private_text(request.stderr_log_path, traceback.format_exc())
            with private_text_writer(request.raw_log_path, "a") as raw:
                raw.write(
                    json.dumps(
                        {
                            "kind": "codex_error",
                            "timestamp": utc_now(),
                            "error": repr(exc),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            raise

    def run(self, request: CodexRequest) -> CodexResult:
        if self.in_process:
            return self._run_in_process(request)
        return self._run_worker(request)
