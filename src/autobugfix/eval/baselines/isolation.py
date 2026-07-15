from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from autobugfix.eval.benchmarks.models import digest_file, digest_payload
from autobugfix.credential_guard import (
    credential_markers,
    redact_credential_leaks,
    snapshot_regular_files,
)
from autobugfix.models import RawCodexBaselineConfig


class RawCodexIsolationError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class RunnerMetadata:
    sdk_version: str
    prompt_template_digest: str
    source_digest: str
    package_digest: str
    environment: Path
    approval_mode: str = "deny_all"
    sandbox: str = "workspace-write"
    network_access: bool = False
    runtime_mounts: tuple[tuple[Path, Path], ...] = ()


@dataclass(slots=True, frozen=True)
class RawProcessRun:
    return_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str
    stdout_path: Path
    stderr_path: Path
    sdk_artifact_root: Path
    process_result_path: Path


def _private_write(path: Path, content: str) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def _allowed_environment() -> dict[str, str]:
    allowed = {
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in allowed or key.startswith("LC_")
    }
    environment["LANG"] = environment.get("LANG", "C.UTF-8")
    environment["LC_ALL"] = environment.get("LC_ALL", "C.UTF-8")
    environment["HOME"] = "/tmp"
    environment["TMPDIR"] = "/tmp"
    environment["TMP"] = "/tmp"
    environment["TEMP"] = "/tmp"
    return environment


def _destination_dirs(paths: tuple[Path, ...], reset_root: Path) -> list[str]:
    directories: set[Path] = set()
    lexical_root = Path(os.path.abspath(reset_root))
    for path in paths:
        current = Path(os.path.abspath(path))
        if not current.is_relative_to(lexical_root):
            continue
        while current != lexical_root:
            directories.add(current)
            current = current.parent
    argv: list[str] = []
    for directory in sorted(
        directories, key=lambda item: (len(item.parts), str(item))
    ):
        argv.extend(("--dir", str(directory)))
    return argv


def _runner_project_digest(project: Path) -> str:
    required = (project / "pyproject.toml", project / "uv.lock")
    files = [*required, *sorted((project / "src").rglob("*.py"))]
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise RawCodexIsolationError(
            "Raw runner source contains a missing file or symlink"
        )
    return digest_payload(
        {
            "files": [
                {
                    "path": path.relative_to(project).as_posix(),
                    "sha256": digest_file(path),
                }
                for path in files
            ]
        }
    )


def _runner_package_digest(project: Path) -> str:
    package_root = project / "src" / "raw_codex_sdk_baseline"
    files = sorted(package_root.rglob("*.py"))
    if not files or any(not path.is_file() or path.is_symlink() for path in files):
        raise RawCodexIsolationError(
            "Raw runner package contains a missing file or symlink"
        )
    return digest_payload(
        {
            "files": [
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "sha256": digest_file(path),
                }
                for path in files
            ]
        }
    )


def _python_runtime_mounts(
    environment: Path,
    host_home: Path,
) -> tuple[tuple[Path, Path], ...]:
    python = environment / "bin" / "python"
    if not python.exists():
        raise RawCodexIsolationError("Raw runner environment has no Python executable")
    current = python
    seen: set[Path] = set()
    external_target: Path | None = None
    while current.is_symlink():
        if current in seen:
            raise RawCodexIsolationError("Raw runner Python symlink cycle")
        seen.add(current)
        raw_target = Path(os.readlink(current))
        next_path = (
            raw_target
            if raw_target.is_absolute()
            else current.parent / raw_target
        )
        next_path = Path(os.path.abspath(next_path))
        if not next_path.is_relative_to(environment):
            external_target = next_path
            break
        current = next_path
    if external_target is None or not external_target.is_relative_to(host_home):
        return ()
    resolved_binary = external_target.resolve(strict=True)
    source_prefix = resolved_binary.parent.parent
    destination_prefix = external_target.parent.parent
    if not source_prefix.is_dir():
        raise RawCodexIsolationError("Raw runner Python prefix is not a directory")
    return ((source_prefix, destination_prefix),)


