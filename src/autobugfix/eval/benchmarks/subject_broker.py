from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_sdk import write_private_text
from autobugfix.config import load_config
from autobugfix.eval.benchmarks.models import (
    digest_file,
    digest_payload,
    record_with_digest,
    verify_record,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.swe_codex import (
    SWECodexServer,
    SWEExecutionLedger,
)
from autobugfix.eval.benchmarks.swe_materialize import SWEMaterializedRepository
from autobugfix.eval.benchmarks.swe_models import (
    SWEInstance,
    SWESubmission,
    SWEVisibleCase,
)
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime, SWERuntimeError
from autobugfix.eval.benchmarks.swe_submission import (
    FrozenSWESubmission,
    SWESubmissionAuthority,
    write_evidence_manifest,
)
from autobugfix.eval.benchmarks.swe_verifier import (
    SWEVerifierServer,
    SWEDockerVisibleVerifier,
    VISIBLE_VERIFIER_COMMAND_ID,
)
from autobugfix.git_utils import git_common_dir, rev_parse, run_git
from autobugfix.models import RepoProfile
from autobugfix.service import AutobugfixService
from autobugfix.study_binding import StudyBindingError, validate_study_binding_shape
from autobugfix.worktree import diff_for_task


class SWESubjectBrokerError(RuntimeError):
    pass


class SWESubjectBroker:
    def __init__(
        self,
        project_root: Path,
        runtime: SWERuntime,
        *,
        authority_root: Path | None = None,
    ):
        self.project_root = project_root.resolve()
        self.runtime = runtime
        self.config = load_config(self.project_root)
        self.trusted_root = (
            authority_root or self.config.eval.benchmarks.trusted_case_root
        ).resolve()
        self.submission_authority = SWESubmissionAuthority(self.trusted_root)

    @staticmethod
    def _git_identity(checkout: Path) -> dict[str, str]:
        return {
            "head": rev_parse(checkout, "HEAD"),
            "tree": rev_parse(checkout, "HEAD^{tree}"),
            "status": run_git(
                checkout,
                ["status", "--porcelain=v1", "--untracked-files=all"],
            ).stdout,
        }

    def _subject_checkout(self, subject_sha: str, artifact_root: Path) -> Path:
        if len(subject_sha) != 40 or any(item not in "0123456789abcdef" for item in subject_sha):
            raise SWESubjectBrokerError("subject SHA must be a full lowercase Git SHA")
        if rev_parse(self.project_root, subject_sha) != subject_sha:
            raise SWESubjectBrokerError("subject SHA is not available in the control repository")
        destination = self.runtime.cache_root / "subjects" / subject_sha
        if destination.exists():
            marker = destination / ".git/autobugfix-sanitized-v1"
            try:
                identity = self._git_identity(destination)
                refs = run_git(destination, ["for-each-ref", "--format=%(refname)"]).stdout
                ignored = run_git(
                    destination,
                    ["status", "--porcelain=v1", "--ignored=matching"],
                ).stdout
                sanitized = (
                    identity["head"] == subject_sha
                    and not identity["status"]
                    and not refs
                    and not ignored
                    and not (destination / ".git/FETCH_HEAD").exists()
                )
            except BaseException:
                sanitized = False
            if marker.is_file():
                if marker.read_text(encoding="utf-8").strip() != subject_sha or not sanitized:
                    raise SWESubjectBrokerError(
                        "materialized subject worktree identity drift"
                    )
            else:
                shutil.rmtree(destination)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            initialize = run_command(
                [
                    "git",
                    "init",
                    str(destination),
                ],
                cwd=self.project_root,
                artifact_dir=artifact_root / "subject-clone",
                name="subject-init",
                timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
            )
            if not initialize.passed:
                raise SWESubjectBrokerError("failed to initialize exact subject repository")
            fetch = run_command(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "-C",
                    str(destination),
                    "fetch",
                    "--depth=1",
                    "--no-tags",
                    self.project_root.as_uri(),
                    subject_sha,
                ],
                cwd=self.project_root,
                artifact_dir=artifact_root / "subject-fetch",
                name="subject-fetch",
                timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
            )
            if not fetch.passed:
                shutil.rmtree(destination, ignore_errors=True)
                raise SWESubjectBrokerError("failed to fetch exact subject SHA")
            checkout = run_command(
                [
                    "git",
                    "-C",
                    str(destination),
                    "checkout",
                    "--detach",
                    "FETCH_HEAD",
                ],
                cwd=self.project_root,
                artifact_dir=artifact_root / "subject-checkout",
                name="subject-checkout",
                timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
            )
            if not checkout.passed:
                shutil.rmtree(destination, ignore_errors=True)
                raise SWESubjectBrokerError("failed to check out exact subject SHA")
            (destination / ".git/FETCH_HEAD").unlink(missing_ok=True)
            (destination / ".git/autobugfix-sanitized-v1").write_text(
                subject_sha + "\n", encoding="utf-8"
            )
        identity = self._git_identity(destination)
        refs = run_git(destination, ["for-each-ref", "--format=%(refname)"]).stdout
        ignored = run_git(
            destination,
            ["status", "--porcelain=v1", "--ignored=matching"],
        ).stdout
        if identity["head"] != subject_sha or identity["status"] or refs or ignored:
            raise SWESubjectBrokerError("materialized subject worktree identity drift")
        return destination.resolve()

    @staticmethod
    def _run_git_step(
        argv: list[str],
        *,
        cwd: Path,
        artifact_root: Path,
        name: str,
        timeout_seconds: int,
    ) -> None:
        evidence = run_command(
            argv,
            cwd=cwd,
            artifact_dir=artifact_root / name,
            name=name,
            timeout_seconds=timeout_seconds,
        )
        if not evidence.passed:
            raise SWESubjectBrokerError(f"target repository preparation failed: {name}")

    def _prepare_target(
        self,
        materialized: SWEMaterializedRepository,
        target_root: Path,
        artifact_root: Path,
    ) -> tuple[Path, Path]:
        source = Path(materialized.source_path).resolve()
        identity = self._git_identity(source)
        if (
            identity["head"] != materialized.base_commit
            or identity["tree"] != materialized.source_tree
            or identity["status"]
        ):
            raise SWESubjectBrokerError("materialized target source identity drift")
        target_root.mkdir(parents=True, exist_ok=True)
        remote = target_root / "remote.git"
        main = target_root / "main"
        self._run_git_step(
            ["git", "clone", "--bare", "--no-hardlinks", str(source), str(remote)],
            cwd=self.project_root,
            artifact_root=artifact_root,
            name="target-bare-clone",
            timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
        )
        self._run_git_step(
            [
                "git",
                "--git-dir",
                str(remote),
                "branch",
                "--force",
                "main",
                materialized.base_commit,
            ],
            cwd=self.project_root,
            artifact_root=artifact_root,
            name="target-main-ref",
            timeout_seconds=120,
        )
        self._run_git_step(
            ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=self.project_root,
            artifact_root=artifact_root,
            name="target-remote-head",
            timeout_seconds=120,
        )
        self._run_git_step(
            ["git", "clone", "--branch", "main", str(remote), str(main)],
            cwd=self.project_root,
            artifact_root=artifact_root,
            name="target-main-clone",
            timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
        )
        for key, value in (
            ("user.email", "autobugfix@example.invalid"),
            ("user.name", "Autobugfix Eval"),
        ):
            run_git(main, ["config", key, value])
        prepared = self._git_identity(main)
        if prepared["head"] != materialized.base_commit or prepared["status"]:
            raise SWESubjectBrokerError("prepared target main checkout identity drift")
        return remote, main.resolve()

    @staticmethod
    def _copy_subject_skills(subject: Path, control: Path) -> None:
        source = subject / ".agents/role-skills"
        destination = control / ".agents/role-skills"
        required = (
            Path("base"),
            Path("execution/writer"),
            Path("execution/evaluator"),
        )
        for relative in required:
            origin = source / relative
            if not origin.is_dir():
                raise SWESubjectBrokerError(
                    f"subject is missing required role skills: {relative.as_posix()}"
                )
            for candidate in (origin, *origin.rglob("*")):
                if candidate.is_symlink():
                    raise SWESubjectBrokerError(
                        "subject role skills cannot contain symlinks: "
                        + candidate.relative_to(source).as_posix()
                    )
            shutil.copytree(origin, destination / relative, symlinks=False)

    @staticmethod
    def _copy_study_memory(snapshot: Path | None, control: Path) -> Path:
        destination = control / ".autobugfix-memory"
        destination.mkdir(mode=0o700)
        if snapshot is None:
            return destination
        source = snapshot.resolve(strict=True)
        if not source.is_dir() or snapshot.is_symlink():
            raise SWESubjectBrokerError("Study Memory snapshot is redirected")
        for candidate in (source, *source.rglob("*")):
            if candidate.is_symlink():
                raise SWESubjectBrokerError("Study Memory snapshot contains a symlink")
        for relative in (Path("active"), Path("skills/approved")):
            origin = source / relative
            if origin.exists():
                if not origin.is_dir():
                    raise SWESubjectBrokerError(
                        f"Study Memory input is not a directory: {relative}"
                    )
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(origin, target, symlinks=False)
        return destination

    @staticmethod
    def _tree_digest(root: Path) -> str:
        files = [path for path in sorted(root.rglob("*")) if path.is_file()]
        return digest_payload(
            {
                "files": [
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": digest_file(path),
                    }
                    for path in files
                ]
            }
        )

    @staticmethod
    def _build_evidence_tree(
        destination: Path,
        sources: Mapping[str, Path],
    ) -> dict[str, Any]:
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
        source_states: dict[str, str] = {}
        for label, source in sorted(sources.items()):
            if not label or "/" in label or label in {".", ".."}:
                raise SWESubjectBrokerError("invalid execution evidence label")
            if not source.exists():
                source_states[label] = "missing"
                continue
            if source.is_symlink():
                raise SWESubjectBrokerError("execution evidence cannot be a symlink")
            target = destination / label
            if source.is_dir():
                for candidate in source.rglob("*"):
                    if candidate.is_symlink():
                        raise SWESubjectBrokerError(
                            "execution evidence cannot contain symlinks"
                        )
                shutil.copytree(source, target, symlinks=False)
                source_states[label] = "directory"
            elif source.is_file():
                shutil.copy2(source, target)
                source_states[label] = "file"
            else:
                raise SWESubjectBrokerError("execution evidence has unsupported type")
        (destination / "source-states.json").write_text(
            json.dumps(source_states, sort_keys=True) + "\n", encoding="utf-8"
        )
        return write_evidence_manifest(destination)

    def _write_control_config(
        self,
        subject: Path,
        control: Path,
        main_checkout: Path,
        worktree_root: Path,
        repo_id: str,
        model: str,
        timeout_seconds: int,
        codex_runtime_root: Path | None = None,
    ) -> Path:
        frozen_config_path = self._subject_config_path(subject)
        subject_config = yaml.safe_load(
            frozen_config_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(subject_config, Mapping):
            raise SWESubjectBrokerError("subject config must be a mapping")
        source_codex = subject_config.get("codex")
        codex = dict(source_codex) if isinstance(source_codex, Mapping) else {}
        codex["default_model"] = model
        codex["writer_model"] = model
        codex["evaluator_model"] = model
        codex["role_runtime"] = {
            "enabled": True,
            "runtime_root": str(
                (codex_runtime_root or (control / ".autobugfix/runtime/codex-sdk"))
                .resolve()
            ),
            "codex_bin": (
                str(self.config.codex.role_runtime.codex_bin)
                if self.config.codex.role_runtime.codex_bin is not None
                else None
            ),
            "bridge_auth": True,
            "skill_guard": True,
            "strict_skill_guard": True,
        }
        roles = dict(codex.get("roles") or {})
        for role, sandbox, approval in (
            ("writer", "workspace-write", "auto_review"),
            ("evaluator", "read-only", "deny_all"),
        ):
            role_config = dict(roles.get(role) or {})
            role_config.update(
                {
                    "backend": "codex",
                    "model": model,
                    "sandbox": sandbox,
                    "approval_mode": approval,
                    "timeout_seconds": timeout_seconds,
                }
            )
            roles[role] = role_config
        codex["roles"] = roles
        config = {
            "task_root": ".autobugfix/tasks",
            "scheduler": {
                "default_max_concurrent": 1,
                "lock_timeout_seconds": timeout_seconds * 3,
                "max_auto_iterations": 2,
                "codex_timeout_seconds": timeout_seconds,
                "writer_timeout_seconds": timeout_seconds,
                "evaluator_timeout_seconds": timeout_seconds,
            },
            "codex": codex,
            "repos": {
                repo_id: {
                    "main_checkout": str(main_checkout),
                    "worktree_root": str(worktree_root),
                    "remote": "origin",
                    "main_branch": "main",
                    "branch_template": "fix/{date}_swe_{slug}",
                    "test_commands": {
                        "targeted": VISIBLE_VERIFIER_COMMAND_ID,
                        "full": VISIBLE_VERIFIER_COMMAND_ID,
                    },
                    "ppe": {"enabled": False, "command_template": None},
                }
            },
        }
        path = control / ".autobugfix/config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return path

    @staticmethod
    def _subject_config_path(subject: Path) -> Path:
        candidates = (
            subject / ".autobugfix/config.yaml",
            subject / "examples/config.yaml",
        )
        for path in candidates:
            if path.is_file() and not path.is_symlink():
                return path
        raise SWESubjectBrokerError(
            "exact subject has no versioned Autobugfix configuration contract"
        )

    @staticmethod
    def _sandbox_dirs(home: Path, paths: tuple[Path, ...]) -> list[str]:
        directories: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(home) or resolved == home:
                continue
            current = resolved
            while current != home:
                directories.add(current)
                current = current.parent
        argv: list[str] = []
        for directory in sorted(directories, key=lambda item: (len(item.parts), str(item))):
            argv.extend(("--dir", str(directory)))
        return argv

    def _sandbox_argv(
        self,
        subject: Path,
        control: Path,
        target: Path,
        request_path: Path,
        result_path: Path,
        capability_root: Path,
    ) -> list[str]:
        bubblewrap = shutil.which("bwrap")
        if not bubblewrap:
            raise SWESubjectBrokerError("exact subject execution requires Bubblewrap")
        host_home = Path.home().resolve()
        runtime_prefix = Path(sys.prefix).resolve()
        base_prefix = Path(sys.base_prefix).resolve()
        runtime_paths = tuple(
            path
            for path in dict.fromkeys(
                (runtime_prefix, base_prefix, base_prefix.parent)
            )
            if path != Path("/") and path.exists()
        )
        runtime_binds = [
            item
            for runtime_path in runtime_paths
            for item in (
                "--ro-bind",
                str(runtime_path),
                str(runtime_path),
            )
        ]
        allowed = (*runtime_paths, subject, control, target, capability_root)
        worker = control / "run_subject.py"
        main = target / "main"
        remote = target / "remote.git"
        docker_authority_masks = [
            path
            for path in (
                Path("/home"),
                Path("/root"),
                Path("/mnt"),
                Path("/media"),
                Path("/srv"),
                Path("/var/lib/docker"),
            )
            if path.is_dir()
        ]
        docker_mask_argv = [
            item
            for path in docker_authority_masks
            for item in ("--tmpfs", str(path))
        ]
        existing_masks = (
            Path("/tmp"),
            Path("/run"),
            host_home,
            self.project_root,
            *docker_authority_masks,
        )
        authority_mask_argv = (
            []
            if any(self.trusted_root.is_relative_to(path) for path in existing_masks)
            else [
                "--tmpfs",
                str(self.trusted_root),
                *self._sandbox_dirs(self.trusted_root, allowed),
            ]
        )
        return [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            *self._sandbox_dirs(Path("/tmp"), allowed),
            "--ro-bind",
            str(capability_root),
            str(capability_root),
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/run",
            *docker_mask_argv,
            "--tmpfs",
            str(host_home),
            *self._sandbox_dirs(host_home, allowed),
            "--tmpfs",
            str(self.project_root),
            *self._sandbox_dirs(self.project_root, allowed),
            *authority_mask_argv,
            *runtime_binds,
            "--ro-bind",
            str(subject),
            str(subject),
            "--tmpfs",
            str(subject / ".git"),
            "--bind",
            str(control),
            str(control),
            "--bind",
            str(target),
            str(target),
            "--ro-bind",
            str(main),
            str(main),
            "--ro-bind",
            str(remote),
            str(remote),
            "--ro-bind",
            str(control / ".autobugfix/config.yaml"),
            str(control / ".autobugfix/config.yaml"),
            "--ro-bind",
            str(control / ".agents"),
            str(control / ".agents"),
            "--ro-bind",
            str(control / ".autobugfix-memory"),
            str(control / ".autobugfix-memory"),
            "--ro-bind",
            str(worker),
            str(worker),
            "--ro-bind",
            str(request_path),
            str(request_path),
            "--setenv",
            "HOME",
            str(control / "broker-home"),
            "--setenv",
            "PYTHONPATH",
            str(subject / "src"),
            "--setenv",
            "VIRTUAL_ENV",
            str(runtime_prefix),
            "--chdir",
            str(control),
            "--",
            sys.executable,
            str(worker),
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]

    @staticmethod
    def _subject_environment() -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key == "LANG" or key.startswith("LC_")
        }
        environment["PATH"] = f"{Path(sys.prefix) / 'bin'}:/usr/local/bin:/usr/bin:/bin"
        environment["TMPDIR"] = "/tmp"
        return environment

    @staticmethod
    def _main_identity(main: Path) -> dict[str, str]:
        return {
            "head": rev_parse(main, "HEAD"),
            "tree": rev_parse(main, "HEAD^{tree}"),
            "status": run_git(
                main, ["status", "--porcelain=v1", "--untracked-files=all"]
            ).stdout,
        }

    @staticmethod
    def _git_control_identity(
        main: Path,
        *,
        allowed_task_branch: str | None = None,
    ) -> dict[str, Any]:
        git_root = main / ".git"
        if not git_root.is_dir() or git_root.is_symlink():
            raise SWESubjectBrokerError("target main Git authority is invalid")
        config = git_root / "config"
        parsed = subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(config),
                "--null",
                "--list",
                "--no-includes",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        config_entries = []
        allowed_entries = (
            {
                f"branch.{allowed_task_branch}.remote": "origin",
                f"branch.{allowed_task_branch}.merge": "refs/heads/main",
            }
            if allowed_task_branch
            else {}
        )
        for item in (value for value in parsed.split("\0") if value):
            key, separator, value = item.partition("\n")
            if not separator:
                raise SWESubjectBrokerError("target Git config output is invalid")
            if key in allowed_entries:
                if value != allowed_entries[key]:
                    raise SWESubjectBrokerError(
                        "target task branch Git config differs from the trusted template"
                    )
                continue
            config_entries.append({"key": key, "value": value})
        candidates = [git_root / "hooks"]
        candidates.extend(
            path
            for path in (
                git_root / "info/attributes",
                git_root / "info/exclude",
            )
            if path.exists()
        )
        worktrees = git_root / "worktrees"
        if worktrees.is_dir():
            candidates.extend(worktrees.glob("*/config.worktree"))
        files: list[dict[str, str]] = []
        for candidate in candidates:
            if not candidate.exists():
                continue
            paths = (
                tuple(path for path in sorted(candidate.rglob("*")) if path.is_file())
                if candidate.is_dir()
                else (candidate,)
            )
            for path in paths:
                if path.is_symlink():
                    raise SWESubjectBrokerError(
                        "target Git control authority cannot contain symlinks"
                    )
                files.append(
                    {
                        "path": path.relative_to(git_root).as_posix(),
                        "sha256": digest_file(path),
                    }
                )
        return record_with_digest(
            {
                "schema": "autobugfix-swe-git-control-v1",
                "config": config_entries,
                "files": files,
            }
        )

    def run(
        self,
        *,
        subject_sha: str,
        expected_subject_tree: str,
        visible_case: SWEVisibleCase,
        instance: SWEInstance,
        materialized: SWEMaterializedRepository,
        image_id: str,
        artifact_root: Path,
        protocol_digest: str,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
        timeout_seconds: int = 900,
        experiment_role: str = "optimization",
        study_binding: Mapping[str, Any] | None = None,
        memory_snapshot: Path | None = None,
        codex_backend_factory: Callable[[str, int, int], CodexBackend] | None = None,
    ) -> FrozenSWESubmission:
        try:
            return self._run_impl(
                subject_sha=subject_sha,
                expected_subject_tree=expected_subject_tree,
                visible_case=visible_case,
                instance=instance,
                materialized=materialized,
                image_id=image_id,
                artifact_root=artifact_root,
                protocol_digest=protocol_digest,
                model=model,
                max_attempts=max_attempts,
                timeout_seconds=timeout_seconds,
                experiment_role=experiment_role,
                study_binding=study_binding,
                memory_snapshot=memory_snapshot,
                codex_backend_factory=codex_backend_factory,
            )
        except BaseException as exc:
            root = artifact_root.resolve()
            failure_path = root / "broker-failure.yaml"
            if root.is_dir() and not failure_path.exists():
                evidence_digest = "unavailable"
                evidence_error = ""
                try:
                    evidence = self._build_evidence_tree(
                        root / "failed-execution-evidence",
                        {
                            "task-root": root / "control/.autobugfix/tasks",
                            "codex-broker": root / "codex-broker",
                            "visible-verifier": root / "visible-verifier",
                            "subject-process": root / "subject-process",
                            "execution-ledger.json": root / "execution-ledger.json",
                            "subject-result.json": root / "control/subject-result.json",
                            "subject-binding.yaml": root / "subject-binding.yaml",
                            "study-binding.yaml": root / "study-binding.yaml",
                        },
                    )
                    evidence_digest = str(evidence["record_digest"])
                except BaseException as evidence_exc:
                    evidence_error = (
                        f"{type(evidence_exc).__name__}: {evidence_exc}"
                    )
                failure = record_with_digest(
                    {
                        "schema": "autobugfix-swe-subject-failure-v1",
                        "error": f"{type(exc).__name__}: {exc}",
                        "evidence_manifest_digest": evidence_digest,
                        "evidence_capture_error": evidence_error,
                    }
                )
                failure_path.write_text(
                    yaml.safe_dump(failure, sort_keys=False), encoding="utf-8"
                )
            raise

    def _run_impl(
        self,
        *,
        subject_sha: str,
        expected_subject_tree: str,
        visible_case: SWEVisibleCase,
        instance: SWEInstance,
        materialized: SWEMaterializedRepository,
        image_id: str,
        artifact_root: Path,
        protocol_digest: str,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
        timeout_seconds: int = 900,
        experiment_role: str = "optimization",
        study_binding: Mapping[str, Any] | None = None,
        memory_snapshot: Path | None = None,
        codex_backend_factory: Callable[[str, int, int], CodexBackend] | None = None,
    ) -> FrozenSWESubmission:
        if model != "gpt-5.4-mini" or max_attempts != 2:
            raise SWESubjectBrokerError("SWE subject budget must use Mini and two attempts")
        if experiment_role not in {"optimization", "sealed_holdout"}:
            raise SWESubjectBrokerError("unsupported SWE experiment role")
        if study_binding is not None:
            if memory_snapshot is None:
                raise SWESubjectBrokerError(
                    "formal SWE subject requires the frozen Study Memory snapshot"
                )
            try:
                verify_record(study_binding)
            except Exception as exc:
                raise SWESubjectBrokerError("SWE Study binding is invalid") from exc
            try:
                validate_study_binding_shape(study_binding)
            except StudyBindingError as exc:
                raise SWESubjectBrokerError(str(exc)) from exc
            if (
                study_binding.get("subject_sha") != subject_sha
                or study_binding.get("subject_tree") != expected_subject_tree
                or study_binding.get("primary_model") != model
                or study_binding.get("target_checkpoint_name") != "H_general"
            ):
                raise SWESubjectBrokerError("SWE Study binding differs from execution")
        root = artifact_root.resolve()
        if not root.is_relative_to(self.trusted_root):
            raise SWESubjectBrokerError("subject run root must be Eval-owned trusted state")
        root.mkdir(parents=True, exist_ok=False)
        subject = self._subject_checkout(subject_sha, root / "subject")
        subject_before = self._git_identity(subject)
        subject_tree = subject_before["tree"]
        if subject_tree != expected_subject_tree:
            raise SWESubjectBrokerError("executed subject tree differs from frozen binding")
        target = root / "target"
        control = root / "control"
        target.mkdir()
        control.mkdir()
        remote, main = self._prepare_target(
            materialized,
            target,
            root / "target-preparation",
        )
        worktree_root = target / "worktrees"
        repo_id = "swe_target"
        self._copy_subject_skills(subject, control)
        memory_root = self._copy_study_memory(memory_snapshot, control)
        subject_config_path = self._subject_config_path(subject)
        config_path = self._write_control_config(
            subject,
            control,
            main,
            worktree_root,
            repo_id,
            model,
            timeout_seconds,
            root / "trusted-codex-runtime",
        )
        worker_source = self.project_root / "harnesses/swebench/scripts/run_subject.py"
        worker = control / "run_subject.py"
        shutil.copy2(worker_source, worker)
        broker_home = control / "broker-home"
        broker_home.mkdir(mode=0o700)
        isolated_config = load_config(control)
        trusted_service = AutobugfixService(control, backend=object())  # type: ignore[arg-type]
        prepared_task = trusted_service.create_task(
            repo_id,
            f"SWE eval {visible_case.case_token}",
            visible_case.problem_statement,
            metadata={
                "origin": "eval",
                "memory_eligible": False,
                "eval_case_token": visible_case.case_token,
                "eval_adapter": visible_case.benchmark,
                "experiment_role": experiment_role,
            },
        )
        for hint in visible_case.public_hints:
            trusted_service.add_context(
                prepared_task.task_id,
                "public-hint",
                hint,
            )
        if not prepared_task.branch or not prepared_task.worktree_path:
            raise SWESubjectBrokerError("trusted broker failed to prepare the Execution task")
        protected_before = {
            "config": digest_file(config_path),
            "skills": self._tree_digest(control / ".agents"),
            "memory": self._tree_digest(memory_root),
            "worker": digest_file(worker),
        }
        main_before = self._main_identity(main)
        git_control_before = self._git_control_identity(
            main,
            allowed_task_branch=prepared_task.branch,
        )
        ledger = SWEExecutionLedger(max_attempts)
        capability_root = Path(
            tempfile.mkdtemp(prefix="autobugfix-swe-cap-", dir="/tmp")
        ).resolve()
        capability_root.chmod(0o700)
        codex_token = secrets.token_hex(32)
        verifier_token = secrets.token_hex(32)
        codex_socket = capability_root / "codex.sock"
        verifier_socket = capability_root / "verifier.sock"
        request_path = control / "subject-request.json"
        result_path = control / "subject-result.json"
        visible_case_digest = str(visible_case.to_dict()["record_digest"])
        subject_binding = record_with_digest({
            "schema": "autobugfix-swe-subject-request-v3",
            "subject_sha": subject_sha,
            "subject_tree": subject_tree,
            "repo_id": repo_id,
            "task_id": prepared_task.task_id,
            "case_token": visible_case.case_token,
            "adapter": visible_case.benchmark,
            "experiment_role": experiment_role,
            "model": model,
            "max_attempts": max_attempts,
            "codex_timeout_seconds": timeout_seconds,
            "verifier_command_id": VISIBLE_VERIFIER_COMMAND_ID,
            "visible_case_digest": visible_case_digest,
            "source_snapshot_digest": materialized.source_digest,
            "source_image_id": image_id,
            "protocol_digest": protocol_digest,
            "runtime_id": self.runtime.runtime_id,
            "study_binding_digest": (
                str(study_binding.get("record_digest") or "")
                if study_binding is not None
                else "development-only"
            ),
            "subject_config_sha256": digest_file(subject_config_path),
            "config_sha256": protected_before["config"],
            "skills_digest": protected_before["skills"],
            "memory_digest": (
                str(study_binding["memory_digest"])
                if study_binding is not None
                else protected_before["memory"]
            ),
            "memory_input_digest": protected_before["memory"],
            "worker_sha256": protected_before["worker"],
        })
        binding_path = root / "subject-binding.yaml"
        binding_path.write_text(
            yaml.safe_dump(subject_binding, sort_keys=False), encoding="utf-8"
        )
        study_binding_path = root / "study-binding.yaml"
        if study_binding is not None:
            study_binding_path.write_text(
                yaml.safe_dump(dict(study_binding), sort_keys=False), encoding="utf-8"
            )
        request = {
            "schema": "autobugfix-swe-subject-capability-v1",
            "binding_digest": subject_binding["record_digest"],
            "control_root": str(control),
            "repo_id": repo_id,
            "task_id": prepared_task.task_id,
            "case_token": visible_case.case_token,
            "adapter": visible_case.benchmark,
            "experiment_role": experiment_role,
            "problem_statement": visible_case.problem_statement,
            "model": model,
            "max_attempts": max_attempts,
            "codex_socket": str(codex_socket),
            "codex_token": codex_token,
            "codex_timeout_seconds": timeout_seconds,
            "verifier_socket": str(verifier_socket),
            "verifier_token": verifier_token,
            "verifier_command_id": VISIBLE_VERIFIER_COMMAND_ID,
        }
        request_path.write_text(json.dumps(request, sort_keys=True) + "\n", encoding="utf-8")
        request_path.chmod(0o600)
        repo = isolated_config.repo(repo_id)
        verifier = SWEDockerVisibleVerifier(
            self.runtime,
            instance,
            repo,
            root / "visible-verifier",
            image_id,
            (control / ".autobugfix/tasks",),
        )
        hidden_paths = tuple(
            dict.fromkeys(
                (
                    self.trusted_root,
                    self.config.eval.benchmarks.cache_root,
                    self.config.operator.state.root,
                    self.config.operator.artifacts.root,
                    self.project_root / ".autobugfix-memory",
                    self.project_root,
                    subject,
                    main,
                    remote,
                )
            )
        )
        command = None
        process_error: BaseException | None = None
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    SWECodexServer(
                        codex_socket,
                        codex_token,
                        control_root=control,
                        repo_id=repo_id,
                        main_checkout=main,
                        worktree_root=worktree_root,
                        artifact_root=root / "codex-broker",
                        hidden_paths=hidden_paths,
                        model=model,
                        ledger=ledger,
                        backend_factory=codex_backend_factory,
                    )
                )
                stack.enter_context(
                    SWEVerifierServer(
                        verifier_socket,
                        verifier_token,
                        verifier,
                        ledger=ledger,
                        max_timeout_seconds=timeout_seconds,
                    )
                )
                command = run_command(
                    self._sandbox_argv(
                        subject,
                        control,
                        target,
                        request_path,
                        result_path,
                        capability_root,
                    ),
                    cwd=self.project_root,
                    artifact_dir=root / "subject-process",
                    name="exact-subject-execution",
                    timeout_seconds=timeout_seconds * max_attempts + 300,
                    env=self._subject_environment(),
                    inherit_env=False,
                )
        except BaseException as exc:
            process_error = exc
        finally:
            try:
                ledger_record = ledger.snapshot()
                write_private_text(
                    root / "execution-ledger.json",
                    json.dumps(ledger_record, sort_keys=True) + "\n",
                )
            finally:
                shutil.rmtree(capability_root, ignore_errors=True)
                request_path.unlink(missing_ok=True)
        if process_error is not None or command is None or not command.passed or not result_path.is_file():
            failure_evidence = root / "failed-execution-evidence"
            failure_manifest = self._build_evidence_tree(
                failure_evidence,
                {
                    "task-root": control / ".autobugfix/tasks",
                    "codex-broker": root / "codex-broker",
                    "visible-verifier": root / "visible-verifier",
                    "subject-process": root / "subject-process",
                    "execution-ledger.json": root / "execution-ledger.json",
                    "subject-result.json": result_path,
                    "subject-binding.yaml": binding_path,
                    "study-binding.yaml": study_binding_path,
                },
            )
            failure = record_with_digest(
                {
                    "schema": "autobugfix-swe-subject-failure-v1",
                    "error": (
                        f"{type(process_error).__name__}: {process_error}"
                        if process_error is not None
                        else "exact subject process failed"
                    ),
                    "command": command.to_dict() if command is not None else None,
                    "evidence_manifest_digest": failure_manifest["record_digest"],
                }
            )
            (root / "broker-failure.yaml").write_text(
                yaml.safe_dump(failure, sort_keys=False), encoding="utf-8"
            )
            raise SWESubjectBrokerError("exact subject process failed")
        protected_after = {
            "config": digest_file(config_path),
            "skills": self._tree_digest(control / ".agents"),
            "memory": self._tree_digest(memory_root),
            "worker": digest_file(worker),
        }
        if protected_after != protected_before:
            raise SWESubjectBrokerError("subject process changed trusted control inputs")
        raw_result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(raw_result, Mapping):
            raise SWESubjectBrokerError("subject process result is invalid")

        task_dirs = tuple((control / ".autobugfix/tasks").glob("*/task.yaml"))
        if len(task_dirs) != 1:
            raise SWESubjectBrokerError("subject process did not create exactly one task")
        task_path = task_dirs[0]
        task_data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        if not isinstance(task_data, Mapping):
            raise SWESubjectBrokerError("subject task record is invalid")
        task_branch = str(task_data.get("branch") or "")
        if not task_branch:
            raise SWESubjectBrokerError("subject task record has no Git branch")
        if (
            self._git_control_identity(
                main, allowed_task_branch=task_branch
            )
            != git_control_before
        ):
            raise SWESubjectBrokerError("subject process changed target Git authority")
        task_dir = task_path.parent
        events_path = task_dir / "events.jsonl"
        worktree = Path(str(task_data.get("worktree_path") or "")).resolve()
        if not worktree.is_relative_to(worktree_root.resolve()):
            raise SWESubjectBrokerError("subject task worktree escaped target ownership")
        if git_common_dir(worktree) != git_common_dir(main):
            raise SWESubjectBrokerError("subject task worktree has foreign Git metadata")
        base_commit = str((task_data.get("metadata") or {}).get("base_commit") or "")
        if base_commit != materialized.base_commit:
            raise SWESubjectBrokerError("subject task base commit differs from case base")
        patch = diff_for_task(repo, worktree, base_commit)
        patch_sha = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        ledger_record = ledger.validate_terminal(patch_sha)
        (root / "execution-ledger.json").write_text(
            json.dumps(ledger_record, sort_keys=True) + "\n", encoding="utf-8"
        )
        if (
            raw_result.get("patch_sha256") != patch_sha
            or raw_result.get("events_sha256") != digest_file(events_path)
            or raw_result.get("task_sha256") != digest_file(task_path)
        ):
            raise SWESubjectBrokerError("candidate-reported result differs from trusted artifacts")

        if self._main_identity(main) != main_before:
            raise SWESubjectBrokerError("subject process changed target main checkout")
        if self._git_identity(subject) != subject_before:
            raise SWESubjectBrokerError("subject process changed exact subject worktree")

        evidence_root = root / "frozen-execution-evidence"
        evidence_manifest = self._build_evidence_tree(
            evidence_root,
            {
                "task": task_dir,
                "codex-broker": root / "codex-broker",
                "visible-verifier": root / "visible-verifier",
                "subject-process": root / "subject-process",
                "execution-ledger.json": root / "execution-ledger.json",
                "subject-result.json": result_path,
                "subject-binding.yaml": binding_path,
                "study-binding.yaml": study_binding_path,
            },
        )
        submission = SWESubmission(
            case_token=visible_case.case_token,
            subject_sha=subject_sha,
            subject_tree=subject_tree,
            base_commit=base_commit,
            patch=patch,
            patch_sha256=patch_sha,
            events_sha256=digest_file(events_path),
            task_sha256=digest_file(task_path),
            subject_request_digest=str(subject_binding["record_digest"]),
            visible_case_digest=visible_case_digest,
            source_snapshot_digest=materialized.source_digest,
            config_digest=protected_after["config"],
            skills_digest=protected_after["skills"],
            execution_ledger_digest=str(ledger_record["record_digest"]),
            evidence_manifest_digest=str(evidence_manifest["record_digest"]),
            frozen_at=str(task_data.get("updated_at") or ""),
        )
        frozen = self.submission_authority.freeze(
            instance.adapter,
            submission,
            evidence_root,
        )
        broker_record = record_with_digest(
            {
                "schema": "autobugfix-swe-subject-broker-v1",
                "case_token": visible_case.case_token,
                "executed_subject_sha": subject_sha,
                "executed_subject_tree": subject_tree,
                "submission_digest": submission.record["record_digest"],
                "subject_request_digest": subject_binding["record_digest"],
                "execution_ledger_digest": ledger_record["record_digest"],
                "command": command.to_dict(),
                "protected_inputs": protected_after,
                "target_main": main_before,
                "task_id": task_path.parent.name,
                "execution_state": str(task_data.get("state") or ""),
                "iterations": int(task_data.get("iterations") or 0),
            }
        )
        (root / "broker-result.yaml").write_text(
            yaml.safe_dump(broker_record, sort_keys=False), encoding="utf-8"
        )
        return frozen
