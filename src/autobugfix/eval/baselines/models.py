from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    PreparedEvaluationCase,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)


RawCohort = Literal["primary", "development"]
RawProcessStatus = Literal["completed", "sdk_error", "timed_out"]


def required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BenchmarkContractError(f"{name} must not be empty")
    return text


def sha256_value(value: object, name: str) -> str:
    text = required(value, name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise BenchmarkContractError(f"{name} must be sha256")
    return text


def required_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkContractError(f"{name} must be boolean")
    return value


@dataclass(slots=True, frozen=True)
class RawBaselineSeedManifest:
    manifest_id: str
    source_evaluation_manifest_id: str
    benchmark: str
    dataset_revision: str
    expected_case_count: int
    development_case_ids: tuple[str, ...]
    model: str
    sdk_version: str
    reasoning_effort: str
    service_tier: str | None
    approval_mode: str
    sandbox: str
    network_access: bool
    timeout_seconds: int
    turns_per_case: int
    concurrency: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported Raw baseline seed schema")
        safe_component(self.manifest_id, "manifest_id")
        safe_component(
            self.source_evaluation_manifest_id,
            "source_evaluation_manifest_id",
        )
        if self.benchmark != "defects4j":
            raise BenchmarkContractError("Raw baseline seed must use defects4j")
        if self.expected_case_count != 16:
            raise BenchmarkContractError("Raw baseline seed requires 16 cases")
        if len(self.development_case_ids) != 3 or len(
            set(self.development_case_ids)
        ) != 3:
            raise BenchmarkContractError(
                "Raw baseline seed requires three unique development cases"
            )
        for case_id in self.development_case_ids:
            safe_component(case_id, "development_case_id")
        if self.model != "gpt-5.4-mini" or self.sdk_version != "0.1.0b3":
            raise BenchmarkContractError(
                "Raw baseline seed model or SDK version is not pinned"
            )
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise BenchmarkContractError("unsupported Raw baseline reasoning effort")
        if (
            self.approval_mode != "deny_all"
            or self.sandbox != "workspace-write"
            or self.network_access
        ):
            raise BenchmarkContractError(
                "Raw baseline must deny approvals and tool network access"
            )
        if (
            self.timeout_seconds < 1
            or self.turns_per_case != 1
            or self.concurrency != 1
        ):
            raise BenchmarkContractError(
                "Raw baseline seed requires positive timeout, one turn, and concurrency one"
            )
        required(self.dataset_revision, "dataset_revision")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RawBaselineSeedManifest":
        raw_development = data.get("development_case_ids")
        if not isinstance(raw_development, Sequence) or isinstance(
            raw_development, (str, bytes)
        ):
            raise BenchmarkContractError(
                "development_case_ids must be a list"
            )
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            source_evaluation_manifest_id=safe_component(
                data.get("source_evaluation_manifest_id"),
                "source_evaluation_manifest_id",
            ),
            benchmark=required(data.get("benchmark"), "benchmark"),
            dataset_revision=required(
                data.get("dataset_revision"), "dataset_revision"
            ),
            expected_case_count=int(data.get("expected_case_count") or 0),
            development_case_ids=tuple(
                safe_component(item, "development_case_id")
                for item in raw_development
            ),
            model=required(data.get("model"), "model"),
            sdk_version=required(data.get("sdk_version"), "sdk_version"),
            reasoning_effort=required(
                data.get("reasoning_effort"), "reasoning_effort"
            ),
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
            concurrency=int(data.get("concurrency") or 0),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "RawBaselineSeedManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("Raw baseline seed must be a mapping")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "source_evaluation_manifest_id": self.source_evaluation_manifest_id,
            "benchmark": self.benchmark,
            "dataset_revision": self.dataset_revision,
            "expected_case_count": self.expected_case_count,
            "development_case_ids": list(self.development_case_ids),
            "model": self.model,
            "sdk_version": self.sdk_version,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "approval_mode": self.approval_mode,
            "sandbox": self.sandbox,
            "network_access": self.network_access,
            "timeout_seconds": self.timeout_seconds,
            "turns_per_case": self.turns_per_case,
            "concurrency": self.concurrency,
        }

    @property
    def manifest_digest(self) -> str:
        return digest_payload(self.to_dict())


@dataclass(slots=True, frozen=True)
class RawBaselineCase:
    case_id: str
    project: str
    bug_id: int
    receipt_digest: str
    cohort: RawCohort

    def __post_init__(self) -> None:
        safe_component(self.case_id, "case_id")
        required(self.project, "project")
        if self.bug_id < 1:
            raise BenchmarkContractError("Raw baseline bug_id must be positive")
        sha256_value(self.receipt_digest, "receipt_digest")
        if self.cohort not in {"primary", "development"}:
            raise BenchmarkContractError("unsupported Raw baseline cohort")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RawBaselineCase":
        return cls(
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=required(data.get("project"), "project"),
            bug_id=int(data.get("bug_id") or 0),
            receipt_digest=sha256_value(
                data.get("receipt_digest"), "receipt_digest"
            ),
            cohort=str(data.get("cohort") or ""),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project": self.project,
            "bug_id": self.bug_id,
            "receipt_digest": self.receipt_digest,
            "cohort": self.cohort,
        }


