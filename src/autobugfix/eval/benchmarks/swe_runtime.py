from __future__ import annotations

import json
import hashlib
import importlib.metadata
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import (
    DoctorCheck,
    DoctorReport,
    digest_file,
    digest_payload,
    record_with_digest,
    verify_record,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.swe_constants import (
    SWE_BENCH_COMMIT,
    SWE_BENCH_REPOSITORY,
    SWE_BENCH_TREE,
    SWE_BENCH_VERSION,
    SWE_LIVE_COMMIT,
    SWE_LIVE_DATASET,
    SWE_LIVE_DATASET_REVISION,
    SWE_LIVE_LAUNCH_COMMIT,
    SWE_LIVE_LAUNCH_REPOSITORY,
    SWE_LIVE_LAUNCH_TREE,
    SWE_LIVE_REPOSITORY,
    SWE_LIVE_TREE,
    SWE_PLATFORM,
    SWE_VERIFIED_DATASET,
    SWE_VERIFIED_DATASET_REVISION,
)
from autobugfix.eval.benchmarks.swe_models import SWESubjectTreatmentRuntime
from autobugfix.models import EvalBenchmarkConfig, SWEBenchmarkConfig, utc_now


class SWERuntimeError(RuntimeError):
    pass


SWE_GUARD_DAEMON_ISOLATION_LABEL = "autobugfix.guard.isolation=dedicated-vm-v1"


@dataclass(slots=True, frozen=True)
class SWEDockerAuthority:
    """Pinned local Docker socket and daemon identity owned by the Human Guard."""

    endpoint: str
    socket_path: Path
    guard_root: Path
    daemon_id: str
    device: int
    inode: int
    owner_uid: int
    owner_gid: int
    mode: int
    docker_executable: str
    isolation_label: str
    daemon_profile_digest: str

    @classmethod
    def capture(
        cls,
        *,
        endpoint: str,
        guard_root: Path,
        daemon_id: str,
        docker_executable: str,
        isolation_label: str,
        daemon_profile_digest: str,
    ) -> "SWEDockerAuthority":
        if not endpoint.startswith("unix://"):
            raise SWERuntimeError(
                "SWE Holdout Guard Docker authority must use a local unix socket"
            )
        raw_path = Path(endpoint.removeprefix("unix://"))
        if not raw_path.is_absolute():
            raise SWERuntimeError("Guard Docker unix socket path must be absolute")
        root = guard_root.resolve(strict=True)
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError as exc:
            raise SWERuntimeError("Guard Docker unix socket is unavailable") from exc
        if resolved != raw_path or not resolved.is_relative_to(root):
            raise SWERuntimeError(
                "Guard Docker unix socket must be canonical and inside the external Guard root"
            )
        cls._validate_private_authority_path(root, resolved)
        observed = resolved.lstat()
        if not stat.S_ISSOCK(observed.st_mode):
            raise SWERuntimeError("Guard Docker endpoint is not a unix socket")
        if stat.S_IMODE(observed.st_mode) != 0o600:
            raise SWERuntimeError("Guard Docker unix socket must have mode 0600")
        expected_uid = getattr(os, "geteuid", lambda: observed.st_uid)()
        if observed.st_uid != expected_uid:
            raise SWERuntimeError("Guard Docker unix socket is not owned by this Guard user")
        if not daemon_id or any(character.isspace() for character in daemon_id):
            raise SWERuntimeError("Guard Docker daemon ID is invalid")
        if isolation_label != SWE_GUARD_DAEMON_ISOLATION_LABEL:
            raise SWERuntimeError(
                "Guard Docker daemon lacks the required dedicated-VM isolation label"
            )
        if len(daemon_profile_digest) != 64:
            raise SWERuntimeError("Guard Docker daemon profile digest is invalid")
        return cls(
            endpoint=endpoint,
            socket_path=resolved,
            guard_root=root,
            daemon_id=daemon_id,
            device=observed.st_dev,
            inode=observed.st_ino,
            owner_uid=observed.st_uid,
            owner_gid=observed.st_gid,
            mode=stat.S_IMODE(observed.st_mode),
            docker_executable=docker_executable,
            isolation_label=isolation_label,
            daemon_profile_digest=daemon_profile_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "socket_path": str(self.socket_path),
            "guard_root": str(self.guard_root),
            "daemon_id": self.daemon_id,
            "device": self.device,
            "inode": self.inode,
            "owner_uid": self.owner_uid,
            "owner_gid": self.owner_gid,
            "mode": self.mode,
            "docker_executable": self.docker_executable,
            "isolation_label": self.isolation_label,
            "daemon_profile_digest": self.daemon_profile_digest,
            "authority_digest": self.authority_digest,
        }

    @staticmethod
    def _validate_private_authority_path(root: Path, socket_path: Path) -> None:
        candidates = [root]
        relative_parent = socket_path.parent.relative_to(root)
        current = root
        for component in relative_parent.parts:
            current = current / component
            candidates.append(current)
        expected_uid = getattr(os, "geteuid", lambda: root.stat().st_uid)()
        for candidate in candidates:
            observed = candidate.lstat()
            if candidate.is_symlink() or not stat.S_ISDIR(observed.st_mode):
                raise SWERuntimeError(
                    "Guard Docker authority directory must not be redirected"
                )
            if observed.st_uid != expected_uid or stat.S_IMODE(observed.st_mode) & 0o077:
                raise SWERuntimeError(
                    "Guard Docker authority directories must be owner-only"
                )

    @property
    def authority_digest(self) -> str:
        return digest_payload(
            {
                "endpoint": self.endpoint,
                "socket_path": str(self.socket_path),
                "daemon_id": self.daemon_id,
                "device": self.device,
                "inode": self.inode,
                "owner_uid": self.owner_uid,
                "owner_gid": self.owner_gid,
                "mode": self.mode,
                "isolation_label": self.isolation_label,
                "daemon_profile_digest": self.daemon_profile_digest,
            }
        )

    def assert_current(self, environment: Mapping[str, str]) -> None:
        self._validate_private_authority_path(self.guard_root, self.socket_path)
        try:
            observed = self.socket_path.lstat()
        except OSError as exc:
            raise SWERuntimeError("Guard Docker unix socket disappeared") from exc
        fingerprint = (
            observed.st_dev,
            observed.st_ino,
            observed.st_uid,
            observed.st_gid,
            stat.S_IMODE(observed.st_mode),
        )
        if not stat.S_ISSOCK(observed.st_mode) or fingerprint != (
            self.device,
            self.inode,
            self.owner_uid,
            self.owner_gid,
            self.mode,
        ):
            raise SWERuntimeError("Guard Docker unix socket authority changed")
        result = subprocess.run(
            [self.docker_executable, "info", "--format", "{{.ID}}"],
            cwd=self.guard_root,
            env=dict(environment),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0 or result.stdout.strip() != self.daemon_id:
            raise SWERuntimeError("Guard Docker daemon identity changed")


@dataclass(slots=True, frozen=True)
class SWEDatasetSnapshot:
    adapter: str
    dataset: str
    revision: str
    split: str
    path: str
    sha256: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-dataset-snapshot-v2",
                "adapter": self.adapter,
                "dataset": self.dataset,
                "revision": self.revision,
                "split": self.split,
                "path": self.path,
                "sha256": self.sha256,
                "row_count": self.row_count,
            }
        )


