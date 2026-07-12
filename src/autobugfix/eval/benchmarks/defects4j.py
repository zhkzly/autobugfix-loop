from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from autobugfix.eval.benchmarks.issues import IssueEvidenceError, IssueEvidenceFetcher
from autobugfix.eval.benchmarks.models import (
    BenchmarkCaseSeed,
    BenchmarkSeedManifest,
    CommandEvidence,
    DoctorCheck,
    DoctorReport,
    EligibilityReceipt,
    ExperimentRole,
    digest_file,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.models import EvalBenchmarkConfig, utc_now


class Defects4JError(RuntimeError):
    pass


class Defects4JEligibilityError(Defects4JError):
    def __init__(self, message: str, *, ineligible: bool = False):
        super().__init__(message)
        self.ineligible = ineligible


class Defects4JRuntime:
    """Pinned Docker authority for materializing and testing Defects4J cases."""

    name = "defects4j"
    _PROJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

    def __init__(self, config: EvalBenchmarkConfig):
        self.config = config
        self.runtime = config.defects4j
        self._docker_bin: str | None = None
        self._image_id: str | None = None
        self._verifier_image_id: str | None = None

    @property
    def docker_bin(self) -> str | None:
        if self._docker_bin is not None:
            return self._docker_bin
        override = os.environ.get("AUTOBUGFIX_DOCKER_BIN")
        candidates = [override] if override else []
        candidates.extend((shutil.which("docker"), shutil.which("docker.exe")))
        for candidate in candidates:
            if not candidate:
                continue
            discovered = shutil.which(candidate)
            path = Path(candidate).expanduser()
            if discovered:
                self._docker_bin = discovered
                return self._docker_bin
            if path.is_file() and os.access(path, os.X_OK):
                self._docker_bin = str(path)
                return self._docker_bin
        return None

    @property
    def runtime_id(self) -> str:
        if self._image_id is None:
            raise Defects4JError("Defects4J Docker image has not passed inspection")
        return self._image_id

    @property
    def verifier_runtime_id(self) -> str:
        if self._verifier_image_id is None:
            raise Defects4JError(
                "Defects4J verifier Docker image has not passed inspection"
            )
        return self._verifier_image_id

    def bind_inspected_runtime(
        self,
        *,
        docker_bin: str,
        runtime_id: str,
        verifier_runtime_id: str,
    ) -> None:
        if not runtime_id.startswith("sha256:") or not verifier_runtime_id.startswith(
            "sha256:"
        ):
            raise Defects4JError("inspected runtime IDs must be immutable image IDs")
        self._docker_bin = docker_bin
        self._image_id = runtime_id
        self._verifier_image_id = verifier_runtime_id

    def _mount_source(self, path: Path) -> str:
        resolved = path.resolve()
        docker = (self.docker_bin or "").lower()
        distro = os.environ.get("WSL_DISTRO_NAME")
        if docker.endswith(".exe") and distro and os.name != "nt":
            suffix = str(resolved).replace("/", "\\")
            return f"\\\\wsl.localhost\\{distro}{suffix}"
        return str(resolved)

    def _docker_run_argv(
        self,
        command: Sequence[str],
        *,
        mounts: Sequence[tuple[Path, str, bool]] = (),
        user: tuple[int, int] | None = None,
        capabilities: Sequence[str] = (),
        cidfile: Path | None = None,
        image: str | None = None,
    ) -> list[str]:
        docker = self.docker_bin
        if docker is None:
            raise Defects4JError("Docker CLI was not found on PATH")
        image_ref = image or self.runtime_id
        argv = [
            docker,
            "run",
            "--rm",
            "--init",
            "--platform",
            self.runtime.platform,
            "--network",
            "none",
            "--memory",
            self.runtime.memory_limit,
            "--cpus",
            str(self.runtime.cpu_limit),
            "--pids-limit",
            str(self.runtime.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-e",
            "HOME=/tmp",
            "-e",
            f"TZ={self.runtime.timezone}",
            "-e",
            "LANG=C.UTF-8",
            "-e",
            "LC_ALL=C.UTF-8",
        ]
        if cidfile is not None:
            argv.extend(("--cidfile", self._mount_source(cidfile)))
        for capability in capabilities:
            argv.extend(("--cap-add", capability))
        if user is not None:
            argv.extend(("--user", f"{user[0]}:{user[1]}"))
        for host, container, readonly in mounts:
            mount = (
                f"type=bind,source={self._mount_source(host)},target={container}"
            )
            if readonly:
                mount += ",readonly"
            argv.extend(("--mount", mount))
        argv.append(image_ref)
        argv.extend(str(item) for item in command)
        return argv

    def _run_host(
        self,
        argv: Sequence[str],
        *,
        artifact_root: Path,
        name: str,
        timeout_seconds: int | None = None,
    ) -> CommandEvidence:
        self.config.cache_root.mkdir(parents=True, exist_ok=True)
        return run_command(
            argv,
            cwd=self.config.cache_root,
            artifact_dir=artifact_root / name / uuid.uuid4().hex,
            name=name,
            timeout_seconds=timeout_seconds or self.config.command_timeout_seconds,
            env={"TZ": self.runtime.timezone},
        )

    def _run_container(
        self,
        command: Sequence[str],
        *,
        artifact_root: Path,
        name: str,
        mounts: Sequence[tuple[Path, str, bool]] = (),
        user: tuple[int, int] | None = None,
        capabilities: Sequence[str] = (),
        image: str | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandEvidence:
        self.config.cache_root.mkdir(parents=True, exist_ok=True)
        invocation_root = artifact_root / name / uuid.uuid4().hex
        invocation_root.mkdir(parents=True, exist_ok=False)
        cidfile = invocation_root / "container.cid"
        evidence = run_command(
            self._docker_run_argv(
                command,
                mounts=mounts,
                user=user,
                capabilities=capabilities,
                cidfile=cidfile,
                image=image,
            ),
            cwd=self.config.cache_root,
            artifact_dir=invocation_root,
            name=name,
            timeout_seconds=timeout_seconds or self.config.command_timeout_seconds,
            env={"TZ": self.runtime.timezone},
        )
        if evidence.timed_out and cidfile.is_file():
            container_id = cidfile.read_text(encoding="utf-8", errors="replace").strip()
            if container_id and self.docker_bin is not None:
                run_command(
                    [self.docker_bin, "rm", "-f", container_id],
                    cwd=self.config.cache_root,
                    artifact_dir=invocation_root / "forced-container-cleanup",
                    name=f"{name}-forced-container-cleanup",
                    timeout_seconds=60,
                    env={"TZ": self.runtime.timezone},
                )
        return evidence

    @staticmethod
    def _output(evidence: CommandEvidence) -> str:
        return "\n".join(
            (
                Path(evidence.stdout_path).read_text(
                    encoding="utf-8", errors="replace"
                ),
                Path(evidence.stderr_path).read_text(
                    encoding="utf-8", errors="replace"
                ),
            )
        ).strip()

    @staticmethod
    def _check(
        name: str,
        evidence: CommandEvidence,
        *,
        expected: str,
        predicate: bool,
        observed: str | None = None,
    ) -> DoctorCheck:
        passed = evidence.passed and predicate
        return DoctorCheck(
            name=name,
            passed=passed,
            expected=expected,
            observed=(observed or Defects4JRuntime._output(evidence))[:1000],
            error="" if passed else "runtime contract failed",
        )

    def _inspect_image(
        self,
        artifact_root: Path,
        *,
        image_ref: str,
        name: str,
    ) -> tuple[CommandEvidence, dict[str, Any], str]:
        docker = self.docker_bin
        if docker is None:
            raise Defects4JError("Docker CLI was not found on PATH")
        evidence = self._run_host(
            [docker, "image", "inspect", image_ref],
            artifact_root=artifact_root,
            name=name,
            timeout_seconds=120,
        )
        if not evidence.passed:
            raise Defects4JError(
                f"Docker image is unavailable: {image_ref}"
            )
        try:
            payload = json.loads(
                Path(evidence.stdout_path).read_text(encoding="utf-8")
            )
            image = payload[0]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise Defects4JError("Docker image inspect returned invalid JSON") from exc
        image_id = str(image.get("Id") or "")
        if not image_id.startswith("sha256:"):
            raise Defects4JError("Docker image inspect did not return an immutable ID")
        return evidence, image, image_id

    def doctor(self, artifact_root: Path) -> DoctorReport:
        artifact_root.mkdir(parents=True, exist_ok=True)
        started_at = utc_now()
        checks: list[DoctorCheck] = []
        docker = self.docker_bin
        if docker is None:
            checks.append(
                DoctorCheck(
                    "docker",
                    False,
                    "Docker client and daemon available",
                    "missing",
                    "Docker CLI was not found on PATH",
                )
            )
        else:
            version = self._run_host(
                [docker, "version", "--format", "{{json .Server}}"],
                artifact_root=artifact_root,
                name="docker-version",
                timeout_seconds=120,
            )
            checks.append(
                self._check(
                    "docker",
                    version,
                    expected="Docker client and daemon available",
                    predicate=bool(self._output(version)),
                )
            )

        image: dict[str, Any] | None = None
        verifier_image: dict[str, Any] | None = None
        if checks and checks[-1].passed:
            try:
                inspect_evidence, image, image_id = self._inspect_image(
                    artifact_root,
                    image_ref=self.runtime.image,
                    name="materializer-image-inspect",
                )
                self._image_id = image_id
                labels = (image.get("Config") or {}).get("Labels") or {}
                revision = str(
                    labels.get("org.autobugfix.defects4j.revision") or ""
                )
                observed_platform = f"{image.get('Os')}/{image.get('Architecture')}"
                checks.extend(
                    (
                        self._check(
                            "image",
                            inspect_evidence,
                            expected=self.runtime.image,
                            predicate=True,
                            observed=image_id,
                        ),
                        self._check(
                            "platform",
                            inspect_evidence,
                            expected=self.runtime.platform,
                            predicate=observed_platform == self.runtime.platform,
                            observed=observed_platform,
                        ),
                        self._check(
                            "framework_revision",
                            inspect_evidence,
                            expected=self.runtime.framework_revision,
                            predicate=revision == self.runtime.framework_revision,
                            observed=revision or "missing label",
                        ),
                    )
                )
            except Defects4JError as exc:
                checks.append(
                    DoctorCheck(
                        "image",
                        False,
                        self.runtime.image,
                        "unavailable",
                        str(exc),
                    )
                )

            try:
                verifier_evidence, verifier_image, verifier_image_id = self._inspect_image(
                    artifact_root,
                    image_ref=self.runtime.verifier_image,
                    name="verifier-image-inspect",
                )
                self._verifier_image_id = verifier_image_id
                labels = (verifier_image.get("Config") or {}).get("Labels") or {}
                revision = str(
                    labels.get("org.autobugfix.defects4j.revision") or ""
                )
                role = str(labels.get("org.autobugfix.defects4j.role") or "")
                observed_platform = (
                    f"{verifier_image.get('Os')}/{verifier_image.get('Architecture')}"
                )
                checks.extend(
                    (
                        self._check(
                            "verifier_image",
                            verifier_evidence,
                            expected=self.runtime.verifier_image,
                            predicate=role == "verifier",
                            observed=f"{verifier_image_id} role={role or 'missing'}",
                        ),
                        self._check(
                            "verifier_platform",
                            verifier_evidence,
                            expected=self.runtime.platform,
                            predicate=observed_platform == self.runtime.platform,
                            observed=observed_platform,
                        ),
                        self._check(
                            "verifier_framework_revision",
                            verifier_evidence,
                            expected=self.runtime.framework_revision,
                            predicate=revision == self.runtime.framework_revision,
                            observed=revision or "missing label",
                        ),
                    )
                )
            except Defects4JError as exc:
                checks.append(
                    DoctorCheck(
                        "verifier_image",
                        False,
                        self.runtime.verifier_image,
                        "unavailable",
                        str(exc),
                    )
                )

        if image is not None and verifier_image is not None and all(item.passed for item in checks):
            revision = self._run_container(
                ["git", "-C", "/defects4j", "rev-parse", "HEAD"],
                artifact_root=artifact_root,
                name="container-framework-revision",
                timeout_seconds=120,
            )
            observed_revision = Path(revision.stdout_path).read_text(
                encoding="utf-8"
            ).strip()
            checks.append(
                self._check(
                    "container_framework_revision",
                    revision,
                    expected=self.runtime.framework_revision,
                    predicate=observed_revision == self.runtime.framework_revision,
                    observed=observed_revision,
                )
            )
            verifier_sanitized = self._run_container(
                [
                    "/bin/sh",
                    "-c",
                    "test ! -d /defects4j/.git "
                    "&& test -f /defects4j/project_repos/README "
                    "&& test -z \"$(find /defects4j/project_repos -mindepth 1 "
                    "-maxdepth 1 ! -name README -print -quit)\" "
                    "&& test -z \"$(find /defects4j/framework/projects -type f "
                    "\\( -path '*/patches/*' -o -path '*/modified_classes/*' "
                    "-o -path '*/loaded_classes/*' -o -path '*/relevant_tests/*' "
                    "-o -path '*/trigger_tests/*' \\) -print -quit)\" "
                    f"&& test \"$(cat /opt/autobugfix/defects4j-revision)\" = \"{self.runtime.framework_revision}\"",
                ],
                artifact_root=artifact_root,
                name="verifier-sanitization",
                image=self.verifier_runtime_id,
                timeout_seconds=120,
            )
            observed_verifier = self._output(verifier_sanitized) or "sanitized"
            checks.append(
                self._check(
                    "verifier_sanitization",
                    verifier_sanitized,
                    expected=(
                        "no Git history, project repository content, or developer patches"
                    ),
                    predicate=verifier_sanitized.passed,
                    observed=observed_verifier,
                )
            )
            verifier_info = self._run_container(
                ["defects4j", "info", "-p", "Lang"],
                artifact_root=artifact_root,
                name="verifier-framework-info",
                image=self.verifier_runtime_id,
                timeout_seconds=120,
            )
            checks.append(
                self._check(
                    "verifier_framework_info",
                    verifier_info,
                    expected="sanitized verifier can initialize Defects4J",
                    predicate="Lang" in self._output(verifier_info),
                )
            )
            java = self._run_container(
                ["java", "-version"],
                artifact_root=artifact_root,
                name="container-java",
                timeout_seconds=120,
            )
            checks.append(
                self._check(
                    "container_java",
                    java,
                    expected="Java major version 11",
                    predicate='version "11.' in self._output(java),
                )
            )
            info = self._run_container(
                ["defects4j", "info", "-p", "Lang"],
                artifact_root=artifact_root,
                name="framework-info",
                timeout_seconds=120,
            )
            checks.append(
                self._check(
                    "framework_info",
                    info,
                    expected="Defects4J info succeeds",
                    predicate="Lang" in self._output(info),
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "framework_info",
                    False,
                    "Defects4J info succeeds",
                    "not run",
                    "Docker or image prerequisite failed",
                )
            )

        free_gb = shutil.disk_usage(self.config.cache_root.parent).free // (1024**3)
        checks.append(
            DoctorCheck(
                "cache_disk",
                free_gb >= self.config.min_free_disk_gb,
                f">= {self.config.min_free_disk_gb} GiB free",
                f"{free_gb} GiB free",
                "" if free_gb >= self.config.min_free_disk_gb else "insufficient disk",
            )
        )
        return DoctorReport(
            adapter=self.name,
            framework_revision=self.runtime.framework_revision,
            started_at=started_at,
            finished_at=utc_now(),
            checks=tuple(checks),
            runtime_id=self._image_id or "unavailable",
            verifier_runtime_id=self._verifier_image_id or "unavailable",
        )

    def _require_project(self, project: str) -> None:
        if not self._PROJECT_RE.fullmatch(project):
            raise Defects4JError(f"invalid Defects4J project: {project!r}")

    def _framework_text(
        self,
        relative_path: str,
        *,
        artifact_root: Path,
        name: str,
        commands: list[CommandEvidence],
    ) -> str:
        evidence = self._run_container(
            ["/bin/cat", f"/defects4j/{relative_path}"],
            artifact_root=artifact_root,
            name=name,
        )
        commands.append(evidence)
        if not evidence.passed:
            raise Defects4JEligibilityError(
                f"Defects4J framework file is unavailable: {relative_path}"
            )
        return Path(evidence.stdout_path).read_text(encoding="utf-8")

    def active_bug(
        self,
        project: str,
        bug_id: int,
        *,
        artifact_root: Path | None = None,
        commands: list[CommandEvidence] | None = None,
    ) -> Mapping[str, str]:
        self._require_project(project)
        target_commands = commands if commands is not None else []
        root = artifact_root or self.config.cache_root / "metadata" / project
        text = self._framework_text(
            f"framework/projects/{project}/active-bugs.csv",
            artifact_root=root,
            name="active-bugs",
            commands=target_commands,
        )
        for row in csv.DictReader(io.StringIO(text)):
            if int(row["bug.id"]) == bug_id:
                return row
        raise Defects4JError(f"inactive or unknown Defects4J case: {project}-{bug_id}")

    def active_bug_ids(self, project: str) -> tuple[int, ...]:
        self._require_project(project)
        commands: list[CommandEvidence] = []
        text = self._framework_text(
            f"framework/projects/{project}/active-bugs.csv",
            artifact_root=self.config.cache_root / "metadata" / project,
            name="active-bug-ids",
            commands=commands,
        )
        return tuple(int(row["bug.id"]) for row in csv.DictReader(io.StringIO(text)))

    @staticmethod
    def _failing_tests(path: Path) -> tuple[str, ...]:
        if not path.is_file():
            raise Defects4JEligibilityError("Defects4J did not write failing_tests")
        failures = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("--- "):
                failures.append(line[4:].strip())
        return tuple(dict.fromkeys(item for item in failures if item))

    @staticmethod
    def _triggering_tests(value: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(line.strip() for line in value.splitlines() if line.strip())
        )

    @staticmethod
    def _source_roots(value: str) -> tuple[str, ...]:
        lines: list[str] = []
        for line in value.splitlines():
            lines.extend(item for item in line.split(os.pathsep) if item)
        return tuple(dict.fromkeys(item.strip() for item in lines if item.strip()))

    @staticmethod
    def _current_user() -> tuple[int, int] | None:
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            return os.getuid(), os.getgid()
        return None

    def _record_success(
        self,
        evidence: CommandEvidence,
        *,
        name: str,
        commands: list[CommandEvidence],
    ) -> CommandEvidence:
        commands.append(evidence)
        if not evidence.passed:
            raise Defects4JEligibilityError(
                f"Defects4J command failed: {name} "
                f"(exit={evidence.exit_code}, timeout={evidence.timed_out})"
            )
        return evidence

    def _checkout(
        self,
        project: str,
        version: str,
        destination: Path,
        *,
        artifact_root: Path,
        name: str,
        commands: list[CommandEvidence],
    ) -> None:
        if destination.exists():
            raise Defects4JEligibilityError(
                f"checkout destination already exists: {destination}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        user = self._current_user()
        destination_path = f"/workspace/{destination.name}"
        command = [
            "defects4j",
            "checkout",
            "-p",
            project,
            "-v",
            version,
            "-w",
            destination_path,
        ]
        if user is not None:
            command = [
                "/bin/sh",
                "-eu",
                "-c",
                (
                    "git config --global --add safe.directory '*' "
                    "&& exec defects4j checkout -p \"$1\" -v \"$2\" -w \"$3\""
                ),
                "autobugfix-checkout",
                project,
                version,
                destination_path,
            ]
        checkout = self._run_container(
            command,
            artifact_root=artifact_root,
            name=name,
            mounts=((destination.parent, "/workspace", False),),
            user=user,
        )
        self._record_success(checkout, name=name, commands=commands)

    def _export(
        self,
        worktree: Path,
        property_name: str,
        *,
        artifact_root: Path,
        name: str,
        commands: list[CommandEvidence],
    ) -> str:
        evidence = self._run_container(
            ["defects4j", "export", "-p", property_name, "-w", "/workspace"],
            artifact_root=artifact_root,
            name=name,
            mounts=((worktree, "/workspace", False),),
            user=self._current_user(),
        )
        self._record_success(evidence, name=name, commands=commands)
        return Path(evidence.stdout_path).read_text(encoding="utf-8").strip()

    def _test(
        self,
        worktree: Path,
        *,
        artifact_root: Path,
        name: str,
        commands: list[CommandEvidence],
    ) -> tuple[str, ...]:
        failing_path = worktree / "failing_tests"
        if failing_path.exists():
            failing_path.unlink()
        evidence = self._run_container(
            ["defects4j", "test", "-w", "/workspace"],
            artifact_root=artifact_root,
            name=name,
            mounts=((worktree, "/workspace", False),),
            user=self._current_user(),
        )
        self._record_success(evidence, name=name, commands=commands)
        failures = self._failing_tests(failing_path)
        shutil.copy2(failing_path, artifact_root / name / "failing_tests")
        return failures

    @staticmethod
    def _repair_contract(
        triggering_tests: Sequence[str],
        observed_buggy: Sequence[Sequence[str]],
        observed_fixed: Sequence[Sequence[str]],
    ) -> tuple[str, ...]:
        if not observed_buggy or not observed_fixed:
            raise Defects4JEligibilityError(
                "preflight requires buggy and fixed observations"
            )
        fixed_sets = [frozenset(items) for items in observed_fixed]
        if any(items != fixed_sets[0] for items in fixed_sets[1:]):
            raise Defects4JEligibilityError(
                "fixed failure baseline was unstable",
                ineligible=True,
            )
        baseline = fixed_sets[0]
        triggering = frozenset(triggering_tests)
        if triggering & baseline:
            raise Defects4JEligibilityError(
                "fixed revision still fails a triggering test",
                ineligible=True,
            )
        expected_buggy = baseline | triggering
        buggy_sets = [frozenset(items) for items in observed_buggy]
        if any(items != expected_buggy for items in buggy_sets):
            raise Defects4JEligibilityError(
                "buggy failures were unstable or differed from fixed baseline plus triggering tests",
                ineligible=True,
            )
        return tuple(sorted(baseline))

    def verify_worktree(
        self,
        worktree: Path,
        *,
        artifact_root: Path,
        name: str = "official-test",
        image: str | None = None,
        single_test: str | None = None,
    ) -> tuple[CommandEvidence, tuple[str, ...]]:
        failing_path = worktree / "failing_tests"
        if failing_path.exists():
            failing_path.unlink()
        command = ["defects4j", "test", "-w", "/workspace"]
        if single_test is not None:
            command.extend(("-t", single_test))
        evidence = self._run_container(
            command,
            artifact_root=artifact_root,
            name=name,
            mounts=((worktree, "/workspace", False),),
            user=self._current_user(),
            image=image,
        )
        failures = self._failing_tests(failing_path) if failing_path.is_file() else ()
        if failing_path.is_file():
            shutil.copy2(failing_path, artifact_root / name / "failing_tests")
        else:
            (artifact_root / name / "failing_tests").write_text(
                "", encoding="utf-8"
            )
        return evidence, failures

    def _sanitize_snapshot(
        self,
        worktree: Path,
        *,
        artifact_root: Path,
        commands: list[CommandEvidence],
    ) -> str:
        for path in sorted(worktree.rglob(".svn"), reverse=True):
            if path.is_dir():
                shutil.rmtree(path)
        git_dir = worktree / ".git"
        if git_dir.is_dir():
            shutil.rmtree(git_dir)
        elif git_dir.exists():
            git_dir.unlink()
        failing_tests = worktree / "failing_tests"
        if failing_tests.exists():
            failing_tests.unlink()
        for metadata_name in (".defects4j.config", "defects4j.build.properties"):
            metadata = worktree / metadata_name
            if metadata.is_dir():
                shutil.rmtree(metadata)
            elif metadata.exists() or metadata.is_symlink():
                metadata.unlink()
        git = shutil.which("git")
        if git is None:
            raise Defects4JEligibilityError("host Git is required for task worktrees")
        fixed_git_env = {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
        for name, argv, env in (
            ("snapshot-git-init", [git, "init", "--initial-branch=main"], None),
            ("snapshot-git-add", [git, "add", "-A"], None),
            (
                "snapshot-git-commit",
                [
                    git,
                    "-c",
                    "user.name=Autobugfix Benchmark Guard",
                    "-c",
                    "user.email=autobugfix-guard@example.invalid",
                    "commit",
                    "-m",
                    "Defects4J buggy snapshot",
                ],
                fixed_git_env,
            ),
        ):
            evidence = run_command(
                argv,
                cwd=worktree,
                artifact_dir=artifact_root / name,
                name=name,
                timeout_seconds=self.config.command_timeout_seconds,
                env=env,
            )
            self._record_success(evidence, name=name, commands=commands)
        head = run_command(
            [git, "rev-parse", "HEAD"],
            cwd=worktree,
            artifact_dir=artifact_root / "snapshot-git-head",
            name="snapshot-git-head",
            timeout_seconds=60,
        )
        self._record_success(head, name="snapshot-git-head", commands=commands)
        return Path(head.stdout_path).read_text(encoding="utf-8").strip()

    def preflight_case(
        self,
        manifest: BenchmarkSeedManifest,
        case: BenchmarkCaseSeed,
        *,
        role: ExperimentRole,
        first_wave: int,
        artifact_root: Path,
    ) -> EligibilityReceipt:
        receipt_id = f"{case.case_id}-{uuid.uuid4().hex[:12]}"
        case_root = artifact_root / receipt_id
        case_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        case_root.chmod(0o700)
        commands: list[CommandEvidence] = []
        issue_digest = "unavailable"
        issue_path = "unavailable"
        buggy_revision = "unavailable"
        fixed_revision = "unavailable"
        triggering_tests: tuple[str, ...] = ()
        baseline_failing_tests: tuple[str, ...] = ()
        source_roots: tuple[str, ...] = ()
        sanitized_repo = "unavailable"
        sanitized_base_sha = "unavailable"
        gold_patch_path = "unavailable"
        gold_patch_sha256 = "unavailable"
        failure_evidence_path = "unavailable"
        failure_evidence_sha256 = "unavailable"
        verifier_metadata_path = "unavailable"
        verifier_metadata_sha256 = "unavailable"
        reproduction_command = "defects4j test -w /workspace"
        status = "harness_error"
        reason = ""
        try:
            metadata = self.active_bug(
                case.project,
                case.bug_id,
                artifact_root=case_root / "commands",
                commands=commands,
            )
            buggy_revision = str(metadata["revision.id.buggy"])
            fixed_revision = str(metadata["revision.id.fixed"])
            try:
                issue = IssueEvidenceFetcher(self.config.issue_timeout_seconds).fetch(
                    report_url=str(metadata["report.url"]),
                    report_id=str(metadata["report.id"]),
                    artifact_dir=case_root / "issue",
                )
                issue_digest = str(issue.to_dict()["record_digest"])
                issue_path = str((case_root / "issue/issue.yaml").resolve())
            except IssueEvidenceError as exc:
                (case_root / "issue").mkdir(parents=True, exist_ok=True)
                (case_root / "issue/fetch-error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
                )

            buggy = case_root / "checkouts/buggy"
            snapshot = self.config.cache_root / "cases" / receipt_id / "source"
            self._checkout(
                case.project,
                f"{case.bug_id}b",
                buggy,
                artifact_root=case_root / "commands",
                name="checkout-buggy",
                commands=commands,
            )
            self._checkout(
                case.project,
                f"{case.bug_id}b",
                snapshot,
                artifact_root=case_root / "commands",
                name="checkout-snapshot",
                commands=commands,
            )
            triggering_tests = self._triggering_tests(
                self._export(
                    buggy,
                    "tests.trigger",
                    artifact_root=case_root / "commands",
                    name="export-triggering-tests",
                    commands=commands,
                )
            )
            source_roots = self._source_roots(
                self._export(
                    buggy,
                    "dir.src.classes",
                    artifact_root=case_root / "commands",
                    name="export-source-roots",
                    commands=commands,
                )
            )
            if not triggering_tests or not source_roots:
                raise Defects4JEligibilityError(
                    "Defects4J exported empty triggering tests or source roots"
                )
            observed_buggy: list[tuple[str, ...]] = []
            observed_fixed: list[tuple[str, ...]] = []
            for repetition in range(1, self.runtime.preflight_repetitions + 1):
                repetition_root = case_root / "checkouts/repetitions" / str(repetition)
                repetition_buggy = repetition_root / "buggy"
                repetition_fixed = repetition_root / "fixed"
                self._checkout(
                    case.project,
                    f"{case.bug_id}b",
                    repetition_buggy,
                    artifact_root=case_root / "commands",
                    name=f"checkout-buggy-{repetition}",
                    commands=commands,
                )
                self._checkout(
                    case.project,
                    f"{case.bug_id}f",
                    repetition_fixed,
                    artifact_root=case_root / "commands",
                    name=f"checkout-fixed-{repetition}",
                    commands=commands,
                )
                observed_buggy.append(
                    self._test(
                        repetition_buggy,
                        artifact_root=case_root / "commands",
                        name=f"buggy-full-{repetition}",
                        commands=commands,
                    )
                )
                if repetition == 1:
                    retained_failure = (
                        case_root
                        / "commands"
                        / "buggy-full-1"
                        / "failing_tests"
                    )
                    if not retained_failure.is_file():
                        raise Defects4JEligibilityError(
                            "Defects4J buggy failure evidence was not retained"
                        )
                    failure_evidence_path = str(retained_failure.resolve())
                    failure_evidence_sha256 = digest_file(retained_failure)
                observed_fixed.append(
                    self._test(
                        repetition_fixed,
                        artifact_root=case_root / "commands",
                        name=f"fixed-full-{repetition}",
                        commands=commands,
                    )
                )
            baseline_failing_tests = self._repair_contract(
                triggering_tests,
                observed_buggy,
                observed_fixed,
            )
            metadata_source = buggy / "defects4j.build.properties"
            if not metadata_source.is_file() or metadata_source.is_symlink():
                raise Defects4JEligibilityError(
                    "Defects4J checkout has no trusted verifier metadata"
                )
            retained_metadata = case_root / "verifier/defects4j.build.properties"
            retained_metadata.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                retained_metadata,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(metadata_source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            verifier_metadata_path = str(retained_metadata.resolve())
            verifier_metadata_sha256 = digest_file(retained_metadata)
            patch = self._framework_text(
                f"framework/projects/{case.project}/patches/{case.bug_id}.src.patch",
                artifact_root=case_root / "commands",
                name="gold-source-patch",
                commands=commands,
            )
            retained_gold = case_root / "oracle/gold.src.patch"
            retained_gold.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                retained_gold,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(patch)
                stream.flush()
                os.fsync(stream.fileno())
            gold_patch_path = str(retained_gold.resolve())
            gold_patch_sha256 = digest_file(retained_gold)
            sanitized_base_sha = self._sanitize_snapshot(
                snapshot,
                artifact_root=case_root / "commands",
                commands=commands,
            )
            sanitized_repo = str(snapshot.resolve())
            status = "eligible"
        except Defects4JEligibilityError as exc:
            status = "ineligible" if exc.ineligible else "harness_error"
            reason = str(exc)
        except Exception as exc:
            status = "harness_error"
            reason = f"{type(exc).__name__}: {exc}"
        return EligibilityReceipt(
            receipt_id=receipt_id,
            manifest_digest=manifest.manifest_digest,
            case_id=case.case_id,
            project=case.project,
            bug_id=case.bug_id,
            role=role,
            first_wave=first_wave,
            framework_revision=manifest.framework_revision,
            dataset_revision=manifest.dataset_revision,
            runtime_id=self._image_id or "unavailable",
            verifier_runtime_id=self._verifier_image_id or "unavailable",
            issue_evidence_digest=issue_digest,
            issue_evidence_path=issue_path,
            buggy_revision=buggy_revision,
            fixed_revision=fixed_revision,
            triggering_tests=triggering_tests,
            baseline_failing_tests=baseline_failing_tests,
            source_roots=source_roots,
            sanitized_repo_path=sanitized_repo,
            sanitized_base_sha=sanitized_base_sha,
            gold_patch_path=gold_patch_path,
            gold_patch_sha256=gold_patch_sha256,
            commands=tuple(item.to_dict() for item in commands),
            status=status,  # type: ignore[arg-type]
            reason=reason,
            created_at=utc_now(),
            failure_evidence_path=failure_evidence_path,
            failure_evidence_sha256=failure_evidence_sha256,
            reproduction_command=reproduction_command,
            verifier_metadata_path=verifier_metadata_path,
            verifier_metadata_sha256=verifier_metadata_sha256,
        )