class RawCodexProcessSandbox:
    def __init__(
        self,
        project_root: Path,
        config: RawCodexBaselineConfig,
        *,
        host_home: Path | None = None,
        hidden_roots: tuple[Path, ...] = (),
    ):
        self.project_root = project_root.resolve()
        self.config = config
        self.runtime_root = config.runtime_root.resolve()
        self.runner_project = config.runner_project.resolve()
        self.host_home = (host_home or Path.home()).resolve()
        self.hidden_roots = tuple(
            sorted(
                {
                    self.project_root,
                    self.runtime_root,
                    *(
                        path.resolve()
                        for path in (
                            Path("/home"),
                            Path("/root"),
                            Path("/mnt"),
                            Path("/media"),
                            Path("/srv"),
                            Path("/var/lib/docker"),
                        )
                        if path.is_dir()
                    ),
                    *(path.resolve() for path in hidden_roots),
                },
                key=lambda path: (len(path.parts), str(path)),
            )
        )

    @property
    def bwrap(self) -> str:
        executable = shutil.which("bwrap")
        if executable is None:
            raise RawCodexIsolationError(
                "Raw Codex formal execution requires Bubblewrap"
            )
        return executable

    @property
    def uv(self) -> str:
        executable = shutil.which("uv")
        if executable is None:
            raise RawCodexIsolationError("uv executable was not found")
        return executable

    def ensure_runner_environment(self) -> RunnerMetadata:
        if not (self.runner_project / "pyproject.toml").is_file():
            raise RawCodexIsolationError(
                "Raw Codex runner project has no pyproject.toml"
            )
        lock_path = self.runner_project / "uv.lock"
        if not lock_path.is_file():
            raise RawCodexIsolationError("Raw Codex runner has no uv.lock")
        lock_digest = digest_file(lock_path)
        source_digest = _runner_project_digest(self.runner_project)
        package_digest = _runner_package_digest(self.runner_project)
        environment_digest = digest_payload(
            {
                "lock_digest": lock_digest,
                "source_digest": source_digest,
            }
        )
        environment = self.runtime_root / "runner-envs" / environment_digest[:20]
        executable = environment / "bin" / "raw-codex-sdk-baseline"
        if not executable.is_file():
            environment.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            command_environment = dict(os.environ)
            command_environment["UV_PROJECT_ENVIRONMENT"] = str(environment)
            command = subprocess.run(
                [
                    self.uv,
                    "sync",
                    "--project",
                    str(self.runner_project),
                    "--frozen",
                    "--no-dev",
                    "--no-editable",
                    "--refresh-package",
                    "raw-codex-sdk-baseline",
                    "--cache-dir",
                    "/tmp/uv-cache",
                ],
                cwd=self.project_root,
                env=command_environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            if command.returncode != 0 or not executable.is_file():
                raise RawCodexIsolationError(
                    "cannot create locked Raw Codex runner environment: "
                    + (command.stderr.strip() or command.stdout.strip())
                )
        metadata = subprocess.run(
            [str(executable), "metadata"],
            cwd=self.runner_project,
            env=_allowed_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if metadata.returncode != 0:
            raise RawCodexIsolationError(
                metadata.stderr.strip() or "Raw runner metadata command failed"
            )
        try:
            value = json.loads(metadata.stdout)
        except json.JSONDecodeError as exc:
            raise RawCodexIsolationError(
                "Raw runner metadata is not valid JSON"
            ) from exc
        if not isinstance(value, Mapping):
            raise RawCodexIsolationError("Raw runner metadata must be an object")
        sdk_version = str(value.get("sdk_version") or "")
        approval_mode = str(value.get("approval_mode") or "")
        sandbox = str(value.get("sandbox") or "")
        network_access = value.get("network_access")
        prompt_digest = str(value.get("prompt_template_digest") or "")
        installed_package_digest = str(value.get("runner_package_digest") or "")
        if sdk_version != self.config.sdk_version:
            raise RawCodexIsolationError(
                "locked Raw runner SDK version differs from configuration"
            )
        if (
            approval_mode != self.config.approval_mode
            or sandbox != self.config.sandbox
            or network_access is not self.config.network_access
        ):
            raise RawCodexIsolationError(
                "locked Raw runner authority policy differs from configuration"
            )
        if len(prompt_digest) != 64:
            raise RawCodexIsolationError(
                "Raw runner prompt template digest is invalid"
            )
        if installed_package_digest != package_digest:
            raise RawCodexIsolationError(
                "installed Raw runner package differs from its source snapshot"
            )
        return RunnerMetadata(
            sdk_version=sdk_version,
            approval_mode=approval_mode,
            sandbox=sandbox,
            network_access=network_access,
            prompt_template_digest=prompt_digest,
            source_digest=source_digest,
            package_digest=installed_package_digest,
            environment=environment,
            runtime_mounts=_python_runtime_mounts(environment, self.host_home),
        )

    def create_codex_home(
        self,
        destination: Path,
        *,
        worktree: Path,
        reasoning_effort: str,
        service_tier: str | None,
    ) -> Path:
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
        destination.chmod(0o700)
        try:
            source_auth = self.host_home / ".codex/auth.json"
            if not source_auth.is_file() or source_auth.is_symlink():
                raise RawCodexIsolationError(
                    "Raw Codex direct SDK treatment requires host Codex auth.json"
                )
            auth_descriptor = os.open(
                destination / "auth.json",
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(auth_descriptor, "wb") as stream:
                stream.write(source_auth.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())

            def scalar(value: object) -> str:
                if isinstance(value, bool):
                    return "true" if value else "false"
                return json.dumps(value, ensure_ascii=True)

            lines = [
                f"model_reasoning_effort = {scalar(reasoning_effort)}",
                "disable_response_storage = true",
                'approval_policy = "never"',
                'sandbox_mode = "workspace-write"',
                "allow_login_shell = false",
                'web_search = "disabled"',
            ]
            if service_tier is not None:
                lines.append(f"service_tier = {scalar(service_tier)}")
            lines.extend(
                (
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
                    + f'PATH = {json.dumps(_allowed_environment().get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"))}, '
                    + f'HOME = {json.dumps(str(worktree.resolve()))}'
                    + " }",
                    "",
                    f"[projects.{json.dumps(str(worktree.resolve()))}]",
                    'trust_level = "trusted"',
                    "",
                )
            )
            _private_write(destination / "config.toml", "\n".join(lines))
            return destination
        except BaseException:
            self._scrub_codex_credentials(destination)
            shutil.rmtree(destination, ignore_errors=True)
            raise

    @staticmethod
    def _scrub_codex_credentials(codex_home: Path) -> None:
        if not codex_home.is_dir():
            return
        for credential in codex_home.rglob("auth.json"):
            if credential.is_symlink() or credential.is_file():
                credential.unlink(missing_ok=True)

    def _sandbox_argv(
        self,
        *,
        runner_environment: Path,
        runtime_mounts: tuple[tuple[Path, Path], ...],
        worktree: Path,
        input_root: Path,
        sdk_output_parent: Path,
        codex_home: Path,
        case_bundle: Path,
        model: str,
        reasoning_effort: str,
        service_tier: str | None,
    ) -> list[str]:
        home = self.host_home
        mounts = (
            runner_environment.resolve(),
            worktree.resolve(),
            input_root.resolve(),
            sdk_output_parent.resolve(),
            codex_home.resolve(),
        )
        runtime_destinations = tuple(
            Path(os.path.abspath(destination))
            for _, destination in runtime_mounts
        )
        executable = runner_environment / "bin" / "raw-codex-sdk-baseline"
        command = [
            str(executable),
            "run",
            "--case-bundle",
            str(case_bundle),
            "--worktree",
            str(worktree),
            "--artifacts",
            str(sdk_output_parent / "sdk"),
            "--model",
            model,
            "--reasoning-effort",
            reasoning_effort,
        ]
        if service_tier is not None:
            command.extend(("--service-tier", service_tier))
        argv = [
            self.bwrap,
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
            "--tmpfs",
            "/run",
            "--tmpfs",
            str(home),
            *_destination_dirs(
                (*self.hidden_roots, *mounts, *runtime_destinations),
                home,
            ),
        ]
        for hidden_root in self.hidden_roots:
            if hidden_root == home or not hidden_root.is_dir():
                continue
            argv.extend(("--tmpfs", str(hidden_root)))
            argv.extend(
                _destination_dirs(
                    (*mounts, *runtime_destinations),
                    hidden_root,
                )
            )
        for source, destination in runtime_mounts:
            argv.extend(
                (
                    "--ro-bind",
                    str(source.resolve()),
                    str(Path(os.path.abspath(destination))),
                )
            )
        argv.extend(
            [
            "--ro-bind",
            str(runner_environment),
            str(runner_environment),
            "--bind",
            str(worktree),
            str(worktree),
            "--ro-bind",
            str(input_root),
            str(input_root),
            "--bind",
            str(sdk_output_parent),
            str(sdk_output_parent),
            "--bind",
            str(codex_home),
            str(codex_home),
            "--chdir",
            str(worktree),
            "--",
            *command,
            ]
        )
        return argv

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=10)

    def run(
        self,
        *,
        runner_metadata: RunnerMetadata,
        worktree: Path,
        input_root: Path,
        case_bundle: Path,
        artifact_root: Path,
        model: str,
        reasoning_effort: str,
        service_tier: str | None,
        timeout_seconds: int,
    ) -> RawProcessRun:
        if artifact_root.exists():
            raise RawCodexIsolationError("Raw process artifact root already exists")
        artifact_root.mkdir(parents=True, mode=0o700)
        sdk_output_parent = artifact_root / "untrusted-sdk-output"
        sdk_output_parent.mkdir(mode=0o700)
        codex_home = self.create_codex_home(
            artifact_root / "codex-home",
            worktree=worktree,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
        )
        launch_config = codex_home / "config.toml"
        launch_config_bytes = launch_config.read_bytes()
        retained_config = artifact_root / "codex-config.toml"
        retained_config.write_bytes(launch_config_bytes)
        retained_config.chmod(0o600)
        environment = _allowed_environment()
        environment["CODEX_HOME"] = str(codex_home)
        environment["PATH"] = (
            f"{runner_metadata.environment / 'bin'}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        markers = credential_markers(codex_home / "auth.json", environment)
        worktree_before = snapshot_regular_files(worktree)
        stdout_path = artifact_root / "worker.stdout.log"
        stderr_path = artifact_root / "worker.stderr.log"
        argv = self._sandbox_argv(
            runner_environment=runner_metadata.environment,
            runtime_mounts=runner_metadata.runtime_mounts,
            worktree=worktree,
            input_root=input_root,
            sdk_output_parent=sdk_output_parent,
            codex_home=codex_home,
            case_bundle=case_bundle,
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
        )
        started = time.monotonic()
        timed_out = False
        stdout = ""
        stderr = ""
        return_code: int | None = None
        config_unchanged = False
        leak_paths: tuple[Path, ...] = ()
        try:
            process = subprocess.Popen(
                argv,
                cwd=self.project_root,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process)
                stdout, stderr = process.communicate()
            return_code = None if timed_out else process.returncode
        finally:
            config_unchanged = (
                launch_config.is_file()
                and not launch_config.is_symlink()
                and launch_config.read_bytes() == launch_config_bytes
            )
            try:
                stdout_path.write_text(stdout or "", encoding="utf-8")
                stderr_path.write_text(stderr or "", encoding="utf-8")
                leak_paths = redact_credential_leaks(
                    (worktree, sdk_output_parent, stdout_path, stderr_path),
                    markers,
                    baseline=worktree_before,
                )
            finally:
                try:
                    self._scrub_codex_credentials(codex_home)
                finally:
                    shutil.rmtree(codex_home, ignore_errors=False)
        if leak_paths:
            raise RawCodexIsolationError(
                "Raw Codex role attempted to publish credential material; "
                "affected files were redacted"
            )
        if not config_unchanged:
            raise RawCodexIsolationError(
                "Raw Codex worker changed its trusted launch configuration"
            )
        sdk_artifact_root = sdk_output_parent / "sdk"
        return RawProcessRun(
            return_code=return_code,
            timed_out=timed_out,
            duration_seconds=time.monotonic() - started,
            stdout=stdout or "",
            stderr=stderr or "",
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            sdk_artifact_root=sdk_artifact_root,
            process_result_path=sdk_artifact_root / "process-result.json",
        )