class SWERuntime:
    ADAPTERS = ("swebench_verified", "swebench_live")

    def __init__(
        self,
        project_root: Path,
        benchmark_config: EvalBenchmarkConfig,
        *,
        docker_environment: Mapping[str, str] | None = None,
        docker_authority: SWEDockerAuthority | None = None,
    ):
        self.project_root = project_root.resolve()
        self.benchmark_config = benchmark_config
        self.config = benchmark_config.swe
        self.cache_root = benchmark_config.cache_root.resolve()
        self._docker_environment = {
            str(key): str(value) for key, value in (docker_environment or {}).items()
        }
        self._docker_authority = docker_authority
        self._validate_config(self.config)

    @staticmethod
    def _validate_config(config: SWEBenchmarkConfig) -> None:
        expected = {
            "platform": SWE_PLATFORM,
            "swebench_version": SWE_BENCH_VERSION,
            "swebench_commit": SWE_BENCH_COMMIT,
            "swebench_tree": SWE_BENCH_TREE,
            "verified_dataset": SWE_VERIFIED_DATASET,
            "verified_dataset_revision": SWE_VERIFIED_DATASET_REVISION,
            "live_repository": SWE_LIVE_REPOSITORY,
            "live_commit": SWE_LIVE_COMMIT,
            "live_tree": SWE_LIVE_TREE,
            "live_launch_repository": SWE_LIVE_LAUNCH_REPOSITORY,
            "live_launch_commit": SWE_LIVE_LAUNCH_COMMIT,
            "live_launch_tree": SWE_LIVE_LAUNCH_TREE,
            "live_dataset": SWE_LIVE_DATASET,
            "live_dataset_revision": SWE_LIVE_DATASET_REVISION,
        }
        drift = [
            key
            for key, value in expected.items()
            if str(getattr(config, key)) != value
        ]
        if drift:
            raise SWERuntimeError(
                "SWE runtime configuration drift: " + ", ".join(sorted(drift))
            )
        if config.verified_namespace is not None:
            raise SWERuntimeError(
                "formal SWE-bench Verified runs require local-build images; "
                "verified_namespace must be null"
            )
        if config.scorer_timeout_seconds < 1:
            raise SWERuntimeError("SWE scorer timeout must be positive")
        if config.cpu_limit <= 0 or config.pids_limit < 1:
            raise SWERuntimeError("SWE Docker resource limits must be positive")

    @property
    def live_checkout(self) -> Path:
        return (
            self.cache_root
            / "frameworks"
            / "swebench-live"
            / self.config.live_commit
        )

    @property
    def swebench_checkout(self) -> Path:
        return (
            self.cache_root
            / "frameworks"
            / "swebench"
            / self.config.swebench_commit
        )

    @property
    def live_launch_checkout(self) -> Path:
        return self.live_checkout / "launch"

    @property
    def harness_lock(self) -> Path:
        return self.config.harness_project / "uv.lock"

    @property
    def evaluator_runtime_id(self) -> str:
        """Identity of the official scorer/materializer, not the Codex subject."""
        if not self.harness_lock.is_file():
            return "unavailable"
        source_entries = []
        evaluator_modules = {
            "models.py",
            "runtime.py",
            "swe_constants.py",
            "swe_live.py",
            "swe_materialize.py",
            "swe_official.py",
            "swe_runtime.py",
            "swe_verified.py",
        }
        for source_root in (
            self.project_root / "src/autobugfix/eval/benchmarks",
            self.project_root / "harnesses/swebench/scripts",
        ):
            if not source_root.is_dir():
                return "unavailable"
            for path in sorted(source_root.rglob("*")):
                if (
                    path.is_file()
                    and "__pycache__" not in path.parts
                    and (
                        source_root.name == "scripts"
                        or path.name in evaluator_modules
                    )
                ):
                    source_entries.append(
                        {
                            "path": path.relative_to(self.project_root).as_posix(),
                            "sha256": digest_file(path),
                        }
                    )
        return "sha256:" + digest_payload(
            {
                "swebench_version": self.config.swebench_version,
                "swebench_commit": self.config.swebench_commit,
                "swebench_tree": self.config.swebench_tree,
                "harness_lock_sha256": digest_file(self.harness_lock),
                "python_version": sys.version,
                "python_executable_sha256": digest_file(Path(sys.executable)),
                "official_adapter_source": source_entries,
                "host_system": platform.system(),
                "host_machine": platform.machine(),
                "live_commit": self.config.live_commit,
                "live_tree": self.config.live_tree,
                "live_launch_commit": self.config.live_launch_commit,
                "live_launch_tree": self.config.live_launch_tree,
                "platform": self.config.platform,
                "verified_image_mode": "local-build",
                "docker_authority_digest": (
                    self._docker_authority.authority_digest
                    if self._docker_authority is not None
                    else None
                ),
            }
        )

    @property
    def runtime_id(self) -> str:
        """Backward-compatible evaluator identity used by official SWE adapters."""
        return self.evaluator_runtime_id

    @staticmethod
    def _distribution_identity(package: str) -> dict[str, str]:
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SWERuntimeError(f"required Codex distribution is missing: {package}") from exc
        record = distribution.read_text("RECORD") or ""
        return {
            "package": package,
            "version": distribution.version,
            "record_sha256": hashlib.sha256(record.encode("utf-8")).hexdigest(),
        }

    def subject_runtime_identity(
        self,
        treatment: SWESubjectTreatmentRuntime,
    ) -> dict[str, Any]:
        """Observe and bind the runtime that will execute the exact subject."""
        root_lock = self.project_root / "uv.lock"
        if not root_lock.is_file():
            raise SWERuntimeError("subject Codex runtime has no root uv.lock")
        sdk = self._distribution_identity(treatment.sdk_package)
        cli = self._distribution_identity(treatment.cli_package)
        expected = {
            "sdk_package": treatment.sdk_package,
            "sdk_version": treatment.sdk_version,
            "cli_package": treatment.cli_package,
            "cli_version": treatment.cli_version,
        }
        observed = {
            "sdk_package": sdk["package"],
            "sdk_version": sdk["version"],
            "cli_package": cli["package"],
            "cli_version": cli["version"],
        }
        if observed != expected:
            raise SWERuntimeError(
                "installed Codex runtime differs from SWE treatment: "
                + ", ".join(
                    key for key in expected if expected[key] != observed[key]
                )
            )
        source_paths = (
            self.project_root / "src/autobugfix/eval/benchmarks/subject_broker.py",
            self.project_root / "src/autobugfix/eval/benchmarks/swe_codex.py",
            self.project_root / "src/autobugfix/eval/benchmarks/swe_submission.py",
            self.project_root / "harnesses/swebench/scripts/run_subject.py",
        )
        if any(not path.is_file() or path.is_symlink() for path in source_paths):
            raise SWERuntimeError("subject Codex runtime source is incomplete")
        return record_with_digest(
            {
                "schema": "autobugfix-swe-subject-runtime-v1",
                "treatment_contract_digest": treatment.contract_digest,
                "treatment": treatment.to_dict(),
                "sdk": sdk,
                "cli": cli,
                "root_lock_sha256": digest_file(root_lock),
                "python_version": sys.version,
                "python_executable_sha256": digest_file(Path(sys.executable)),
                "source": [
                    {
                        "path": path.relative_to(self.project_root).as_posix(),
                        "sha256": digest_file(path),
                    }
                    for path in source_paths
                ],
            }
        )

    @property
    def docker_authority_digest(self) -> str | None:
        return (
            self._docker_authority.authority_digest
            if self._docker_authority is not None
            else None
        )

    def command_env(self, writable_state_root: Path | None = None) -> dict[str, str]:
        state_root = (
            writable_state_root.resolve()
            if writable_state_root is not None
            else self.cache_root
        )
        cache = state_root / "uv"
        hf_home = state_root / "huggingface"
        xdg_cache = state_root / "xdg-cache"
        client_home = state_root / "client-home"
        docker_config = client_home / ".docker"
        for path in (cache, hf_home, xdg_cache, client_home, docker_config):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        environment = {
            "UV_CACHE_DIR": str(cache),
            "UV_LINK_MODE": "copy",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "HF_HOME": str(hf_home),
            "HF_DATASETS_CACHE": str(hf_home / "datasets"),
            "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
            "XDG_CACHE_HOME": str(xdg_cache),
        }
        allowed_host = {
            "DOCKER_HOST",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "LANG",
            "NO_PROXY",
            "PATH",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_FILE",
        }
        environment.update(
            {
                key: value
                for key, value in os.environ.items()
                if key in allowed_host or key.startswith("LC_")
            }
        )
        environment.update(self._docker_environment)
        environment["HOME"] = str(client_home)
        environment["DOCKER_CONFIG"] = str(docker_config)
        environment.setdefault(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        if self._docker_authority is not None:
            self._docker_authority.assert_current(environment)
        return environment

    def live_command_env(
        self,
        writable_state_root: Path | None = None,
    ) -> dict[str, str]:
        environment = self.command_env(writable_state_root)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(self.live_checkout), str(self.live_launch_checkout))
        )
        return environment

    def isolated_official_argv(
        self,
        argv: list[str],
        *,
        cwd: Path,
        writable_roots: tuple[Path, ...],
        readable_roots: tuple[Path, ...] = (),
    ) -> list[str]:
        """Run pinned external scorer code without host credential visibility."""

        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise SWERuntimeError(
                "bubblewrap is required for the official SWE scorer"
            )
        resolved_cwd = cwd.resolve(strict=True)
        writable = tuple(path.resolve(strict=True) for path in writable_roots)
        readable = tuple(path.resolve(strict=True) for path in readable_roots)
        if not any(
            resolved_cwd == root or resolved_cwd.is_relative_to(root)
            for root in writable
        ):
            raise SWERuntimeError(
                "official SWE scorer cwd must be inside an explicit writable root"
            )

        host_home = Path.home().resolve()
        hidden_candidates = [Path("/tmp"), host_home]
        for candidate in (
            Path("/home"),
            Path("/root"),
            Path("/mnt"),
            Path("/media"),
            Path("/srv"),
            Path("/var/lib/docker"),
        ):
            if candidate.is_dir():
                hidden_candidates.append(candidate.resolve())
        if self._docker_authority is not None:
            hidden_candidates.append(self._docker_authority.guard_root)
        hidden: list[Path] = []
        for candidate in sorted(set(hidden_candidates), key=lambda item: len(item.parts)):
            if not any(
                candidate == parent or candidate.is_relative_to(parent)
                for parent in hidden
            ):
                hidden.append(candidate)

        mounts: list[tuple[Path, Path, bool]] = [
            (self.cache_root, self.cache_root, True),
            *((root, root, True) for root in readable),
            *((root, root, False) for root in writable),
        ]
        launcher = Path(argv[0])
        if launcher.is_absolute() and launcher.is_file():
            launcher = launcher.resolve(strict=True)
            mounts.append((launcher, launcher, True))
        harness_python = self.config.harness_project / ".venv/bin/python"
        if harness_python.is_symlink():
            raw_target = Path(os.readlink(harness_python))
            if not raw_target.is_absolute():
                raw_target = harness_python.parent / raw_target
            destination_runtime = raw_target.parent.parent
            source_runtime = harness_python.resolve(strict=True).parent.parent
            mounts.append((source_runtime, destination_runtime, True))
        if self._docker_authority is not None:
            mounts.append(
                (
                    self._docker_authority.socket_path,
                    self._docker_authority.socket_path,
                    True,
                )
            )

        command = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
        ]
        for root in hidden:
            command.extend(("--tmpfs", str(root)))

        def destination_directories(destination: Path) -> list[Path]:
            directories: list[Path] = []
            for root in hidden:
                if destination == root or not destination.is_relative_to(root):
                    continue
                current = root
                parts = destination.relative_to(root).parts
                if destination.is_file():
                    parts = parts[:-1]
                for component in parts:
                    current = current / component
                    directories.append(current)
            return directories

        created: set[Path] = set()

        def create_destinations(destinations: tuple[Path, ...]) -> None:
            for destination in destinations:
                for directory in destination_directories(destination):
                    if directory not in created:
                        command.extend(("--dir", str(directory)))
                        created.add(directory)

        create_destinations((self.project_root,))
        command.extend(("--ro-bind", str(self.project_root), str(self.project_root)))

        sensitive_project_roots = (
            self.project_root / ".autobugfix",
            self.project_root / ".autobugfix-memory",
            self.project_root / ".codex",
            self.project_root / ".trellis",
        )
        masked_project_roots: list[Path] = []
        for root in sensitive_project_roots:
            if root.exists():
                command.extend(("--tmpfs", str(root)))
                masked_project_roots.append(root)

        for _, destination, _ in mounts:
            for directory in destination_directories(destination):
                if directory not in created:
                    command.extend(("--dir", str(directory)))
                    created.add(directory)
            for root in masked_project_roots:
                if destination == root or destination.is_relative_to(root):
                    current = root
                    parts = destination.relative_to(root).parts
                    if destination.is_file():
                        parts = parts[:-1]
                    for component in parts:
                        current = current / component
                        command.extend(("--dir", str(current)))
        for source, destination, read_only in mounts:
            command.extend(
                (
                    "--ro-bind" if read_only else "--bind",
                    str(source),
                    str(destination),
                )
            )

        command.extend(("--chdir", str(resolved_cwd), "--", *argv))
        return command

    @staticmethod
    def _git_value(checkout: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SWERuntimeError(result.stderr.strip() or "Git command failed")
        return result.stdout.strip()

    def _verify_live_superproject(self, root: Path) -> None:
        root = root.resolve()
        if not (root / ".git").exists():
            raise SWERuntimeError("pinned SWE-bench-Live checkout is missing")
        observed = {
            "head": self._git_value(root, "rev-parse", "HEAD"),
            "tree": self._git_value(root, "rev-parse", "HEAD^{tree}"),
            "remote": self._git_value(root, "remote", "get-url", "origin"),
            "status": self._git_value(
                root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=all",
            ),
            "launch_url": self._git_value(
                root,
                "config",
                "-f",
                ".gitmodules",
                "--get",
                "submodule.launch.url",
            ),
            "launch_gitlink": self._git_value(root, "rev-parse", "HEAD:launch"),
        }
        if observed["head"] != self.config.live_commit:
            raise SWERuntimeError("SWE-bench-Live checkout commit drift")
        if observed["tree"] != self.config.live_tree:
            raise SWERuntimeError("SWE-bench-Live checkout tree drift")
        if observed["remote"] != self.config.live_repository:
            raise SWERuntimeError("SWE-bench-Live checkout remote drift")
        if observed["launch_url"] != self.config.live_launch_repository:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch URL drift")
        if observed["launch_gitlink"] != self.config.live_launch_commit:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch gitlink drift")
        if observed["status"]:
            raise SWERuntimeError("SWE-bench-Live checkout is dirty")

    def verify_swebench_checkout(self, checkout: Path | None = None) -> None:
        root = (checkout or self.swebench_checkout).resolve()
        if not (root / ".git").exists():
            raise SWERuntimeError("pinned SWE-bench checkout is missing")
        observed = {
            "head": self._git_value(root, "rev-parse", "HEAD"),
            "tree": self._git_value(root, "rev-parse", "HEAD^{tree}"),
            "remote": self._git_value(root, "remote", "get-url", "origin"),
            "status": self._git_value(
                root, "status", "--porcelain=v1", "--untracked-files=all"
            ),
        }
        if observed["head"] != self.config.swebench_commit:
            raise SWERuntimeError("SWE-bench checkout commit drift")
        if observed["tree"] != self.config.swebench_tree:
            raise SWERuntimeError("SWE-bench checkout tree drift")
        if observed["remote"] != SWE_BENCH_REPOSITORY:
            raise SWERuntimeError("SWE-bench checkout remote drift")
        if observed["status"]:
            raise SWERuntimeError("SWE-bench checkout is dirty")

    def ensure_swebench_checkout(self, artifact_root: Path) -> Path:
        destination = self.swebench_checkout
        if destination.exists():
            self.verify_swebench_checkout(destination)
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=".swebench-checkout-", dir=destination.parent)
        )
        try:
            commands = (
                ("init", ["git", "init", str(temporary)]),
                (
                    "remote",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "remote",
                        "add",
                        "origin",
                        SWE_BENCH_REPOSITORY,
                    ],
                ),
                (
                    "fetch",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "fetch",
                        "--depth",
                        "1",
                        "--no-tags",
                        "origin",
                        self.config.swebench_commit,
                    ],
                ),
                (
                    "checkout",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "checkout",
                        "--detach",
                        "FETCH_HEAD",
                    ],
                ),
            )
            for name, argv in commands:
                evidence = run_command(
                    argv,
                    cwd=self.project_root,
                    artifact_dir=artifact_root / f"swebench-{name}",
                    name=f"swebench-{name}",
                    timeout_seconds=self.benchmark_config.command_timeout_seconds,
                )
                if not evidence.passed:
                    raise SWERuntimeError(
                        f"failed to materialize pinned SWE-bench checkout: {name}"
                    )
            self.verify_swebench_checkout(temporary)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return destination

    def verify_live_checkout(self, checkout: Path | None = None) -> None:
        root = (checkout or self.live_checkout).resolve()
        self._verify_live_superproject(root)
        launch = root / "launch"
        if not (launch / ".git").exists():
            raise SWERuntimeError("pinned SWE-bench-Live RepoLaunch checkout is missing")
        observed = {
            "head": self._git_value(launch, "rev-parse", "HEAD"),
            "tree": self._git_value(launch, "rev-parse", "HEAD^{tree}"),
            "remote": self._git_value(launch, "remote", "get-url", "origin"),
            "status": self._git_value(
                launch, "status", "--porcelain=v1", "--untracked-files=all"
            ),
        }
        if observed["head"] != self.config.live_launch_commit:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch commit drift")
        if observed["tree"] != self.config.live_launch_tree:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch tree drift")
        if observed["remote"] != self.config.live_launch_repository:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch remote drift")
        if observed["status"]:
            raise SWERuntimeError("SWE-bench-Live RepoLaunch checkout is dirty")

    def _ensure_live_submodule(self, checkout: Path, artifact_root: Path) -> None:
        evidence = run_command(
            [
                "git",
                "-c",
                "protocol.version=2",
                "-C",
                str(checkout),
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--depth",
                "1",
            ],
            cwd=self.project_root,
            artifact_dir=artifact_root / "live-launch-submodule",
            name="live-launch-submodule",
            timeout_seconds=self.benchmark_config.command_timeout_seconds,
        )
        if not evidence.passed:
            raise SWERuntimeError("failed to materialize pinned RepoLaunch submodule")

    def ensure_live_checkout(self, artifact_root: Path) -> Path:
        destination = self.live_checkout
        if destination.exists():
            self._verify_live_superproject(destination)
            if not (destination / "launch" / ".git").exists():
                self._ensure_live_submodule(destination, artifact_root)
            self.verify_live_checkout(destination)
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=".live-checkout-", dir=destination.parent)
        )
        try:
            commands = (
                ("init", ["git", "init", str(temporary)]),
                (
                    "remote",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "remote",
                        "add",
                        "origin",
                        self.config.live_repository,
                    ],
                ),
                (
                    "fetch",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "fetch",
                        "--depth",
                        "1",
                        "origin",
                        self.config.live_commit,
                    ],
                ),
                (
                    "checkout",
                    [
                        "git",
                        "-C",
                        str(temporary),
                        "checkout",
                        "--detach",
                        "FETCH_HEAD",
                    ],
                ),
            )
            for name, argv in commands:
                evidence = run_command(
                    argv,
                    cwd=self.project_root,
                    artifact_dir=artifact_root / f"live-{name}",
                    name=f"live-{name}",
                    timeout_seconds=self.benchmark_config.command_timeout_seconds,
                )
                if not evidence.passed:
                    raise SWERuntimeError(
                        f"failed to materialize SWE-bench-Live checkout: {name}"
                    )
            self._verify_live_superproject(temporary)
            self._ensure_live_submodule(temporary, artifact_root)
            self.verify_live_checkout(temporary)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return destination

    def _dataset_identity(self, adapter: str) -> tuple[str, str, str]:
        if adapter == "swebench_verified":
            return (
                self.config.verified_dataset,
                self.config.verified_dataset_revision,
                "test",
            )
        if adapter == "swebench_live":
            return self.config.live_dataset, self.config.live_dataset_revision, "all"
        raise SWERuntimeError(f"unsupported SWE adapter: {adapter}")

    def _snapshot_root(self, adapter: str, revision: str) -> Path:
        return self.cache_root / "datasets" / adapter / revision / "canonical-v2"

    @staticmethod
    def _count_jsonl(path: Path) -> int:
        with path.open("r", encoding="utf-8") as stream:
            return sum(1 for line in stream if line.strip())

    def read_dataset_snapshot(self, adapter: str) -> SWEDatasetSnapshot:
        dataset, revision, split = self._dataset_identity(adapter)
        root = self._snapshot_root(adapter, revision)
        metadata_path = root / "snapshot.yaml"
        data_path = root / "test.jsonl"
        if not metadata_path.is_file() or not data_path.is_file():
            raise SWERuntimeError(f"pinned dataset snapshot is missing: {adapter}")
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise SWERuntimeError("SWE dataset snapshot metadata is invalid")
        verify_record(raw)
        if raw.get("schema") != "autobugfix-swe-dataset-snapshot-v2":
            raise SWERuntimeError("unsupported SWE dataset snapshot schema")
        snapshot = SWEDatasetSnapshot(
            adapter=str(raw.get("adapter") or ""),
            dataset=str(raw.get("dataset") or ""),
            revision=str(raw.get("revision") or ""),
            split=str(raw.get("split") or ""),
            path=str(raw.get("path") or ""),
            sha256=str(raw.get("sha256") or ""),
            row_count=int(raw.get("row_count") or 0),
        )
        if snapshot.adapter != adapter or snapshot.dataset != dataset:
            raise SWERuntimeError("SWE dataset snapshot identity drift")
        if snapshot.revision != revision or snapshot.split != split:
            raise SWERuntimeError("SWE dataset snapshot revision drift")
        if Path(snapshot.path).resolve() != data_path.resolve():
            raise SWERuntimeError("SWE dataset snapshot path drift")
        if digest_file(data_path) != snapshot.sha256:
            raise SWERuntimeError("SWE dataset snapshot digest drift")
        if self._count_jsonl(data_path) != snapshot.row_count:
            raise SWERuntimeError("SWE dataset snapshot row count drift")
        return snapshot

    def ensure_dataset_snapshot(
        self,
        adapter: str,
        artifact_root: Path,
    ) -> SWEDatasetSnapshot:
        try:
            return self.read_dataset_snapshot(adapter)
        except SWERuntimeError as exc:
            if "missing" not in str(exc):
                raise
        dataset, revision, split = self._dataset_identity(adapter)
        destination = self._snapshot_root(adapter, revision)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=".dataset-", dir=destination.parent)
        )
        try:
            output = temporary / "test.jsonl"
            uv = shutil.which("uv")
            if not uv:
                raise SWERuntimeError("uv executable is unavailable")
            evidence = run_command(
                [
                    uv,
                    "run",
                    "--project",
                    str(self.config.harness_project),
                    "--frozen",
                    "python",
                    str(
                        self.config.harness_project
                        / "scripts/snapshot_dataset.py"
                    ),
                    "--dataset",
                    dataset,
                    "--revision",
                    revision,
                    "--split",
                    split,
                    "--out",
                    str(output),
                ],
                cwd=self.project_root,
                artifact_dir=artifact_root / "dataset-snapshot",
                name=f"snapshot-{adapter}",
                timeout_seconds=self.benchmark_config.command_timeout_seconds,
                env=self.command_env(),
            )
            if not evidence.passed or not output.is_file():
                raise SWERuntimeError(f"failed to snapshot pinned dataset: {adapter}")
            snapshot = SWEDatasetSnapshot(
                adapter=adapter,
                dataset=dataset,
                revision=revision,
                split=split,
                path=str((destination / "test.jsonl").resolve()),
                sha256=digest_file(output),
                row_count=self._count_jsonl(output),
            )
            (temporary / "snapshot.yaml").write_text(
                yaml.safe_dump(snapshot.to_dict(), sort_keys=False),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self.read_dataset_snapshot(adapter)

    @staticmethod
    def _check(
        checks: list[DoctorCheck],
        name: str,
        expected: str,
        observed: str,
        passed: bool,
        error: str = "",
    ) -> None:
        checks.append(
            DoctorCheck(
                name=name,
                passed=passed,
                expected=expected,
                observed=observed,
                error=error,
            )
        )

    def doctor(self, adapter: str, artifact_root: Path) -> DoctorReport:
        if adapter not in self.ADAPTERS:
            raise SWERuntimeError(f"unsupported SWE adapter: {adapter}")
        started_at = utc_now()
        checks: list[DoctorCheck] = []

        observed_platform = f"{platform.system().lower()}/{platform.machine().lower()}"
        platform_ok = platform.system() == "Linux" and platform.machine().lower() in {
            "x86_64",
            "amd64",
        }
        self._check(checks, "platform", SWE_PLATFORM, observed_platform, platform_ok)

        harness_ok = (
            (self.config.harness_project / "pyproject.toml").is_file()
            and self.harness_lock.is_file()
        )
        self._check(
            checks,
            "harness-lock",
            "locked harnesses/swebench project",
            str(self.config.harness_project),
            harness_ok,
        )

        uv = shutil.which("uv")
        self._check(checks, "uv", "available", uv or "unavailable", bool(uv))
        bwrap = shutil.which("bwrap")
        self._check(
            checks,
            "bubblewrap",
            "available for isolated official scorer",
            bwrap or "unavailable",
            bool(bwrap),
        )
        if uv and harness_ok:
            version = run_command(
                [
                    uv,
                    "run",
                    "--project",
                    str(self.config.harness_project),
                    "--frozen",
                    "python",
                    "-c",
                    "import swebench; print(swebench.__version__)",
                ],
                cwd=self.project_root,
                artifact_dir=artifact_root / "harness-version",
                name="harness-version",
                timeout_seconds=self.benchmark_config.command_timeout_seconds,
                env=self.command_env(),
                inherit_env=False,
            )
            observed_version = Path(version.stdout_path).read_text(encoding="utf-8").strip()
            self._check(
                checks,
                "swebench-version",
                SWE_BENCH_VERSION,
                observed_version,
                version.passed and observed_version == SWE_BENCH_VERSION,
            )

        try:
            checkout = self.ensure_swebench_checkout(artifact_root)
            observed_checkout = (
                f"{self._git_value(checkout, 'rev-parse', 'HEAD')}:"
                f"{self._git_value(checkout, 'rev-parse', 'HEAD^{tree}')}"
            )
            self._check(
                checks,
                "swebench-checkout",
                f"{SWE_BENCH_COMMIT}:{SWE_BENCH_TREE}",
                observed_checkout,
                observed_checkout == f"{SWE_BENCH_COMMIT}:{SWE_BENCH_TREE}",
            )
        except Exception as exc:
            self._check(
                checks,
                "swebench-checkout",
                f"{SWE_BENCH_COMMIT}:{SWE_BENCH_TREE}",
                "unavailable",
                False,
                str(exc),
            )

        host_cpus = os.cpu_count() or 0
        self._check(
            checks,
            "host-cpu",
            f">={self.config.cpu_limit:g}",
            str(host_cpus),
            host_cpus >= self.config.cpu_limit,
        )

        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            descriptor, probe_name = tempfile.mkstemp(
                prefix=".doctor-write-", dir=self.cache_root
            )
            os.close(descriptor)
            Path(probe_name).unlink()
            cache_writable = True
            cache_error = ""
        except OSError as exc:
            cache_writable = False
            cache_error = str(exc)
        self._check(
            checks,
            "cache-writable",
            "writable",
            str(self.cache_root),
            cache_writable,
            cache_error,
        )

        docker = shutil.which("docker")
        self._check(checks, "docker", "available", docker or "unavailable", bool(docker))
        if docker:
            evidence = run_command(
                [docker, "version", "--format", "{{json .Server}}"],
                cwd=self.project_root,
                artifact_dir=artifact_root / "docker-version",
                name="docker-version",
                timeout_seconds=60,
                env=self.command_env(),
                inherit_env=False,
            )
            observed = Path(evidence.stdout_path).read_text(encoding="utf-8").strip()
            docker_ok = False
            if evidence.passed:
                try:
                    server = json.loads(observed)
                    docker_ok = (
                        str(server.get("Os") or "").lower() == "linux"
                        and str(server.get("Arch") or "").lower() in {"amd64", "x86_64"}
                    )
                except json.JSONDecodeError:
                    pass
            self._check(
                checks,
                "docker-server",
                SWE_PLATFORM,
                observed,
                docker_ok,
                "" if docker_ok else "Docker server is unavailable or incompatible",
            )
            if evidence.passed and docker_ok:
                api_version = str(server.get("ApiVersion") or "")
                try:
                    api_parts = tuple(int(item) for item in api_version.split("."))
                    api_ok = api_parts >= (1, 40)
                except ValueError:
                    api_ok = False
                self._check(
                    checks,
                    "docker-api",
                    ">=1.40",
                    api_version or "unavailable",
                    api_ok,
                )
            info = run_command(
                [docker, "info", "--format", "{{json .}}"],
                cwd=self.project_root,
                artifact_dir=artifact_root / "docker-info",
                name="docker-info",
                timeout_seconds=60,
                env=self.command_env(),
                inherit_env=False,
            )
            docker_cpus = 0.0
            docker_memory = 0
            if info.passed:
                try:
                    info_data = json.loads(
                        Path(info.stdout_path).read_text(encoding="utf-8")
                    )
                    docker_cpus = float(info_data.get("NCPU") or 0)
                    docker_memory = int(info_data.get("MemTotal") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            self._check(
                checks,
                "docker-cpu",
                f">={self.config.cpu_limit:g}",
                f"{docker_cpus:g}",
                info.passed and docker_cpus >= self.config.cpu_limit,
            )
            memory_gib = docker_memory / (1024**3)
            requested_memory = int(self.config.memory_limit.rstrip("gG"))
            self._check(
                checks,
                "docker-memory",
                f">={requested_memory} GiB",
                f"{memory_gib:.2f} GiB",
                info.passed and memory_gib >= requested_memory,
            )
            images = run_command(
                [docker, "image", "ls", "--format", "{{.ID}}"],
                cwd=self.project_root,
                artifact_dir=artifact_root / "docker-image-api",
                name="docker-image-api",
                timeout_seconds=60,
                env=self.command_env(),
                inherit_env=False,
            )
            image_count = len(
                [
                    line
                    for line in Path(images.stdout_path)
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
            )
            self._check(
                checks,
                "docker-image-api",
                "accessible",
                f"{image_count} local images",
                images.passed,
            )

        free_gb = shutil.disk_usage(self.cache_root.parent).free / (1024**3)
        self._check(
            checks,
            "free-disk",
            f">={self.benchmark_config.min_free_disk_gb} GiB",
            f"{free_gb:.2f} GiB",
            free_gb >= self.benchmark_config.min_free_disk_gb,
        )

        try:
            snapshot = self.ensure_dataset_snapshot(adapter, artifact_root)
            self._check(
                checks,
                "dataset-snapshot",
                self._dataset_identity(adapter)[1],
                f"{snapshot.revision}:{snapshot.row_count}:{snapshot.sha256}",
                snapshot.row_count > 0,
            )
        except Exception as exc:
            self._check(
                checks,
                "dataset-snapshot",
                self._dataset_identity(adapter)[1],
                "unavailable",
                False,
                str(exc),
            )

        if adapter == "swebench_live":
            try:
                checkout = self.ensure_live_checkout(artifact_root)
                self._check(
                    checks,
                    "live-checkout",
                    (
                        f"{SWE_LIVE_COMMIT}:{SWE_LIVE_TREE}:"
                        f"{SWE_LIVE_LAUNCH_COMMIT}:{SWE_LIVE_LAUNCH_TREE}"
                    ),
                    (
                        f"{self._git_value(checkout, 'rev-parse', 'HEAD')}:"
                        f"{self._git_value(checkout, 'rev-parse', 'HEAD^{tree}')}:"
                        f"{self._git_value(checkout / 'launch', 'rev-parse', 'HEAD')}:"
                        f"{self._git_value(checkout / 'launch', 'rev-parse', 'HEAD^{tree}')}"
                    ),
                    True,
                )
            except Exception as exc:
                self._check(
                    checks,
                    "live-checkout",
                    (
                        f"{SWE_LIVE_COMMIT}:{SWE_LIVE_TREE}:"
                        f"{SWE_LIVE_LAUNCH_COMMIT}:{SWE_LIVE_LAUNCH_TREE}"
                    ),
                    "unavailable",
                    False,
                    str(exc),
                )

        return DoctorReport(
            adapter=adapter,
            framework_revision=(
                self.config.swebench_commit
                if adapter == "swebench_verified"
                else self.config.live_commit
            ),
            runtime_id=self.runtime_id,
            verifier_runtime_id=self.runtime_id,
            started_at=started_at,
            finished_at=utc_now(),
            checks=tuple(checks),
        )
