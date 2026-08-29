"""Trusted Eval execution and recovery boundary for Exp2 resume studies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from autobugfix.config import load_config
from autobugfix.eval.benchmarks.exp2_records import Exp2EmptyMemoryFixture
from autobugfix.eval.benchmarks.exp2_resume import (
    Exp2CaseAttemptIntent,
    Exp2CaseAttemptReceipt,
    Exp2OciImageIdentity,
    Exp2ResumeCoordinator,
    Exp2ResumeError,
    Exp2ResumeProtocol,
    Exp2ResumeStudyPlan,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    digest_payload,
    record_with_digest,
    verify_record,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.service import (
    EvalBenchmarkService,
    EvalBenchmarkServiceError,
)
from autobugfix.eval.benchmarks.swe_models import SWEExperimentProtocol
from autobugfix.eval.benchmarks.swe_submission import verify_evidence_manifest
from autobugfix.git_utils import rev_parse, run_git
from autobugfix.operator.service import (
    OperatorGovernanceError,
    OperatorGovernanceService,
    exp2_protected_memory_roots,
)


class Exp2EvalAuthorityError(RuntimeError):
    """The trusted Eval boundary cannot execute or reconcile an Exp2 case."""


class Exp2EvalAuthority:
    """Bind the v2 journal to the real SWE Eval service and trusted artifacts."""

    authority_id = "eval-benchmark-service-v2"

    def __init__(
        self,
        project_root: Path,
        coordinator: Exp2ResumeCoordinator,
        *,
        service: EvalBenchmarkService | None = None,
        operator_service: OperatorGovernanceService | None = None,
        image_gate_resolver: Callable[
            [Exp2CaseAttemptIntent, Exp2OciImageIdentity], Mapping[str, Any]
        ]
        | None = None,
    ):
        self.project_root = project_root.resolve()
        self.coordinator = coordinator
        self.config = load_config(self.project_root)
        self.service = service or EvalBenchmarkService(self.project_root)
        self.operator_service = operator_service or OperatorGovernanceService(
            self.project_root
        )
        self.image_gate_resolver = image_gate_resolver
        self.plan = coordinator.load_plan()
        self.protocol = coordinator.load_protocol()
        self._validate_static_authority()

    @staticmethod
    def _read_yaml_record(path: Path, label: str) -> dict[str, Any]:
        source = path.resolve()
        if path.is_symlink() or not source.is_file():
            raise Exp2EvalAuthorityError(f"{label} is missing or redirected")
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2EvalAuthorityError(f"{label} is not a mapping")
        try:
            verify_record(raw)
        except BenchmarkContractError as exc:
            raise Exp2EvalAuthorityError(f"{label} digest is invalid") from exc
        return dict(raw)

    @staticmethod
    def _read_json_record(path: Path, label: str) -> dict[str, Any]:
        source = path.resolve()
        if path.is_symlink() or not source.is_file():
            raise Exp2EvalAuthorityError(f"{label} is missing or redirected")
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Exp2EvalAuthorityError(f"{label} is not JSON") from exc
        if not isinstance(raw, Mapping):
            raise Exp2EvalAuthorityError(f"{label} is not a mapping")
        try:
            verify_record(raw)
        except BenchmarkContractError as exc:
            raise Exp2EvalAuthorityError(f"{label} digest is invalid") from exc
        return dict(raw)

    @staticmethod
    def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
        serialized = yaml.safe_dump(dict(payload), sort_keys=False)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.exists():
            if path.is_symlink() or path.read_text(encoding="utf-8") != serialized:
                raise Exp2EvalAuthorityError(
                    f"immutable Exp2 Eval artifact already differs: {path}"
                )
            return
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())

    def _validate_static_authority(self) -> None:
        benchmark = self.config.eval.benchmarks
        trusted_root = benchmark.trusted_case_root.resolve()
        visible_root = benchmark.visible_manifest_root.resolve()
        if Path(self.plan.eval_root).resolve() != trusted_root:
            raise Exp2EvalAuthorityError(
                "Exp2 plan Eval root differs from configured trusted Eval state"
            )
        if not Path(self.plan.artifact_root).resolve().is_relative_to(trusted_root):
            raise Exp2EvalAuthorityError(
                "Exp2 artifact root is outside configured trusted Eval state"
            )
        if Path(self.plan.operator_root).resolve() != (
            self.config.operator.state.root.resolve()
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 Operator root differs from configured governance state"
            )
        memory_source = Path(self.plan.memory_root).expanduser()
        memory_root = memory_source.resolve()
        guard_source = Path(self.plan.guard_root).expanduser()
        try:
            resolved_guard_root = guard_source.resolve(strict=True)
        except OSError as exc:
            raise Exp2EvalAuthorityError(
                "Exp2 Guard root must be an absolute real protected directory"
            ) from exc
        if (
            not guard_source.is_absolute()
            or guard_source != resolved_guard_root
            or not resolved_guard_root.is_dir()
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 Guard root must be an absolute real protected directory"
            )
        protected_memory_roots = exp2_protected_memory_roots(
            self.project_root,
            self.config,
            guard_root=resolved_guard_root,
        )
        if any(
            memory_root == protected
            or memory_root.is_relative_to(protected)
            or protected.is_relative_to(memory_root)
            for protected in protected_memory_roots
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 empty Memory root overlaps protected or canonical state"
            )
        try:
            memory_digest = (
                self.operator_service.validate_exp2_empty_memory_root(
                    memory_source
                )
            )
        except OperatorGovernanceError as exc:
            raise Exp2EvalAuthorityError(str(exc)) from exc
        if memory_digest != self.plan.memory_fixture_digest:
            raise Exp2EvalAuthorityError(
                "Exp2 empty Memory root differs from the frozen fixture digest"
            )
        if self.plan.public_manifest_path is not None and not Path(
            self.plan.public_manifest_path
        ).resolve().is_relative_to(visible_root):
            raise Exp2EvalAuthorityError(
                "Exp2 public manifest is outside Eval visible state"
            )
        apparatus = self._read_yaml_record(
            Path(self.plan.apparatus_receipt_path),
            "Exp2 apparatus receipt",
        )
        if (
            apparatus.get("record_digest")
            != self.plan.apparatus_receipt_digest
            or apparatus.get("apparatus_sha") != self.plan.apparatus_sha
            or apparatus.get("apparatus_tree") != self.plan.apparatus_tree
            or rev_parse(self.project_root, "HEAD") != self.plan.apparatus_sha
            or rev_parse(self.project_root, "HEAD^{tree}")
            != self.plan.apparatus_tree
        ):
            raise Exp2EvalAuthorityError(
                "current Git apparatus differs from the frozen receipt"
            )
        if run_git(
            self.project_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        ).stdout.strip():
            raise Exp2EvalAuthorityError(
                "formal Exp2 execution requires a clean apparatus checkout"
            )
        raw_checks = apparatus.get("source_checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise Exp2EvalAuthorityError(
                "Exp2 apparatus lacks passing source-check receipts"
            )
        for reference in raw_checks:
            if not isinstance(reference, Mapping):
                raise Exp2EvalAuthorityError(
                    "Exp2 apparatus source-check reference is invalid"
                )
            check_path = Path(str(reference.get("path") or ""))
            check = self._read_yaml_record(
                check_path,
                "Exp2 source-check receipt",
            )
            if (
                digest_file(check_path) != reference.get("sha256")
                or check.get("record_digest")
                != reference.get("receipt_digest")
                or check.get("passed") is not True
                or check.get("exit_code") != 0
            ):
                raise Exp2EvalAuthorityError(
                    "Exp2 source-check receipt no longer verifies"
                )
        operator = self.operator_service
        if (
            operator.governance_context()["digest"]
            != self.protocol.operator_policy_digest
        ):
            raise Exp2EvalAuthorityError(
                "current Operator policy differs from the frozen protocol"
            )
        role_digests = operator.exp2_role_skill_digests(
            subject_sha=self.plan.h0_subject_sha,
            primary_model=self.protocol.model,
        )
        if (
            role_digests["operator_role_skill_digest"]
            != self.protocol.operator_role_skill_digest
            or role_digests["execution_role_skill_digest"]
            != self.protocol.execution_role_skill_digest
        ):
            raise Exp2EvalAuthorityError(
                "current H0 role skills differ from the frozen protocol"
            )

        runtime = self.service.exp2_runtime_identity(
            Path(self.plan.swe_protocol_path)
        )
        try:
            verify_record(runtime)
        except BenchmarkContractError as exc:
            raise Exp2EvalAuthorityError(
                "Exp2 Eval runtime identity is invalid"
            ) from exc
        expected = {
            "swe_protocol_sha256": self.plan.swe_protocol_sha256,
            "dataset_revision": self.protocol.dataset_revision,
            "scorer_digest": self.protocol.scorer_digest,
            "runtime_digest": self.protocol.runtime_digest,
            "model": self.protocol.model,
            "reasoning_effort": self.protocol.reasoning_effort,
            "max_attempts": self.protocol.max_attempts,
            "timeout_seconds": self.protocol.timeout_seconds,
            "case_concurrency": self.protocol.case_concurrency,
        }
        if any(runtime.get(key) != value for key, value in expected.items()):
            raise Exp2EvalAuthorityError(
                "Exp2 Eval runtime differs from the frozen v2 protocol"
            )

    def _validate_execution_roots(self) -> None:
        disposable = Path(self.plan.disposable_root).resolve()
        if (
            disposable == Path("/")
            or disposable == Path("/tmp")
            or disposable.is_symlink()
            or not disposable.is_dir()
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 disposable root must be a dedicated real directory"
            )
        protected = (
            self.project_root,
            Path(self.plan.eval_root).resolve(),
            Path(self.plan.operator_root).resolve(),
            Path(self.plan.memory_root).resolve(),
            Path(self.plan.guard_root).resolve(),
        )
        for root in protected:
            if disposable == root or disposable.is_relative_to(root) or root.is_relative_to(
                disposable
            ):
                raise Exp2EvalAuthorityError(
                    "Exp2 disposable root overlaps protected authority state"
                )

    def _case_image(self, case_id: str) -> Exp2OciImageIdentity:
        matches = [
            item for item in self.protocol.oci_images if item.case_id == case_id
        ]
        if len(matches) != 1:
            raise Exp2EvalAuthorityError(
                "Exp2 case has no unique frozen image identity"
            )
        return matches[0]

    @staticmethod
    def _docker_digest(value: object, label: str) -> str:
        text = str(value or "")
        if text.startswith("sha256:"):
            text = text.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", text):
            raise Exp2EvalAuthorityError(f"Docker {label} digest is invalid")
        return text

    def _image_gate(self, intent: Exp2CaseAttemptIntent) -> dict[str, Any]:
        identity = self._case_image(intent.case_id)
        qualification = self.service.exp2_qualification_receipt(
            Path(self.plan.swe_protocol_path),
            intent.case_id,
        )
        try:
            verify_record(qualification)
        except BenchmarkContractError as exc:
            raise Exp2EvalAuthorityError(
                "Exp2 qualification receipt is invalid"
            ) from exc
        if (
            qualification.get("record_digest")
            != identity.qualification_digest
            or self._docker_digest(
                qualification.get("image_id"), "qualified image"
            )
            != identity.local_image_id
            or qualification.get("eligible") is not True
        ):
            raise Exp2EvalAuthorityError(
                "current Exp2 qualification differs from the frozen protocol"
            )
        gate_path = (
            self.coordinator.state_root / "image-gates" / f"{intent.run_id}.yaml"
        )
        if gate_path.exists():
            gate = self._read_yaml_record(gate_path, "Exp2 image gate")
            if (
                gate.get("case_id") != intent.case_id
                or gate.get("image_identity_digest")
                != digest_payload(identity.to_dict())
                or gate.get("qualification_digest")
                != identity.qualification_digest
                or gate.get("manifest_digest") != identity.manifest_digest
                or gate.get("config_digest") != identity.config_digest
                or gate.get("local_image_id") != identity.local_image_id
                or tuple(gate.get("layer_digests") or ())
                != identity.layer_digests
                or tuple(gate.get("rootfs_diff_ids") or ())
                != identity.rootfs_diff_ids
                or gate.get("platform") != identity.platform
            ):
                raise Exp2EvalAuthorityError(
                    "persisted Exp2 image gate differs from the frozen image"
                )
            return gate

        if self.image_gate_resolver is not None:
            supplied = dict(self.image_gate_resolver(intent, identity))
            try:
                verify_record(supplied)
            except BenchmarkContractError as exc:
                raise Exp2EvalAuthorityError(
                    "injected Exp2 image gate is invalid"
                ) from exc
            if (
                supplied.get("schema") != "autobugfix-exp2-image-gate-v2"
                or supplied.get("study_id") != intent.study_id
                or supplied.get("case_id") != intent.case_id
                or supplied.get("run_id") != intent.run_id
                or supplied.get("image_identity_digest")
                != digest_payload(identity.to_dict())
                or supplied.get("manifest_digest") != identity.manifest_digest
                or supplied.get("qualification_digest")
                != identity.qualification_digest
                or supplied.get("config_digest") != identity.config_digest
                or tuple(supplied.get("layer_digests") or ())
                != identity.layer_digests
                or supplied.get("local_image_id") != identity.local_image_id
                or tuple(supplied.get("rootfs_diff_ids") or ())
                != identity.rootfs_diff_ids
                or supplied.get("platform") != identity.platform
            ):
                raise Exp2EvalAuthorityError(
                    "injected Exp2 image gate differs from the frozen image"
                )
            self._write_once(gate_path, supplied)
            return supplied

        docker = shutil.which("docker")
        if docker is None:
            raise Exp2EvalAuthorityError("Docker executable is unavailable")
        command = run_command(
            [docker, "image", "inspect", identity.image, "--format", "{{json .}}"],
            cwd=self.project_root,
            artifact_dir=(
                self.coordinator.state_root
                / "image-gate-commands"
                / intent.run_id
            ),
            name="exp2-image-identity-gate",
            timeout_seconds=60,
        )
        if not command.passed:
            raise Exp2EvalAuthorityError(
                f"frozen Exp2 image is unavailable: {identity.image}"
            )
        try:
            raw = json.loads(Path(command.stdout_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise Exp2EvalAuthorityError(
                "Docker image inspection output is invalid"
            ) from exc
        if not isinstance(raw, Mapping):
            raise Exp2EvalAuthorityError(
                "Docker image inspection output is not a mapping"
            )
        descriptor = raw.get("Descriptor")
        root_fs = raw.get("RootFS")
        if not isinstance(descriptor, Mapping) or not isinstance(root_fs, Mapping):
            raise Exp2EvalAuthorityError(
                "Docker image inspection lacks OCI descriptor or layers"
            )
        manifest_digest = self._docker_digest(
            descriptor.get("digest"), "manifest"
        )
        local_image_id = self._docker_digest(raw.get("Id"), "local image ID")
        raw_diff_ids = root_fs.get("Layers")
        if not isinstance(raw_diff_ids, list):
            raise Exp2EvalAuthorityError("Docker image layers are invalid")
        rootfs_diff_ids = tuple(
            self._docker_digest(item, "rootfs diff ID")
            for item in raw_diff_ids
        )
        platform = f"{raw.get('Os')}/{raw.get('Architecture')}"
        if (
            manifest_digest != identity.manifest_digest
            or local_image_id != identity.local_image_id
            or rootfs_diff_ids != identity.rootfs_diff_ids
            or platform != identity.platform
        ):
            raise Exp2EvalAuthorityError(
                "current Docker image differs from the frozen Exp2 OCI identity"
            )
        gate = record_with_digest(
            {
                "schema": "autobugfix-exp2-image-gate-v2",
                "study_id": intent.study_id,
                "case_id": intent.case_id,
                "run_id": intent.run_id,
                "image": identity.image,
                "image_identity_digest": digest_payload(identity.to_dict()),
                "qualification_digest": identity.qualification_digest,
                "manifest_digest": manifest_digest,
                "config_digest": identity.config_digest,
                "layer_digests": list(identity.layer_digests),
                "local_image_id": local_image_id,
                "rootfs_diff_ids": list(rootfs_diff_ids),
                "platform": platform,
                "command_digest": command.to_dict()["record_digest"],
            }
        )
        self._write_once(gate_path, gate)
        return gate

    def _report_path(self, intent: Exp2CaseAttemptIntent) -> Path:
        root = Path(intent.output_root)
        if intent.attempt_kind == "scorer_only_retry":
            return root / "scorer-retry-report.yaml"
        if intent.stage == "CALIBRATION":
            return root / "exp2-calibration-case-report.yaml"
        return root / "formal-case-report.yaml"

    def _evidence_source_root(
        self,
        intent: Exp2CaseAttemptIntent,
        report: Mapping[str, Any],
    ) -> Path:
        if intent.attempt_kind == "scorer_only_retry":
            source = Path(str(report.get("source_run_root") or "")).resolve()
            if intent.retry_source_output_root is None or source != Path(
                intent.retry_source_output_root
            ).resolve():
                raise Exp2EvalAuthorityError(
                    "scorer retry report differs from its source run root"
                )
            return source
        return Path(intent.output_root).resolve()

    def _validate_execution_evidence(
        self,
        intent: Exp2CaseAttemptIntent,
        report: Mapping[str, Any],
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        source_root = self._evidence_source_root(intent, report)
        evidence_root = (
            source_root / "subject-run" / "frozen-execution-evidence"
        )
        if not evidence_root.is_dir() or evidence_root.is_symlink():
            raise Exp2EvalAuthorityError(
                "Exp2 run has no canonical frozen Execution evidence tree"
            )
        ledger = self._read_json_record(
            evidence_root / "execution-ledger.json",
            "Exp2 execution ledger",
        )
        broker = self._read_yaml_record(
            source_root / "subject-run" / "broker-result.yaml",
            "Exp2 subject broker result",
        )
        execution = report.get("execution_receipt")
        if not isinstance(execution, Mapping):
            raise Exp2EvalAuthorityError(
                "Exp2 report lacks an execution receipt"
            )
        required_authority = {
            self.project_root,
            Path(self.plan.eval_root).resolve(),
            Path(self.plan.operator_root).resolve(),
            Path(self.plan.memory_root).resolve(),
            Path(self.plan.guard_root).resolve(),
            Path.home().resolve(),
        }
        task_worktree = Path(
            str(execution.get("task_worktree_path") or "")
        ).resolve()
        sdk_receipts = execution.get("sdk_call_receipt_digests")
        command = broker.get("command")
        command_argv = command.get("argv") if isinstance(command, Mapping) else None
        if (
            execution.get("execution_mode") != self.protocol.execution_mode
            or broker.get("execution_mode") != self.protocol.execution_mode
            or execution.get("broker_result_digest")
            != broker.get("record_digest")
            or not isinstance(command, Mapping)
            or execution.get("broker_command_digest")
            != command.get("record_digest")
            or not isinstance(command_argv, list)
            or not isinstance(sdk_receipts, list)
            or not sdk_receipts
            or execution.get("execution_ledger_digest")
            != ledger.get("record_digest")
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 report does not bind the frozen execution boundary"
            )
        if self.protocol.execution_mode == "protected":
            masked_roots = {
                Path(str(command_argv[index + 1])).resolve()
                for index, item in enumerate(command_argv[:-1])
                if item == "--tmpfs"
            }
            guard_root = Path(self.plan.guard_root).resolve()
            dedicated_memory_root = Path(self.plan.memory_root).resolve()
            additional_hidden_roots = {
                dedicated_memory_root,
                guard_root,
            }
            if (
                execution.get("direct_sdk_in_process") is not False
                or execution.get("outer_bubblewrap") is not True
                or execution.get("workspace_only_preflight_digest") is not None
                or not any(Path(str(item)).name == "bwrap" for item in command_argv)
                or broker.get("additional_hidden_paths")
                != sorted(str(root) for root in additional_hidden_roots)
                or any(
                    not any(
                        hidden == root or hidden.is_relative_to(root)
                        for root in masked_roots
                    )
                    for hidden in additional_hidden_roots
                )
                or not task_worktree.is_relative_to(
                    source_root / "subject-run" / "target" / "worktrees"
                )
            ):
                raise Exp2EvalAuthorityError(
                    "Exp2 protected execution lacks its outer Bubblewrap proof"
                )
        else:
            preflight = self._read_json_record(
                source_root / "subject-run" / "workspace-only-preflight.json",
                "Exp2 workspace-only preflight",
            )
            authority_roots = preflight.get("authority_roots")
            observed_authority = (
                {Path(str(item)).resolve() for item in authority_roots}
                if isinstance(authority_roots, list)
                else set()
            )
            workspace_root = Path(
                str(preflight.get("workspace_root") or "")
            ).resolve()
            if (
                preflight.get("execution_mode") != "workspace_only"
                or preflight.get("direct_sdk_in_process") is not True
                or preflight.get("sdk_bubblewrap") is not False
                or preflight.get("outer_bubblewrap") is not False
                or Path(str(preflight.get("disposable_root") or "")).resolve()
                != Path(self.plan.disposable_root).resolve()
                or Path(str(preflight.get("artifact_root") or "")).resolve()
                != source_root / "subject-run"
                or not required_authority.issubset(observed_authority)
                or not task_worktree.is_relative_to(workspace_root)
                or execution.get("workspace_only_preflight_digest")
                != preflight.get("record_digest")
            ):
                raise Exp2EvalAuthorityError(
                    "Exp2 workspace-only execution lacks external isolation proof"
                )
        writer_calls = int(ledger.get("writer_calls") or 0)
        if writer_calls < 1 or writer_calls > self.protocol.max_attempts:
            raise Exp2EvalAuthorityError(
                "Exp2 execution ledger Writer count is invalid"
            )
        sdk_records = [
            self._read_json_record(path, "Exp2 SDK call receipt")
            for path in sorted(
                (evidence_root / "codex-broker").glob(
                    "call-*/receipt.json"
                )
            )
        ]
        if [item["record_digest"] for item in sdk_records] != sdk_receipts:
            raise Exp2EvalAuthorityError(
                "Exp2 SDK call receipts differ from the execution receipt"
            )
        for record in sdk_records:
            hidden = record.get("hidden_paths")
            if (
                record.get("execution_mode") != self.protocol.execution_mode
                or record.get("sdk_in_process")
                is not (self.protocol.execution_mode == "workspace_only")
                or record.get("sdk_bubblewrap")
                is not (self.protocol.execution_mode == "protected")
                or Path(str(record.get("cwd") or "")).resolve()
                != task_worktree
                or Path(
                    str(record.get("expected_task_worktree") or "")
                ).resolve()
                != task_worktree
                or not isinstance(hidden, list)
                or record.get("hidden_paths_digest")
                != hashlib.sha256(
                    json.dumps(
                        sorted(str(item) for item in hidden or []),
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                or not required_authority.issubset(
                    {Path(str(item)).resolve() for item in hidden}
                )
            ):
                raise Exp2EvalAuthorityError(
                    "Exp2 SDK receipt does not prove isolated authorities and exact cwd"
                )

        def total_or_none(field_name: str) -> int | float | None:
            values = [item.get(field_name) for item in sdk_records]
            if not values or any(value is None for value in values):
                return None
            return sum(values)

        usage = record_with_digest(
            {
                "schema": "autobugfix-exp2-case-usage-v2",
                "study_id": intent.study_id,
                "run_id": intent.run_id,
                "source_run_root": str(source_root),
                "execution_ledger_digest": ledger["record_digest"],
                "sdk_call_receipt_digests": list(sdk_receipts),
                "model_calls": len(sdk_records),
                "input_tokens": total_or_none("input_tokens"),
                "cached_input_tokens": total_or_none("cached_input_tokens"),
                "output_tokens": total_or_none("output_tokens"),
                "reasoning_tokens": total_or_none("reasoning_tokens"),
                "model_time_seconds": total_or_none("duration_seconds"),
                "pricing_snapshot_digest": None,
                "model_cost_usd": None,
            }
        )
        self._write_once(
            self.coordinator.state_root
            / "usage"
            / f"{usage['record_digest']}.yaml",
            usage,
        )
        return writer_calls, usage, ledger

    def _adopt_report(
        self,
        intent: Exp2CaseAttemptIntent,
        image_gate: Mapping[str, Any],
    ) -> Exp2CaseAttemptReceipt:
        report = self._read_yaml_record(
            self._report_path(intent),
            "Exp2 official case report",
        )
        if report.get("image_digest") != image_gate.get("local_image_id"):
            raise Exp2EvalAuthorityError(
                "Exp2 report image differs from its trusted image gate"
            )
        try:
            live_memory_digest = (
                self.operator_service.validate_exp2_empty_memory_root(
                    Path(self.plan.memory_root)
                )
            )
        except (OperatorGovernanceError, OSError) as exc:
            raise Exp2EvalAuthorityError(
                "Exp2 Memory root no longer matches the frozen empty fixture"
            ) from exc
        if (
            live_memory_digest != self.plan.memory_fixture_digest
            or report.get("memory_digest") != self.plan.memory_fixture_digest
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 report Memory differs from the frozen empty fixture"
            )
        submission_digest = str(report.get("submission_digest") or "")
        frozen_verification = self.service.verify_exp2_frozen_submission(
            submission_digest=submission_digest,
            expected_case_token=self._expected_case_token(intent),
            expected_subject_sha=intent.subject_sha,
            expected_subject_tree=intent.subject_tree,
        )
        try:
            verify_record(frozen_verification)
            frozen_identity = frozen_verification["frozen_identity"]
            if not isinstance(frozen_identity, Mapping):
                raise TypeError("frozen_identity must be a mapping")
            verify_evidence_manifest(
                self._evidence_source_root(intent, report)
                / "subject-run"
                / "frozen-execution-evidence",
                str(frozen_identity["evidence_manifest_digest"]),
            )
        except (BenchmarkContractError, KeyError, TypeError) as exc:
            raise Exp2EvalAuthorityError(
                "Exp2 report frozen submission evidence does not verify"
            ) from exc
        writer_calls, usage, ledger = self._validate_execution_evidence(
            intent,
            report,
        )
        if intent.attempt_kind == "scorer_only_retry":
            source_usage = usage
            usage = record_with_digest(
                {
                    "schema": "autobugfix-exp2-case-usage-v2",
                    "study_id": intent.study_id,
                    "run_id": intent.run_id,
                    "source_run_root": str(Path(intent.output_root).resolve()),
                    "source_execution_usage_digest": source_usage[
                        "record_digest"
                    ],
                    "execution_ledger_digest": source_usage[
                        "execution_ledger_digest"
                    ],
                    "sdk_call_receipt_digests": [],
                    "model_calls": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "model_time_seconds": 0.0,
                    "pricing_snapshot_digest": None,
                    "model_cost_usd": None,
                }
            )
            self._write_once(
                self.coordinator.state_root
                / "usage"
                / f"{usage['record_digest']}.yaml",
                usage,
            )
        verifier_outcomes = [
            str(event.get("outcome") or "")
            for event in ledger.get("events") or []
            if isinstance(event, Mapping)
            and event.get("kind") == "verifier_finished"
        ]
        official = report.get("official_result")
        resolved = (
            official.get("resolved")
            if isinstance(official, Mapping)
            else None
        )
        execution_summary = {
            "first_verifier_outcome": (
                verifier_outcomes[0] if verifier_outcomes else None
            ),
            "loop_rescue": (
                writer_calls >= 2
                and bool(verifier_outcomes)
                and verifier_outcomes[0] == "repair_failure"
                and resolved is True
            ),
        }
        patch_summary = frozen_verification.get("patch_summary")
        if not isinstance(patch_summary, Mapping):
            raise Exp2EvalAuthorityError(
                "Exp2 frozen submission lacks patch-shape evidence"
            )
        return Exp2CaseAttemptReceipt.from_official_report(
            intent,
            report,
            writer_attempts=(
                0 if intent.attempt_kind == "scorer_only_retry" else writer_calls
            ),
            failure_stage=str(report.get("failure_stage") or "unknown"),  # type: ignore[arg-type]
            image_digest=str(image_gate["local_image_id"]),
            runtime_digest=self.protocol.runtime_digest,
            usage_digest=str(usage["record_digest"]),
            usage_summary=usage,
            execution_summary=execution_summary,
            patch_summary=patch_summary,
        )

    def _execute(self, raw_intent: Mapping[str, Any]) -> Exp2CaseAttemptReceipt:
        intent = Exp2CaseAttemptIntent.from_dict(raw_intent)
        image_gate = self._image_gate(intent)
        output_parent = Path(intent.output_root).resolve().parent
        if intent.attempt_kind == "scorer_only_retry":
            if (
                intent.frozen_submission_digest is None
                or intent.retry_source_output_root is None
            ):
                raise Exp2EvalAuthorityError(
                    "scorer retry intent lacks its frozen source"
                )
            self.service.rescore_swe_exp2_submission(
                Path(self.plan.swe_protocol_path),
                instance_id=intent.case_id,
                submission_digest=intent.frozen_submission_digest,
                source_run_root=Path(intent.retry_source_output_root),
                out_root=output_parent,
                run_id=intent.run_id,
            )
        elif intent.stage == "CALIBRATION":
            self.service.run_swe_exp2_calibration_case(
                Path(self.plan.swe_protocol_path),
                adapter="swebench_verified",
                instance_id=intent.case_id,
                run_id=intent.run_id,
                execution_mode=self.protocol.execution_mode,
                disposable_root=Path(self.plan.disposable_root),
                out_root=output_parent,
                additional_hidden_paths=(
                    Path(self.plan.memory_root),
                    Path(self.plan.guard_root),
                ),
                memory_root=Path(self.plan.memory_root),
            )
        else:
            if self.plan.public_manifest_path is None:
                raise Exp2EvalAuthorityError(
                    "formal Exp2 stage has no public Eval manifest"
                )
            if intent.arm == "H0":
                binding_path = self.plan.h0_binding_path
                expected_kinds = {"BASELINE"}
            else:
                transition = self.coordinator.load_candidate_transition()
                binding_path = (
                    transition.eval_study_binding_path
                    if transition is not None
                    else None
                )
                expected_kinds = {"CANDIDATE"}
            if binding_path is None:
                raise Exp2EvalAuthorityError(
                    "formal Exp2 stage has no trusted Study binding"
                )
            self.service.run_swe_exp2_case(
                Path(self.plan.public_manifest_path),
                swe_protocol_path=Path(self.plan.swe_protocol_path),
                case_selector=intent.case_id,
                study_binding_path=Path(binding_path),
                out_root=output_parent,
                run_id=intent.run_id,
                expected_binding_kinds=expected_kinds,
                execution_mode=self.protocol.execution_mode,
                disposable_root=Path(self.plan.disposable_root),
                additional_hidden_paths=(
                    Path(self.plan.memory_root),
                    Path(self.plan.guard_root),
                ),
            )
        return self._adopt_report(intent, image_gate)

    @staticmethod
    def _trusted_evidence_root(run_root: Path) -> Path | None:
        subject_root = run_root / "subject-run"
        for name in (
            "frozen-execution-evidence",
            "failed-execution-evidence",
        ):
            candidate = subject_root / name
            if candidate.is_dir() and not candidate.is_symlink():
                return candidate
        return None

    def _sdk_call_records(self, run_root: Path) -> tuple[dict[str, Any], ...]:
        evidence_root = self._trusted_evidence_root(run_root)
        if evidence_root is None:
            return ()
        values = []
        for path in sorted(
            (evidence_root / "codex-broker").glob(
                "call-*/receipt.json"
            )
        ):
            record = self._read_json_record(path, "Exp2 SDK call receipt")
            values.append(record)
        return tuple(values)

    def _sdk_receipts(self, run_root: Path) -> tuple[str, ...]:
        return tuple(
            str(record["record_digest"])
            for record in self._sdk_call_records(run_root)
        )

    def _expected_case_token(self, intent: Exp2CaseAttemptIntent) -> str:
        if intent.stage == "CALIBRATION":
            return "dev-" + hashlib.sha256(
                f"swebench_verified:{intent.case_id}".encode("utf-8")
            ).hexdigest()[:24]
        if self.plan.public_manifest_path is None:
            raise Exp2EvalAuthorityError(
                "formal Exp2 case has no public manifest"
            )
        public = self._read_yaml_record(
            Path(self.plan.public_manifest_path),
            "Exp2 public manifest",
        )
        raw_cases = public.get("optimization_cases")
        if not isinstance(raw_cases, list):
            raise Exp2EvalAuthorityError(
                "Exp2 public manifest Optimization projection is invalid"
            )
        matches = []
        for item in raw_cases:
            if not isinstance(item, Mapping):
                continue
            visible = item.get("visible_case")
            if (
                item.get("benchmark_instance_id") == intent.case_id
                and isinstance(visible, Mapping)
            ):
                matches.append(str(visible.get("case_token") or ""))
        if len(matches) != 1 or not matches[0]:
            raise Exp2EvalAuthorityError(
                "Exp2 case has no unique public case token"
            )
        return matches[0]

    def _reconciliation_artifact(
        self,
        intent: Exp2CaseAttemptIntent,
        *,
        classification: str,
        observed: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = record_with_digest(
            {
                "schema": "autobugfix-exp2-reconciliation-v2",
                "study_id": intent.study_id,
                "run_id": intent.run_id,
                "case_id": intent.case_id,
                "started_event_digest": intent.record_digest,
                "classification": classification,
                "observed": dict(observed),
            }
        )
        path = (
            self.coordinator.state_root
            / "reconciliation"
            / f"{intent.run_id}.yaml"
        )
        self._write_once(path, record)
        return record

    def _invalid_from_run(
        self,
        intent: Exp2CaseAttemptIntent,
        image_gate: Mapping[str, Any],
    ) -> Exp2CaseAttemptReceipt:
        run_root = Path(intent.output_root).resolve()
        if intent.attempt_kind == "scorer_only_retry":
            if (
                intent.frozen_submission_digest is None
                or intent.retry_source_output_root is None
            ):
                raise Exp2EvalAuthorityError(
                    "failed scorer retry lacks its frozen source"
                )
            recovery = self._reconciliation_artifact(
                intent,
                classification="scorer_infrastructure_invalid",
                observed={
                    "source_run_root": intent.retry_source_output_root,
                    "submission_digest": intent.frozen_submission_digest,
                    "retry_exhausted": True,
                },
            )
            return Exp2CaseAttemptReceipt(
                study_id=intent.study_id,
                issuer=self.authority_id,
                stage=intent.stage,
                arm=intent.arm,
                case_id=intent.case_id,
                slice=intent.slice,
                started_event_digest=intent.record_digest,
                run_id=intent.run_id,
                attempt_kind=intent.attempt_kind,
                terminal_status="scorer_infrastructure_invalid",
                subject_sha=intent.subject_sha,
                subject_tree=intent.subject_tree,
                frozen_input_digest=intent.frozen_input_digest,
                binding_digest=intent.binding_digest,
                execution_mode=self.protocol.execution_mode,
                sdk_call_occurred=False,
                failure_artifact_digest=str(recovery["record_digest"]),
                submission_digest=intent.frozen_submission_digest,
                image_digest=str(image_gate["local_image_id"]),
                runtime_digest=self.protocol.runtime_digest,
                failure_stage="infrastructure",
                writer_attempts=0,
                frozen_submission=True,
                scorer_retry_legal=False,
            )
        subject_root = run_root / "subject-run"
        evidence_root = self._trusted_evidence_root(run_root)
        if evidence_root is not None:
            manifest = self._read_yaml_record(
                evidence_root / "manifest.yaml",
                "Exp2 execution evidence manifest",
            )
            try:
                verify_evidence_manifest(
                    evidence_root,
                    str(manifest["record_digest"]),
                )
            except (BenchmarkContractError, KeyError) as exc:
                raise Exp2EvalAuthorityError(
                    "Exp2 execution evidence tree is invalid"
                ) from exc
        rejection_path = subject_root / "workspace-only-preflight-rejection.yaml"
        failure_path = subject_root / "broker-failure.yaml"
        broker_path = subject_root / "broker-result.yaml"
        preflight_path = subject_root / "workspace-only-preflight.json"
        ledger_path = (
            evidence_root / "execution-ledger.json"
            if evidence_root is not None
            else subject_root / "execution-ledger.json"
        )

        sdk_records = (
            self._sdk_call_records(run_root) if subject_root.is_dir() else ()
        )
        sdk_receipts = tuple(
            str(record["record_digest"]) for record in sdk_records
        )
        preflight = (
            self._read_json_record(preflight_path, "Exp2 preflight")
            if preflight_path.is_file()
            else None
        )
        ledger = (
            self._read_json_record(ledger_path, "Exp2 execution ledger")
            if ledger_path.is_file()
            else None
        )
        writer_calls = int((ledger or {}).get("writer_calls") or 0)
        usage_digest = str(ledger["record_digest"]) if ledger else None
        preflight_digest = (
            str(preflight["record_digest"]) if preflight else None
        )

        if rejection_path.is_file():
            rejection = self._read_yaml_record(
                rejection_path,
                "Exp2 preflight rejection",
            )
            if sdk_receipts or rejection.get("sdk_call_occurred") is not False:
                raise Exp2EvalAuthorityError(
                    "preflight rejection conflicts with observed SDK calls"
                )
            status = "preflight_rejected"
            failure_digest = str(rejection["record_digest"])
            frozen_submission = False
            scorer_retry_legal = False
            submission_digest = None
            sdk_call_occurred = False
        elif broker_path.is_file():
            broker = self._read_yaml_record(
                broker_path,
                "Exp2 broker result",
            )
            submission_digest = str(broker.get("submission_digest") or "")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", submission_digest)
                or not sdk_receipts
                or preflight is None
            ):
                raise Exp2EvalAuthorityError(
                    "incomplete broker result cannot authorize scorer retry"
                )
            frozen_verification = self.service.verify_exp2_frozen_submission(
                submission_digest=submission_digest,
                expected_case_token=self._expected_case_token(intent),
                expected_subject_sha=intent.subject_sha,
                expected_subject_tree=intent.subject_tree,
            )
            try:
                verify_record(frozen_verification)
            except BenchmarkContractError as exc:
                raise Exp2EvalAuthorityError(
                    "frozen submission verification record is invalid"
                ) from exc
            recovery = self._reconciliation_artifact(
                intent,
                classification="scorer_infrastructure_invalid",
                observed={
                    "broker_result_digest": broker["record_digest"],
                    "submission_digest": submission_digest,
                    "frozen_submission_verification_digest": (
                        frozen_verification["record_digest"]
                    ),
                    "sdk_call_receipt_digests": list(sdk_receipts),
                },
            )
            status = "scorer_infrastructure_invalid"
            failure_digest = str(recovery["record_digest"])
            frozen_submission = True
            scorer_retry_legal = intent.attempt_kind == "execution"
            sdk_call_occurred = intent.attempt_kind == "execution"
        else:
            failure = (
                self._read_yaml_record(failure_path, "Exp2 broker failure")
                if failure_path.is_file()
                else None
            )
            recovery = self._reconciliation_artifact(
                intent,
                classification="execution_infrastructure_invalid",
                observed={
                    "run_root_exists": run_root.is_dir(),
                    "broker_failure_digest": (
                        failure.get("record_digest") if failure else None
                    ),
                    "preflight_digest": preflight_digest,
                    "sdk_call_receipt_digests": list(sdk_receipts),
                },
            )
            status = "execution_infrastructure_invalid"
            failure_digest = str(recovery["record_digest"])
            frozen_submission = False
            scorer_retry_legal = False
            submission_digest = None
            sdk_call_occurred = bool(sdk_receipts)

        return Exp2CaseAttemptReceipt(
            study_id=intent.study_id,
            issuer=self.authority_id,
            stage=intent.stage,
            arm=intent.arm,
            case_id=intent.case_id,
            slice=intent.slice,
            started_event_digest=intent.record_digest,
            run_id=intent.run_id,
            attempt_kind=intent.attempt_kind,
            terminal_status=status,  # type: ignore[arg-type]
            subject_sha=intent.subject_sha,
            subject_tree=intent.subject_tree,
            frozen_input_digest=intent.frozen_input_digest,
            binding_digest=intent.binding_digest,
            execution_mode=self.protocol.execution_mode,
            sdk_call_occurred=sdk_call_occurred,
            failure_artifact_digest=failure_digest,
            submission_digest=submission_digest,
            workspace_only_preflight_digest=preflight_digest,
            image_digest=str(image_gate["local_image_id"]),
            runtime_digest=self.protocol.runtime_digest,
            usage_digest=usage_digest,
            model_calls=len(sdk_records),
            input_tokens=(
                sum(int(item["input_tokens"]) for item in sdk_records)
                if sdk_records
                and all(item.get("input_tokens") is not None for item in sdk_records)
                else None
            ),
            cached_input_tokens=(
                sum(int(item["cached_input_tokens"]) for item in sdk_records)
                if sdk_records
                and all(
                    item.get("cached_input_tokens") is not None
                    for item in sdk_records
                )
                else None
            ),
            output_tokens=(
                sum(int(item["output_tokens"]) for item in sdk_records)
                if sdk_records
                and all(item.get("output_tokens") is not None for item in sdk_records)
                else None
            ),
            reasoning_tokens=(
                sum(int(item["reasoning_tokens"]) for item in sdk_records)
                if sdk_records
                and all(
                    item.get("reasoning_tokens") is not None
                    for item in sdk_records
                )
                else None
            ),
            model_time_seconds=(
                sum(float(item["duration_seconds"]) for item in sdk_records)
                if sdk_records
                and all(
                    item.get("duration_seconds") is not None
                    for item in sdk_records
                )
                else None
            ),
            failure_stage="infrastructure",
            writer_attempts=writer_calls,
            frozen_submission=frozen_submission,
            scorer_retry_legal=scorer_retry_legal,
        )

    def _reconcile(
        self,
        raw_intent: Mapping[str, Any],
    ) -> Exp2CaseAttemptReceipt:
        intent = Exp2CaseAttemptIntent.from_dict(raw_intent)
        image_gate = self._image_gate(intent)
        report_path = self._report_path(intent)
        if report_path.is_file():
            return self._adopt_report(intent, image_gate)
        return self._invalid_from_run(intent, image_gate)

    def register_operator_h0(
        self,
        operator_study_id: str,
        *,
        operator_service: OperatorGovernanceService,
    ) -> dict[str, Any]:
        """Issue Eval-owned apparatus/feasibility evidence for Operator H0."""

        state = self.coordinator._replay()
        if (
            state.state != "SOURCE_RELEASED"
            or state.source_bundle is None
            or state.source_bundle.feasibility != "passed"
            or state.plan.h0_binding_path is None
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 H0 is not ready for the Operator handoff"
            )
        binding = self._read_yaml_record(
            Path(state.plan.h0_binding_path),
            "Exp2 H0 Study binding",
        )
        if (
            binding.get("study_id") != operator_study_id
            or binding.get("record_digest") != state.plan.h0_binding_digest
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 H0 binding differs from the Operator Study"
            )
        receipts = [
            self.coordinator._last_receipt(
                state,
                "H0",
                "H0",
                case.case_id,
            )
            for case in state.protocol.h0_cases
        ]
        if any(
            item is None or item.terminal_status != "official_terminal"
            for item in receipts
        ):
            raise Exp2EvalAuthorityError(
                "Exp2 H0 handoff requires ten apparatus-valid receipts"
            )
        payload = {
            "schema": "autobugfix-study-baseline-v1",
            "study_id": binding["study_id"],
            "line_id": binding["line_id"],
            "subject_sha": binding["subject_sha"],
            "manifest_digest": binding["manifest_digest"],
            "success_contract_digest": binding[
                "success_contract_digest"
            ],
            "metrics": {
                "apparatus_valid": True,
                "h0_terminal_coverage": 1.0,
                "adaptation_feasible": True,
            },
            "guard_run_id": self.coordinator.study_id,
            "evidence_digest": state.source_bundle.record_digest,
        }
        metric = {
            **payload,
            "receipt_digest": digest_payload(payload),
        }
        metric_path = self.coordinator.state_root / "eval-h0-metric.yaml"
        serialized = yaml.safe_dump(metric, sort_keys=False)
        if metric_path.exists():
            if metric_path.is_symlink() or metric_path.read_text(
                encoding="utf-8"
            ) != serialized:
                raise Exp2EvalAuthorityError(
                    "immutable Eval H0 metric already differs"
                )
        else:
            descriptor = os.open(
                metric_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
        return operator_service.register_exp2_h0_handoff(
            operator_study_id,
            binding_path=Path(state.plan.h0_binding_path),
            metric_path=metric_path,
            source_projection_path=(
                self.coordinator.state_root / "source-projection-bundle.yaml"
            ),
        )

    def resume(self, *, execute: bool = False) -> dict[str, Any]:
        """Inspect or execute exactly one trusted case transition."""

        if not execute:
            return self.coordinator.resume()
        self._validate_static_authority()
        self._validate_execution_roots()
        try:
            return self.coordinator.resume(
                self._execute,
                reconciler=self._reconcile,
            )
        except (EvalBenchmarkServiceError, Exp2ResumeError) as exc:
            raise Exp2EvalAuthorityError(str(exc)) from exc


def build_exp2_resume_protocol(
    project_root: Path,
    *,
    protocol_id: str,
    swe_protocol_path: Path,
    empty_memory_fixture_path: Path,
    execution_allowlist: tuple[str, ...],
    artifact_root: Path,
    evaluation_mode: str = "legacy_pilot",
) -> Exp2ResumeProtocol:
    """Build a qualified v2 protocol from replayed receipts and local OCI state."""

    root = project_root.resolve()
    service = EvalBenchmarkService(root)
    operator = OperatorGovernanceService(root)
    swe_protocol = SWEExperimentProtocol.from_yaml(swe_protocol_path)
    if swe_protocol.verified_image_mode != "pinned-official-import":
        raise Exp2EvalAuthorityError(
            "Exp2 v2 requires selected pinned official SWE images"
        )
    runtime = service.exp2_runtime_identity(swe_protocol_path)
    try:
        verify_record(runtime)
    except BenchmarkContractError as exc:
        raise Exp2EvalAuthorityError(
            "Exp2 protocol runtime identity is invalid"
        ) from exc
    memory_fixture = Exp2EmptyMemoryFixture.from_yaml(
        empty_memory_fixture_path
    )
    role_digests = operator.exp2_role_skill_digests(
        subject_sha=swe_protocol.h0_subject,
        primary_model=swe_protocol.codex_runtime.model,
    )
    pending = Exp2ResumeProtocol(
        protocol_id=protocol_id,
        schema_version=(3 if evaluation_mode == "iterative_full" else 2),
        evaluation_mode=evaluation_mode,  # type: ignore[arg-type]
        dataset_revision=str(runtime["dataset_revision"]),
        scorer_digest=str(runtime["scorer_digest"]),
        runtime_digest=str(runtime["runtime_digest"]),
        memory_fixture_spec_digest=memory_fixture.fixture_file_digest,
        memory_fixture_digest=operator.exp2_empty_memory_digest(),
        operator_policy_digest=str(operator.governance_context()["digest"]),
        operator_role_skill_digest=role_digests[
            "operator_role_skill_digest"
        ],
        execution_role_skill_digest=role_digests[
            "execution_role_skill_digest"
        ],
        model=swe_protocol.codex_runtime.model,
        reasoning_effort=swe_protocol.codex_runtime.reasoning_effort,
        execution_mode="protected",
        max_attempts=swe_protocol.codex_runtime.max_attempts,
        timeout_seconds=swe_protocol.codex_runtime.timeout_seconds,
        case_concurrency=swe_protocol.case_concurrency,
        execution_allowlist=execution_allowlist,
    )
    selected_h0 = tuple(
        item.instance_id for item in swe_protocol.optimization_cases
    )
    if set(selected_h0) != {item.case_id for item in pending.h0_cases}:
        raise Exp2EvalAuthorityError(
            "SWE protocol cases differ from the frozen Exp2 H0 cohort"
        )
    selected_all = tuple(
        item.case_id for item in (*pending.calibration_cases, *pending.h0_cases)
    )
    if tuple(runtime.get("verified_image_instance_ids") or ()) != selected_all:
        raise Exp2EvalAuthorityError(
            "pinned official image manifest differs from the exact Exp2 cohort/order"
        )
    raw_pins = runtime.get("verified_image_pins")
    if (
        not isinstance(raw_pins, list)
        or not all(isinstance(item, Mapping) for item in raw_pins)
    ):
        raise Exp2EvalAuthorityError(
            "Exp2 runtime lacks selected pinned image authority"
        )
    runtime_pins = {
        str(item.get("instance_id") or ""): {
            "source_ref": str(item.get("source_ref") or ""),
            "manifest_digest": str(item.get("manifest_digest") or ""),
        }
        for item in raw_pins
    }
    if tuple(runtime_pins) != selected_all:
        raise Exp2EvalAuthorityError(
            "Exp2 runtime pinned image mapping differs from cohort/order"
        )
    docker = shutil.which("docker")
    if docker is None:
        raise Exp2EvalAuthorityError("Docker executable is unavailable")
    identities = []
    artifact = artifact_root.resolve()
    artifact.mkdir(parents=True, mode=0o700, exist_ok=True)
    for case in (*pending.calibration_cases, *pending.h0_cases):
        expected_pin = runtime_pins[case.case_id]
        qualification = service.exp2_qualification_receipt(
            swe_protocol_path,
            case.case_id,
        )
        try:
            verify_record(qualification)
        except BenchmarkContractError as exc:
            raise Exp2EvalAuthorityError(
                f"Exp2 qualification receipt is invalid for {case.case_id}"
            ) from exc
        base_commit = str(qualification.get("base_commit") or "")
        source_tree = str(qualification.get("source_tree") or "")
        source_digest = str(qualification.get("source_digest") or "")
        image = str(qualification.get("image") or "")
        if (
            qualification.get("schema") != "autobugfix-swe-qualification-v5"
            or qualification.get("instance_id") != case.case_id
            or qualification.get("dataset_revision") != pending.dataset_revision
            or qualification.get("repository") != case.repository
            or qualification.get("eligible") is not True
            or not re.fullmatch(r"[0-9a-f]{40}", base_commit)
            or not re.fullmatch(r"[0-9a-f]{40}", source_tree)
            or not re.fullmatch(r"[0-9a-f]{64}", source_digest)
            or not image
        ):
            raise Exp2EvalAuthorityError(
                f"Exp2 replay-qualified case metadata drift for {case.case_id}"
            )
        command = run_command(
            [docker, "image", "inspect", image, "--format", "{{json .}}"],
            cwd=root,
            artifact_dir=artifact / case.case_id,
            name="exp2-protocol-image-inspect",
            timeout_seconds=60,
        )
        if not command.passed:
            raise Exp2EvalAuthorityError(
                f"selected Exp2 image is not locally qualified: {case.case_id}"
            )
        try:
            raw = json.loads(
                Path(command.stdout_path).read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise Exp2EvalAuthorityError(
                f"Docker inspection is invalid for {case.case_id}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise Exp2EvalAuthorityError(
                f"Docker inspection is not a mapping for {case.case_id}"
            )
        descriptor = raw.get("Descriptor")
        root_fs = raw.get("RootFS")
        if not isinstance(descriptor, Mapping) or not isinstance(root_fs, Mapping):
            raise Exp2EvalAuthorityError(
                f"Docker inspection lacks OCI identity for {case.case_id}"
            )
        raw_diff_ids = root_fs.get("Layers")
        if not isinstance(raw_diff_ids, list):
            raise Exp2EvalAuthorityError(
                f"Docker inspection layers are invalid for {case.case_id}"
            )
        import_path = Path(
            str(qualification.get("image_source_receipt_path") or "")
        )
        trusted_root = service.config.eval.benchmarks.trusted_case_root.resolve()
        if (
            not import_path.is_absolute()
            or not import_path.resolve().is_relative_to(trusted_root)
        ):
            raise Exp2EvalAuthorityError(
                f"pinned image import receipt escapes Eval state: {case.case_id}"
            )
        import_receipt = Exp2EvalAuthority._read_yaml_record(
            import_path,
            "Exp2 pinned image import receipt",
        )
        imported_layers = import_receipt.get("layer_digests")
        imported_diff_ids = import_receipt.get("rootfs_diff_ids")
        if not isinstance(imported_layers, list) or not isinstance(
            imported_diff_ids, list
        ):
            raise Exp2EvalAuthorityError(
                f"pinned image import receipt lacks OCI layers: {case.case_id}"
            )
        identities.append(
            Exp2OciImageIdentity(
                case_id=case.case_id,
                image=image,
                qualification_digest=str(qualification["record_digest"]),
                manifest_digest=Exp2EvalAuthority._docker_digest(
                    descriptor.get("digest"), "manifest"
                ),
                config_digest=Exp2EvalAuthority._docker_digest(
                    import_receipt.get("config_digest"), "config"
                ),
                layer_digests=tuple(
                    Exp2EvalAuthority._docker_digest(item, "layer")
                    for item in imported_layers
                ),
                local_image_id=Exp2EvalAuthority._docker_digest(
                    raw.get("Id"), "local image ID"
                ),
                rootfs_diff_ids=tuple(
                    Exp2EvalAuthority._docker_digest(item, "rootfs diff ID")
                    for item in raw_diff_ids
                ),
                platform=f"{raw.get('Os')}/{raw.get('Architecture')}",
            )
        )
        if Exp2EvalAuthority._docker_digest(
            qualification.get("image_id"),
            "qualified image",
        ) != identities[-1].local_image_id:
            raise Exp2EvalAuthorityError(
                f"qualified image differs from Docker state for {case.case_id}"
            )
        if (
            qualification.get("image_source_mode")
            != "pinned-official-import"
            or qualification.get("image_source_manifest_digest")
            != expected_pin["manifest_digest"]
            or qualification.get("image_source_ref")
            != expected_pin["source_ref"]
            or identities[-1].manifest_digest
            != expected_pin["manifest_digest"]
        ):
            raise Exp2EvalAuthorityError(
                f"qualified image lacks pinned official provenance: {case.case_id}"
            )
        if (
            import_receipt.get("record_digest")
            != qualification.get("image_source_receipt_digest")
            or import_receipt.get("instance_id") != case.case_id
            or import_receipt.get("source_ref")
            != qualification.get("image_source_ref")
            or import_receipt.get("manifest_digest")
            != identities[-1].manifest_digest
            or import_receipt.get("manifest_record_digest")
            != runtime.get("verified_image_manifest_digest")
            or import_receipt.get("local_image") != identities[-1].image
            or Exp2EvalAuthority._docker_digest(
                import_receipt.get("local_image_id"), "imported image"
            )
            != identities[-1].local_image_id
            or Exp2EvalAuthority._docker_digest(
                import_receipt.get("config_digest"), "imported config"
            )
            != identities[-1].config_digest
            or import_receipt.get("platform") != identities[-1].platform
            or tuple(
                Exp2EvalAuthority._docker_digest(layer, "imported layer")
                for layer in imported_layers
            )
            != identities[-1].layer_digests
            or tuple(
                Exp2EvalAuthority._docker_digest(diff_id, "imported diff ID")
                for diff_id in imported_diff_ids
            )
            != identities[-1].rootfs_diff_ids
            or identities[-1].rootfs_diff_ids
            != tuple(
                Exp2EvalAuthority._docker_digest(diff_id, "local diff ID")
                for diff_id in raw_diff_ids
            )
        ):
            raise Exp2EvalAuthorityError(
                f"pinned image import receipt drift: {case.case_id}"
            )
    return replace(
        pending,
        oci_images=tuple(identities),
        qualification_status="qualified",
    )


def build_exp2_study_plan(
    project_root: Path,
    *,
    study_id: str,
    study_kind: str,
    protocol_path: Path,
    swe_protocol_path: Path,
    apparatus_receipt_path: Path,
    memory_fixture_spec_path: Path,
    memory_root: Path,
    disposable_root: Path,
    guard_root: Path,
    public_manifest_path: Path | None = None,
    h0_binding_path: Path | None = None,
    calibration_terminal_receipt_path: Path | None = None,
) -> Exp2ResumeStudyPlan:
    """Construct a v2 study plan from content-addressed authority inputs."""

    from autobugfix.eval.benchmarks.exp2_resume import (
        Exp2CalibrationTerminalReceipt,
    )

    root = project_root.resolve()
    config = load_config(root)
    protocol = Exp2ResumeProtocol.from_yaml(protocol_path)
    memory_fixture = Exp2EmptyMemoryFixture.from_yaml(
        memory_fixture_spec_path
    )
    if memory_fixture.fixture_file_digest != protocol.memory_fixture_spec_digest:
        raise Exp2EvalAuthorityError(
            "Exp2 Memory fixture spec differs from protocol"
        )
    operator = OperatorGovernanceService(root)
    memory_source = memory_root.expanduser()
    resolved_memory_root = memory_source.resolve()
    guard_source = guard_root.expanduser()
    try:
        resolved_guard_root = guard_source.resolve(strict=True)
    except OSError as exc:
        raise Exp2EvalAuthorityError(
            "Exp2 Guard root must be an absolute real protected directory"
        ) from exc
    if (
        not guard_source.is_absolute()
        or guard_source != resolved_guard_root
        or not resolved_guard_root.is_dir()
    ):
        raise Exp2EvalAuthorityError(
            "Exp2 Guard root must be an absolute real protected directory"
        )
    protected_memory_roots = exp2_protected_memory_roots(
        root,
        config,
        guard_root=resolved_guard_root,
    )
    if any(
        resolved_memory_root == protected
        or resolved_memory_root.is_relative_to(protected)
        or protected.is_relative_to(resolved_memory_root)
        for protected in protected_memory_roots
    ):
        raise Exp2EvalAuthorityError(
            "Exp2 empty Memory root overlaps protected or canonical state"
        )
    try:
        memory_digest = operator.validate_exp2_empty_memory_root(memory_source)
    except OperatorGovernanceError as exc:
        raise Exp2EvalAuthorityError(str(exc)) from exc
    if memory_digest != protocol.memory_fixture_digest:
        raise Exp2EvalAuthorityError(
            "Exp2 empty Memory root differs from protocol"
        )
    swe_protocol = SWEExperimentProtocol.from_yaml(swe_protocol_path)
    apparatus = Exp2EvalAuthority._read_yaml_record(
        apparatus_receipt_path,
        "Exp2 apparatus receipt",
    )
    apparatus_sha = str(apparatus.get("apparatus_sha") or "")
    apparatus_tree = str(apparatus.get("apparatus_tree") or "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", apparatus_sha)
        or not re.fullmatch(r"[0-9a-f]{40}", apparatus_tree)
        or rev_parse(root, apparatus_sha) != apparatus_sha
        or rev_parse(root, f"{apparatus_sha}^{{tree}}") != apparatus_tree
    ):
        raise Exp2EvalAuthorityError(
            "Exp2 apparatus receipt does not bind a current Git object"
        )
    if swe_protocol.h0_subject != rev_parse(root, swe_protocol.h0_subject):
        raise Exp2EvalAuthorityError("Exp2 H0 subject is unavailable in Git")
    h0_tree = rev_parse(root, f"{swe_protocol.h0_subject}^{{tree}}")
    selected_images_digest = digest_payload(
        {"oci_images": [item.to_dict() for item in protocol.oci_images]}
    )
    public_path = None
    public_digest = None
    h0_path = None
    if study_kind == "resume_pilot":
        if (
            public_manifest_path is None
            or h0_binding_path is None
            or calibration_terminal_receipt_path is None
        ):
            raise Exp2EvalAuthorityError(
                "resume-pilot plan requires public manifest, H0 binding, and calibration receipt"
            )
        public = Exp2EvalAuthority._read_yaml_record(
            public_manifest_path,
            "Exp2 public manifest",
        )
        h0_binding = Exp2EvalAuthority._read_yaml_record(
            h0_binding_path,
            "Exp2 H0 binding",
        )
        terminal = Exp2CalibrationTerminalReceipt.from_dict(
            Exp2EvalAuthority._read_yaml_record(
                calibration_terminal_receipt_path,
                "Exp2 calibration terminal receipt",
            )
        )
        public_path = str(public_manifest_path.resolve())
        public_digest = str(public["record_digest"])
        h0_path = str(h0_binding_path.resolve())
        h0_binding_digest = str(h0_binding["record_digest"])
        terminal_path = str(calibration_terminal_receipt_path.resolve())
        terminal_digest = terminal.record_digest
    elif study_kind == "calibration":
        h0_binding_digest = digest_payload(
            {
                "schema": "autobugfix-exp2-calibration-binding-v2",
                "protocol_digest": protocol.record_digest,
                "subject_sha": swe_protocol.h0_subject,
                "subject_tree": h0_tree,
            }
        )
        terminal_path = None
        terminal_digest = None
    else:
        raise Exp2EvalAuthorityError("unsupported Exp2 study kind")
    return Exp2ResumeStudyPlan(
        study_id=study_id,
        study_kind=study_kind,  # type: ignore[arg-type]
        protocol_path=str(protocol_path.resolve()),
        protocol_digest=protocol.record_digest,
        swe_protocol_path=str(swe_protocol_path.resolve()),
        swe_protocol_sha256=digest_file(swe_protocol_path),
        apparatus_sha=apparatus_sha,
        apparatus_tree=apparatus_tree,
        apparatus_receipt_path=str(apparatus_receipt_path.resolve()),
        apparatus_receipt_digest=str(apparatus["record_digest"]),
        h0_subject_sha=swe_protocol.h0_subject,
        h0_subject_tree=h0_tree,
        h0_binding_digest=h0_binding_digest,
        scorer_digest=protocol.scorer_digest,
        runtime_digest=protocol.runtime_digest,
        memory_fixture_spec_path=str(memory_fixture_spec_path.resolve()),
        memory_fixture_digest=protocol.memory_fixture_digest,
        operator_policy_digest=protocol.operator_policy_digest,
        operator_role_skill_digest=protocol.operator_role_skill_digest,
        execution_role_skill_digest=protocol.execution_role_skill_digest,
        selected_images_digest=selected_images_digest,
        disposable_root=str(disposable_root.resolve()),
        artifact_root=str(
            (
                config.eval.benchmarks.trusted_case_root
                / "exp2"
                / study_id
                / "runs"
            ).resolve()
        ),
        eval_root=str(config.eval.benchmarks.trusted_case_root.resolve()),
        operator_root=str(config.operator.state.root.resolve()),
        memory_root=str(resolved_memory_root),
        guard_root=str(guard_source),
        public_manifest_path=public_path,
        public_manifest_digest=public_digest,
        h0_binding_path=h0_path,
        calibration_terminal_receipt_path=terminal_path,
        calibration_terminal_receipt_digest=terminal_digest,
    )


def build_exp2_apparatus_receipt(
    project_root: Path,
    *,
    protocol_path: Path,
    swe_protocol_path: Path,
    check_artifacts: tuple[Path, ...],
) -> dict[str, Any]:
    """Freeze one clean source apparatus and its completed check artifacts."""

    root = project_root.resolve()
    status = run_git(
        root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    ).stdout
    if status.strip():
        raise Exp2EvalAuthorityError(
            "Exp2 apparatus receipt requires a clean committed worktree"
        )
    protocol = Exp2ResumeProtocol.from_yaml(protocol_path)
    runtime = EvalBenchmarkService(root).exp2_runtime_identity(swe_protocol_path)
    if (
        runtime.get("scorer_digest") != protocol.scorer_digest
        or runtime.get("runtime_digest") != protocol.runtime_digest
        or runtime.get("dataset_revision") != protocol.dataset_revision
    ):
        raise Exp2EvalAuthorityError(
            "Exp2 apparatus runtime differs from the frozen protocol"
        )
    checks = []
    for source in check_artifacts:
        path = source.resolve()
        if source.is_symlink() or not path.is_file():
            raise Exp2EvalAuthorityError(
                f"Exp2 source-check artifact is missing: {source}"
            )
        check = Exp2EvalAuthority._read_yaml_record(
            path,
            "Exp2 source-check receipt",
        )
        if (
            check.get("schema") != "autobugfix-exp2-source-check-v2"
            or check.get("passed") is not True
            or check.get("exit_code") != 0
            or not isinstance(check.get("argv"), list)
            or not check.get("argv")
        ):
            raise Exp2EvalAuthorityError(
                f"Exp2 source check did not pass: {source}"
            )
        checks.append(
            {
                "path": str(path),
                "sha256": digest_file(path),
                "size": path.stat().st_size,
                "receipt_digest": check["record_digest"],
            }
        )
    if not checks:
        raise Exp2EvalAuthorityError(
            "Exp2 apparatus receipt requires source-check artifacts"
        )
    head = rev_parse(root, "HEAD")
    tree = rev_parse(root, "HEAD^{tree}")
    return record_with_digest(
        {
            "schema": "autobugfix-exp2-apparatus-receipt-v2",
            "apparatus_sha": head,
            "apparatus_tree": tree,
            "protocol_digest": protocol.record_digest,
            "swe_protocol_sha256": digest_file(swe_protocol_path),
            "scorer_digest": protocol.scorer_digest,
            "runtime_digest": protocol.runtime_digest,
            "memory_fixture_spec_digest": protocol.memory_fixture_spec_digest,
            "memory_fixture_digest": protocol.memory_fixture_digest,
            "operator_policy_digest": protocol.operator_policy_digest,
            "operator_role_skill_digest": protocol.operator_role_skill_digest,
            "execution_role_skill_digest": protocol.execution_role_skill_digest,
            "source_checks": checks,
            "git_status": "",
        }
    )


def run_exp2_source_check(
    project_root: Path,
    *,
    name: str,
    argv: tuple[str, ...],
    artifact_root: Path,
    timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Run one source check and return a content-addressed pass/fail receipt."""

    if not name.strip() or not argv:
        raise Exp2EvalAuthorityError(
            "Exp2 source check requires a name and argv"
        )
    command = run_command(
        argv,
        cwd=project_root.resolve(),
        artifact_dir=artifact_root.resolve() / name,
        name=f"exp2-source-check-{name}",
        timeout_seconds=timeout_seconds,
    )
    evidence = command.to_dict()
    return record_with_digest(
        {
            "schema": "autobugfix-exp2-source-check-v2",
            "name": name,
            "argv": list(argv),
            "cwd": str(project_root.resolve()),
            "passed": command.passed,
            "exit_code": command.exit_code,
            "timed_out": command.timed_out,
            "duration_seconds": command.duration_seconds,
            "stdout_sha256": command.stdout_sha256,
            "stderr_sha256": command.stderr_sha256,
            "command_digest": evidence["record_digest"],
        }
    )
