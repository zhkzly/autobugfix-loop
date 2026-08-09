from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.config import load_config
from autobugfix.eval.artifacts import write_yaml
from autobugfix.eval.benchmarks.authority import (
    GuardCodeIdentity,
    resolve_guard_code_identity,
)
from autobugfix.eval.benchmarks.defects4j import Defects4JRuntime
from autobugfix.eval.benchmarks.guard import (
    GuardBundle,
    GuardCaseSpec,
    decrypt_json,
    encrypt_artifact_tree,
    encrypt_json,
    guard_aad,
    guard_artifact_digest,
    metric_payload,
    new_guard_id,
    signed_metric,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkCaseSeed,
    BenchmarkContractError,
    BenchmarkSeedManifest,
    EligibilityReceipt,
    EvaluationSeedManifest,
    PreparedEvaluationCase,
    PreparedEvaluationManifest,
    canonical_json,
    digest_file,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.store import BenchmarkStore
from autobugfix.eval.benchmarks.subject_broker import (
    SWEExecutionMode,
    SWESubjectBroker,
)
from autobugfix.eval.benchmarks.swe_guard import SWEGuardStore, SWEGuardStoreError
from autobugfix.eval.benchmarks.swe_live import SWELiveAdapter
from autobugfix.eval.benchmarks.swe_materialize import (
    SWEImageMaterializer,
    SWEMaterializedRepository,
)
from autobugfix.eval.benchmarks.swe_models import (
    SWEAttachment,
    SWEExperimentProtocol,
    SWEInstance,
    SWESubjectTreatmentRuntime,
    SWEVisibleCase,
)
from autobugfix.eval.benchmarks.swe_runtime import (
    SWE_GUARD_DAEMON_ISOLATION_LABEL,
    SWEDockerAuthority,
    SWERuntime,
    SWERuntimeError,
)
from autobugfix.eval.benchmarks.swe_verified import SWEVerifiedAdapter
from autobugfix.eval.benchmarks.verify import (
    managed_verifier_for_receipt,
    official_oracle_for_receipt,
)
from autobugfix.eval.reporting import write_evaluation_report
from autobugfix.eval.runner import run_eval
from autobugfix.git_utils import rev_parse, run_git
from autobugfix.models import utc_now
from autobugfix.operator.service import (
    OperatorGovernanceError,
    OperatorGovernanceService,
)
from autobugfix.role_config import resolve_role
from autobugfix.study_binding import StudyBindingError, validate_study_binding_shape


class EvalBenchmarkServiceError(RuntimeError):
    pass


class EvalBenchmarkService:
    def __init__(
        self,
        project_root: Path,
        *,
        guard_authority_resolver: Callable[
            [Path, str], GuardCodeIdentity
        ] = resolve_guard_code_identity,
    ):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        self._guard_authority_resolver = guard_authority_resolver
        benchmark_config = self.config.eval.benchmarks
        self.store = BenchmarkStore(
            benchmark_config.trusted_case_root,
            benchmark_config.visible_manifest_root,
            benchmark_config.cache_root,
        )

    def guard_authority(self) -> GuardCodeIdentity:
        return self._guard_authority_resolver(
            self.project_root,
            self.config.eval.benchmarks.guard.trusted_ref,
        )

    def _doctor_runtime(
        self,
        adapter: str,
        runtime: Defects4JRuntime | SWERuntime,
    ) -> dict[str, Any]:
        artifact_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "doctor-artifacts"
            / adapter
            / uuid.uuid4().hex
        )
        report = (
            runtime.doctor(artifact_root)
            if isinstance(runtime, Defects4JRuntime)
            else runtime.doctor(adapter, artifact_root)
        )
        data = report.to_dict()
        path = self.store.write_doctor(adapter, data)
        return {
            "adapter": adapter,
            "passed": report.passed,
            "framework_revision": report.framework_revision,
            "runtime_id": report.runtime_id,
            "verifier_runtime_id": report.verifier_runtime_id,
            "checks": [item.to_dict() for item in report.checks],
            "report_digest": data["record_digest"],
            "report_path": str(path),
        }

    def doctor(self, adapter: str) -> dict[str, Any]:
        if adapter == "defects4j":
            runtime: Defects4JRuntime | SWERuntime = Defects4JRuntime(
                self.config.eval.benchmarks
            )
        elif adapter in SWERuntime.ADAPTERS:
            runtime = SWERuntime(self.project_root, self.config.eval.benchmarks)
        else:
            raise EvalBenchmarkServiceError(f"unsupported benchmark adapter: {adapter}")
        return self._doctor_runtime(adapter, runtime)

    def _swe_adapter(self, adapter: str):
        runtime = SWERuntime(self.project_root, self.config.eval.benchmarks)
        if adapter == "swebench_verified":
            return SWEVerifiedAdapter(runtime)
        if adapter == "swebench_live":
            return SWELiveAdapter(runtime)
        raise EvalBenchmarkServiceError(f"unsupported SWE adapter: {adapter}")

    def _swe_guard_store(self, guard_root: Path) -> SWEGuardStore:
        codex_runtime = self.config.codex.role_runtime.runtime_root
        if not codex_runtime.is_absolute():
            codex_runtime = self.project_root / codex_runtime
        task_root = self.config.task_root
        if not task_root.is_absolute():
            task_root = self.project_root / task_root
        forbidden_roots = [
            self.project_root,
            task_root,
            self.config.archive_root,
            self.config.eval.benchmarks.cache_root,
            self.config.eval.benchmarks.trusted_case_root,
            self.config.eval.benchmarks.visible_manifest_root,
            self.config.eval.benchmarks.raw_codex.runtime_root,
            self.config.operator.state.root,
            self.config.operator.artifacts.root,
            self.config.operator.worktrees.root,
            self.config.operator.experiment_lines.root,
            self.config.operator.experiment_lines.checkpoint_root,
            self.config.operator.experiment_lines.active_release_root,
            self.config.operator.promotion.release_root,
            self.config.operator.promotion.active_release_link,
            self.project_root / ".autobugfix-memory",
            codex_runtime,
        ]
        for repo in self.config.repos.values():
            forbidden_roots.append(repo.main_checkout)
            if repo.worktree_root is not None:
                forbidden_roots.append(repo.worktree_root)
        return SWEGuardStore(
            guard_root,
            forbidden_roots=forbidden_roots,
        )

    def _swe_guard_docker_environment(self) -> dict[str, str]:
        guard_host = self.config.eval.benchmarks.guard.docker_host
        if not guard_host:
            raise EvalBenchmarkServiceError(
                "SWE Holdout Guard requires eval.benchmarks.guard.docker_host"
            )
        regular_host = os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
        aliases = {
            "/var/run/docker.sock": "unix:///var/run/docker.sock",
            "unix:///var/run/docker.sock": "unix:///var/run/docker.sock",
        }
        if aliases.get(guard_host, guard_host) == aliases.get(
            regular_host, regular_host
        ):
            raise EvalBenchmarkServiceError(
                "SWE Holdout Guard Docker endpoint must differ from the regular Eval endpoint"
            )
        return {"DOCKER_HOST": guard_host}

    @staticmethod
    def _docker_daemon_id(evidence_path: Path) -> str:
        value = evidence_path.read_text(encoding="utf-8", errors="replace").strip()
        if not value or any(character.isspace() for character in value):
            raise EvalBenchmarkServiceError("Docker daemon did not expose one stable ID")
        return value

    @staticmethod
    def _docker_daemon_profile(
        evidence_path: Path,
    ) -> tuple[str, dict[str, Any], str]:
        try:
            raw = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvalBenchmarkServiceError(
                "Guard Docker daemon did not expose valid JSON authority"
            ) from exc
        if not isinstance(raw, Mapping):
            raise EvalBenchmarkServiceError("Guard Docker authority must be a mapping")
        daemon_id = str(raw.get("ID") or "").strip()
        labels = sorted(str(item) for item in (raw.get("Labels") or []))
        if not daemon_id or any(character.isspace() for character in daemon_id):
            raise EvalBenchmarkServiceError("Guard Docker daemon ID is invalid")
        if SWE_GUARD_DAEMON_ISOLATION_LABEL not in labels:
            raise EvalBenchmarkServiceError(
                "Guard Docker daemon must be an independently administered VM and "
                f"publish label {SWE_GUARD_DAEMON_ISOLATION_LABEL!r}"
            )
        profile = {
            "id": daemon_id,
            "name": str(raw.get("Name") or ""),
            "operating_system": str(raw.get("OperatingSystem") or ""),
            "os_type": str(raw.get("OSType") or ""),
            "architecture": str(raw.get("Architecture") or ""),
            "docker_root_dir": str(raw.get("DockerRootDir") or ""),
            "driver": str(raw.get("Driver") or ""),
            "security_options": sorted(
                str(item) for item in (raw.get("SecurityOptions") or [])
            ),
            "labels": labels,
        }
        return daemon_id, profile, digest_payload(profile)

    @staticmethod
    def _write_private_authority(path: Path, record: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(yaml.safe_dump(dict(record), sort_keys=False))
            stream.flush()
            os.fsync(stream.fileno())

    def _verify_swe_guard_daemon(
        self,
        store: SWEGuardStore,
    ) -> tuple[dict[str, str], SWEDockerAuthority]:
        environment = self._swe_guard_docker_environment()
        docker = shutil.which("docker")
        if not docker:
            raise EvalBenchmarkServiceError("docker executable is unavailable")
        authority_root = store.root / "docker-authority"
        artifact_root = authority_root / "checks" / uuid.uuid4().hex
        regular = run_command(
            [docker, "info", "--format", "{{.ID}}"],
            cwd=self.project_root,
            artifact_dir=artifact_root / "regular",
            name="regular-docker-daemon-id",
            timeout_seconds=60,
            env=SWERuntime(self.project_root, self.config.eval.benchmarks).command_env(),
            inherit_env=False,
        )
        guarded_runtime = SWERuntime(
            self.project_root,
            self.config.eval.benchmarks,
            docker_environment=environment,
        )
        guarded = run_command(
            [docker, "info", "--format", "{{json .}}"],
            cwd=self.project_root,
            artifact_dir=artifact_root / "guard",
            name="guard-docker-daemon-id",
            timeout_seconds=60,
            env=guarded_runtime.command_env(),
            inherit_env=False,
        )
        if not regular.passed or not guarded.passed:
            raise EvalBenchmarkServiceError(
                "regular and Guard Docker daemons must both be reachable"
            )
        regular_id = self._docker_daemon_id(Path(regular.stdout_path))
        guarded_id, daemon_profile, daemon_profile_digest = (
            self._docker_daemon_profile(Path(guarded.stdout_path))
        )
        if regular_id == guarded_id:
            raise EvalBenchmarkServiceError(
                "SWE Holdout Guard Docker endpoint resolves to the regular Eval daemon"
            )
        try:
            authority = SWEDockerAuthority.capture(
                endpoint=environment["DOCKER_HOST"],
                guard_root=store.root,
                daemon_id=guarded_id,
                docker_executable=docker,
                isolation_label=SWE_GUARD_DAEMON_ISOLATION_LABEL,
                daemon_profile_digest=daemon_profile_digest,
            )
        except SWERuntimeError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        authority_record = record_with_digest(
            {
                "schema": "autobugfix-swe-docker-authority-v2",
                "authority": authority.to_dict(),
                "daemon_profile": daemon_profile,
            }
        )
        pinned_path = authority_root / "authority.yaml"
        if pinned_path.exists():
            if pinned_path.is_symlink():
                raise EvalBenchmarkServiceError(
                    "pinned Guard Docker authority was redirected"
                )
            pinned = yaml.safe_load(pinned_path.read_text(encoding="utf-8")) or {}
            if not isinstance(pinned, Mapping):
                raise EvalBenchmarkServiceError(
                    "pinned Guard Docker authority is invalid"
                )
            verify_record(pinned)
            if dict(pinned) != authority_record:
                raise EvalBenchmarkServiceError(
                    "Guard Docker authority changed after its first qualification"
                )
        else:
            self._write_private_authority(pinned_path, authority_record)
        write_yaml(artifact_root / "observed-authority.yaml", authority_record)
        return environment, authority

    def _swe_guard_runtime(self, store: SWEGuardStore) -> SWERuntime:
        environment, authority = self._verify_swe_guard_daemon(store)
        private_config = replace(
            self.config.eval.benchmarks,
            cache_root=store.root / "runtime-cache",
            trusted_case_root=store.root / "runtime-state",
            visible_manifest_root=store.root / "visible-projection",
        )
        return SWERuntime(
            self.project_root,
            private_config,
            docker_environment=environment,
            docker_authority=authority,
        )

    def _swe_guard_adapter(self, adapter: str, store: SWEGuardStore):
        runtime = self._swe_guard_runtime(store)
        if adapter == "swebench_live":
            return SWELiveAdapter(runtime)
        if adapter == "swebench_verified":
            return SWEVerifiedAdapter(runtime)
        raise EvalBenchmarkServiceError(f"unsupported SWE Guard adapter: {adapter}")

    def inspect_swe(self, adapter: str, instance_id: str) -> dict[str, Any]:
        runner = self._swe_adapter(adapter)
        artifact_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe"
            / "inspection-artifacts"
            / adapter
            / safe_component(instance_id, "instance_id")
            / uuid.uuid4().hex
        )
        instance = runner.load_instance(instance_id, artifact_root)
        return record_with_digest(
            {
                "schema": "autobugfix-swe-public-inspection-v1",
                "adapter": adapter,
                "instance_id": instance.instance_id,
                "repository": instance.repository,
                "base_commit": instance.base_commit,
                "language": instance.language,
                "problem_statement": instance.problem_statement,
                "hints_text": instance.hints_text,
                "created_at": instance.created_at,
                "docker_image": instance.docker_image,
                "dataset_revision": runner.snapshot.revision,
                "dataset_snapshot_sha256": runner.snapshot.sha256,
                "runtime_id": runner.runtime.runtime_id,
            }
        )

    def qualify_swe(
        self,
        protocol_path: Path,
        adapter: str,
        instance_id: str,
        guard_root: Path | None = None,
        guard_secret: str | bytes | None = None,
    ) -> dict[str, Any]:
        protocol = SWEExperimentProtocol.from_yaml(protocol_path)
        if adapter == "swebench_verified":
            expected_dataset = protocol.optimization_dataset
            role = "optimization"
        elif adapter == "swebench_live":
            expected_dataset = protocol.holdout_dataset
            role = "sealed_holdout"
        else:
            raise EvalBenchmarkServiceError(f"unsupported SWE adapter: {adapter}")
        guard_store = None
        if adapter == "swebench_live":
            if guard_root is None or guard_secret is None:
                raise EvalBenchmarkServiceError(
                    "SWE-bench-Live qualification requires external encrypted Guard storage"
                )
            guard_store = self._swe_guard_store(guard_root)
            runner = self._swe_guard_adapter(adapter, guard_store)
        else:
            runner = self._swe_adapter(adapter)
        if runner.snapshot.dataset != expected_dataset:
            raise EvalBenchmarkServiceError("SWE protocol dataset does not match adapter")
        identity = safe_component(instance_id, "instance_id")
        run_token = uuid.uuid4().hex
        temporary = None
        if guard_store is not None:
            staging = guard_store.root / "staging"
            staging.mkdir(parents=True, mode=0o700, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(
                prefix="qualification-", dir=staging
            )
            run_root = Path(temporary.name)
        else:
            run_root = (
                self.config.eval.benchmarks.trusted_case_root
                / "swe"
                / "qualification-runs"
                / adapter
                / identity
                / run_token
            )
        inspect_root = run_root / "inspection"
        instance = runner.load_instance(instance_id, inspect_root)
        official_attempts = []
        expected_image_id: str | None = None
        for attempt in range(1, protocol.qualification_repeats + 1):
            official = runner.score(
                instance,
                run_root / f"gold-score-{attempt}",
                run_id=f"gold-{identity}-{run_token[:8]}-{attempt}",
                gold=True,
                expected_image_id=expected_image_id,
            )
            official_record = official.to_dict()
            official_path = (
                self.store.write_swe_record(
                    "official-results",
                    adapter,
                    identity,
                    official_record,
                )
                if guard_store is None
                else None
            )
            official_attempts.append(
                {
                    "attempt": attempt,
                    "record_digest": official_record["record_digest"],
                    "record_path": (
                        str(official_path)
                        if official_path is not None
                        else f"encrypted:official-attempt-{attempt}"
                    ),
                    "resolved": official.resolved,
                    "harness_error": official.harness_error,
                    "image_id": official.image_id,
                }
            )
            if attempt == 1 and official.image_id.startswith("sha256:"):
                expected_image_id = official.image_id
        materialized = None
        harness_error = next(
            (
                str(item["harness_error"])
                for item in official_attempts
                if item["harness_error"]
            ),
            "",
        )
        stable_gold = all(item["resolved"] for item in official_attempts)
        stable_image = len({item["image_id"] for item in official_attempts}) == 1
        eligibility_reason = harness_error
        if not stable_gold and not harness_error:
            eligibility_reason = "repeated official gold patches did not resolve the case"
        if not stable_image and not harness_error:
            eligibility_reason = "official image identity changed across qualification runs"
        if stable_gold and stable_image and not harness_error:
            try:
                materialized = SWEImageMaterializer(runner).materialize(
                    instance,
                    run_root / "materialization",
                )
            except Exception as exc:
                harness_error = f"materialization failed: {exc}"
                eligibility_reason = harness_error
        eligible = (
            stable_gold
            and stable_image
            and not harness_error
            and materialized is not None
        )
        if eligible:
            eligibility_reason = (
                "two official gold scorer runs and materialization passed"
            )
        receipt = record_with_digest(
            {
                "schema": "autobugfix-swe-qualification-v4",
                "qualification_contract_digest": protocol.qualification_contract_digest,
                "adapter": adapter,
                "role": role,
                "instance_id": instance.instance_id,
                "repository": instance.repository,
                "base_commit": instance.base_commit,
                "language": instance.language,
                "dataset": runner.snapshot.dataset,
                "dataset_revision": runner.snapshot.revision,
                "dataset_snapshot_sha256": runner.snapshot.sha256,
                "runtime_id": runner.runtime.runtime_id,
                "docker_authority_digest": runner.runtime.docker_authority_digest,
                "evaluator_runtime_id": runner.runtime.evaluator_runtime_id,
                "official_attempts": official_attempts,
                "official_result_digest": official_attempts[-1]["record_digest"],
                "official_result_path": official_attempts[-1]["record_path"],
                "image": instance.docker_image,
                "image_id": official.image_id,
                "source_path": materialized.source_path if materialized else "unavailable",
                "source_tree": materialized.source_tree if materialized else "unavailable",
                "source_digest": materialized.source_digest if materialized else "unavailable",
                "eligible": eligible,
                "eligibility_reason": eligibility_reason,
                "harness_error": harness_error,
                "qualified_at": utc_now(),
            }
        )
        if guard_store is not None:
            assert guard_secret is not None
            projection = guard_store.write_qualification(
                receipt,
                run_root,
                secret=guard_secret,
                protocol_digest=protocol.qualification_contract_digest,
                runtime_id=runner.runtime.evaluator_runtime_id,
            )
            if materialized is not None:
                source = Path(materialized.source_path)
                if source.is_relative_to(runner.runtime.cache_root):
                    shutil.rmtree(source)
            if temporary is not None:
                temporary.cleanup()
            return projection
        receipt_path = self.store.write_swe_record(
            "qualification", adapter, identity, receipt
        )
        return {
            **receipt,
            "receipt_path": str(receipt_path),
            "official_record_path": str(official_attempts[-1]["record_path"]),
        }

    @staticmethod
    def _swe_task_type(problem_statement: str) -> str:
        text = problem_statement.lower()
        if any(
            marker in text
            for marker in (
                "add support",
                "new feature",
                "implement ",
                "feature request",
                "enhancement",
            )
        ):
            return "feature"
        if any(
            marker in text
            for marker in (
                "deprecat",
                "refactor",
                "documentation",
                "maintenance",
                "cleanup",
            )
        ):
            return "maintenance"
        return "bugfix"

    @staticmethod
    def _swe_public_attachments(*texts: str) -> tuple[SWEAttachment, ...]:
        references: list[str] = []
        patterns = (
            r"!\[[^\]]*\]\((https?://[^)\s]+)\)",
            r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']",
            r"(https?://[^\s<>()]+\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s<>()]*)?)",
        )
        for text in texts:
            for pattern in patterns:
                references.extend(re.findall(pattern, text, flags=re.IGNORECASE))
        attachments = []
        for uri in dict.fromkeys(reference.rstrip(".,;:") for reference in references):
            suffix = uri.split("?", 1)[0].rsplit(".", 1)[-1].lower()
            media = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "gif": "image/gif",
                "webp": "image/webp",
                "svg": "image/svg+xml",
            }.get(suffix, "application/octet-stream")
            attachments.append(
                SWEAttachment(
                    kind="upstream-reference",
                    uri=uri,
                    sha256=hashlib.sha256(uri.encode("utf-8")).hexdigest(),
                    media_type=media,
                    description="Public attachment referenced by the benchmark issue",
                )
            )
        return tuple(attachments)

    def run_swe_development_case(
        self,
        protocol_path: Path,
        adapter: str,
        instance_id: str,
        *,
        run_id: str,
        subject_sha: str | None = None,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
        timeout_seconds: int = 900,
        execution_mode: SWEExecutionMode = "protected",
        disposable_root: Path | None = None,
    ) -> dict[str, Any]:
        protocol = SWEExperimentProtocol.from_yaml(protocol_path)
        if (
            model != protocol.model
            or max_attempts != protocol.max_attempts
            or timeout_seconds != protocol.timeout_seconds
        ):
            raise EvalBenchmarkServiceError(
                "development execution budget differs from the frozen SWE protocol"
            )
        identity = safe_component(instance_id, "instance_id")
        run_name = safe_component(run_id, "run_id")
        selected_subject = subject_sha or protocol.h0_subject
        if selected_subject != protocol.h0_subject:
            raise EvalBenchmarkServiceError(
                "development acceptance currently permits only the frozen H0 subject"
            )
        if adapter != "swebench_verified":
            raise EvalBenchmarkServiceError(
                "development execution is restricted to visible SWE-bench Verified cases; "
                "SWE-bench-Live identity belongs to the sealed Guard"
            )
        runner = self._swe_adapter(adapter)
        root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe"
            / "development-runs"
            / adapter
            / identity
            / run_name
        )
        if root.exists():
            raise EvalBenchmarkServiceError("SWE development run_id already exists")
        inspection_root = root / "inspection"
        instance = runner.load_instance(identity, inspection_root)
        image_id = runner.image_id(instance, root / "image", allow_pull=False)
        materialized = SWEImageMaterializer(runner).materialize(
            instance,
            root / "materialization",
        )
        visible = SWEVisibleCase(
            case_token=f"dev-{hashlib.sha256(f'{adapter}:{identity}'.encode()).hexdigest()[:24]}",
            benchmark=adapter,  # type: ignore[arg-type]
            dataset_revision=runner.snapshot.revision,
            harness_commit=(
                runner.runtime.config.swebench_commit
                if adapter == "swebench_verified"
                else runner.runtime.config.live_commit
            ),
            repository=instance.repository,
            base_commit=instance.base_commit,
            language=instance.language,
            task_type=self._swe_task_type(instance.problem_statement),  # type: ignore[arg-type]
            problem_statement=instance.problem_statement,
            public_hints=tuple(
                item.strip()
                for item in instance.hints_text.split("\n\n")
                if item.strip()
            ),
            attachments=self._swe_public_attachments(
                instance.problem_statement, instance.hints_text
            ),
            first_wave=3,
            source_snapshot_digest=materialized.source_digest,
            verifier_profile="swe-visible-v1",
        )
        visible_path = self.store.write_swe_record(
            "development-visible",
            adapter,
            identity,
            visible.to_dict(),
        )
        subject_runtime = runner.runtime.subject_runtime_identity(
            protocol.codex_runtime
        )
        frozen = SWESubjectBroker(self.project_root, runner.runtime).run(
            subject_sha=selected_subject,
            expected_subject_tree=rev_parse(
                self.project_root, f"{selected_subject}^{{tree}}"
            ),
            visible_case=visible,
            instance=instance,
            materialized=materialized,
            image_id=image_id,
            artifact_root=root / "subject-run",
            protocol_digest=protocol.protocol_digest,
            treatment=protocol.codex_runtime,
            subject_runtime=subject_runtime,
            experiment_role="optimization",
            execution_mode=execution_mode,
            disposable_root=disposable_root,
        )
        broker_result_path = root / "subject-run" / "broker-result.yaml"
        if broker_result_path.is_symlink() or not broker_result_path.is_file():
            raise EvalBenchmarkServiceError(
                "Exp2 calibration execution did not produce a broker receipt"
            )
        broker_result = yaml.safe_load(
            broker_result_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(broker_result, Mapping):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration broker receipt is invalid"
            )
        verify_record(broker_result)
        command = broker_result.get("command")
        if not isinstance(command, Mapping) or not isinstance(
            command.get("record_digest"), str
        ):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration broker command receipt is invalid"
            )
        if broker_result.get("execution_mode") != execution_mode:
            raise EvalBenchmarkServiceError(
                "Exp2 calibration execution mode receipt drift"
            )
        preflight_digest: str | None = None
        if execution_mode == "workspace_only":
            preflight_path = root / "subject-run" / "workspace-only-preflight.json"
            if preflight_path.is_symlink() or not preflight_path.is_file():
                raise EvalBenchmarkServiceError(
                    "Exp2 calibration has no workspace-only preflight receipt"
                )
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            if not isinstance(preflight, Mapping):
                raise EvalBenchmarkServiceError(
                    "Exp2 calibration preflight receipt is invalid"
                )
            verify_record(preflight)
            if (
                preflight.get("execution_mode") != "workspace_only"
                or preflight.get("direct_sdk_in_process") is not True
                or preflight.get("sdk_bubblewrap") is not False
                or preflight.get("outer_bubblewrap") is not False
            ):
                raise EvalBenchmarkServiceError(
                    "Exp2 calibration preflight is not direct and Bubblewrap-free"
                )
            preflight_digest = str(preflight["record_digest"])
        execution_receipt = record_with_digest(
            {
                "schema": "autobugfix-exp2-execution-receipt-v1",
                "execution_mode": execution_mode,
                "direct_sdk_in_process": execution_mode == "workspace_only",
                "outer_bubblewrap": execution_mode == "protected",
                "broker_command_digest": command["record_digest"],
                "broker_result_digest": broker_result["record_digest"],
                "task_worktree_path": broker_result.get("task_worktree_path"),
                "workspace_only_preflight_digest": preflight_digest,
            }
        )
        development_binding_path = root / "subject-run" / "subject-binding.yaml"
        development_binding = yaml.safe_load(
            development_binding_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(development_binding, Mapping):
            raise EvalBenchmarkServiceError("SWE development subject binding is invalid")
        verify_record(development_binding)
        before = frozen.identity()
        official = runner.score(
            instance,
            root / "official-score",
            run_id=f"dev-{run_name}",
            submission=frozen.submission,
            expected_image_id=image_id,
        )
        official_record = official.to_dict()
        official_path = self.store.write_swe_record(
            "official-results",
            adapter,
            identity,
            official_record,
        )
        noninterference = self.submission_noninterference(
            frozen,
            before,
            official_result_digest=str(official_record["record_digest"]),
        )
        noninterference_path = self.store.write_swe_record(
            "noninterference",
            adapter,
            identity,
            noninterference,
        )
        report = record_with_digest(
            {
                "schema": "autobugfix-swe-development-run-v1",
                "development_only": True,
                "protocol_digest": protocol.protocol_digest,
                "codex_runtime": protocol.codex_runtime.to_dict(),
                "subject_runtime_contract_digest": protocol.subject_runtime_contract_digest,
                "subject_runtime_digest": subject_runtime["record_digest"],
                "evaluator_runtime_id": runner.runtime.evaluator_runtime_id,
                "memory_digest": str(development_binding.get("memory_digest") or ""),
                "role_config_digest": digest_payload(
                    {"config_sha256": development_binding.get("config_sha256")}
                ),
                "policy_digest": digest_payload(
                    {"development_only": True, "protocol_digest": protocol.protocol_digest}
                ),
                "adapter": adapter,
                "instance_id": identity,
                "visible_case_digest": visible.to_dict()["record_digest"],
                "visible_case_path": str(visible_path),
                "executed_subject_sha": frozen.submission.subject_sha,
                "executed_subject_tree": frozen.submission.subject_tree,
                "submission_digest": frozen.submission.record["record_digest"],
                "official_result_digest": official_record["record_digest"],
                "official_result_path": str(official_path),
                "noninterference_digest": noninterference["record_digest"],
                "noninterference_path": str(noninterference_path),
                "execution_receipt": execution_receipt,
                "resolved": official.resolved,
                "harness_error": official.harness_error,
            }
        )
        report_path = self.store.write_swe_record(
            "development-reports",
            adapter,
            identity,
            report,
        )
        return {**report, "report_path": str(report_path), "run_root": str(root)}

    def run_swe_exp2_calibration_case(
        self,
        protocol_path: Path,
        *,
        adapter: str,
        instance_id: str,
        run_id: str,
        execution_mode: SWEExecutionMode = "protected",
        disposable_root: Path | None = None,
    ) -> dict[str, Any]:
        """Adapt an existing H0 development run into Exp2 calibration input.

        Calibration is deliberately outside the formal ten-case denominator
        and therefore uses the existing H0-only development endpoint.  The
        official result is copied into a result projection without exposing
        any scorer-private fields to the coordinator.
        """

        report = self.run_swe_development_case(
            protocol_path,
            adapter,
            instance_id,
            run_id=run_id,
            execution_mode=execution_mode,
            disposable_root=disposable_root,
        )
        trusted_root = self.config.eval.benchmarks.trusted_case_root.resolve()
        official_source = Path(str(report.get("official_result_path") or ""))
        official_path = official_source.resolve()
        if (
            official_source.is_symlink()
            or not official_source.is_file()
            or not official_path.is_relative_to(trusted_root)
        ):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration official result is outside trusted Eval state"
            )
        official = yaml.safe_load(official_path.read_text(encoding="utf-8")) or {}
        if not isinstance(official, Mapping):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration official result is invalid"
            )
        verify_record(official)
        noninterference_source = Path(
            str(report.get("noninterference_path") or "")
        )
        noninterference_path = noninterference_source.resolve()
        if (
            noninterference_source.is_symlink()
            or not noninterference_path.is_file()
            or not noninterference_path.is_relative_to(trusted_root)
        ):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration noninterference receipt is outside trusted Eval state"
            )
        noninterference = yaml.safe_load(
            noninterference_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(noninterference, Mapping):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration noninterference receipt is invalid"
            )
        verify_record(noninterference)
        if (
            noninterference.get("schema") != "autobugfix-swe-noninterference-v1"
            or noninterference.get("unchanged") is not True
            or noninterference.get("submission_digest") != report["submission_digest"]
            or noninterference.get("official_result_digest")
            != official.get("record_digest")
        ):
            raise EvalBenchmarkServiceError(
                "Exp2 calibration noninterference receipt is invalid"
            )
        binding_digest = digest_payload(
            {
                "schema": "autobugfix-exp2-calibration-binding-v1",
                "protocol_digest": report["protocol_digest"],
                "subject_sha": report["executed_subject_sha"],
            }
        )
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-calibration-case-v1",
                "source_report_digest": report["record_digest"],
                "case_token": report["instance_id"],
                "executed_subject_sha": report["executed_subject_sha"],
                "submission_digest": report["submission_digest"],
                "protocol_digest": report["protocol_digest"],
                "subject_runtime_contract_digest": report[
                    "subject_runtime_contract_digest"
                ],
                "subject_runtime_digest": report["subject_runtime_digest"],
                "evaluator_runtime_id": report["evaluator_runtime_id"],
                "codex_runtime": report.get("codex_runtime", {}),
                "memory_digest": report.get("memory_digest", ""),
                "role_config_digest": report.get("role_config_digest", ""),
                "policy_digest": report.get("policy_digest", ""),
                "study_binding_digest": binding_digest,
                "official_result": dict(official),
                "noninterference": dict(noninterference),
                "execution_receipt": dict(
                    report.get("execution_receipt") or {}
                ),
                "harness_error": report["harness_error"],
            }
        )

    @staticmethod
    def submission_noninterference(
        frozen,
        before: Mapping[str, str],
        *,
        official_result_digest: str,
    ) -> dict[str, Any]:
        from autobugfix.eval.benchmarks.swe_submission import SWESubmissionAuthority

        return SWESubmissionAuthority.noninterference_receipt(
            frozen,
            before,
            official_result_digest=official_result_digest,
        )

    def _load_swe_public_manifest(self, path: Path) -> dict[str, Any]:
        resolved = path.resolve()
        visible_root = self.config.eval.benchmarks.visible_manifest_root.resolve()
        if not resolved.is_relative_to(visible_root) or not resolved.is_file():
            raise EvalBenchmarkServiceError(
                "formal SWE manifest must be an Eval-owned visible projection"
            )
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise EvalBenchmarkServiceError("formal SWE manifest is invalid")
        verify_record(raw)
        if raw.get("schema") != "autobugfix-swe-sealed-manifest-v2":
            raise EvalBenchmarkServiceError("unsupported formal SWE manifest")
        runtime = raw.get("codex_runtime")
        subject_runtime = raw.get("subject_runtime")
        if not isinstance(runtime, Mapping) or not isinstance(subject_runtime, Mapping):
            raise EvalBenchmarkServiceError("formal SWE manifest runtime binding is invalid")
        SWESubjectTreatmentRuntime.from_dict(runtime)
        verify_record(subject_runtime)
        return dict(raw)

    def _load_swe_study_binding(
        self,
        path: Path,
        public_manifest: Mapping[str, Any],
        *,
        expected_kinds: set[str],
    ) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise EvalBenchmarkServiceError("SWE Study binding is missing or redirected")
        raw = yaml.safe_load(path.resolve().read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise EvalBenchmarkServiceError("SWE Study binding is invalid")
        verify_record(raw)
        try:
            validate_study_binding_shape(raw)
        except StudyBindingError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        if (
            raw.get("kind") not in expected_kinds
            or raw.get("manifest_digest") != public_manifest.get("record_digest")
            or raw.get("target_checkpoint_name") != "H_general"
            or raw.get("primary_model")
            != SWESubjectTreatmentRuntime.from_dict(
                public_manifest["codex_runtime"]
            ).model
            or not raw.get("cohort_id")
        ):
            raise EvalBenchmarkServiceError(
                "SWE Study binding differs from the sealed experiment"
            )
        operator = OperatorGovernanceService(self.project_root)
        try:
            authoritative = operator.verify_guard_study_binding(raw)
            baseline = operator.study_baseline_identity(str(raw["study_id"]))
        except (OperatorGovernanceError, StudyBindingError) as exc:
            raise EvalBenchmarkServiceError(
                "SWE Study binding is not derived from trusted Operator state"
            ) from exc
        raw_guard = public_manifest.get("guard")
        code_identity = (
            raw_guard.get("code_identity")
            if isinstance(raw_guard, Mapping)
            else None
        )
        if (
            baseline["subject_sha"] != public_manifest.get("h0_subject")
            or baseline["subject_tree"] != public_manifest.get("h0_tree")
            or not isinstance(code_identity, Mapping)
            or baseline["harness_sha"] != code_identity.get("trusted_commit")
        ):
            raise EvalBenchmarkServiceError(
                "SWE Study H0 subject or trusted harness differs from the sealed experiment"
            )
        return authoritative

    def _execute_formal_swe_case(
        self,
        *,
        runner,
        instance: SWEInstance,
        visible: SWEVisibleCase,
        materialized: SWEMaterializedRepository,
        image_id: str,
        study_binding: Mapping[str, Any],
        protocol_digest: str,
        treatment: SWESubjectTreatmentRuntime,
        subject_runtime: Mapping[str, Any],
        evaluator_runtime_id: str,
        run_root: Path,
        run_id: str,
        experiment_role: str,
        authority_root: Path,
        execution_mode: SWEExecutionMode = "protected",
        disposable_root: Path | None = None,
    ) -> dict[str, Any]:
        current_subject_runtime = runner.runtime.subject_runtime_identity(treatment)
        if current_subject_runtime.get("record_digest") != subject_runtime.get(
            "record_digest"
        ):
            raise EvalBenchmarkServiceError("SWE subject runtime identity drift")
        if runner.runtime.evaluator_runtime_id != evaluator_runtime_id:
            raise EvalBenchmarkServiceError("SWE evaluator runtime identity drift")
        subject_sha = str(study_binding["subject_sha"])
        subject_tree = str(study_binding["subject_tree"])
        try:
            memory_snapshot = OperatorGovernanceService(
                self.project_root
            ).study_memory_snapshot(
                str(study_binding["study_id"]),
                expected_digest=str(study_binding["memory_digest"]),
            )
        except OperatorGovernanceError as exc:
            raise EvalBenchmarkServiceError(
                "SWE Study Memory is not available from trusted Operator state"
            ) from exc
        codex_backend_factory = None
        if experiment_role == "optimization" and study_binding.get("kind") in {
            "OPTIMIZATION",
            "CANDIDATE",
        }:
            operator = OperatorGovernanceService(self.project_root)
            try:
                if study_binding.get("kind") == "OPTIMIZATION":
                    grant = operator.validate_optimization_case_binding(
                        study_binding,
                        case_id=instance.instance_id,
                        first_wave=visible.first_wave,
                    )
                else:
                    # A terminal CANDIDATE binding is intentionally closed by
                    # Operator before formal scoring, so the existing
                    # optimization-only validator cannot be reused verbatim.
                    # Re-derive the same grant constraints read-only here.
                    authoritative = operator.verify_guard_study_binding(
                        study_binding
                    )
                    grant_id = str(authoritative.get("budget_grant_id") or "")
                    grant = operator.store.read_budget_grant(grant_id)
                    grants = operator.store.read_budget_grants(
                        str(authoritative["study_id"])
                    )
                    if (
                        not grants
                        or grants[-1].grant_id != grant.grant_id
                        or grant.grant_digest != authoritative.get("budget_digest")
                        or instance.instance_id not in grant.case_ids
                        or visible.first_wave > grant.wave
                    ):
                        raise OperatorGovernanceError(
                            "candidate case is outside the current trusted budget wave"
                        )
            except OperatorGovernanceError as exc:
                raise EvalBenchmarkServiceError(
                    "SWE Optimization case is outside trusted Operator budget"
                ) from exc
            execution_id = f"swe:{run_id}:{instance.instance_id}"

            def codex_backend_factory(role: str, attempt: int, sequence: int):
                return operator.metered_codex_backend(
                    grant_id=grant.grant_id,
                    call_key=f"{execution_id}:{sequence}:{role}",
                    execution_id=execution_id,
                    case_id=instance.instance_id,
                    attempt=attempt,
                    backend=CodexSDKBackend(
                        in_process=execution_mode == "workspace_only"
                    ),
                )

        frozen = SWESubjectBroker(
            self.project_root,
            runner.runtime,
            authority_root=authority_root,
        ).run(
            subject_sha=subject_sha,
            expected_subject_tree=subject_tree,
            visible_case=visible,
            instance=instance,
            materialized=materialized,
            image_id=image_id,
            artifact_root=run_root / "subject-run",
            protocol_digest=protocol_digest,
            treatment=treatment,
            subject_runtime=subject_runtime,
            experiment_role=experiment_role,
            study_binding=study_binding,
            memory_snapshot=memory_snapshot,
            codex_backend_factory=codex_backend_factory,
            execution_mode=execution_mode,
            disposable_root=disposable_root,
        )
        broker_result_path = run_root / "subject-run" / "broker-result.yaml"
        if broker_result_path.is_symlink() or not broker_result_path.is_file():
            raise EvalBenchmarkServiceError(
                "SWE formal execution did not produce a broker receipt"
            )
        broker_result = yaml.safe_load(
            broker_result_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(broker_result, Mapping):
            raise EvalBenchmarkServiceError("SWE formal broker receipt is invalid")
        verify_record(broker_result)
        command = broker_result.get("command")
        command_argv = command.get("argv") if isinstance(command, Mapping) else None
        if not isinstance(command_argv, list) or not all(
            isinstance(item, str) for item in command_argv
        ):
            raise EvalBenchmarkServiceError("SWE formal broker command receipt is invalid")
        if broker_result.get("execution_mode") != execution_mode:
            raise EvalBenchmarkServiceError("SWE formal execution mode receipt drift")
        if execution_mode == "workspace_only" and any(
            Path(item).name == "bwrap" or item == "bwrap" for item in command_argv
        ):
            raise EvalBenchmarkServiceError(
                "workspace-only execution recorded an outer Bubblewrap command"
            )
        preflight_digest: str | None = None
        if execution_mode == "workspace_only":
            preflight_path = run_root / "subject-run" / "workspace-only-preflight.json"
            if preflight_path.is_symlink() or not preflight_path.is_file():
                raise EvalBenchmarkServiceError(
                    "workspace-only execution has no preflight receipt"
                )
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            if not isinstance(preflight, Mapping):
                raise EvalBenchmarkServiceError(
                    "workspace-only preflight receipt is invalid"
                )
            verify_record(preflight)
            if (
                preflight.get("execution_mode") != "workspace_only"
                or preflight.get("direct_sdk_in_process") is not True
                or preflight.get("sdk_bubblewrap") is not False
                or preflight.get("outer_bubblewrap") is not False
            ):
                raise EvalBenchmarkServiceError(
                    "workspace-only preflight receipt is not direct and Bubblewrap-free"
                )
            preflight_digest = str(preflight["record_digest"])
            sdk_receipts = broker_result.get("sdk_call_receipt_digests")
            if not isinstance(sdk_receipts, list) or not sdk_receipts or any(
                not isinstance(item, str) or len(item) != 64 for item in sdk_receipts
            ):
                raise EvalBenchmarkServiceError(
                    "workspace-only execution has no valid direct SDK call receipts"
                )
        execution_receipt = record_with_digest(
            {
                "schema": "autobugfix-exp2-execution-receipt-v1",
                "execution_mode": execution_mode,
                "direct_sdk_in_process": execution_mode == "workspace_only",
                "outer_bubblewrap": execution_mode == "protected",
                "broker_command_digest": broker_result["command"].get("record_digest")
                if isinstance(broker_result.get("command"), Mapping)
                else None,
                "broker_result_digest": broker_result["record_digest"],
                "task_worktree_path": broker_result.get("task_worktree_path"),
                "workspace_only_preflight_digest": preflight_digest,
            }
        )
        frozen_before = frozen.identity()
        official = runner.score(
            instance,
            run_root / "official-score",
            run_id=run_id,
            submission=frozen.submission,
            expected_image_id=image_id,
        )
        official_record = official.to_dict()
        noninterference = self.submission_noninterference(
            frozen,
            frozen_before,
            official_result_digest=str(official_record["record_digest"]),
        )
        report = record_with_digest(
            {
                "schema": "autobugfix-swe-formal-case-v2",
                "protocol_digest": protocol_digest,
                "codex_runtime": treatment.to_dict(),
                "subject_runtime_contract_digest": treatment.contract_digest,
                "subject_runtime_digest": subject_runtime["record_digest"],
                "evaluator_runtime_id": evaluator_runtime_id,
                "case_token": visible.case_token,
                "experiment_role": experiment_role,
                "study_id": study_binding["study_id"],
                "cohort_id": study_binding["cohort_id"],
                "treatment": study_binding["target_checkpoint_name"],
                "study_binding_digest": study_binding["record_digest"],
                "memory_digest": study_binding["memory_digest"],
                "role_config_digest": study_binding["role_config_digest"],
                "policy_digest": study_binding["policy_digest"],
                "executed_subject_sha": frozen.submission.subject_sha,
                "executed_subject_tree": frozen.submission.subject_tree,
                "submission_digest": frozen.submission.record["record_digest"],
                "official_result": official_record,
                "noninterference": noninterference,
                "resolved": official.resolved,
                "harness_error": official.harness_error,
                "execution_receipt": execution_receipt,
            }
        )
        write_yaml(run_root / "formal-case-report.yaml", report)
        return report

    def run_swe_optimization_case(
        self,
        public_manifest_path: Path,
        *,
        case_selector: str,
        study_binding_path: Path,
        out_root: Path,
        run_id: str,
        execution_mode: SWEExecutionMode = "protected",
        disposable_root: Path | None = None,
    ) -> dict[str, Any]:
        public = self._load_swe_public_manifest(public_manifest_path)
        binding = self._load_swe_study_binding(
            study_binding_path,
            public,
            expected_kinds={"OPTIMIZATION"},
        )
        current_identity = self.guard_authority()
        raw_guard = public.get("guard")
        if not isinstance(raw_guard, Mapping):
            raise EvalBenchmarkServiceError("SWE manifest Guard projection is invalid")
        sealed_identity = raw_guard.get("code_identity")
        if not isinstance(sealed_identity, Mapping):
            raise EvalBenchmarkServiceError("SWE manifest code identity is invalid")
        if current_identity != GuardCodeIdentity.from_dict(sealed_identity):
            raise EvalBenchmarkServiceError("SWE trusted Eval code identity drift")
        raw_cases = public.get("optimization_cases")
        if not isinstance(raw_cases, list):
            raise EvalBenchmarkServiceError("SWE Optimization projection is invalid")
        matches = []
        for item in raw_cases:
            if not isinstance(item, Mapping):
                continue
            candidate_visible = item.get("visible_case")
            case_token = (
                str(candidate_visible.get("case_token") or "")
                if isinstance(candidate_visible, Mapping)
                else ""
            )
            if case_selector in {
                str(item.get("benchmark_instance_id") or ""),
                case_token,
            }:
                matches.append(item)
        if len(matches) != 1:
            raise EvalBenchmarkServiceError("no unique SWE Optimization case selected")
        selected = matches[0]
        verify_record(selected)
        raw_visible = selected.get("visible_case")
        if not isinstance(raw_visible, Mapping):
            raise EvalBenchmarkServiceError("SWE visible case is invalid")
        visible = SWEVisibleCase.from_dict(raw_visible)
        treatment = SWESubjectTreatmentRuntime.from_dict(public["codex_runtime"])
        subject_runtime = public["subject_runtime"]
        instance_id = str(selected["benchmark_instance_id"])
        runner = self._swe_adapter("swebench_verified")
        output = out_root.resolve()
        trusted_root = self.config.eval.benchmarks.trusted_case_root.resolve()
        if not output.is_relative_to(trusted_root):
            raise EvalBenchmarkServiceError(
                "SWE Optimization output must remain in trusted Eval state"
            )
        root = output / safe_component(run_id, "run_id")
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        instance = runner.load_instance(instance_id, root / "inspection")
        image_id = runner.image_id(instance, root / "image", allow_pull=False)
        materialized = SWEImageMaterializer(runner).materialize(
            instance, root / "materialization"
        )
        if (
            visible.repository != instance.repository
            or visible.base_commit != instance.base_commit
            or visible.source_snapshot_digest != materialized.source_digest
        ):
            raise EvalBenchmarkServiceError("SWE Optimization case materialization drift")
        return self._execute_formal_swe_case(
            runner=runner,
            instance=instance,
            visible=visible,
            materialized=materialized,
            image_id=image_id,
            study_binding=binding,
            protocol_digest=str(public["protocol_digest"]),
            treatment=treatment,
            subject_runtime=subject_runtime,
            evaluator_runtime_id=str(public["evaluator_runtime_id"]),
            run_root=root,
            run_id=safe_component(run_id, "run_id"),
            experiment_role="optimization",
            authority_root=trusted_root,
            execution_mode=execution_mode,
            disposable_root=disposable_root,
        )

    def run_swe_exp2_case(
        self,
        public_manifest_path: Path,
        *,
        case_selector: str,
        study_binding_path: Path,
        out_root: Path,
        run_id: str,
        expected_binding_kinds: set[str],
        execution_mode: SWEExecutionMode = "protected",
        disposable_root: Path | None = None,
    ) -> dict[str, Any]:
        """Run one explicitly bound Exp2 public case.

        This is a thin adapter over the existing official SWE scorer.  It
        adds only the H0/H1 binding selector and execution-mode receipt; it
        does not alter scorer inputs or result semantics.
        """

        public = self._load_swe_public_manifest(public_manifest_path)
        binding = self._load_swe_study_binding(
            study_binding_path,
            public,
            expected_kinds=set(expected_binding_kinds),
        )
        current_identity = self.guard_authority()
        raw_guard = public.get("guard")
        if not isinstance(raw_guard, Mapping):
            raise EvalBenchmarkServiceError("SWE manifest Guard projection is invalid")
        sealed_identity = raw_guard.get("code_identity")
        if not isinstance(sealed_identity, Mapping):
            raise EvalBenchmarkServiceError("SWE manifest code identity is invalid")
        if current_identity != GuardCodeIdentity.from_dict(sealed_identity):
            raise EvalBenchmarkServiceError("SWE trusted Eval code identity drift")
        raw_cases = public.get("optimization_cases")
        if not isinstance(raw_cases, list):
            raise EvalBenchmarkServiceError("SWE Optimization projection is invalid")
        matches = []
        for item in raw_cases:
            if not isinstance(item, Mapping):
                continue
            candidate_visible = item.get("visible_case")
            case_token = (
                str(candidate_visible.get("case_token") or "")
                if isinstance(candidate_visible, Mapping)
                else ""
            )
            if case_selector in {
                str(item.get("benchmark_instance_id") or ""),
                case_token,
            }:
                matches.append(item)
        if len(matches) != 1:
            raise EvalBenchmarkServiceError("no unique SWE Exp2 case selected")
        selected = matches[0]
        verify_record(selected)
        raw_visible = selected.get("visible_case")
        if not isinstance(raw_visible, Mapping):
            raise EvalBenchmarkServiceError("SWE visible case is invalid")
        visible = SWEVisibleCase.from_dict(raw_visible)
        treatment = SWESubjectTreatmentRuntime.from_dict(public["codex_runtime"])
        instance_id = str(selected["benchmark_instance_id"])
        runner = self._swe_adapter("swebench_verified")
        output = out_root.resolve()
        trusted_root = self.config.eval.benchmarks.trusted_case_root.resolve()
        if not output.is_relative_to(trusted_root):
            raise EvalBenchmarkServiceError(
                "SWE Exp2 output must remain in trusted Eval state"
            )
        root = output / safe_component(run_id, "run_id")
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        instance = runner.load_instance(instance_id, root / "inspection")
        image_id = runner.image_id(instance, root / "image", allow_pull=False)
        materialized = SWEImageMaterializer(runner).materialize(
            instance, root / "materialization"
        )
        if (
            visible.repository != instance.repository
            or visible.base_commit != instance.base_commit
            or visible.source_snapshot_digest != materialized.source_digest
        ):
            raise EvalBenchmarkServiceError("SWE Exp2 case materialization drift")
        return self._execute_formal_swe_case(
            runner=runner,
            instance=instance,
            visible=visible,
            materialized=materialized,
            image_id=image_id,
            study_binding=binding,
            protocol_digest=str(public["protocol_digest"]),
            treatment=treatment,
            subject_runtime=public["subject_runtime"],
            evaluator_runtime_id=str(public["evaluator_runtime_id"]),
            run_root=root,
            run_id=safe_component(run_id, "run_id"),
            experiment_role="optimization",
            authority_root=trusted_root,
            execution_mode=execution_mode,
            disposable_root=disposable_root,
        )

    @staticmethod
    def _swe_executed_subject_sha(
        reports: Sequence[Mapping[str, Any]], expected_subject_sha: str
    ) -> str:
        observed = {
            str(report.get("executed_subject_sha") or "") for report in reports
        }
        if observed != {expected_subject_sha}:
            raise EvalBenchmarkServiceError(
                "SWE case reports do not prove one expected executed subject"
            )
        return observed.pop()

    def guard_run_swe(
        self,
        public_manifest_path: Path,
        *,
        guard_root: Path,
        guard_secret: str | bytes,
        wave_token: str,
        study_binding_path: Path,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        public = self._load_swe_public_manifest(public_manifest_path)
        binding = self._load_swe_study_binding(
            study_binding_path,
            public,
            expected_kinds={"BASELINE", "CANDIDATE"},
        )
        raw_guard = public.get("guard")
        if not isinstance(raw_guard, Mapping):
            raise EvalBenchmarkServiceError("SWE Guard projection is invalid")
        guard_id = safe_component(raw_guard.get("guard_id"), "guard_id")
        guard_store = self._swe_guard_store(guard_root)
        try:
            bundle = guard_store.load_preparation(
                guard_id,
                expected_sha256=str(raw_guard.get("bundle_sha256") or ""),
                secret=guard_secret,
                protocol_digest=str(public["protocol_digest"]),
                runtime_id=str(public["guard_runtime_id"]),
            )
        except SWEGuardStoreError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        if (
            bundle.get("schema") != "autobugfix-swe-guard-bundle-v2"
            or bundle.get("guard_id") != guard_id
            or bundle.get("preparation_digest") != public.get("preparation_digest")
            or bundle.get("qualification_contract_digest")
            != public.get("qualification_contract_digest")
            or bundle.get("evaluator_runtime_id")
            != public.get("evaluator_runtime_id")
            or bundle.get("codex_runtime") != public.get("codex_runtime")
            or bundle.get("subject_runtime") != public.get("subject_runtime")
        ):
            raise EvalBenchmarkServiceError("SWE Guard bundle binding drift")
        raw_identity = bundle.get("code_identity")
        if not isinstance(raw_identity, Mapping):
            raise EvalBenchmarkServiceError("SWE Guard code identity is invalid")
        code_identity = GuardCodeIdentity.from_dict(raw_identity)
        if code_identity != self.guard_authority():
            raise EvalBenchmarkServiceError(
                "current trusted Eval code differs from the sealed SWE Guard"
            )
        raw_tokens = bundle.get("wave_tokens")
        if not isinstance(raw_tokens, Mapping):
            raise EvalBenchmarkServiceError("SWE Guard wave authority is invalid")
        matched = [
            int(wave)
            for wave, token in raw_tokens.items()
            if hmac.compare_digest(str(token), wave_token)
        ]
        if len(matched) != 1:
            raise EvalBenchmarkServiceError("invalid opaque SWE Guard wave token")
        wave = matched[0]
        bound_wave = binding.get("wave")
        if binding.get("kind") == "CANDIDATE" and int(bound_wave or 0) != wave:
            raise EvalBenchmarkServiceError(
                "SWE Guard wave differs from the candidate budget grant"
            )
        if binding.get("kind") == "BASELINE" and wave != 16:
            raise EvalBenchmarkServiceError("SWE H0 Guard baseline must use all six cases")
        private = bundle.get("private_cohort")
        if not isinstance(private, Mapping):
            raise EvalBenchmarkServiceError("SWE private cohort is invalid")
        verify_record(private)
        raw_cases = private.get("cases")
        if not isinstance(raw_cases, list):
            raise EvalBenchmarkServiceError("SWE private cohort cases are invalid")
        selected = [
            item
            for item in raw_cases
            if isinstance(item, Mapping)
            and item.get("role") == "sealed_holdout"
            and int(item.get("first_wave") or 0) <= wave
        ]
        expected_count = {3: 1, 8: 3, 16: 6}[wave]
        if len(selected) != expected_count:
            raise EvalBenchmarkServiceError("SWE Guard wave selection contract is invalid")
        runner = self._swe_guard_adapter("swebench_live", guard_store)
        if (
            runner.runtime.runtime_id != public.get("guard_runtime_id")
            or runner.runtime.docker_authority_digest
            != public.get("docker_authority_digest")
            or bundle.get("guard_runtime_id") != public.get("guard_runtime_id")
            or bundle.get("docker_authority_digest")
            != public.get("docker_authority_digest")
            or bundle.get("evaluator_runtime_id")
            != public.get("evaluator_runtime_id")
        ):
            raise EvalBenchmarkServiceError("SWE Guard runtime identity drift")
        output = out_root.resolve()
        if not output.is_relative_to(guard_store.root):
            raise EvalBenchmarkServiceError(
                "SWE Guard output must remain under the external Guard authority root"
            )
        output.mkdir(parents=True, exist_ok=True)
        run_name = safe_component(run_id, "run_id")
        encrypted_artifacts = output / f"{run_name}.artifacts.abfg"
        metric_path = output / f"{run_name}.metric.yaml"
        if encrypted_artifacts.exists() or metric_path.exists():
            raise EvalBenchmarkServiceError("SWE Guard output run_id already exists")
        reports: list[dict[str, Any]] = []
        staging = guard_store.root / "staging"
        staging.mkdir(parents=True, mode=0o700, exist_ok=True)
        fatal: BaseException | None = None
        with tempfile.TemporaryDirectory(prefix="formal-run-", dir=staging) as temp:
            private_root = Path(temp)
            private_root.chmod(0o700)
            try:
                for index, raw_case in enumerate(selected, start=1):
                    raw_instance = raw_case.get("instance")
                    raw_visible = raw_case.get("visible_case")
                    raw_materialized = raw_case.get("materialized")
                    if not all(
                        isinstance(item, Mapping)
                        for item in (raw_instance, raw_visible, raw_materialized)
                    ):
                        raise EvalBenchmarkServiceError(
                            "SWE Guard case contract is invalid"
                        )
                    instance = SWEInstance.from_trusted_record(raw_instance)
                    visible = SWEVisibleCase.from_dict(raw_visible)
                    case_root = private_root / f"case-{index:02d}"
                    case_root.mkdir(mode=0o700)
                    image_id = runner.image_id(
                        instance, case_root / "image", allow_pull=False
                    )
                    if image_id != raw_materialized.get("image_id"):
                        raise EvalBenchmarkServiceError(
                            "SWE Holdout image identity drift"
                        )
                    materialized = SWEImageMaterializer(runner).materialize(
                        instance, case_root / "materialization"
                    )
                    if (
                        materialized.source_tree
                        != raw_materialized.get("source_tree")
                        or materialized.source_digest
                        != raw_materialized.get("source_digest")
                    ):
                        raise EvalBenchmarkServiceError(
                            "SWE Holdout source snapshot drift"
                        )
                    report = self._execute_formal_swe_case(
                        runner=runner,
                        instance=instance,
                        visible=visible,
                        materialized=materialized,
                        image_id=image_id,
                        study_binding=binding,
                        protocol_digest=str(public["protocol_digest"]),
                        treatment=SWESubjectTreatmentRuntime.from_dict(
                            public["codex_runtime"]
                        ),
                        subject_runtime=public["subject_runtime"],
                        evaluator_runtime_id=str(public["evaluator_runtime_id"]),
                        run_root=case_root,
                        run_id=f"{run_name}-case-{index:02d}",
                        experiment_role="sealed_holdout",
                        authority_root=private_root,
                    )
                    if report.get("harness_error"):
                        raise EvalBenchmarkServiceError(
                            "official SWE Holdout scorer reported a harness error"
                        )
                    reports.append(report)
                    source = Path(materialized.source_path)
                    if source.is_relative_to(runner.runtime.cache_root):
                        shutil.rmtree(source)
            except BaseException as exc:
                fatal = exc
                (private_root / "guard-fatal.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
                )
            finally:
                encrypt_artifact_tree(
                    private_root,
                    encrypted_artifacts,
                    secret=guard_secret,
                    aad=canonical_json(
                        {
                            "schema": "autobugfix-swe-run-artifacts-v1",
                            "guard_id": guard_id,
                            "run_id": run_name,
                            "wave": wave,
                            "study_binding_digest": binding["record_digest"],
                        }
                    ).encode("ascii"),
                )
        if fatal is not None:
            raise EvalBenchmarkServiceError(
                "SWE Guard run failed; partial evidence was encrypted under "
                "the external Guard root"
            ) from fatal
        passed = sum(1 for report in reports if report.get("resolved") is True)
        failed = len(reports) - passed
        executed_subject_sha = self._swe_executed_subject_sha(
            reports, str(binding["subject_sha"])
        )
        metric = signed_metric(
            metric_payload(
                guard_id=guard_id,
                run_id=run_name,
                wave=wave,
                case_count=len(reports),
                passed_count=passed,
                failed_count=failed,
                harness_error_count=0,
                encrypted_artifact_sha256=guard_artifact_digest(
                    encrypted_artifacts
                ),
                public_manifest_digest=str(public["record_digest"]),
                code_identity=code_identity,
                study_binding=binding,
                executed_subject_sha=executed_subject_sha,
            ),
            guard_secret,
        )
        write_yaml(metric_path, metric)
        return {
            "guard_id": guard_id,
            "run_id": run_name,
            "wave": wave,
            "case_count": len(reports),
            "passed_count": passed,
            "failed_count": failed,
            "harness_error_count": 0,
            "pass_rate": passed / len(reports),
            "metric_receipt": str(metric_path),
            "encrypted_artifacts_sha256": guard_artifact_digest(
                encrypted_artifacts
            ),
        }

    def _swe_qualification_pool(
        self,
        protocol: SWEExperimentProtocol,
        adapter: str,
    ) -> list[dict[str, Any]]:
        runner = self._swe_adapter(adapter)
        root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe/qualification"
            / adapter
        )
        by_instance: dict[str, dict[str, Any]] = {}
        if not root.is_dir():
            return []
        for path in sorted(root.glob("*/*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, Mapping):
                raise EvalBenchmarkServiceError("SWE qualification record is invalid")
            verify_record(raw)
            if path.name != f"{raw['record_digest']}.yaml":
                raise EvalBenchmarkServiceError("SWE qualification path digest drift")
            if raw.get("schema") != "autobugfix-swe-qualification-v4":
                continue
            if (
                raw.get("qualification_contract_digest")
                != protocol.qualification_contract_digest
            ):
                continue
            if raw.get("evaluator_runtime_id") != runner.runtime.evaluator_runtime_id:
                continue
            if raw.get("dataset_revision") != runner.snapshot.revision:
                continue
            instance_id = safe_component(raw.get("instance_id"), "instance_id")
            if path.parent.name != instance_id:
                raise EvalBenchmarkServiceError("SWE qualification identity path drift")
            current = by_instance.get(instance_id)
            if current is None or str(raw.get("qualified_at") or "") > str(
                current.get("qualified_at") or ""
            ):
                by_instance[instance_id] = dict(raw)
        return [
            record
            for record in by_instance.values()
            if record.get("schema") == "autobugfix-swe-qualification-v4"
            and record.get("eligible")
        ]

    @staticmethod
    def _swe_first_wave(index: int, *, role: str) -> int:
        if role == "optimization":
            return 3 if index < 2 else 8 if index < 5 else 16
        return 3 if index < 1 else 8 if index < 3 else 16

    def _swe_visible_case(
        self,
        runner,
        instance,
        qualification: Mapping[str, Any],
        *,
        case_token: str,
        first_wave: int,
        task_type: str | None = None,
    ) -> SWEVisibleCase:
        return SWEVisibleCase(
            case_token=case_token,
            benchmark=instance.adapter,
            dataset_revision=runner.snapshot.revision,
            harness_commit=(
                runner.runtime.config.swebench_commit
                if instance.adapter == "swebench_verified"
                else runner.runtime.config.live_commit
            ),
            repository=instance.repository,
            base_commit=instance.base_commit,
            language=instance.language,
            task_type=task_type or self._swe_task_type(instance.problem_statement),
            problem_statement=instance.problem_statement,
            public_hints=tuple(
                item.strip()
                for item in instance.hints_text.split("\n\n")
                if item.strip()
            ),
            attachments=self._swe_public_attachments(
                instance.problem_statement, instance.hints_text
            ),
            first_wave=first_wave,
            source_snapshot_digest=str(qualification["source_digest"]),
            verifier_profile="swe-visible-v1",
        )

    def _validate_swe_qualification_source(
        self,
        runner,
        instance,
        qualification: Mapping[str, Any],
        artifact_root: Path,
    ) -> SWEMaterializedRepository:
        image_id = runner.image_id(instance, artifact_root / "image", allow_pull=False)
        if image_id != qualification.get("image_id"):
            raise EvalBenchmarkServiceError("qualified SWE image identity drift")
        materialized = SWEMaterializedRepository(
            instance_id=instance.instance_id,
            repository=instance.repository,
            base_commit=instance.base_commit,
            source_path=str(qualification["source_path"]),
            source_tree=str(qualification["source_tree"]),
            source_digest=str(qualification["source_digest"]),
            image=instance.docker_image,
            image_id=image_id,
        )
        observed = SWEImageMaterializer(runner)._verify_existing(
            instance,
            Path(materialized.source_path),
            image_id,
        )
        if observed != materialized:
            raise EvalBenchmarkServiceError("qualified SWE source snapshot drift")
        return materialized

    def prepare_swe(
        self,
        protocol_path: Path,
        *,
        guard_root: Path,
        guard_secret: str | bytes,
    ) -> dict[str, Any]:
        protocol = SWEExperimentProtocol.from_yaml(protocol_path)
        preparation_id = f"swe-prep-{uuid.uuid4().hex}"
        guard_store = self._swe_guard_store(guard_root)
        guard_staging_parent = guard_store.root / "staging"
        guard_staging_parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        guard_temporary = tempfile.TemporaryDirectory(
            prefix="preparation-", dir=guard_staging_parent
        )
        holdout_staging = Path(guard_temporary.name)
        verified_runner = self._swe_adapter("swebench_verified")
        live_runner = self._swe_guard_adapter("swebench_live", guard_store)
        evaluator_runtime_id = verified_runner.runtime.evaluator_runtime_id
        guard_runtime_id = live_runner.runtime.evaluator_runtime_id
        subject_runtime = verified_runner.runtime.subject_runtime_identity(
            protocol.codex_runtime
        )
        if (
            live_runner.runtime.subject_runtime_identity(protocol.codex_runtime).get(
                "record_digest"
            )
            != subject_runtime.get("record_digest")
        ):
            raise EvalBenchmarkServiceError(
                "SWE Verified and Live subject runtime identities differ"
            )
        optimization_pool = self._swe_qualification_pool(
            protocol, "swebench_verified"
        )
        try:
            holdout_pool = [
                record
                for record in guard_store.qualification_records(
                    secret=guard_secret,
                    protocol_digest=protocol.qualification_contract_digest,
                    runtime_id=live_runner.runtime.evaluator_runtime_id,
                )
                if record.get("schema") == "autobugfix-swe-qualification-v4"
                and record.get("eligible")
            ]
        except SWEGuardStoreError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        selected_types = {
            selection.instance_id: selection.task_type
            for selection in protocol.optimization_cases
        }
        optimization_pool = [
            record
            for record in optimization_pool
            if record.get("instance_id") in selected_types
        ]
        if len(optimization_pool) != protocol.optimization_count:
            raise EvalBenchmarkServiceError(
                "SWE preparation requires exactly ten current eligible Optimization receipts; "
                f"observed {len(optimization_pool)}"
            )
        if len(holdout_pool) != protocol.holdout_count:
            raise EvalBenchmarkServiceError(
                "SWE preparation requires exactly six current eligible Holdout receipts; "
                f"observed {len(holdout_pool)}"
            )

        optimization_candidates = []
        for record in optimization_pool:
            instance = verified_runner.load_instance(
                str(record["instance_id"]),
                self.config.eval.benchmarks.trusted_case_root
                / "swe/preparation-validation/optimization"
                / preparation_id
                / str(record["record_digest"]),
            )
            task_type = selected_types[instance.instance_id]
            optimization_candidates.append((task_type, instance, record))
        optimization_candidates.sort(
            key=lambda item: (
                {"feature": 0, "maintenance": 1, "bugfix": 2}[item[0]],
                item[1].repository,
                item[1].instance_id,
            )
        )
        optimization_repositories = {
            item[1].repository for item in optimization_candidates
        }
        task_types = {item[0] for item in optimization_candidates}
        if len(optimization_repositories) < 4 or task_types != {
            "bugfix",
            "feature",
            "maintenance",
        }:
            raise EvalBenchmarkServiceError(
                "Optimization cohort must cover at least four repositories and all task types"
            )

        holdout_candidates = []
        for record in holdout_pool:
            instance = live_runner.load_instance(
                str(record["instance_id"]),
                holdout_staging
                / "inspection"
                / str(record["record_digest"]),
            )
            holdout_candidates.append((instance, record))
        holdout_candidates.sort(key=lambda item: (item[0].language, item[0].repository))
        holdout_repositories = {item[0].repository for item in holdout_candidates}
        if len(holdout_repositories) != protocol.holdout_count:
            raise EvalBenchmarkServiceError(
                "Holdout cohort must contain six repository-unique cases"
            )
        if optimization_repositories & holdout_repositories:
            raise EvalBenchmarkServiceError(
                "Holdout repositories must be absent from Optimization"
            )
        if len({item[0].language for item in holdout_candidates}) < 4:
            raise EvalBenchmarkServiceError(
                "Holdout cohort must cover at least four language families"
            )

        validation_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe/preparation-runs"
            / preparation_id
        )
        private_cases: list[dict[str, Any]] = []
        visible_optimization: list[dict[str, Any]] = []
        for index, (_, instance, record) in enumerate(optimization_candidates):
            token = "opt-" + hashlib.sha256(
                f"{protocol.protocol_digest}:{instance.instance_id}".encode("utf-8")
            ).hexdigest()[:24]
            wave = self._swe_first_wave(index, role="optimization")
            materialized = self._validate_swe_qualification_source(
                verified_runner,
                instance,
                record,
                validation_root / "optimization" / token,
            )
            visible = self._swe_visible_case(
                verified_runner,
                instance,
                record,
                case_token=token,
                first_wave=wave,
                task_type=selected_types[instance.instance_id],
            )
            visible_optimization.append(
                record_with_digest(
                    {
                        "schema": "autobugfix-swe-optimization-case-v1",
                        "benchmark_instance_id": instance.instance_id,
                        "visible_case": visible.to_dict(),
                    }
                )
            )
            private_cases.append(
                {
                    "role": "optimization",
                    "case_token": token,
                    "first_wave": wave,
                    "qualification_digest": record["record_digest"],
                    "instance": instance.trusted_record(),
                    "visible_case": visible.to_dict(),
                    "materialized": {
                        "instance_id": materialized.instance_id,
                        "repository": materialized.repository,
                        "base_commit": materialized.base_commit,
                        "source_path": materialized.source_path,
                        "source_tree": materialized.source_tree,
                        "source_digest": materialized.source_digest,
                        "image": materialized.image,
                        "image_id": materialized.image_id,
                    },
                }
            )
        for index, (instance, record) in enumerate(holdout_candidates):
            token = f"holdout-{secrets.token_hex(24)}"
            wave = self._swe_first_wave(index, role="sealed_holdout")
            image_id = live_runner.image_id(
                instance,
                holdout_staging / "validation" / token / "image",
                allow_pull=False,
            )
            if image_id != record.get("image_id"):
                raise EvalBenchmarkServiceError("qualified Holdout image identity drift")
            materialized = SWEImageMaterializer(live_runner).materialize(
                instance,
                holdout_staging / "validation" / token / "materialization",
            )
            if (
                materialized.source_tree != record.get("source_tree")
                or materialized.source_digest != record.get("source_digest")
            ):
                raise EvalBenchmarkServiceError(
                    "qualified Holdout source snapshot drift"
                )
            visible = self._swe_visible_case(
                live_runner,
                instance,
                record,
                case_token=token,
                first_wave=wave,
            )
            private_cases.append(
                {
                    "role": "sealed_holdout",
                    "case_token": token,
                    "first_wave": wave,
                    "qualification_digest": record["record_digest"],
                    "instance": instance.trusted_record(),
                    "visible_case": visible.to_dict(),
                    "materialized": {
                        "instance_id": materialized.instance_id,
                        "repository": materialized.repository,
                        "base_commit": materialized.base_commit,
                        "source_path": "guard-rematerialize",
                        "source_tree": materialized.source_tree,
                        "source_digest": materialized.source_digest,
                        "image": materialized.image,
                        "image_id": materialized.image_id,
                    },
                }
            )
            source = Path(materialized.source_path)
            if source.is_relative_to(live_runner.runtime.cache_root):
                shutil.rmtree(source)

        private_record = record_with_digest(
            {
                "schema": "autobugfix-swe-private-cohort-v2",
                "preparation_id": preparation_id,
                "protocol_digest": protocol.protocol_digest,
                "runtime_id": evaluator_runtime_id,
                "guard_runtime_id": guard_runtime_id,
                "docker_authority_digest": live_runner.runtime.docker_authority_digest,
                "qualification_contract_digest": protocol.qualification_contract_digest,
                "evaluator_runtime_id": evaluator_runtime_id,
                "codex_runtime": protocol.codex_runtime.to_dict(),
                "subject_runtime": subject_runtime,
                "h0_subject": protocol.h0_subject,
                "cases": private_cases,
                "created_at": utc_now(),
            }
        )
        _, encrypted_preparation_sha256 = guard_store.write_preparation(
            preparation_id,
            private_record,
            secret=guard_secret,
            protocol_digest=protocol.protocol_digest,
            runtime_id=guard_runtime_id,
        )
        guard_temporary.cleanup()
        prepared = record_with_digest(
            {
                "schema": "autobugfix-swe-preparation-v2",
                "preparation_id": preparation_id,
                "protocol_digest": protocol.protocol_digest,
                "runtime_id": evaluator_runtime_id,
                "guard_runtime_id": guard_runtime_id,
                "docker_authority_digest": live_runner.runtime.docker_authority_digest,
                "qualification_contract_digest": protocol.qualification_contract_digest,
                "evaluator_runtime_id": evaluator_runtime_id,
                "codex_runtime": protocol.codex_runtime.to_dict(),
                "subject_runtime": subject_runtime,
                "h0_subject": protocol.h0_subject,
                "h0_tree": rev_parse(
                    self.project_root, f"{protocol.h0_subject}^{{tree}}"
                ),
                "optimization_cases": visible_optimization,
                "optimization_count": len(visible_optimization),
                "holdout_count": protocol.holdout_count,
                "private_bundle_digest": private_record["record_digest"],
                "encrypted_preparation_sha256": encrypted_preparation_sha256,
                "waves": {"3": 3, "8": 8, "16": 16},
                "created_at": utc_now(),
            }
        )
        prepared_path = self.store.write_swe_record(
            "prepared",
            "swe_experiment_2",
            preparation_id,
            prepared,
        )
        return {
            "preparation_id": preparation_id,
            "preparation_digest": prepared["record_digest"],
            "prepared_path": str(prepared_path),
            "optimization_count": len(visible_optimization),
            "holdout_count": protocol.holdout_count,
            "encrypted_preparation_sha256": encrypted_preparation_sha256,
            "waves": {3: 3, 8: 8, 16: 16},
        }

    def seal_swe(
        self,
        prepared_path: Path,
        *,
        guard_root: Path,
        guard_secret: str | bytes,
    ) -> dict[str, Any]:
        resolved = prepared_path.resolve()
        expected_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe/prepared/swe_experiment_2"
        ).resolve()
        if not resolved.is_relative_to(expected_root):
            raise EvalBenchmarkServiceError("SWE preparation is outside trusted state")
        prepared = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(prepared, Mapping):
            raise EvalBenchmarkServiceError("SWE preparation record is invalid")
        verify_record(prepared)
        if prepared.get("schema") != "autobugfix-swe-preparation-v2":
            raise EvalBenchmarkServiceError("unsupported SWE preparation schema")
        if resolved.name != f"{prepared['record_digest']}.yaml":
            raise EvalBenchmarkServiceError("SWE preparation path digest drift")
        optimization_cases = prepared.get("optimization_cases")
        if (
            not isinstance(optimization_cases, list)
            or len(optimization_cases) != 10
            or int(prepared.get("optimization_count") or 0) != 10
            or int(prepared.get("holdout_count") or 0) != 6
            or prepared.get("waves") != {"3": 3, "8": 8, "16": 16}
        ):
            raise EvalBenchmarkServiceError("SWE preparation cohort shape is invalid")
        raw_treatment = prepared.get("codex_runtime")
        raw_subject_runtime = prepared.get("subject_runtime")
        if not isinstance(raw_treatment, Mapping) or not isinstance(
            raw_subject_runtime, Mapping
        ):
            raise EvalBenchmarkServiceError("SWE preparation runtime binding is invalid")
        SWESubjectTreatmentRuntime.from_dict(raw_treatment)
        verify_record(raw_subject_runtime)
        guard_store = self._swe_guard_store(guard_root)
        try:
            private = guard_store.load_preparation(
                str(prepared["preparation_id"]),
                expected_sha256=str(prepared["encrypted_preparation_sha256"]),
                secret=guard_secret,
                protocol_digest=str(prepared["protocol_digest"]),
                runtime_id=str(prepared["guard_runtime_id"]),
            )
        except SWEGuardStoreError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        if private.get("record_digest") != prepared.get("private_bundle_digest"):
            raise EvalBenchmarkServiceError("SWE private cohort digest drift")
        private_cases = private.get("cases")
        if (
            private.get("preparation_id") != prepared.get("preparation_id")
            or private.get("protocol_digest") != prepared.get("protocol_digest")
            or private.get("runtime_id") != prepared.get("runtime_id")
            or private.get("guard_runtime_id") != prepared.get("guard_runtime_id")
            or private.get("docker_authority_digest")
            != prepared.get("docker_authority_digest")
            or private.get("qualification_contract_digest")
            != prepared.get("qualification_contract_digest")
            or private.get("evaluator_runtime_id")
            != prepared.get("evaluator_runtime_id")
            or private.get("h0_subject") != prepared.get("h0_subject")
            or private.get("codex_runtime") != prepared.get("codex_runtime")
            or private.get("subject_runtime") != prepared.get("subject_runtime")
            or not isinstance(private_cases, list)
            or len(private_cases) != 16
            or sum(
                1
                for item in private_cases
                if isinstance(item, Mapping) and item.get("role") == "optimization"
            )
            != 10
            or sum(
                1
                for item in private_cases
                if isinstance(item, Mapping) and item.get("role") == "sealed_holdout"
            )
            != 6
        ):
            raise EvalBenchmarkServiceError("SWE private cohort shape is invalid")
        code_identity = self.guard_authority()
        guard_id = new_guard_id()
        wave_tokens = {
            "3": secrets.token_urlsafe(32),
            "8": secrets.token_urlsafe(32),
            "16": secrets.token_urlsafe(32),
        }
        guard_bundle = record_with_digest(
            {
                "schema": "autobugfix-swe-guard-bundle-v2",
                "guard_id": guard_id,
                "preparation_digest": prepared["record_digest"],
                "protocol_digest": prepared["protocol_digest"],
                "runtime_id": prepared["runtime_id"],
                "guard_runtime_id": prepared["guard_runtime_id"],
                "docker_authority_digest": prepared["docker_authority_digest"],
                "qualification_contract_digest": prepared["qualification_contract_digest"],
                "evaluator_runtime_id": prepared["evaluator_runtime_id"],
                "codex_runtime": prepared["codex_runtime"],
                "subject_runtime": prepared["subject_runtime"],
                "code_identity": code_identity.to_dict(),
                "wave_tokens": wave_tokens,
                "private_cohort": private,
            }
        )
        _, bundle_digest = guard_store.write_preparation(
            guard_id,
            guard_bundle,
            secret=guard_secret,
            protocol_digest=str(prepared["protocol_digest"]),
            runtime_id=str(prepared["guard_runtime_id"]),
        )
        public_manifest = record_with_digest(
            {
                "schema": "autobugfix-swe-sealed-manifest-v2",
                "manifest_id": f"swe-general-{guard_id}",
                "preparation_digest": prepared["record_digest"],
                "protocol_digest": prepared["protocol_digest"],
                "runtime_id": prepared["runtime_id"],
                "guard_runtime_id": prepared["guard_runtime_id"],
                "docker_authority_digest": prepared["docker_authority_digest"],
                "qualification_contract_digest": prepared["qualification_contract_digest"],
                "evaluator_runtime_id": prepared["evaluator_runtime_id"],
                "codex_runtime": prepared["codex_runtime"],
                "subject_runtime": prepared["subject_runtime"],
                "h0_subject": prepared["h0_subject"],
                "h0_tree": prepared["h0_tree"],
                "optimization_cases": list(prepared["optimization_cases"]),
                "optimization_count": int(prepared["optimization_count"]),
                "guard": {
                    "guard_id": guard_id,
                    "code_identity": code_identity.to_dict(),
                    "bundle_sha256": bundle_digest,
                    "holdout_count": int(prepared["holdout_count"]),
                    "waves": dict(prepared["waves"]),
                },
                "sealed_at": utc_now(),
            }
        )
        manifest_id = str(public_manifest["manifest_id"])
        manifest_path = self.store.write_visible_yaml(
            manifest_id, "manifest.yaml", public_manifest
        )
        optimization_path = self.store.write_visible_jsonl_rows(
            manifest_id,
            "optimization.jsonl",
            list(prepared["optimization_cases"]),
        )
        return {
            "manifest_id": manifest_id,
            "guard_id": guard_id,
            "manifest_digest": public_manifest["record_digest"],
            "visible_manifest": str(manifest_path),
            "optimization_dataset": str(optimization_path),
            "optimization_count": int(prepared["optimization_count"]),
            "sealed_holdout_count": int(prepared["holdout_count"]),
            "encrypted_bundle_sha256": bundle_digest,
            "waves": {3: 3, 8: 8, 16: 16},
            "wave_tokens": wave_tokens,
        }

    @staticmethod
    def _file_set_digest(
        project_root: Path,
        roots: Sequence[Path],
    ) -> str:
        entries: list[dict[str, str]] = []
        for root in roots:
            resolved = root.resolve()
            if not resolved.exists():
                entries.append({"path": str(resolved), "state": "missing"})
                continue
            files = (resolved,) if resolved.is_file() else tuple(
                path for path in sorted(resolved.rglob("*")) if path.is_file()
            )
            for path in files:
                try:
                    display = path.resolve().relative_to(project_root).as_posix()
                except ValueError:
                    display = str(path.resolve())
                entries.append(
                    {
                        "path": display,
                        "sha256": digest_file(path),
                    }
                )
        return digest_payload({"files": entries})

    def _evaluation_subject_fingerprint(self, model: str) -> dict[str, str]:
        status = run_git(
            self.project_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
        ).stdout
        if status.strip():
            raise EvalBenchmarkServiceError(
                "evaluation subject checkout must be clean before freezing or running"
            )
        config = load_config(self.project_root)
        config_path = self.project_root / ".autobugfix/config.yaml"
        if not config_path.is_file():
            raise EvalBenchmarkServiceError(
                "evaluation subject has no .autobugfix/config.yaml"
            )
        roles: dict[str, dict[str, Any]] = {}
        skill_roots: list[Path] = []
        for role in ("writer", "evaluator"):
            resolved = resolve_role(config, role)
            encoded = resolved.to_dict(self.project_root)
            encoded["model"] = model
            roles[role] = encoded
            skill_roots.extend(resolved.skill_paths)
        return {
            "subject_sha": rev_parse(self.project_root, "HEAD"),
            "subject_tree": rev_parse(self.project_root, "HEAD^{tree}"),
            "config_digest": digest_file(config_path),
            "roles_digest": digest_payload({"roles": roles}),
            "skills_digest": self._file_set_digest(
                self.project_root,
                tuple(dict.fromkeys(skill_roots)),
            ),
            "memory_digest": self._file_set_digest(
                self.project_root,
                (
                    self.project_root / ".autobugfix-memory/active",
                    self.project_root / ".autobugfix-memory/skills/approved",
                ),
            ),
        }

    def prepare_evaluation(self, manifest_path: Path) -> dict[str, Any]:
        manifest = EvaluationSeedManifest.from_yaml(manifest_path.resolve())
        before = self._evaluation_subject_fingerprint(manifest.model)
        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed: {doctor['report_digest']}"
            )
        run_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "preflight-runs"
            / manifest.manifest_id
            / uuid.uuid4().hex
        )
        prepared_cases: list[PreparedEvaluationCase] = []
        failures: list[str] = []
        for case in manifest.cases:
            receipt = runtime.preflight_case(
                manifest,
                case,
                role="evaluation",
                first_wave=case.first_wave,
                artifact_root=run_root,
            )
            self.store.write_receipt(receipt)
            if (
                receipt.case_id != case.case_id
                or receipt.project != case.project
                or receipt.bug_id != case.bug_id
                or receipt.role != "evaluation"
                or receipt.first_wave != case.first_wave
                or receipt.manifest_digest != manifest.manifest_digest
                or receipt.framework_revision != manifest.framework_revision
                or receipt.dataset_revision != manifest.dataset_revision
                or receipt.runtime_id != doctor["runtime_id"]
                or receipt.verifier_runtime_id != doctor["verifier_runtime_id"]
            ):
                raise EvalBenchmarkServiceError(
                    f"qualification receipt disagrees with case: {case.case_id}"
                )
            if receipt.status != "eligible":
                failures.append(f"{case.case_id}: {receipt.reason}")
                continue
            prepared_cases.append(
                PreparedEvaluationCase(
                    case_id=receipt.case_id,
                    project=receipt.project,
                    bug_id=receipt.bug_id,
                    receipt_digest=str(receipt.to_dict()["record_digest"]),
                )
            )
        if failures:
            raise EvalBenchmarkServiceError(
                "evaluation qualification failed: " + "; ".join(failures)
            )
        after = self._evaluation_subject_fingerprint(manifest.model)
        if after != before:
            raise EvalBenchmarkServiceError(
                "evaluation subject changed during no-model qualification"
            )
        prepared = PreparedEvaluationManifest(
            manifest_id=manifest.manifest_id,
            seed_manifest_digest=manifest.manifest_digest,
            benchmark=manifest.benchmark,
            framework_revision=manifest.framework_revision,
            dataset_revision=manifest.dataset_revision,
            runtime_id=str(doctor["runtime_id"]),
            verifier_runtime_id=str(doctor["verifier_runtime_id"]),
            subject_sha=before["subject_sha"],
            subject_tree=before["subject_tree"],
            config_digest=before["config_digest"],
            roles_digest=before["roles_digest"],
            skills_digest=before["skills_digest"],
            memory_digest=before["memory_digest"],
            model=manifest.model,
            max_attempts=manifest.max_attempts,
            expected_case_count=manifest.expected_case_count,
            cases=tuple(prepared_cases),
            prepared_at=utc_now(),
        )
        data = prepared.to_dict()
        path = self.store.write_trusted_manifest(
            manifest.manifest_id,
            f"evaluation-{data['record_digest']}.yaml",
            data,
        )
        return {
            "manifest_id": prepared.manifest_id,
            "prepared_manifest": str(path),
            "prepared_manifest_digest": data["record_digest"],
            "subject_sha": prepared.subject_sha,
            "case_count": len(prepared.cases),
            "model": prepared.model,
            "max_attempts": prepared.max_attempts,
        }

    def run_evaluation(
        self,
        prepared_manifest_path: Path,
        *,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        prepared = self.store.read_prepared_evaluation_manifest(
            prepared_manifest_path
        )
        observed_before = self._evaluation_subject_fingerprint(prepared.model)
        expected = {
            "subject_sha": prepared.subject_sha,
            "subject_tree": prepared.subject_tree,
            "config_digest": prepared.config_digest,
            "roles_digest": prepared.roles_digest,
            "skills_digest": prepared.skills_digest,
            "memory_digest": prepared.memory_digest,
        }
        if observed_before != expected:
            raise EvalBenchmarkServiceError(
                "current H0 inputs differ from the prepared evaluation manifest"
            )
        receipts: list[EligibilityReceipt] = []
        rows: list[dict[str, Any]] = []
        for reference in prepared.cases:
            receipt = self.store.read_receipt(
                self.store.receipt_path(
                    reference.case_id,
                    reference.receipt_digest,
                )
            )
            if (
                receipt.status != "eligible"
                or receipt.role != "evaluation"
                or receipt.manifest_digest != prepared.seed_manifest_digest
                or receipt.runtime_id != prepared.runtime_id
                or receipt.verifier_runtime_id != prepared.verifier_runtime_id
                or receipt.project != reference.project
                or receipt.bug_id != reference.bug_id
                or str(receipt.to_dict()["record_digest"])
                != reference.receipt_digest
            ):
                raise EvalBenchmarkServiceError(
                    f"prepared evaluation receipt mismatch: {reference.case_id}"
                )
            receipts.append(receipt)
            rows.append(self._visible_case_row(receipt))
        dataset = self.store.write_visible_jsonl_rows(
            prepared.manifest_id,
            f"{safe_component(run_id, 'run_id')}.jsonl",
            rows,
        )
        run_dir = run_eval(
            self.project_root,
            dataset,
            out_root.resolve(),
            run_id=run_id,
            model=prepared.model,
            max_attempts=prepared.max_attempts,
            verifier_backends={
                receipt.case_id: managed_verifier_for_receipt(
                    receipt,
                    self.config.eval.benchmarks,
                )
                for receipt in receipts
            },
            official_evaluators={
                receipt.case_id: official_oracle_for_receipt(
                    receipt,
                    self.config.eval.benchmarks,
                )
                for receipt in receipts
            },
            sdk_hidden_paths=tuple(
                path.resolve()
                for path in (
                    self.config.eval.benchmarks.cache_root,
                    self.config.eval.benchmarks.trusted_case_root,
                    self.config.operator.state.root,
                    self.config.operator.artifacts.root,
                    self.project_root / ".autobugfix-memory",
                )
            ),
        )
        observed_after = self._evaluation_subject_fingerprint(prepared.model)
        unchanged = observed_after == expected
        write_yaml(
            run_dir / "subject-noninterference.yaml",
            record_with_digest(
                {
                    "schema": "autobugfix-evaluation-subject-noninterference-v1",
                    "prepared_manifest_digest": prepared.to_dict()["record_digest"],
                    "unchanged": unchanged,
                    "expected": expected,
                    "observed": observed_after,
                    "checked_at": utc_now(),
                }
            ),
        )
        if not unchanged:
            raise EvalBenchmarkServiceError(
                "formal evaluation changed the frozen H0 inputs"
            )
        evaluation_report = write_evaluation_report(run_dir)
        summary = yaml.safe_load(
            (run_dir / "summary.yaml").read_text(encoding="utf-8")
        ) or {}
        return {
            "run_dir": str(run_dir),
            "prepared_manifest_digest": prepared.to_dict()["record_digest"],
            "subject_sha": prepared.subject_sha,
            "summary": summary,
            "evaluation_report": str(evaluation_report),
        }

    @staticmethod
    def report_evaluation(run_dir: Path) -> dict[str, Any]:
        path = write_evaluation_report(run_dir)
        report = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(report, dict):
            raise EvalBenchmarkServiceError(
                "formal evaluation report must be a mapping"
            )
        return {"evaluation_report": str(path), "report": report}

    @staticmethod
    def _seed_manifest(
        manifest_path: Path,
    ) -> BenchmarkSeedManifest | EvaluationSeedManifest:
        data = yaml.safe_load(manifest_path.resolve().read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise EvalBenchmarkServiceError("benchmark manifest must be a mapping")
        if int(data.get("schema_version") or 0) == 3 and "cases" in data:
            return EvaluationSeedManifest.from_dict(data)
        return BenchmarkSeedManifest.from_dict(data)

    def preflight(
        self,
        manifest_path: Path,
        *,
        case_selector: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._seed_manifest(manifest_path)
        if manifest.benchmark != "defects4j":
            raise EvalBenchmarkServiceError("preflight manifest is not Defects4J")
        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed: {doctor['report_digest']}"
            )
        evaluation_mode = isinstance(manifest, EvaluationSeedManifest)
        source_cases = (
            manifest.cases if evaluation_mode else manifest.optimization_cases
        )
        selected = [
            case
            for case in source_cases
            if case_selector is None or case.case_id == case_selector
        ]
        if not selected:
            raise EvalBenchmarkServiceError("no benchmark case selected")
        run_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "preflight-runs"
            / manifest.manifest_id
            / uuid.uuid4().hex
        )
        projected: list[dict[str, Any]] = []
        eligible = 0
        for case in selected:
            receipt = runtime.preflight_case(
                manifest,
                case,
                role="evaluation" if evaluation_mode else "optimization",
                first_wave=case.first_wave,
                artifact_root=run_root,
            )
            receipt_path = self.store.write_receipt(receipt)
            if receipt.status == "eligible":
                eligible += 1
            projected.append(
                {
                    "case_id": receipt.case_id,
                    "status": receipt.status,
                    "reason": receipt.reason,
                    "first_wave": receipt.first_wave,
                    "receipt_digest": receipt.to_dict()["record_digest"],
                    "receipt_path": str(receipt_path),
                }
            )
        return {
            "manifest_id": manifest.manifest_id,
            "manifest_digest": manifest.manifest_digest,
            "selected_count": len(selected),
            "eligible_count": eligible,
            "failed_count": len(selected) - eligible,
            "cases": projected,
        }

    @staticmethod
    def _holdout_wave(index: int) -> int:
        if index == 0:
            return 3
        if index < 3:
            return 8
        return 16

    @staticmethod
    def _holdout_candidates(
        runtime: Defects4JRuntime,
        manifest: BenchmarkSeedManifest,
        project: str,
    ) -> list[int]:
        def rank(bug_id: int) -> str:
            payload = (
                f"{manifest.manifest_digest}:sealed-holdout:{project}:{bug_id}"
            )
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return sorted(runtime.active_bug_ids(project), key=rank)

    @staticmethod
    def _receipt_semantic_fingerprint(receipt: EligibilityReceipt) -> str:
        return digest_payload(
            {
                "project": receipt.project,
                "bug_id": receipt.bug_id,
                "first_wave": receipt.first_wave,
                "framework_revision": receipt.framework_revision,
                "dataset_revision": receipt.dataset_revision,
                "runtime_id": receipt.runtime_id,
                "verifier_runtime_id": receipt.verifier_runtime_id,
                "triggering_tests": list(receipt.triggering_tests),
                "baseline_failing_tests": list(receipt.baseline_failing_tests),
                "source_roots": list(receipt.source_roots),
                "sanitized_base_sha": receipt.sanitized_base_sha,
                "gold_patch_sha256": receipt.gold_patch_sha256,
                "verifier_metadata_sha256": receipt.verifier_metadata_sha256,
                "status": receipt.status,
            }
        )

    @classmethod
    def _guard_case(cls, receipt: EligibilityReceipt) -> GuardCaseSpec:
        problem, attachments = cls._problem_statement(receipt)
        return GuardCaseSpec(
            case_token=receipt.case_id,
            project=receipt.project,
            bug_id=receipt.bug_id,
            first_wave=receipt.first_wave,
            semantic_fingerprint=cls._receipt_semantic_fingerprint(receipt),
            problem_statement=problem,
            attachments=tuple(attachments),
        )

    @staticmethod
    def _private_holdout_projects(
        manifest: BenchmarkSeedManifest,
        projects: Sequence[str],
    ) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(item).strip() for item in projects if str(item).strip()))
        if len(normalized) < 3:
            raise EvalBenchmarkServiceError(
                "trusted Guard requires at least three private Holdout repository groups"
            )
        optimization_projects = {item.project for item in manifest.optimization_cases}
        overlap = optimization_projects & set(normalized)
        if overlap:
            raise EvalBenchmarkServiceError(
                "private Holdout repositories overlap Optimization: "
                + ", ".join(sorted(overlap))
            )
        return normalized

    def seal(
        self,
        manifest_path: Path,
        *,
        guard_secret: str | bytes,
        holdout_projects: Sequence[str],
    ) -> dict[str, Any]:
        code_identity = self.guard_authority()
        manifest = BenchmarkSeedManifest.from_yaml(manifest_path.resolve())
        if manifest.benchmark != "defects4j":
            raise EvalBenchmarkServiceError("seal manifest is not Defects4J")
        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed: {doctor['report_digest']}"
            )

        optimization_run_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "seal-runs"
            / manifest.manifest_id
            / uuid.uuid4().hex
            / "optimization"
        )
        optimization_references: list[dict[str, Any]] = []
        optimization_receipts: list[EligibilityReceipt] = []
        failed_optimization: list[str] = []
        for case in manifest.optimization_cases:
            receipt = runtime.preflight_case(
                manifest,
                case,
                role="optimization",
                first_wave=case.first_wave,
                artifact_root=optimization_run_root,
            )
            receipt_path = self.store.write_receipt(receipt)
            if receipt.status != "eligible":
                failed_optimization.append(f"{case.case_id}: {receipt.reason}")
                continue
            optimization_receipts.append(receipt)
            optimization_references.append(
                {
                    "case_token": case.case_id,
                    "case_id": receipt.case_id,
                    "project": receipt.project,
                    "bug_id": receipt.bug_id,
                    "first_wave": receipt.first_wave,
                    "receipt_digest": str(receipt.to_dict()["record_digest"]),
                }
            )
        if failed_optimization:
            raise EvalBenchmarkServiceError(
                "Optimization preflight failed: " + "; ".join(failed_optimization)
            )

        private_projects = self._private_holdout_projects(manifest, holdout_projects)
        guard_id = new_guard_id()
        aad = guard_aad(
            guard_id,
            manifest.manifest_digest,
            manifest.framework_revision,
            manifest.dataset_revision,
            code_identity.identity_digest,
        )
        guard_root = self.config.eval.benchmarks.trusted_case_root / "guard" / guard_id
        guard_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        guard_root.chmod(0o700)
        preflight_archive_name = "holdout-preflight.abfg"
        preflight_archive = guard_root / preflight_archive_name
        holdout_cases: list[GuardCaseSpec] = []
        with tempfile.TemporaryDirectory(prefix="autobugfix-holdout-seal-") as temporary:
            private_root = Path(temporary)
            private_root.chmod(0o700)
            private_config = replace(
                self.config.eval.benchmarks,
                cache_root=private_root / "cache",
                trusted_case_root=private_root / "trusted",
                visible_manifest_root=private_root / "visible",
            )
            private_runtime = Defects4JRuntime(private_config)
            docker_bin = runtime.docker_bin
            if docker_bin is None:
                raise EvalBenchmarkServiceError("inspected Docker binary disappeared")
            private_runtime.bind_inspected_runtime(
                docker_bin=docker_bin,
                runtime_id=str(doctor["runtime_id"]),
                verifier_runtime_id=str(doctor["verifier_runtime_id"]),
            )
            candidate_queues = {
                project: self._holdout_candidates(private_runtime, manifest, project)
                for project in private_projects
            }
            active_projects = list(private_projects)
            while len(holdout_cases) < manifest.holdout_count and active_projects:
                accepted_this_round = False
                for project in tuple(active_projects):
                    queue = candidate_queues[project]
                    accepted_for_project = False
                    while queue:
                        bug_id = queue.pop(0)
                        case_token = f"holdout-{secrets.token_hex(24)}"
                        case = BenchmarkCaseSeed(
                            case_id=case_token,
                            project=project,
                            bug_id=bug_id,
                            first_wave=self._holdout_wave(len(holdout_cases)),
                        )
                        receipt = private_runtime.preflight_case(
                            manifest,
                            case,
                            role="sealed_holdout",
                            first_wave=case.first_wave,
                            artifact_root=private_root / "preflight",
                        )
                        if receipt.status != "eligible":
                            continue
                        holdout_cases.append(self._guard_case(receipt))
                        accepted_this_round = True
                        accepted_for_project = True
                        break
                    if not queue and not accepted_for_project:
                        active_projects.remove(project)
                    if len(holdout_cases) == manifest.holdout_count:
                        break
                if not accepted_this_round:
                    break
            if len(holdout_cases) != manifest.holdout_count:
                raise EvalBenchmarkServiceError(
                    "private Holdout pool did not yield six eligible repository-isolated cases"
                )
            encrypt_artifact_tree(
                private_root,
                preflight_archive,
                secret=guard_secret,
                aad=aad + b":preflight",
            )

        wave_tokens = {
            str(wave): f"wave-{wave}-{secrets.token_hex(24)}"
            for wave in (3, 8, 16)
        }
        bundle = GuardBundle(
            guard_id=guard_id,
            seed_manifest_digest=manifest.manifest_digest,
            framework_revision=manifest.framework_revision,
            dataset_revision=manifest.dataset_revision,
            runtime_id=str(doctor["runtime_id"]),
            verifier_runtime_id=str(doctor["verifier_runtime_id"]),
            code_identity=code_identity,
            preflight_archive_name=preflight_archive_name,
            preflight_archive_sha256=guard_artifact_digest(preflight_archive),
            wave_tokens=wave_tokens,
            holdout_cases=tuple(holdout_cases),
            created_at=utc_now(),
        )
        bundle_path = guard_root / "holdout.bundle.abfg"
        encrypt_json(bundle.to_dict(), bundle_path, secret=guard_secret, aad=aad)
        bundle_digest = guard_artifact_digest(bundle_path)
        manifest_id = f"{manifest.manifest_id}-guarded"
        public_manifest = record_with_digest(
            {
                "schema_version": 3,
                "manifest_id": manifest_id,
                "seed_manifest_digest": manifest.manifest_digest,
                "framework_revision": manifest.framework_revision,
                "dataset_revision": manifest.dataset_revision,
                "runtime_id": str(doctor["runtime_id"]),
                "verifier_runtime_id": str(doctor["verifier_runtime_id"]),
                "optimization_cases": optimization_references,
                "guard": {
                    "guard_id": guard_id,
                    "code_identity": code_identity.to_dict(),
                    "bundle_sha256": bundle_digest,
                    "preflight_archive_sha256": bundle.preflight_archive_sha256,
                    "waves": {
                        str(wave): {
                            "token": wave_tokens[str(wave)],
                            "holdout_count": {3: 1, 8: 3, 16: 6}[wave],
                            "total_case_count": wave,
                        }
                        for wave in (3, 8, 16)
                    },
                },
            }
        )
        visible_path = self.store.write_visible_yaml(
            manifest_id,
            "manifest.yaml",
            public_manifest,
        )
        visible_dataset = self.store.write_visible_jsonl_rows(
            manifest_id,
            "optimization.jsonl",
            [
                self._visible_case_row(receipt)
                for receipt in optimization_receipts
            ],
        )
        return {
            "manifest_id": manifest_id,
            "guard_id": guard_id,
            "encrypted_bundle_sha256": bundle_digest,
            "visible_manifest": str(visible_path),
            "optimization_dataset": str(visible_dataset),
            "optimization_count": len(optimization_references),
            "sealed_holdout_count": len(holdout_cases),
            "waves": {3: 3, 8: 8, 16: 16},
        }

    @staticmethod
    def _problem_statement(receipt) -> tuple[str, list[dict[str, str]]]:
        issue_path = Path(receipt.issue_evidence_path)
        attachments: list[dict[str, str]] = []
        title = f"Repair Defects4J {receipt.project}-{receipt.bug_id}"
        body = ""
        if issue_path.is_file():
            data = yaml.safe_load(issue_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                title = str(data.get("title") or title)
                body = str(data.get("body") or "")
                for uri in data.get("attachment_uris") or []:
                    attachments.append(
                        {
                            "kind": "upstream-attachment",
                            "uri": str(uri),
                            "description": "Attachment referenced by the upstream issue",
                        }
                    )
        trigger_text = "\n".join(f"- {item}" for item in receipt.triggering_tests)
        failure_text = ""
        if receipt.failure_evidence_path != "unavailable":
            failure_path = Path(receipt.failure_evidence_path)
            if failure_path.is_file():
                failure_text = failure_path.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
        reproduction = (
            receipt.reproduction_command
            if receipt.reproduction_command != "unavailable"
            else "defects4j test -w /workspace"
        )
        problem = "\n\n".join(
            part
            for part in (
                title,
                body,
                "Official triggering tests:\n" + trigger_text,
                "Pinned reproduction command:\n" + reproduction,
                (
                    "Observed buggy failure output and stack trace:\n" + failure_text
                    if failure_text
                    else ""
                ),
                "Modify production source only. The Execution verifier will run only the declared visible triggering tests.",
            )
            if part.strip()
        )
        return problem, attachments

    def _visible_case_row(
        self,
        receipt,
        *,
        problem_override: str | None = None,
        attachments_override: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        source_repo = Path(receipt.sanitized_repo_path).resolve()
        receipt_digest = str(receipt.to_dict()["record_digest"])
        managed_verifier = managed_verifier_for_receipt(
            receipt,
            self.config.eval.benchmarks,
        )
        if problem_override is None:
            problem, attachments = self._problem_statement(receipt)
        else:
            problem = problem_override
            attachments = [dict(item) for item in attachments_override or ()]
        return {
            "schema_version": 1,
            "case_id": receipt.case_id,
            "source": {
                "adapter": "defects4j",
                "benchmark": "defects4j",
                "revision": receipt.dataset_revision,
                "split": receipt.role,
                "instance_id": receipt.case_id,
            },
            "task": {
                "type": "bugfix",
                "problem_statement": problem,
                "agent_prompt": problem,
                "expected_behavior": "The declared visible triggering tests pass after the repair.",
                "attachments": attachments,
            },
            "repository": {
                "repo_id": f"defects4j-{receipt.project.lower()}-{receipt.bug_id}",
                "worktree_path": str(source_repo),
                "base_commit": receipt.sanitized_base_sha,
            },
            "environment": {
                "image": receipt.verifier_runtime_id,
                "platform": self.config.eval.benchmarks.defects4j.platform,
            },
            "execution": {"test_command": managed_verifier.command_id},
            "oracle": {
                "type": "defects4j",
                "require_patch": True,
                "timeout_seconds": self.config.eval.benchmarks.command_timeout_seconds,
                "visibility": "hidden",
            },
            "benchmark": {
                "framework_revision": receipt.framework_revision,
                "dataset_revision": receipt.dataset_revision,
                "runtime_id": receipt.runtime_id,
                "eligibility_receipt_digest": receipt_digest,
                "visible_evidence_digest": receipt.issue_evidence_digest,
            },
            "experiment": {
                "role": receipt.role,
                "first_wave": receipt.first_wave,
                "repository_group": receipt.project,
                "case_token": receipt.case_id,
            },
            "defects4j": {
                "project": receipt.project,
                "bug_id": receipt.bug_id,
                "triggering_tests": list(receipt.triggering_tests),
                "source_roots": list(receipt.source_roots),
                "verifier_command_id": managed_verifier.command_id,
            },
        }

    def _execute_receipt(
        self,
        receipt: EligibilityReceipt,
        *,
        manifest_id: str,
        out_root: Path,
        run_id: str,
        max_attempts: int,
        private_root: Path | None = None,
        problem_override: str | None = None,
        attachments_override: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        safe_component(run_id, "run_id")
        row = self._visible_case_row(
            receipt,
            problem_override=problem_override,
            attachments_override=attachments_override,
        )
        if private_root is None:
            dataset = self.store.write_visible_jsonl(
                manifest_id,
                f"{run_id}-{receipt.case_id}.jsonl",
                row,
            )
        else:
            dataset = private_root / "datasets" / f"{run_id}.jsonl"
            dataset.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            dataset.write_text(
                json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            dataset.chmod(0o600)
        run_dir = run_eval(
            self.project_root,
            dataset,
            out_root.resolve(),
            case_selector=receipt.case_id,
            run_id=run_id,
            model="gpt-5.4-mini",
            max_attempts=max_attempts,
            verifier_backends={
                receipt.case_id: managed_verifier_for_receipt(
                    receipt,
                    self.config.eval.benchmarks,
                )
            },
            official_evaluators={
                receipt.case_id: official_oracle_for_receipt(
                    receipt,
                    self.config.eval.benchmarks,
                )
            },
            sdk_hidden_paths=tuple(
                dict.fromkeys(
                    path.resolve()
                    for path in (
                        self.config.eval.benchmarks.cache_root,
                        self.config.eval.benchmarks.trusted_case_root,
                        self.config.operator.state.root,
                        self.config.operator.artifacts.root,
                        (
                            self.config.task_root
                            if self.config.task_root.is_absolute()
                            else self.project_root / self.config.task_root
                        ),
                        self.project_root / ".autobugfix-memory",
                        self.project_root / ".autobugfix/archive",
                        self.project_root / ".autobugfix/controller",
                        *((private_root,) if private_root is not None else ()),
                    )
                )
            ),
        )
        summary = yaml.safe_load((run_dir / "summary.yaml").read_text(encoding="utf-8")) or {}
        report = yaml.safe_load(
            (run_dir / receipt.case_id / "report.yaml").read_text(encoding="utf-8")
        ) or {}
        return {
            "run_dir": str(run_dir),
            "dataset": str(dataset),
            "receipt_digest": str(receipt.to_dict()["record_digest"]),
            "summary": summary,
            "report": report,
        }

    def run_case(
        self,
        manifest_path: Path,
        *,
        case_selector: str,
        out_root: Path,
        run_id: str,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        if model != "gpt-5.4-mini":
            raise EvalBenchmarkServiceError(
                "Defects4J experiment model must be gpt-5.4-mini"
            )
        manifest_data = yaml.safe_load(manifest_path.resolve().read_text(encoding="utf-8")) or {}
        if not isinstance(manifest_data, dict):
            raise EvalBenchmarkServiceError("benchmark manifest must be a mapping")
        if "guard" in manifest_data:
            try:
                verify_record(manifest_data)
            except BenchmarkContractError as exc:
                raise EvalBenchmarkServiceError(str(exc)) from exc
            if int(manifest_data.get("schema_version") or 0) != 3:
                raise EvalBenchmarkServiceError("unsupported guarded manifest schema")
            raw_references = manifest_data.get("optimization_cases") or []
            if not isinstance(raw_references, list):
                raise EvalBenchmarkServiceError(
                    "guarded Optimization references must be a list"
                )
            references = [
                item
                for item in raw_references
                if isinstance(item, dict)
                and case_selector in {item.get("case_id"), item.get("case_token")}
            ]
            if len(references) != 1:
                raise EvalBenchmarkServiceError(
                    "no unique guarded Optimization case selected; Holdout requires guard-run"
                )
            reference = references[0]
            receipt_digest = str(reference.get("receipt_digest") or "")
            receipt_path = self.store.receipt_path(
                str(reference.get("case_id") or ""),
                receipt_digest,
            )
            receipt = self.store.read_receipt(receipt_path)
            if (
                str(receipt.to_dict()["record_digest"]) != receipt_digest
                or receipt.role != "optimization"
                or receipt.project != str(reference.get("project") or "")
                or receipt.bug_id != int(reference.get("bug_id") or 0)
                or receipt.runtime_id != str(manifest_data.get("runtime_id") or "")
                or receipt.verifier_runtime_id
                != str(manifest_data.get("verifier_runtime_id") or "")
            ):
                raise EvalBenchmarkServiceError(
                    "guarded Optimization reference does not match its receipt"
                )
            manifest_id = safe_component(
                manifest_data.get("manifest_id"),
                "manifest_id",
            )
            projected_receipt_digest = receipt_digest
        elif "holdout_cases" in manifest_data:
            raise EvalBenchmarkServiceError(
                "legacy plaintext sealed manifests are compromised and cannot be executed; "
                "create a schema v3 code-bound encrypted Guard seal"
            )
        else:
            preflight = self.preflight(manifest_path, case_selector=case_selector)
            case_projection = preflight["cases"][0]
            if case_projection["status"] != "eligible":
                raise EvalBenchmarkServiceError(
                    f"Defects4J case is not eligible: {case_projection['reason']}"
                )
            manifest = self._seed_manifest(manifest_path)
            if isinstance(manifest, EvaluationSeedManifest):
                if model != manifest.model:
                    raise EvalBenchmarkServiceError(
                        "model differs from the pre-registered evaluation manifest"
                    )
                if max_attempts != manifest.max_attempts:
                    raise EvalBenchmarkServiceError(
                        "max_attempts differs from the pre-registered evaluation manifest"
                    )
            receipt_path = Path(str(case_projection["receipt_path"])).resolve()
            receipt = self.store.read_receipt(receipt_path)
            manifest_id = manifest.manifest_id
            projected_receipt_digest = str(case_projection["receipt_digest"])
        result = self._execute_receipt(
            receipt,
            manifest_id=manifest_id,
            out_root=out_root,
            run_id=run_id,
            max_attempts=max_attempts,
        )
        if result["receipt_digest"] != projected_receipt_digest:
            raise EvalBenchmarkServiceError("executed receipt digest changed")
        return result

    def _load_guard_bundle(
        self,
        public_manifest_path: Path,
        guard_secret: str | bytes,
    ) -> tuple[dict[str, Any], GuardBundle, bytes]:
        public = yaml.safe_load(
            public_manifest_path.resolve().read_text(encoding="utf-8")
        ) or {}
        if not isinstance(public, dict):
            raise EvalBenchmarkServiceError("Guard public manifest must be a mapping")
        try:
            verify_record(public)
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        guard = public.get("guard")
        if int(public.get("schema_version") or 0) != 3 or not isinstance(guard, dict):
            raise EvalBenchmarkServiceError("unsupported Guard public manifest")
        guard_id = safe_component(guard.get("guard_id"), "guard_id")
        raw_identity = guard.get("code_identity") or {}
        if not isinstance(raw_identity, Mapping):
            raise EvalBenchmarkServiceError("Guard public code identity must be a mapping")
        try:
            public_identity = GuardCodeIdentity.from_dict(raw_identity)
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        aad = guard_aad(
            guard_id,
            str(public.get("seed_manifest_digest") or ""),
            str(public.get("framework_revision") or ""),
            str(public.get("dataset_revision") or ""),
            public_identity.identity_digest,
        )
        bundle_path = (
            self.config.eval.benchmarks.trusted_case_root
            / "guard"
            / guard_id
            / "holdout.bundle.abfg"
        ).resolve()
        if not bundle_path.is_relative_to(
            self.config.eval.benchmarks.trusted_case_root
        ) or not bundle_path.is_file():
            raise EvalBenchmarkServiceError("encrypted Guard bundle is missing")
        if guard_artifact_digest(bundle_path) != str(guard.get("bundle_sha256") or ""):
            raise EvalBenchmarkServiceError("encrypted Guard bundle digest mismatch")
        try:
            bundle = GuardBundle.from_dict(
                decrypt_json(
                    bundle_path,
                    secret=guard_secret,
                    aad=aad,
                )
            )
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(f"Guard bundle authentication failed: {exc}") from exc
        if (
            bundle.guard_id != guard_id
            or bundle.seed_manifest_digest
            != str(public.get("seed_manifest_digest") or "")
            or bundle.runtime_id != str(public.get("runtime_id") or "")
            or bundle.verifier_runtime_id
            != str(public.get("verifier_runtime_id") or "")
            or bundle.code_identity != public_identity
        ):
            raise EvalBenchmarkServiceError(
                "decrypted Guard bundle does not match public authority projection"
            )
        preflight_archive = bundle_path.parent / bundle.preflight_archive_name
        if (
            not preflight_archive.is_file()
            or guard_artifact_digest(preflight_archive)
            != bundle.preflight_archive_sha256
        ):
            raise EvalBenchmarkServiceError(
                "encrypted Guard preflight evidence is missing or changed"
            )
        return public, bundle, aad

    def guard_run(
        self,
        public_manifest_path: Path,
        *,
        wave_token: str,
        out_root: Path,
        run_id: str,
        guard_secret: str | bytes,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
        study_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if model != "gpt-5.4-mini":
            raise EvalBenchmarkServiceError(
                "Guard experiment model must be gpt-5.4-mini"
            )
        safe_component(run_id, "run_id")
        current_identity = self.guard_authority()
        public, bundle, aad = self._load_guard_bundle(
            public_manifest_path,
            guard_secret,
        )
        if current_identity != bundle.code_identity:
            raise EvalBenchmarkServiceError(
                "current Guard control-plane identity differs from the sealed authority"
            )
        if study_binding is not None:
            try:
                verify_record(study_binding)
            except BenchmarkContractError as exc:
                raise EvalBenchmarkServiceError(
                    f"Guard Study binding is invalid: {exc}"
                ) from exc
            if (
                str(study_binding.get("subject_sha") or "")
                != current_identity.trusted_commit
            ):
                raise EvalBenchmarkServiceError(
                    "direct Guard runner can measure only its trusted checkout; "
                    "candidate Study metrics require an isolated subject broker"
                )
        matched_waves = [
            int(wave)
            for wave, token in bundle.wave_tokens.items()
            if hmac.compare_digest(token, wave_token)
        ]
        if len(matched_waves) != 1:
            raise EvalBenchmarkServiceError("invalid opaque Guard wave token")
        wave = matched_waves[0]
        selected = [case for case in bundle.holdout_cases if case.first_wave <= wave]
        expected_count = {3: 1, 8: 3, 16: 6}[wave]
        if len(selected) != expected_count:
            raise EvalBenchmarkServiceError("Guard wave selection contract is invalid")

        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed before Guard execution: {doctor['report_digest']}"
            )
        if (
            doctor["runtime_id"] != bundle.runtime_id
            or doctor["verifier_runtime_id"] != bundle.verifier_runtime_id
        ):
            raise EvalBenchmarkServiceError(
                "current Docker authorities differ from the sealed Guard runtimes"
            )

        output_root = out_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        encrypted_artifacts = output_root / f"{run_id}.artifacts.abfg"
        metric_path = output_root / f"{run_id}.metric.yaml"
        if encrypted_artifacts.exists() or metric_path.exists():
            raise EvalBenchmarkServiceError("Guard output already exists for run_id")
        reports: list[dict[str, Any]] = []
        fatal: BaseException | None = None
        artifact_aad = aad + f":run:{run_id}:wave:{wave}".encode("ascii")
        with tempfile.TemporaryDirectory(prefix="autobugfix-holdout-run-") as temporary:
            private_root = Path(temporary)
            private_root.chmod(0o700)
            private_config = replace(
                self.config.eval.benchmarks,
                cache_root=private_root / "cache",
                trusted_case_root=private_root / "trusted",
                visible_manifest_root=private_root / "visible",
            )
            private_runtime = Defects4JRuntime(private_config)
            docker_bin = runtime.docker_bin
            if docker_bin is None:
                raise EvalBenchmarkServiceError("inspected Docker binary disappeared")
            private_runtime.bind_inspected_runtime(
                docker_bin=docker_bin,
                runtime_id=bundle.runtime_id,
                verifier_runtime_id=bundle.verifier_runtime_id,
            )
            try:
                for index, case_spec in enumerate(selected, start=1):
                    case_root = private_root / "cases" / f"case-{index:02d}"
                    case_root.mkdir(parents=True, mode=0o700, exist_ok=False)
                    case = BenchmarkCaseSeed(
                        case_id=case_spec.case_token,
                        project=case_spec.project,
                        bug_id=case_spec.bug_id,
                        first_wave=case_spec.first_wave,
                    )
                    receipt = private_runtime.preflight_case(
                        BenchmarkSeedManifest(
                            manifest_id="guard-private-revalidation",
                            benchmark="defects4j",
                            framework_revision=bundle.framework_revision,
                            dataset_revision=bundle.dataset_revision,
                            optimization_cases=tuple(
                                BenchmarkCaseSeed(
                                    case_id=f"placeholder-{number}",
                                    project="GuardPlaceholderA"
                                    if number <= 5
                                    else "GuardPlaceholderB",
                                    bug_id=number,
                                    first_wave=3 if number <= 2 else 8 if number <= 5 else 16,
                                )
                                for number in range(1, 11)
                            ),
                        ),
                        case,
                        role="sealed_holdout",
                        first_wave=case.first_wave,
                        artifact_root=case_root / "preflight",
                    )
                    observed_fingerprint = self._receipt_semantic_fingerprint(receipt)
                    write_yaml(
                        case_root / "guard-revalidation.yaml",
                        {
                            "status": receipt.status,
                            "expected_fingerprint": case_spec.semantic_fingerprint,
                            "observed_fingerprint": observed_fingerprint,
                            "matched": observed_fingerprint
                            == case_spec.semantic_fingerprint,
                        },
                    )
                    if (
                        receipt.status != "eligible"
                        or observed_fingerprint != case_spec.semantic_fingerprint
                    ):
                        reports.append(
                            {
                                "decision": "error",
                                "failure_stage": "guard_revalidation",
                            }
                        )
                        continue
                    result = self._execute_receipt(
                        receipt,
                        manifest_id=bundle.guard_id,
                        out_root=case_root / "eval-runs",
                        run_id=f"case-{index:02d}",
                        max_attempts=max_attempts,
                        private_root=case_root,
                        problem_override=case_spec.problem_statement,
                        attachments_override=case_spec.attachments,
                    )
                    reports.append(dict(result["report"]))
            except BaseException as exc:
                fatal = exc
                (private_root / "guard-fatal.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
            finally:
                encrypt_artifact_tree(
                    private_root,
                    encrypted_artifacts,
                    secret=guard_secret,
                    aad=artifact_aad,
                )

        if fatal is not None:
            raise EvalBenchmarkServiceError(
                "Guard run failed; partial evidence was encrypted under the "
                "external Guard root"
            ) from fatal
        passed = sum(1 for report in reports if report.get("decision") == "pass")
        failed = sum(1 for report in reports if report.get("decision") == "fail")
        errors = len(reports) - passed - failed
        if errors:
            raise EvalBenchmarkServiceError(
                "Guard run contained harness errors; encrypted evidence was "
                "retained and no metric authority was issued"
            )
        metric = signed_metric(
            metric_payload(
                guard_id=bundle.guard_id,
                run_id=run_id,
                wave=wave,
                case_count=len(selected),
                passed_count=passed,
                failed_count=failed,
                harness_error_count=errors,
                encrypted_artifact_sha256=guard_artifact_digest(encrypted_artifacts),
                public_manifest_digest=str(public["record_digest"]),
                code_identity=current_identity,
                study_binding=study_binding,
            ),
            guard_secret,
        )
        write_yaml(metric_path, metric)
        metric_path.chmod(0o644)
        return {
            "guard_id": bundle.guard_id,
            "run_id": run_id,
            "wave": wave,
            "case_count": len(selected),
            "passed_count": passed,
            "failed_count": failed,
            "harness_error_count": errors,
            "pass_rate": passed / len(selected),
            "metric_receipt": str(metric_path),
            "encrypted_artifacts": str(encrypted_artifacts),
            "encrypted_artifacts_sha256": metric[
                "encrypted_artifact_sha256"
            ],
            "public_manifest_digest": public["record_digest"],
        }
