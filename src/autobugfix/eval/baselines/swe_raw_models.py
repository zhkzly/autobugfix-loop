from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.eval.baselines.models import required, required_bool, sha256_value
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.swe_models import SWEVisibleCase


def _sha1(value: object, name: str) -> str:
    text = required(value, name)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        raise BenchmarkContractError(f"{name} must be a full lowercase Git SHA")
    return text


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkContractError(f"{name} must be a mapping")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BenchmarkContractError(f"{name} must be a list")
    return value


@dataclass(slots=True, frozen=True)
class SWERawTreatmentProtocol:
    treatment_id: str
    source_protocol_digest: str
    benchmark: str
    experiment_role: str
    expected_case_count: int
    runner_project: str
    model: str
    sdk_version: str
    reasoning_effort: str
    service_tier: str | None
    approval_mode: str
    sandbox: str
    network_access: bool
    timeout_seconds: int
    turns_per_case: int
    case_concurrency: int
    prompt_template_digest: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported SWE Raw treatment schema")
        safe_component(self.treatment_id, "treatment_id")
        sha256_value(self.source_protocol_digest, "source_protocol_digest")
        sha256_value(self.prompt_template_digest, "prompt_template_digest")
        if self.benchmark != "swebench_verified":
            raise BenchmarkContractError("SWE Raw treatment must use SWE-bench Verified")
        if self.experiment_role != "optimization":
            raise BenchmarkContractError("SWE Raw public treatment must be Optimization")
        if self.expected_case_count != 10:
            raise BenchmarkContractError("SWE Raw treatment requires ten public cases")
        if self.runner_project != "baselines/raw_codex_sdk":
            raise BenchmarkContractError("SWE Raw treatment runner project is not pinned")
        if self.model != "gpt-5.4-mini" or self.sdk_version != "0.1.0b3":
            raise BenchmarkContractError("SWE Raw treatment model or SDK is not pinned")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise BenchmarkContractError("unsupported SWE Raw reasoning effort")
        if (
            self.approval_mode != "deny_all"
            or self.sandbox != "workspace-write"
            or self.network_access
        ):
            raise BenchmarkContractError(
                "SWE Raw treatment must deny approvals and tool network access"
            )
        if self.timeout_seconds != 900:
            raise BenchmarkContractError("SWE Raw treatment timeout must be 900 seconds")
        if self.turns_per_case != 1 or self.case_concurrency != 1:
            raise BenchmarkContractError(
                "SWE Raw treatment requires one turn and serial case execution"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWERawTreatmentProtocol":
        allowed = {
            "schema_version",
            "treatment_id",
            "source_protocol_digest",
            "benchmark",
            "experiment_role",
            "expected_case_count",
            "runner_project",
            "model",
            "sdk_version",
            "reasoning_effort",
            "service_tier",
            "approval_mode",
            "sandbox",
            "network_access",
            "timeout_seconds",
            "turns_per_case",
            "case_concurrency",
            "prompt_template_digest",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise BenchmarkContractError(
                "SWE Raw treatment contains unsupported fields: " + ", ".join(unknown)
            )
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            treatment_id=safe_component(data.get("treatment_id"), "treatment_id"),
            source_protocol_digest=sha256_value(
                data.get("source_protocol_digest"), "source_protocol_digest"
            ),
            benchmark=required(data.get("benchmark"), "benchmark"),
            experiment_role=required(data.get("experiment_role"), "experiment_role"),
            expected_case_count=int(data.get("expected_case_count") or 0),
            runner_project=required(data.get("runner_project"), "runner_project"),
            model=required(data.get("model"), "model"),
            sdk_version=required(data.get("sdk_version"), "sdk_version"),
            reasoning_effort=required(data.get("reasoning_effort"), "reasoning_effort"),
            service_tier=(
                str(data["service_tier"])
                if data.get("service_tier") is not None
                else None
            ),
            approval_mode=required(data.get("approval_mode"), "approval_mode"),
            sandbox=required(data.get("sandbox"), "sandbox"),
            network_access=required_bool(
                data.get("network_access"), "network_access"
            ),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
            turns_per_case=int(data.get("turns_per_case") or 0),
            case_concurrency=int(data.get("case_concurrency") or 0),
            prompt_template_digest=sha256_value(
                data.get("prompt_template_digest"), "prompt_template_digest"
            ),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "SWERawTreatmentProtocol":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("SWE Raw treatment protocol must be a mapping")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "treatment_id": self.treatment_id,
            "source_protocol_digest": self.source_protocol_digest,
            "benchmark": self.benchmark,
            "experiment_role": self.experiment_role,
            "expected_case_count": self.expected_case_count,
            "runner_project": self.runner_project,
            "model": self.model,
            "sdk_version": self.sdk_version,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "approval_mode": self.approval_mode,
            "sandbox": self.sandbox,
            "network_access": self.network_access,
            "timeout_seconds": self.timeout_seconds,
            "turns_per_case": self.turns_per_case,
            "case_concurrency": self.case_concurrency,
            "prompt_template_digest": self.prompt_template_digest,
        }

    @property
    def treatment_digest(self) -> str:
        return digest_payload(self.to_dict())


@dataclass(slots=True, frozen=True)
class PreparedSWERawCase:
    instance_id: str
    qualification_digest: str
    image_id: str
    source_tree: str
    source_digest: str
    visible_case: SWEVisibleCase

    def __post_init__(self) -> None:
        safe_component(self.instance_id, "instance_id")
        sha256_value(self.qualification_digest, "qualification_digest")
        if not self.image_id.startswith("sha256:") or len(self.image_id) != 71:
            raise BenchmarkContractError("image_id must be an immutable Docker image ID")
        _sha1(self.source_tree, "source_tree")
        sha256_value(self.source_digest, "source_digest")
        if self.visible_case.benchmark != "swebench_verified":
            raise BenchmarkContractError("SWE Raw case must be a Verified visible case")
        _sha1(self.visible_case.base_commit, "visible_case.base_commit")
        if self.source_digest != self.visible_case.source_snapshot_digest:
            raise BenchmarkContractError(
                "SWE Raw source digest differs from visible case"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedSWERawCase":
        allowed = {
            "instance_id",
            "qualification_digest",
            "image_id",
            "source_tree",
            "source_digest",
            "visible_case",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise BenchmarkContractError(
                "prepared SWE Raw case contains unsupported fields: "
                + ", ".join(unknown)
            )
        visible = _mapping(data.get("visible_case"), "visible_case")
        return cls(
            instance_id=safe_component(data.get("instance_id"), "instance_id"),
            qualification_digest=sha256_value(
                data.get("qualification_digest"), "qualification_digest"
            ),
            image_id=required(data.get("image_id"), "image_id"),
            source_tree=_sha1(data.get("source_tree"), "source_tree"),
            source_digest=sha256_value(data.get("source_digest"), "source_digest"),
            visible_case=SWEVisibleCase.from_dict(visible),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "qualification_digest": self.qualification_digest,
            "image_id": self.image_id,
            "source_tree": self.source_tree,
            "source_digest": self.source_digest,
            "visible_case": self.visible_case.to_dict(),
        }


@dataclass(slots=True, frozen=True)
class PreparedSWERawManifest:
    manifest_id: str
    source_protocol_digest: str
    treatment: SWERawTreatmentProtocol
    runtime_id: str
    control_sha: str
    control_tree: str
    runner_source_digest: str
    runner_install_digest: str
    runner_lock_digest: str
    config_digest: str
    cases: tuple[PreparedSWERawCase, ...]
    prepared_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported prepared SWE Raw manifest schema")
        safe_component(self.manifest_id, "manifest_id")
        sha256_value(self.source_protocol_digest, "source_protocol_digest")
        if self.source_protocol_digest != self.treatment.source_protocol_digest:
            raise BenchmarkContractError("prepared SWE Raw source protocol drift")
        if not self.runtime_id.startswith("sha256:") or len(self.runtime_id) != 71:
            raise BenchmarkContractError("runtime_id must be immutable")
        _sha1(self.control_sha, "control_sha")
        _sha1(self.control_tree, "control_tree")
        for value, name in (
            (self.runner_source_digest, "runner_source_digest"),
            (self.runner_install_digest, "runner_install_digest"),
            (self.runner_lock_digest, "runner_lock_digest"),
            (self.config_digest, "config_digest"),
        ):
            sha256_value(value, name)
        if len(self.cases) != self.treatment.expected_case_count:
            raise BenchmarkContractError("prepared SWE Raw case count is invalid")
        if len({case.instance_id for case in self.cases}) != len(self.cases):
            raise BenchmarkContractError("prepared SWE Raw case IDs must be unique")
        required(self.prepared_at, "prepared_at")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedSWERawManifest":
        verify_record(data)
        if data.get("schema") != "autobugfix-swe-raw-prepared-v1":
            raise BenchmarkContractError("unsupported prepared SWE Raw manifest")
        allowed = {
            "schema",
            "schema_version",
            "manifest_id",
            "source_protocol_digest",
            "treatment",
            "runtime_id",
            "control_sha",
            "control_tree",
            "runner_source_digest",
            "runner_install_digest",
            "runner_lock_digest",
            "config_digest",
            "cases",
            "prepared_at",
            "record_digest",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise BenchmarkContractError(
                "prepared SWE Raw manifest contains unsupported fields: "
                + ", ".join(unknown)
            )
        treatment = SWERawTreatmentProtocol.from_dict(
            _mapping(data.get("treatment"), "treatment")
        )
        raw_cases = _sequence(data.get("cases"), "cases")
        if not all(isinstance(item, Mapping) for item in raw_cases):
            raise BenchmarkContractError("prepared SWE Raw cases must be mappings")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            source_protocol_digest=sha256_value(
                data.get("source_protocol_digest"), "source_protocol_digest"
            ),
            treatment=treatment,
            runtime_id=required(data.get("runtime_id"), "runtime_id"),
            control_sha=_sha1(data.get("control_sha"), "control_sha"),
            control_tree=_sha1(data.get("control_tree"), "control_tree"),
            runner_source_digest=sha256_value(
                data.get("runner_source_digest"), "runner_source_digest"
            ),
            runner_install_digest=sha256_value(
                data.get("runner_install_digest"), "runner_install_digest"
            ),
            runner_lock_digest=sha256_value(
                data.get("runner_lock_digest"), "runner_lock_digest"
            ),
            config_digest=sha256_value(data.get("config_digest"), "config_digest"),
            cases=tuple(PreparedSWERawCase.from_dict(item) for item in raw_cases),
            prepared_at=required(data.get("prepared_at"), "prepared_at"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "PreparedSWERawManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("prepared SWE Raw manifest must be a mapping")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-raw-prepared-v1",
                "schema_version": self.schema_version,
                "manifest_id": self.manifest_id,
                "source_protocol_digest": self.source_protocol_digest,
                "treatment": self.treatment.to_dict(),
                "runtime_id": self.runtime_id,
                "control_sha": self.control_sha,
                "control_tree": self.control_tree,
                "runner_source_digest": self.runner_source_digest,
                "runner_install_digest": self.runner_install_digest,
                "runner_lock_digest": self.runner_lock_digest,
                "config_digest": self.config_digest,
                "cases": [case.to_dict() for case in self.cases],
                "prepared_at": self.prepared_at,
            }
        )


@dataclass(slots=True, frozen=True)
class SWERawSubmission:
    case_token: str
    instance_id: str
    manifest_digest: str
    base_commit: str
    patch: str
    patch_sha256: str
    case_bundle_digest: str
    process_status: str
    timed_out: bool
    process_result_digest: str | None
    process_artifact_digests: Mapping[str, str]
    evidence_manifest_digest: str
    changed_paths: tuple[str, ...]
    source_digest: str
    frozen_at: str

    def __post_init__(self) -> None:
        safe_component(self.case_token, "case_token")
        safe_component(self.instance_id, "instance_id")
        sha256_value(self.manifest_digest, "manifest_digest")
        sha256_value(self.patch_sha256, "patch_sha256")
        sha256_value(self.case_bundle_digest, "case_bundle_digest")
        sha256_value(self.source_digest, "source_digest")
        sha256_value(self.evidence_manifest_digest, "evidence_manifest_digest")
        _sha1(self.base_commit, "base_commit")
        if self.process_status not in {"completed", "timed_out"}:
            raise BenchmarkContractError("unsupported frozen Raw process status")
        if self.timed_out != (self.process_status == "timed_out"):
            raise BenchmarkContractError(
                "frozen Raw timeout flag differs from process status"
            )
        if self.process_result_digest is not None:
            sha256_value(self.process_result_digest, "process_result_digest")
        expected_artifacts = {
            "worker_stdout",
            "worker_stderr",
            "codex_config",
            "sdk_request",
            "sdk_events",
            "sdk_stderr",
            "sdk_result",
        }
        if set(self.process_artifact_digests) != expected_artifacts:
            raise BenchmarkContractError(
                "frozen Raw process artifact keys differ from the evidence contract"
            )
        for name, value in self.process_artifact_digests.items():
            if value != "missing":
                sha256_value(value, f"process_artifact_digests.{name}")
        if not self.timed_out and (
            self.process_result_digest is None
            or "missing" in self.process_artifact_digests.values()
        ):
            raise BenchmarkContractError(
                "completed Raw process has incomplete evidence"
            )
        if hashlib.sha256(self.patch.encode("utf-8")).hexdigest() != self.patch_sha256:
            raise BenchmarkContractError("SWE Raw patch digest mismatch")
        if len(set(self.changed_paths)) != len(self.changed_paths):
            raise BenchmarkContractError("SWE Raw changed paths must be unique")
        for changed_path in self.changed_paths:
            relative = PurePosixPath(changed_path)
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or changed_path != relative.as_posix()
            ):
                raise BenchmarkContractError("SWE Raw changed path is unsafe")
        required(self.frozen_at, "frozen_at")

    @property
    def record(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-raw-submission-v1",
                "case_token": self.case_token,
                "instance_id": self.instance_id,
                "manifest_digest": self.manifest_digest,
                "base_commit": self.base_commit,
                "patch_sha256": self.patch_sha256,
                "case_bundle_digest": self.case_bundle_digest,
                "process_status": self.process_status,
                "timed_out": self.timed_out,
                "process_result_digest": self.process_result_digest,
                "process_artifact_digests": dict(self.process_artifact_digests),
                "evidence_manifest_digest": self.evidence_manifest_digest,
                "changed_paths": list(self.changed_paths),
                "source_digest": self.source_digest,
                "frozen_at": self.frozen_at,
            }
        )

    @classmethod
    def from_record(cls, data: Mapping[str, Any], *, patch: str) -> "SWERawSubmission":
        verify_record(data)
        if data.get("schema") != "autobugfix-swe-raw-submission-v1":
            raise BenchmarkContractError("unsupported SWE Raw submission")
        allowed = {
            "schema",
            "case_token",
            "instance_id",
            "manifest_digest",
            "base_commit",
            "patch_sha256",
            "case_bundle_digest",
            "process_status",
            "timed_out",
            "process_result_digest",
            "process_artifact_digests",
            "evidence_manifest_digest",
            "changed_paths",
            "source_digest",
            "frozen_at",
            "record_digest",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise BenchmarkContractError(
                "frozen SWE Raw submission contains unsupported fields: "
                + ", ".join(unknown)
            )
        artifacts = _mapping(data.get("process_artifact_digests"), "process artifacts")
        paths = _sequence(data.get("changed_paths"), "changed_paths")
        return cls(
            case_token=safe_component(data.get("case_token"), "case_token"),
            instance_id=safe_component(data.get("instance_id"), "instance_id"),
            manifest_digest=sha256_value(data.get("manifest_digest"), "manifest_digest"),
            base_commit=required(data.get("base_commit"), "base_commit"),
            patch=patch,
            patch_sha256=sha256_value(data.get("patch_sha256"), "patch_sha256"),
            case_bundle_digest=sha256_value(
                data.get("case_bundle_digest"), "case_bundle_digest"
            ),
            process_status=required(data.get("process_status"), "process_status"),
            timed_out=bool(data.get("timed_out")),
            process_result_digest=(
                sha256_value(data.get("process_result_digest"), "process_result_digest")
                if data.get("process_result_digest") is not None
                else None
            ),
            process_artifact_digests={str(key): str(value) for key, value in artifacts.items()},
            evidence_manifest_digest=sha256_value(
                data.get("evidence_manifest_digest"), "evidence_manifest_digest"
            ),
            changed_paths=tuple(str(item) for item in paths),
            source_digest=sha256_value(data.get("source_digest"), "source_digest"),
            frozen_at=required(data.get("frozen_at"), "frozen_at"),
        )