@dataclass(slots=True, frozen=True)
class PreparedRawBaselineManifest:
    manifest_id: str
    seed_manifest_digest: str
    source_evaluation_manifest_digest: str
    h0_report_digest: str
    benchmark: str
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    verifier_runtime_id: str
    runner_git_sha: str
    runner_git_tree: str
    runner_source_digest: str
    runner_install_digest: str
    runner_lock_digest: str
    sdk_version: str
    prompt_template_digest: str
    config_digest: str
    model: str
    reasoning_effort: str
    service_tier: str | None
    approval_mode: str
    sandbox: str
    network_access: bool
    timeout_seconds: int
    turns_per_case: int
    concurrency: int
    cases: tuple[RawBaselineCase, ...]
    prepared_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported prepared Raw baseline schema")
        safe_component(self.manifest_id, "manifest_id")
        if self.benchmark != "defects4j":
            raise BenchmarkContractError("Raw baseline benchmark must be defects4j")
        if self.model != "gpt-5.4-mini":
            raise BenchmarkContractError("Raw baseline model must be gpt-5.4-mini")
        if self.sdk_version != "0.1.0b3":
            raise BenchmarkContractError("Raw baseline SDK must be 0.1.0b3")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise BenchmarkContractError("unsupported Raw baseline reasoning effort")
        if (
            self.approval_mode != "deny_all"
            or self.sandbox != "workspace-write"
            or self.network_access
        ):
            raise BenchmarkContractError(
                "Raw baseline must deny approvals and tool network access"
            )
        if (
            self.timeout_seconds < 1
            or self.turns_per_case != 1
            or self.concurrency != 1
        ):
            raise BenchmarkContractError(
                "Raw baseline requires positive timeout, one turn, and concurrency one"
            )
        if len(self.cases) != 16:
            raise BenchmarkContractError("Raw baseline requires exactly 16 cases")
        if len({case.case_id for case in self.cases}) != 16:
            raise BenchmarkContractError("Raw baseline case IDs must be unique")
        if sum(case.cohort == "primary" for case in self.cases) != 13:
            raise BenchmarkContractError("Raw baseline requires 13 primary cases")
        if sum(case.cohort == "development" for case in self.cases) != 3:
            raise BenchmarkContractError(
                "Raw baseline requires three development cases"
            )
        for name, value in (
            (
                "seed_manifest_digest",
                self.seed_manifest_digest,
            ),
            (
                "source_evaluation_manifest_digest",
                self.source_evaluation_manifest_digest,
            ),
            ("h0_report_digest", self.h0_report_digest),
            ("runner_source_digest", self.runner_source_digest),
            ("runner_install_digest", self.runner_install_digest),
            ("runner_lock_digest", self.runner_lock_digest),
            ("prompt_template_digest", self.prompt_template_digest),
            ("config_digest", self.config_digest),
        ):
            sha256_value(value, name)
        for name, value in (
            ("runtime_id", self.runtime_id),
            ("verifier_runtime_id", self.verifier_runtime_id),
        ):
            if not value.startswith("sha256:") or len(value) != 71:
                raise BenchmarkContractError(
                    f"{name} must be an immutable image ID"
                )
        for name, value in (
            ("framework_revision", self.framework_revision),
            ("dataset_revision", self.dataset_revision),
            ("runner_git_sha", self.runner_git_sha),
            ("runner_git_tree", self.runner_git_tree),
            ("prepared_at", self.prepared_at),
        ):
            required(value, name)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any]
    ) -> "PreparedRawBaselineManifest":
        verify_record(data)
        raw_cases = data.get("cases")
        if not isinstance(raw_cases, Sequence) or isinstance(
            raw_cases, (str, bytes)
        ):
            raise BenchmarkContractError("Raw baseline cases must be a list")
        if not all(isinstance(item, Mapping) for item in raw_cases):
            raise BenchmarkContractError("Raw baseline case must be a mapping")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            seed_manifest_digest=sha256_value(
                data.get("seed_manifest_digest"), "seed_manifest_digest"
            ),
            source_evaluation_manifest_digest=sha256_value(
                data.get("source_evaluation_manifest_digest"),
                "source_evaluation_manifest_digest",
            ),
            h0_report_digest=sha256_value(
                data.get("h0_report_digest"), "h0_report_digest"
            ),
            benchmark=required(data.get("benchmark"), "benchmark"),
            framework_revision=required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=required(
                data.get("dataset_revision"), "dataset_revision"
            ),
            runtime_id=required(data.get("runtime_id"), "runtime_id"),
            verifier_runtime_id=required(
                data.get("verifier_runtime_id"), "verifier_runtime_id"
            ),
            runner_git_sha=required(
                data.get("runner_git_sha"), "runner_git_sha"
            ),
            runner_git_tree=required(
                data.get("runner_git_tree"), "runner_git_tree"
            ),
            runner_source_digest=sha256_value(
                data.get("runner_source_digest"), "runner_source_digest"
            ),
            runner_install_digest=sha256_value(
                data.get("runner_install_digest"), "runner_install_digest"
            ),
            runner_lock_digest=sha256_value(
                data.get("runner_lock_digest"), "runner_lock_digest"
            ),
            sdk_version=required(data.get("sdk_version"), "sdk_version"),
            prompt_template_digest=sha256_value(
                data.get("prompt_template_digest"), "prompt_template_digest"
            ),
            config_digest=sha256_value(
                data.get("config_digest"), "config_digest"
            ),
            model=required(data.get("model"), "model"),
            reasoning_effort=required(
                data.get("reasoning_effort"), "reasoning_effort"
            ),
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
            concurrency=int(data.get("concurrency") or 0),
            cases=tuple(RawBaselineCase.from_dict(item) for item in raw_cases),
            prepared_at=required(data.get("prepared_at"), "prepared_at"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "PreparedRawBaselineManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError(
                "prepared Raw baseline manifest must be a mapping"
            )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "manifest_id": self.manifest_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "source_evaluation_manifest_digest": self.source_evaluation_manifest_digest,
                "h0_report_digest": self.h0_report_digest,
                "benchmark": self.benchmark,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "runner_git_sha": self.runner_git_sha,
                "runner_git_tree": self.runner_git_tree,
                "runner_source_digest": self.runner_source_digest,
                "runner_install_digest": self.runner_install_digest,
                "runner_lock_digest": self.runner_lock_digest,
                "sdk_version": self.sdk_version,
                "prompt_template_digest": self.prompt_template_digest,
                "config_digest": self.config_digest,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "service_tier": self.service_tier,
                "approval_mode": self.approval_mode,
                "sandbox": self.sandbox,
                "network_access": self.network_access,
                "timeout_seconds": self.timeout_seconds,
                "turns_per_case": self.turns_per_case,
                "concurrency": self.concurrency,
                "cases": [case.to_dict() for case in self.cases],
                "prepared_at": self.prepared_at,
            }
        )


@dataclass(slots=True, frozen=True)
class RawProcessObservation:
    case_id: str
    case_digest: str
    sdk_version: str
    model: str
    reasoning_effort: str
    service_tier: str | None
    approval_mode: str
    sandbox: str
    network_access: bool
    prompt_template_digest: str
    thread_id: str | None
    turn_id: str | None
    status: RawProcessStatus
    error: str
    final_response: str
    usage: Mapping[str, Any] | None
    event_count: int
    request_sha256: str
    events_sha256: str
    stderr_sha256: str
    started_unix: float
    finished_unix: float
    duration_seconds: float
    record_digest: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RawProcessObservation":
        verify_record(data)
        if data.get("schema") != "raw-codex-sdk-process-result-v1":
            raise BenchmarkContractError(
                "unsupported Raw process result schema"
            )
        status = str(data.get("status") or "")
        if status not in {"completed", "sdk_error", "timed_out"}:
            raise BenchmarkContractError("unsupported Raw process status")
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, Mapping):
            raise BenchmarkContractError(
                "Raw process usage must be a mapping or null"
            )
        return cls(
            case_id=safe_component(data.get("case_id"), "case_id"),
            case_digest=sha256_value(data.get("case_digest"), "case_digest"),
            sdk_version=required(data.get("sdk_version"), "sdk_version"),
            model=required(data.get("model"), "model"),
            reasoning_effort=required(
                data.get("reasoning_effort"), "reasoning_effort"
            ),
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
            prompt_template_digest=sha256_value(
                data.get("prompt_template_digest"), "prompt_template_digest"
            ),
            thread_id=str(data["thread_id"]) if data.get("thread_id") else None,
            turn_id=str(data["turn_id"]) if data.get("turn_id") else None,
            status=status,  # type: ignore[arg-type]
            error=str(data.get("error") or ""),
            final_response=str(data.get("final_response") or ""),
            usage=dict(usage) if usage is not None else None,
            event_count=int(data.get("event_count") or 0),
            request_sha256=sha256_value(
                data.get("request_sha256"), "request_sha256"
            ),
            events_sha256=sha256_value(
                data.get("events_sha256"), "events_sha256"
            ),
            stderr_sha256=sha256_value(
                data.get("stderr_sha256"), "stderr_sha256"
            ),
            started_unix=float(data.get("started_unix") or 0.0),
            finished_unix=float(data.get("finished_unix") or 0.0),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
            record_digest=sha256_value(
                data.get("record_digest"), "record_digest"
            ),
        )


def raw_case_from_prepared(
    case: PreparedEvaluationCase,
    *,
    development_case_ids: set[str],
) -> RawBaselineCase:
    return RawBaselineCase(
        case_id=case.case_id,
        project=case.project,
        bug_id=case.bug_id,
        receipt_digest=case.receipt_digest,
        cohort=(
            "development"
            if case.case_id in development_case_ids
            else "primary"
        ),
    )
