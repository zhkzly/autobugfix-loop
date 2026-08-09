from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.models import utc_now


class Exp2ContractError(BenchmarkContractError):
    """A malformed or out-of-scope Experiment 2 control record."""


Exp2StageName = Literal[
    "H0_CALIBRATION",
    "H0_PUBLIC",
    "H1A_PUBLIC",
    "H1B_PUBLIC",
    "PUBLIC_REPLAY",
    "SEALED_HOLDOUT",
]
Exp2Arm = Literal["H0", "H1"]
Exp2ExecutionMode = Literal["protected", "workspace_only"]

PUBLIC_CASE_COUNTS: dict[int, int] = {3: 2, 8: 5, 16: 10}
SEALED_CASE_COUNTS: dict[int, int] = {3: 0, 8: 0, 16: 6}
OPERATOR_BUDGET_COUNTS: dict[int, int] = {3: 3, 8: 8, 16: 16}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Exp2ContractError(f"{field} must not be empty")
    return text


def _digest(value: object, field: str) -> str:
    text = _required(value, field)
    if not _SHA256.fullmatch(text):
        raise Exp2ContractError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _git_sha(value: object, field: str) -> str:
    text = _required(value, field)
    if not _SHA1.fullmatch(text):
        raise Exp2ContractError(f"{field} must be a lowercase Git SHA")
    return text


def _tuple_strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Exp2ContractError(f"{field} must be a list")
    result = tuple(safe_component(item, field) for item in value)
    if len(set(result)) != len(result):
        raise Exp2ContractError(f"{field} must contain unique values")
    return result


def _tuple_digests(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Exp2ContractError(f"{field} must be a list")
    return tuple(_digest(item, field) for item in value)


def _tuple_relative_paths(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Exp2ContractError(f"{field} must be a list")
    result: list[str] = []
    for item in value:
        text = _required(item, field).replace("\\", "/")
        path = Path(text)
        if path.is_absolute() or ".." in path.parts or text.startswith("./"):
            raise Exp2ContractError(f"{field} contains an unsafe relative path")
        result.append(path.as_posix())
    if len(set(result)) != len(result):
        raise Exp2ContractError(f"{field} must contain unique values")
    return tuple(result)


def _only_fields(data: Mapping[str, Any], fields: set[str], label: str) -> None:
    unknown = sorted(set(data) - fields)
    if unknown:
        raise Exp2ContractError(
            f"{label} contains unsupported fields: {', '.join(unknown)}"
        )


def _verify_exp2_record(data: Mapping[str, Any]) -> None:
    try:
        verify_record(data)
    except BenchmarkContractError as exc:
        raise Exp2ContractError(str(exc)) from exc


def _path(value: object, field: str) -> str:
    text = _required(value, field)
    if not Path(text).is_absolute():
        raise Exp2ContractError(f"{field} must be an absolute path")
    return str(Path(text).resolve())


def _optional_path(value: object, field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _path(value, field)


def opaque_budget_slot_ids(wave: int) -> tuple[str, ...]:
    if wave not in OPERATOR_BUDGET_COUNTS:
        raise Exp2ContractError("Exp2 Operator wave must be 3, 8, or 16")
    count = OPERATOR_BUDGET_COUNTS[wave] - PUBLIC_CASE_COUNTS[wave]
    return tuple(
        f"exp2-budget-slot-{wave}-{index:02d}" for index in range(1, count + 1)
    )


@dataclass(slots=True, frozen=True)
class Exp2BudgetAllocation:
    """The public/opaque namespace split used at one Operator wave."""

    wave: int
    public_case_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        expected = PUBLIC_CASE_COUNTS.get(self.wave)
        if expected is None:
            raise Exp2ContractError("Exp2 Operator wave must be 3, 8, or 16")
        if len(self.public_case_ids) != expected:
            raise Exp2ContractError(
                f"wave {self.wave} requires exactly {expected} cumulative public cases"
            )
        if len(set(self.public_case_ids)) != len(self.public_case_ids):
            raise Exp2ContractError("Exp2 public case IDs must be unique")
        for case_id in self.public_case_ids:
            safe_component(case_id, "public_case_ids")

    @property
    def opaque_slot_ids(self) -> tuple[str, ...]:
        return opaque_budget_slot_ids(self.wave)

    @property
    def operator_case_ids(self) -> tuple[str, ...]:
        values = (*self.public_case_ids, *self.opaque_slot_ids)
        if len(values) != OPERATOR_BUDGET_COUNTS[self.wave]:
            raise Exp2ContractError("Exp2 Operator slot allocation has the wrong size")
        if len(set(values)) != len(values):
            raise Exp2ContractError("Exp2 Operator case namespace collides")
        return values

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "autobugfix-exp2-budget-allocation-v1",
            "wave": self.wave,
            "public_case_ids": list(self.public_case_ids),
            "opaque_slot_ids": list(self.opaque_slot_ids),
            "operator_case_ids": list(self.operator_case_ids),
        }


@dataclass(slots=True, frozen=True)
class Exp2ResultProjection:
    """Public projection of one terminal official Eval result.

    This intentionally contains no gold patch, hidden test, scorer diagnosis,
    or raw oracle fields.  It is a feedback label, not a causal explanation.
    """

    study_id: str
    arm: Exp2Arm
    stage: Exp2StageName
    case_id: str
    source_report_digest: str
    official_result_digest: str
    resolved: bool
    harness_error: bool
    public_label: Literal["resolved", "unresolved", "harness_error"]
    executed_subject_sha: str
    submission_digest: str
    same_case_retry_forbidden: bool = True

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        safe_component(self.case_id, "case_id")
        _digest(self.source_report_digest, "source_report_digest")
        _digest(self.official_result_digest, "official_result_digest")
        _git_sha(self.executed_subject_sha, "executed_subject_sha")
        _digest(self.submission_digest, "submission_digest")
        if self.stage not in {
            "H0_CALIBRATION",
            "H0_PUBLIC",
            "H1A_PUBLIC",
            "H1B_PUBLIC",
            "PUBLIC_REPLAY",
        }:
            raise Exp2ContractError("unsupported Exp2 stage")
        if self.arm not in {"H0", "H1"}:
            raise Exp2ContractError("unsupported Exp2 arm")
        expected_arm = "H0" if self.stage in {"H0_CALIBRATION", "H0_PUBLIC"} else "H1"
        if self.arm != expected_arm:
            raise Exp2ContractError("Exp2 projection arm disagrees with stage")
        expected = (
            "harness_error"
            if self.harness_error
            else "resolved"
            if self.resolved
            else "unresolved"
        )
        if self.public_label != expected:
            raise Exp2ContractError("Exp2 public label disagrees with terminal result")
        if self.same_case_retry_forbidden is not True:
            raise Exp2ContractError("same-case official retry must remain forbidden")

    @classmethod
    def from_report(
        cls,
        report: Mapping[str, Any],
        *,
        study_id: str,
        arm: Exp2Arm,
        stage: Exp2StageName,
        case_id: str | None = None,
    ) -> Exp2ResultProjection:
        _verify_exp2_record(report)
        official = report.get("official_result")
        if not isinstance(official, Mapping):
            raise Exp2ContractError("Exp2 report has no terminal official result")
        _verify_exp2_record(official)
        if official.get("schema") != "autobugfix-swe-official-result-v1":
            raise Exp2ContractError("Exp2 report has an unsupported official result")
        noninterference = report.get("noninterference")
        if not isinstance(noninterference, Mapping):
            raise Exp2ContractError("Exp2 report has no noninterference receipt")
        _verify_exp2_record(noninterference)
        if (
            noninterference.get("schema") != "autobugfix-swe-noninterference-v1"
            or noninterference.get("unchanged") is not True
            or noninterference.get("submission_digest")
            != report.get("submission_digest")
            or noninterference.get("official_result_digest")
            != official.get("record_digest")
        ):
            raise Exp2ContractError("Exp2 report noninterference receipt is invalid")
        official_case = safe_component(
            official.get("instance_id"), "official.instance_id"
        )
        selected_case = safe_component(case_id or official_case, "case_id")
        if selected_case != official_case:
            raise Exp2ContractError(
                "Exp2 report official instance does not match the frozen case schedule"
            )
        subject_sha = _git_sha(
            report.get("executed_subject_sha"), "executed_subject_sha"
        )
        submission_digest = _digest(
            report.get("submission_digest"), "submission_digest"
        )
        resolved = official.get("resolved")
        harness_error = bool(official.get("harness_error")) or bool(
            report.get("harness_error")
        )
        if type(resolved) is not bool:
            raise Exp2ContractError("official resolved result must be boolean")
        return cls(
            study_id=safe_component(study_id, "study_id"),
            arm=arm,
            stage=stage,
            case_id=selected_case,
            source_report_digest=_digest(
                report.get("record_digest"), "source_report_digest"
            ),
            official_result_digest=_digest(
                official.get("record_digest"), "official_result_digest"
            ),
            resolved=resolved,
            harness_error=harness_error,
            public_label=(
                "harness_error"
                if harness_error
                else "resolved"
                if resolved
                else "unresolved"
            ),
            executed_subject_sha=subject_sha,
            submission_digest=submission_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-result-projection-v1",
                "study_id": self.study_id,
                "arm": self.arm,
                "stage": self.stage,
                "case_id": self.case_id,
                "source_report_digest": self.source_report_digest,
                "official_result_digest": self.official_result_digest,
                "resolved": self.resolved,
                "harness_error": self.harness_error,
                "public_label": self.public_label,
                "executed_subject_sha": self.executed_subject_sha,
                "submission_digest": self.submission_digest,
                "same_case_retry_forbidden": self.same_case_retry_forbidden,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2ResultProjection:
        _verify_exp2_record(data)
        expected_fields = {
            "schema",
            "study_id",
            "arm",
            "stage",
            "case_id",
            "source_report_digest",
            "official_result_digest",
            "resolved",
            "harness_error",
            "public_label",
            "executed_subject_sha",
            "submission_digest",
            "same_case_retry_forbidden",
            "record_digest",
        }
        _only_fields(data, expected_fields, "Exp2 result projection")
        if data.get("schema") != "autobugfix-exp2-result-projection-v1":
            raise Exp2ContractError("unsupported Exp2 result projection schema")
        for field_name in ("resolved", "harness_error", "same_case_retry_forbidden"):
            if type(data.get(field_name)) is not bool:
                raise Exp2ContractError(
                    f"Exp2 result projection field {field_name} must be boolean"
                )
        return cls(
            study_id=str(data.get("study_id") or ""),
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            case_id=str(data.get("case_id") or ""),
            source_report_digest=str(data.get("source_report_digest") or ""),
            official_result_digest=str(data.get("official_result_digest") or ""),
            resolved=data["resolved"],
            harness_error=data["harness_error"],
            public_label=str(data.get("public_label") or ""),  # type: ignore[arg-type]
            executed_subject_sha=str(data.get("executed_subject_sha") or ""),
            submission_digest=str(data.get("submission_digest") or ""),
            same_case_retry_forbidden=data["same_case_retry_forbidden"],
        )


@dataclass(slots=True, frozen=True)
class Exp2AttributionRecord:
    """A bounded hypothesis supplied to the frozen-skill Operator.

    The record does not assert causality.  It records the hypothesis and the
    one permitted change scope so a later coordinator can reject scope drift.
    """

    study_id: str
    arm: Exp2Arm
    stage: Exp2StageName
    source_projection_digest: str
    failure_stage: Literal[
        "execution",
        "visible_verifier",
        "official_eval",
        "infrastructure",
        "unknown",
    ]
    hypothesis: str
    confidence: float
    supporting_evidence_digests: tuple[str, ...]
    expected_mechanism: str
    change_scope: Literal["execution_harness", "execution_role_skill"]
    validation_plan: tuple[str, ...]
    parent_candidate_sha: str
    revision: int
    author: str
    approver: str
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _digest(self.source_projection_digest, "source_projection_digest")
        if self.failure_stage not in {
            "execution",
            "visible_verifier",
            "official_eval",
            "infrastructure",
            "unknown",
        }:
            raise Exp2ContractError("unsupported Exp2 failure stage")
        if not self.hypothesis.strip() or not self.expected_mechanism.strip():
            raise Exp2ContractError("Exp2 attribution hypothesis must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise Exp2ContractError("Exp2 attribution confidence must be in [0, 1]")
        if not self.supporting_evidence_digests:
            raise Exp2ContractError("Exp2 attribution requires supporting evidence")
        for value in self.supporting_evidence_digests:
            _digest(value, "supporting_evidence_digests")
        if self.change_scope not in {"execution_harness", "execution_role_skill"}:
            raise Exp2ContractError("Exp2 attribution change scope is not allowlisted")
        if not self.validation_plan or any(
            not item.strip() for item in self.validation_plan
        ):
            raise Exp2ContractError("Exp2 attribution requires a validation plan")
        _git_sha(self.parent_candidate_sha, "parent_candidate_sha")
        if self.revision not in {1, 2}:
            raise Exp2ContractError("Exp2 attribution revision must be one or two")
        if not self.author.strip() or not self.approver.strip():
            raise Exp2ContractError("Exp2 attribution author and approver are required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2AttributionRecord:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "arm",
                "stage",
                "source_projection_digest",
                "failure_stage",
                "hypothesis",
                "confidence",
                "supporting_evidence_digests",
                "expected_mechanism",
                "change_scope",
                "validation_plan",
                "parent_candidate_sha",
                "revision",
                "author",
                "approver",
                "created_at",
                "record_digest",
            },
            "Exp2 attribution",
        )
        if data.get("schema") != "autobugfix-exp2-attribution-v1":
            raise Exp2ContractError("unsupported Exp2 attribution schema")
        return cls(
            study_id=str(data.get("study_id") or ""),
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            source_projection_digest=str(data.get("source_projection_digest") or ""),
            failure_stage=str(data.get("failure_stage") or ""),  # type: ignore[arg-type]
            hypothesis=str(data.get("hypothesis") or ""),
            confidence=float(data.get("confidence") or 0.0),
            supporting_evidence_digests=tuple(
                str(item) for item in data.get("supporting_evidence_digests") or ()
            ),
            expected_mechanism=str(data.get("expected_mechanism") or ""),
            change_scope=str(data.get("change_scope") or ""),  # type: ignore[arg-type]
            validation_plan=tuple(
                str(item) for item in data.get("validation_plan") or ()
            ),
            parent_candidate_sha=str(data.get("parent_candidate_sha") or ""),
            revision=int(data.get("revision") or 0),
            author=str(data.get("author") or ""),
            approver=str(data.get("approver") or ""),
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-attribution-v1",
                "study_id": self.study_id,
                "arm": self.arm,
                "stage": self.stage,
                "source_projection_digest": self.source_projection_digest,
                "failure_stage": self.failure_stage,
                "hypothesis": self.hypothesis,
                "confidence": self.confidence,
                "supporting_evidence_digests": list(self.supporting_evidence_digests),
                "expected_mechanism": self.expected_mechanism,
                "change_scope": self.change_scope,
                "validation_plan": list(self.validation_plan),
                "parent_candidate_sha": self.parent_candidate_sha,
                "revision": self.revision,
                "author": self.author,
                "approver": self.approver,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2StageReceipt:
    stage_id: str
    study_id: str
    stage: Exp2StageName
    arm: Exp2Arm
    case_ids: tuple[str, ...]
    projection_digests: tuple[str, ...]
    report_digests: tuple[str, ...]
    subject_sha: str
    frozen_input_digest: str
    binding_digest: str
    execution_mode: Exp2ExecutionMode
    direct_sdk_in_process: bool
    outer_bubblewrap: bool
    workspace_only_preflight_digests: tuple[str, ...]
    predecessor_receipt_digest: str | None = None
    attribution_digest: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.stage_id, "stage_id")
        safe_component(self.study_id, "study_id")
        if self.stage not in {
            "H0_CALIBRATION",
            "H0_PUBLIC",
            "H1A_PUBLIC",
            "H1B_PUBLIC",
            "PUBLIC_REPLAY",
        }:
            raise Exp2ContractError(
                "sealed Holdout stage records are Guard-private and cannot enter the coordinator ledger"
            )
        expected_arm = "H0" if self.stage in {"H0_CALIBRATION", "H0_PUBLIC"} else "H1"
        if self.arm != expected_arm:
            raise Exp2ContractError("Exp2 stage arm disagrees with stage")
        if not self.case_ids or len(set(self.case_ids)) != len(self.case_ids):
            raise Exp2ContractError("Exp2 stage must contain unique case IDs")
        for case_id in self.case_ids:
            safe_component(case_id, "case_ids")
        if len(self.projection_digests) != len(self.case_ids):
            raise Exp2ContractError("Exp2 stage projection count differs from cases")
        if len(self.report_digests) != len(self.case_ids):
            raise Exp2ContractError("Exp2 stage report count differs from cases")
        for value in (*self.projection_digests, *self.report_digests):
            _digest(value, "Exp2 stage digest")
        _git_sha(self.subject_sha, "subject_sha")
        _digest(self.frozen_input_digest, "frozen_input_digest")
        _digest(self.binding_digest, "binding_digest")
        if self.execution_mode not in {"protected", "workspace_only"}:
            raise Exp2ContractError("unsupported Exp2 execution mode")
        if type(self.direct_sdk_in_process) is not bool:
            raise Exp2ContractError("Exp2 stage direct SDK flag must be boolean")
        if type(self.outer_bubblewrap) is not bool:
            raise Exp2ContractError("Exp2 stage outer Bubblewrap flag must be boolean")
        if self.execution_mode == "workspace_only":
            if not self.direct_sdk_in_process or self.outer_bubblewrap:
                raise Exp2ContractError(
                    "workspace-only stage must prove direct SDK and no Bubblewrap"
                )
            if len(self.workspace_only_preflight_digests) != len(self.case_ids):
                raise Exp2ContractError(
                    "workspace-only stage must bind one preflight receipt per case"
                )
            for value in self.workspace_only_preflight_digests:
                _digest(value, "workspace_only_preflight_digests")
        else:
            if self.direct_sdk_in_process or not self.outer_bubblewrap:
                raise Exp2ContractError(
                    "protected stage must retain the outer Bubblewrap contract"
                )
            if self.workspace_only_preflight_digests:
                raise Exp2ContractError(
                    "protected stage cannot carry workspace-only preflight receipts"
                )
        if self.predecessor_receipt_digest is not None:
            _digest(self.predecessor_receipt_digest, "predecessor_receipt_digest")
        if self.attribution_digest is not None:
            _digest(self.attribution_digest, "attribution_digest")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-stage-receipt-v1",
                "stage_id": self.stage_id,
                "study_id": self.study_id,
                "stage": self.stage,
                "arm": self.arm,
                "case_ids": list(self.case_ids),
                "projection_digests": list(self.projection_digests),
                "report_digests": list(self.report_digests),
                "subject_sha": self.subject_sha,
                "frozen_input_digest": self.frozen_input_digest,
                "binding_digest": self.binding_digest,
                "execution_mode": self.execution_mode,
                "direct_sdk_in_process": self.direct_sdk_in_process,
                "outer_bubblewrap": self.outer_bubblewrap,
                "workspace_only_preflight_digests": list(
                    self.workspace_only_preflight_digests
                ),
                "predecessor_receipt_digest": self.predecessor_receipt_digest,
                "attribution_digest": self.attribution_digest,
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2StageReceipt:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "stage_id",
                "study_id",
                "stage",
                "arm",
                "case_ids",
                "projection_digests",
                "report_digests",
                "subject_sha",
                "frozen_input_digest",
                "binding_digest",
                "execution_mode",
                "direct_sdk_in_process",
                "outer_bubblewrap",
                "workspace_only_preflight_digests",
                "predecessor_receipt_digest",
                "attribution_digest",
                "created_at",
                "record_digest",
            },
            "Exp2 stage receipt",
        )
        if data.get("schema") != "autobugfix-exp2-stage-receipt-v1":
            raise Exp2ContractError("unsupported Exp2 stage receipt schema")
        return cls(
            stage_id=str(data.get("stage_id") or ""),
            study_id=str(data.get("study_id") or ""),
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            case_ids=_tuple_strings(data.get("case_ids") or (), "case_ids"),
            projection_digests=_tuple_digests(
                data.get("projection_digests") or (), "projection_digests"
            ),
            report_digests=_tuple_digests(
                data.get("report_digests") or (), "report_digests"
            ),
            subject_sha=str(data.get("subject_sha") or ""),
            frozen_input_digest=str(data.get("frozen_input_digest") or ""),
            binding_digest=str(data.get("binding_digest") or ""),
            execution_mode=str(data.get("execution_mode") or ""),  # type: ignore[arg-type]
            direct_sdk_in_process=data["direct_sdk_in_process"],
            outer_bubblewrap=data["outer_bubblewrap"],
            workspace_only_preflight_digests=_tuple_digests(
                data.get("workspace_only_preflight_digests") or (),
                "workspace_only_preflight_digests",
            ),
            predecessor_receipt_digest=(
                str(data["predecessor_receipt_digest"])
                if data.get("predecessor_receipt_digest") is not None
                else None
            ),
            attribution_digest=(
                str(data["attribution_digest"])
                if data.get("attribution_digest") is not None
                else None
            ),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class Exp2EmptyMemoryFixture:
    """Identity for the fixed empty Memory input used by an Exp2 Study."""

    fixture_id: str
    fixture_path: str
    fixture_file_digest: str
    active_entries: tuple[str, ...] = ()
    approved_skill_entries: tuple[str, ...] = ()
    maintenance_enabled: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        safe_component(self.fixture_id, "fixture_id")
        _path(self.fixture_path, "fixture_path")
        _digest(self.fixture_file_digest, "fixture_file_digest")
        if self.schema_version != 1:
            raise Exp2ContractError("unsupported Exp2 empty Memory fixture schema")
        if self.active_entries or self.approved_skill_entries:
            raise Exp2ContractError("Exp2 Memory fixture must be empty")
        if self.maintenance_enabled is not False:
            raise Exp2ContractError(
                "Exp2 empty Memory fixture cannot enable maintenance"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2EmptyMemoryFixture:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "schema_version",
                "fixture_id",
                "fixture_path",
                "fixture_file_digest",
                "active_entries",
                "approved_skill_entries",
                "maintenance_enabled",
                "record_digest",
            },
            "Exp2 empty Memory fixture",
        )
        if data.get("schema") != "autobugfix-exp2-empty-memory-fixture-v1":
            raise Exp2ContractError("unsupported Exp2 empty Memory fixture schema")
        if type(data.get("maintenance_enabled")) is not bool:
            raise Exp2ContractError("Memory maintenance flag must be boolean")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            fixture_id=str(data.get("fixture_id") or ""),
            fixture_path=str(data.get("fixture_path") or ""),
            fixture_file_digest=str(data.get("fixture_file_digest") or ""),
            active_entries=_tuple_strings(
                data.get("active_entries") or (), "active_entries"
            ),
            approved_skill_entries=_tuple_strings(
                data.get("approved_skill_entries") or (),
                "approved_skill_entries",
            ),
            maintenance_enabled=data["maintenance_enabled"],
        )

    @classmethod
    def from_yaml(cls, path: Path) -> Exp2EmptyMemoryFixture:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file():
            raise Exp2ContractError(
                "Exp2 empty Memory fixture is missing or redirected"
            )
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2ContractError("Exp2 empty Memory fixture must be a mapping")
        if raw.get("schema") == "autobugfix-exp2-empty-memory-fixture-spec-v1":
            _only_fields(
                raw,
                {
                    "schema",
                    "fixture_id",
                    "active_entries",
                    "approved_skill_entries",
                },
                "Exp2 empty Memory fixture spec",
            )
            return cls(
                fixture_id=str(raw.get("fixture_id") or ""),
                fixture_path=str(resolved),
                fixture_file_digest=digest_file(resolved),
                active_entries=_tuple_strings(
                    raw.get("active_entries") or (), "active_entries"
                ),
                approved_skill_entries=_tuple_strings(
                    raw.get("approved_skill_entries") or (),
                    "approved_skill_entries",
                ),
            )
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-empty-memory-fixture-v1",
                "schema_version": self.schema_version,
                "fixture_id": self.fixture_id,
                "fixture_path": self.fixture_path,
                "fixture_file_digest": self.fixture_file_digest,
                "active_entries": list(self.active_entries),
                "approved_skill_entries": list(self.approved_skill_entries),
                "maintenance_enabled": self.maintenance_enabled,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2CohortAudit:
    """Pre-registered split and namespace audit for the small-cohort pilot."""

    study_id: str
    protocol_digest: str
    calibration_case_ids: tuple[str, ...]
    public_case_ids: tuple[str, ...]
    calibration_repositories: tuple[str, ...]
    public_repositories: tuple[str, ...]
    holdout_case_count: int = 6
    holdout_repository_count: int = 6
    holdout_language_count: int = 4
    calibration_exclusion_digest: str = ""
    public_schedule: Mapping[int, int] = field(
        default_factory=lambda: dict(PUBLIC_CASE_COUNTS)
    )
    sealed_schedule: Mapping[int, int] = field(
        default_factory=lambda: dict(SEALED_CASE_COUNTS)
    )
    operator_schedule: Mapping[int, int] = field(
        default_factory=lambda: dict(OPERATOR_BUDGET_COUNTS)
    )

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _digest(self.protocol_digest, "protocol_digest")
        if len(self.calibration_case_ids) not in {2, 3}:
            raise Exp2ContractError("Exp2 calibration cohort must contain 2 or 3 cases")
        if len(self.public_case_ids) != 10:
            raise Exp2ContractError("Exp2 public cohort must contain exactly 10 cases")
        for field_name, values in (
            ("calibration_case_ids", self.calibration_case_ids),
            ("public_case_ids", self.public_case_ids),
            ("calibration_repositories", self.calibration_repositories),
            ("public_repositories", self.public_repositories),
        ):
            if len(set(values)) != len(values):
                raise Exp2ContractError(f"{field_name} must contain unique values")
            for value in values:
                safe_component(value, field_name)
        if set(self.calibration_repositories) & set(self.public_repositories):
            raise Exp2ContractError(
                "calibration repositories must be disjoint from public repositories"
            )
        if len(self.public_repositories) != 6:
            raise Exp2ContractError(
                "the formal public cohort must cover six repository clusters"
            )
        if self.holdout_case_count != 6 or self.holdout_repository_count != 6:
            raise Exp2ContractError(
                "Exp2 Holdout audit requires six repository-unique cases"
            )
        if self.holdout_language_count < 4:
            raise Exp2ContractError(
                "Exp2 Holdout audit requires at least four languages"
            )
        _digest(self.calibration_exclusion_digest, "calibration_exclusion_digest")
        if dict(self.public_schedule) != PUBLIC_CASE_COUNTS:
            raise Exp2ContractError("Exp2 public schedule must be 2/5/10")
        if dict(self.sealed_schedule) != SEALED_CASE_COUNTS:
            raise Exp2ContractError("Exp2 sealed schedule must be 0/0/6")
        if dict(self.operator_schedule) != OPERATOR_BUDGET_COUNTS:
            raise Exp2ContractError("Exp2 Operator schedule must be 3/8/16")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2CohortAudit:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "protocol_digest",
                "calibration_case_ids",
                "public_case_ids",
                "calibration_repositories",
                "public_repositories",
                "holdout_case_count",
                "holdout_repository_count",
                "holdout_language_count",
                "calibration_exclusion_digest",
                "public_schedule",
                "sealed_schedule",
                "operator_schedule",
                "record_digest",
            },
            "Exp2 cohort audit",
        )
        if data.get("schema") != "autobugfix-exp2-cohort-audit-v1":
            raise Exp2ContractError("unsupported Exp2 cohort audit schema")
        schedules = (
            data.get("public_schedule"),
            data.get("sealed_schedule"),
            data.get("operator_schedule"),
        )
        if any(not isinstance(value, Mapping) for value in schedules):
            raise Exp2ContractError("Exp2 cohort schedules must be mappings")
        return cls(
            study_id=str(data.get("study_id") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            calibration_case_ids=_tuple_strings(
                data.get("calibration_case_ids") or (), "calibration_case_ids"
            ),
            public_case_ids=_tuple_strings(
                data.get("public_case_ids") or (), "public_case_ids"
            ),
            calibration_repositories=_tuple_strings(
                data.get("calibration_repositories") or (),
                "calibration_repositories",
            ),
            public_repositories=_tuple_strings(
                data.get("public_repositories") or (), "public_repositories"
            ),
            holdout_case_count=int(data.get("holdout_case_count") or 0),
            holdout_repository_count=int(data.get("holdout_repository_count") or 0),
            holdout_language_count=int(data.get("holdout_language_count") or 0),
            calibration_exclusion_digest=str(
                data.get("calibration_exclusion_digest") or ""
            ),
            public_schedule={
                int(key): int(value) for key, value in dict(schedules[0]).items()
            },
            sealed_schedule={
                int(key): int(value) for key, value in dict(schedules[1]).items()
            },
            operator_schedule={
                int(key): int(value) for key, value in dict(schedules[2]).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-cohort-audit-v1",
                "study_id": self.study_id,
                "protocol_digest": self.protocol_digest,
                "calibration_case_ids": list(self.calibration_case_ids),
                "public_case_ids": list(self.public_case_ids),
                "calibration_repositories": list(self.calibration_repositories),
                "public_repositories": list(self.public_repositories),
                "holdout_case_count": self.holdout_case_count,
                "holdout_repository_count": self.holdout_repository_count,
                "holdout_language_count": self.holdout_language_count,
                "calibration_exclusion_digest": self.calibration_exclusion_digest,
                "public_schedule": dict(self.public_schedule),
                "sealed_schedule": dict(self.sealed_schedule),
                "operator_schedule": dict(self.operator_schedule),
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2PolicyRecord:
    """Frozen study policy; Operator skills/policy remain external authorities."""

    study_id: str
    memory_fixture_digest: str
    operator_role_skill_digest: str
    execution_allowlist: tuple[str, ...]
    max_h1_revisions: int = 2
    sealed_holdout_min_wave: int = 16
    final_holdout_requires_treatment_lock: bool = True
    public_regression_limit: int = 0
    sealed_regression_limit: int = 0
    no_memory_maintenance: bool = True
    no_holdout_before_lock: bool = True

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _digest(self.memory_fixture_digest, "memory_fixture_digest")
        _digest(self.operator_role_skill_digest, "operator_role_skill_digest")
        if self.max_h1_revisions != 2:
            raise Exp2ContractError("Exp2 policy allows exactly two H1 revisions")
        if self.sealed_holdout_min_wave != 16:
            raise Exp2ContractError("Exp2 Holdout unlock is restricted to wave 16")
        if self.public_regression_limit != 0 or self.sealed_regression_limit != 0:
            raise Exp2ContractError("Exp2 regression limits must remain zero")
        if not self.execution_allowlist:
            raise Exp2ContractError("Exp2 execution allowlist must not be empty")
        for path in self.execution_allowlist:
            if not path.strip() or path.startswith("/"):
                raise Exp2ContractError("Exp2 execution allowlist paths are invalid")
        for field_name in (
            "final_holdout_requires_treatment_lock",
            "no_memory_maintenance",
            "no_holdout_before_lock",
        ):
            if getattr(self, field_name) is not True:
                raise Exp2ContractError(f"Exp2 policy must set {field_name}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2PolicyRecord:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "memory_fixture_digest",
                "operator_role_skill_digest",
                "execution_allowlist",
                "max_h1_revisions",
                "sealed_holdout_min_wave",
                "final_holdout_requires_treatment_lock",
                "public_regression_limit",
                "sealed_regression_limit",
                "no_memory_maintenance",
                "no_holdout_before_lock",
                "record_digest",
            },
            "Exp2 policy",
        )
        if data.get("schema") != "autobugfix-exp2-policy-v1":
            raise Exp2ContractError("unsupported Exp2 policy schema")
        for field_name in (
            "final_holdout_requires_treatment_lock",
            "no_memory_maintenance",
            "no_holdout_before_lock",
        ):
            if type(data.get(field_name)) is not bool:
                raise Exp2ContractError(
                    f"Exp2 policy field {field_name} must be boolean"
                )
        return cls(
            study_id=str(data.get("study_id") or ""),
            memory_fixture_digest=str(data.get("memory_fixture_digest") or ""),
            operator_role_skill_digest=str(
                data.get("operator_role_skill_digest") or ""
            ),
            execution_allowlist=_tuple_relative_paths(
                data.get("execution_allowlist") or (), "execution_allowlist"
            ),
            max_h1_revisions=int(data.get("max_h1_revisions") or 0),
            sealed_holdout_min_wave=int(data.get("sealed_holdout_min_wave") or 0),
            final_holdout_requires_treatment_lock=data[
                "final_holdout_requires_treatment_lock"
            ],
            public_regression_limit=int(data.get("public_regression_limit") or 0),
            sealed_regression_limit=int(data.get("sealed_regression_limit") or 0),
            no_memory_maintenance=data["no_memory_maintenance"],
            no_holdout_before_lock=data["no_holdout_before_lock"],
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-policy-v1",
                "study_id": self.study_id,
                "memory_fixture_digest": self.memory_fixture_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_allowlist": list(self.execution_allowlist),
                "max_h1_revisions": self.max_h1_revisions,
                "sealed_holdout_min_wave": self.sealed_holdout_min_wave,
                "final_holdout_requires_treatment_lock": self.final_holdout_requires_treatment_lock,
                "public_regression_limit": self.public_regression_limit,
                "sealed_regression_limit": self.sealed_regression_limit,
                "no_memory_maintenance": self.no_memory_maintenance,
                "no_holdout_before_lock": self.no_holdout_before_lock,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2ApparatusReceipt:
    """Shared apparatus identity that must be frozen before H0 collection."""

    study_id: str
    apparatus_sha: str
    apparatus_tree: str
    protocol_digest: str
    evaluator_runtime_digest: str
    subject_runtime_contract_digest: str
    scorer_digest: str
    projection_digest: str
    reporting_digest: str
    memory_fixture_digest: str
    operator_role_skill_digest: str
    execution_mode: Exp2ExecutionMode
    preflight_digest: str
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _git_sha(self.apparatus_sha, "apparatus_sha")
        _git_sha(self.apparatus_tree, "apparatus_tree")
        for field_name in (
            "protocol_digest",
            "evaluator_runtime_digest",
            "subject_runtime_contract_digest",
            "scorer_digest",
            "projection_digest",
            "reporting_digest",
            "memory_fixture_digest",
            "operator_role_skill_digest",
            "preflight_digest",
        ):
            _digest(getattr(self, field_name), field_name)
        if self.execution_mode not in {"protected", "workspace_only"}:
            raise Exp2ContractError("unsupported Exp2 apparatus execution mode")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2ApparatusReceipt:
        _verify_exp2_record(data)
        expected = {
            "schema",
            "study_id",
            "apparatus_sha",
            "apparatus_tree",
            "protocol_digest",
            "evaluator_runtime_digest",
            "subject_runtime_contract_digest",
            "scorer_digest",
            "projection_digest",
            "reporting_digest",
            "memory_fixture_digest",
            "operator_role_skill_digest",
            "execution_mode",
            "preflight_digest",
            "created_at",
            "record_digest",
        }
        _only_fields(data, expected, "Exp2 apparatus receipt")
        if data.get("schema") != "autobugfix-exp2-apparatus-receipt-v1":
            raise Exp2ContractError("unsupported Exp2 apparatus receipt schema")
        return cls(
            study_id=str(data.get("study_id") or ""),
            apparatus_sha=str(data.get("apparatus_sha") or ""),
            apparatus_tree=str(data.get("apparatus_tree") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            evaluator_runtime_digest=str(data.get("evaluator_runtime_digest") or ""),
            subject_runtime_contract_digest=str(
                data.get("subject_runtime_contract_digest") or ""
            ),
            scorer_digest=str(data.get("scorer_digest") or ""),
            projection_digest=str(data.get("projection_digest") or ""),
            reporting_digest=str(data.get("reporting_digest") or ""),
            memory_fixture_digest=str(data.get("memory_fixture_digest") or ""),
            operator_role_skill_digest=str(
                data.get("operator_role_skill_digest") or ""
            ),
            execution_mode=str(data.get("execution_mode") or ""),  # type: ignore[arg-type]
            preflight_digest=str(data.get("preflight_digest") or ""),
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-apparatus-receipt-v1",
                "study_id": self.study_id,
                "apparatus_sha": self.apparatus_sha,
                "apparatus_tree": self.apparatus_tree,
                "protocol_digest": self.protocol_digest,
                "evaluator_runtime_digest": self.evaluator_runtime_digest,
                "subject_runtime_contract_digest": self.subject_runtime_contract_digest,
                "scorer_digest": self.scorer_digest,
                "projection_digest": self.projection_digest,
                "reporting_digest": self.reporting_digest,
                "memory_fixture_digest": self.memory_fixture_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_mode": self.execution_mode,
                "preflight_digest": self.preflight_digest,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2WorkspaceTreatmentBinding:
    """Digest-bound per-run treatment identity and direct-mode proof."""

    study_id: str
    arm: Exp2Arm
    stage: Exp2StageName
    case_id: str
    apparatus_digest: str
    protocol_digest: str
    subject_sha: str
    subject_tree: str
    evaluator_runtime_id: str
    subject_runtime_digest: str
    model: str
    reasoning_effort: str
    max_attempts: int
    timeout_seconds: int
    case_concurrency: int
    visible_verifier_command: str
    memory_digest: str
    operator_role_skill_digest: str
    execution_role_skill_digest: str
    execution_mode: Exp2ExecutionMode
    task_worktree_root: str
    output_root: str
    opaque_budget_slot_ids: tuple[str, ...]
    revision: int
    allowlist_digest: str
    public_evidence_cutoff: str
    direct_sdk_in_process: bool
    outer_bubblewrap: bool
    parent_candidate_sha: str | None = None
    candidate_diff_digest: str | None = None
    treatment_locked: bool = False

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        safe_component(self.case_id, "case_id")
        _digest(self.apparatus_digest, "apparatus_digest")
        _digest(self.protocol_digest, "protocol_digest")
        _git_sha(self.subject_sha, "subject_sha")
        _git_sha(self.subject_tree, "subject_tree")
        _digest(self.subject_runtime_digest, "subject_runtime_digest")
        _digest(self.memory_digest, "memory_digest")
        _digest(self.operator_role_skill_digest, "operator_role_skill_digest")
        _digest(self.execution_role_skill_digest, "execution_role_skill_digest")
        _digest(self.allowlist_digest, "allowlist_digest")
        _path(self.task_worktree_root, "task_worktree_root")
        _path(self.output_root, "output_root")
        if Path(self.task_worktree_root).resolve() == Path(self.output_root).resolve():
            raise Exp2ContractError("Exp2 task worktree and output root must differ")
        if self.execution_mode not in {"protected", "workspace_only"}:
            raise Exp2ContractError("unsupported Exp2 treatment execution mode")
        if self.execution_mode == "workspace_only" and (
            not self.direct_sdk_in_process or self.outer_bubblewrap
        ):
            raise Exp2ContractError(
                "workspace-only treatment must be direct SDK and Bubblewrap-free"
            )
        if self.execution_mode == "protected" and not self.outer_bubblewrap:
            raise Exp2ContractError("protected treatment must retain outer Bubblewrap")
        if self.max_attempts != 2 or self.timeout_seconds != 900:
            raise Exp2ContractError("Exp2 treatment runtime budget drift")
        if self.case_concurrency != 1:
            raise Exp2ContractError("Exp2 treatment case concurrency must be one")
        if (
            not self.evaluator_runtime_id.strip()
            or not self.visible_verifier_command.strip()
        ):
            raise Exp2ContractError("Exp2 treatment runtime identity is incomplete")
        for slot in self.opaque_budget_slot_ids:
            if not slot.startswith("exp2-budget-slot-"):
                raise Exp2ContractError("Exp2 budget slot escaped opaque namespace")
        if self.arm == "H0":
            if (
                self.revision != 0
                or self.parent_candidate_sha
                or self.candidate_diff_digest
            ):
                raise Exp2ContractError(
                    "H0 treatment cannot carry candidate revision state"
                )
        else:
            if self.revision not in {1, 2}:
                raise Exp2ContractError("H1 treatment revision must be one or two")
            _git_sha(self.parent_candidate_sha, "parent_candidate_sha")
            _digest(self.candidate_diff_digest, "candidate_diff_digest")
        if self.treatment_locked and self.arm != "H1":
            raise Exp2ContractError("only H1 can be treatment-locked")
        if not self.public_evidence_cutoff.strip():
            raise Exp2ContractError("Exp2 public evidence cutoff is required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2WorkspaceTreatmentBinding:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "arm",
                "stage",
                "case_id",
                "apparatus_digest",
                "protocol_digest",
                "subject_sha",
                "subject_tree",
                "evaluator_runtime_id",
                "subject_runtime_digest",
                "model",
                "reasoning_effort",
                "max_attempts",
                "timeout_seconds",
                "case_concurrency",
                "visible_verifier_command",
                "memory_digest",
                "operator_role_skill_digest",
                "execution_role_skill_digest",
                "execution_mode",
                "task_worktree_root",
                "output_root",
                "opaque_budget_slot_ids",
                "revision",
                "allowlist_digest",
                "public_evidence_cutoff",
                "direct_sdk_in_process",
                "outer_bubblewrap",
                "parent_candidate_sha",
                "candidate_diff_digest",
                "treatment_locked",
                "record_digest",
            },
            "Exp2 workspace treatment binding",
        )
        if data.get("schema") != "autobugfix-exp2-workspace-treatment-binding-v1":
            raise Exp2ContractError(
                "unsupported Exp2 workspace treatment binding schema"
            )
        for field_name in (
            "direct_sdk_in_process",
            "outer_bubblewrap",
            "treatment_locked",
        ):
            if type(data.get(field_name)) is not bool:
                raise Exp2ContractError(
                    f"Exp2 binding field {field_name} must be boolean"
                )
        return cls(
            study_id=str(data.get("study_id") or ""),
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            case_id=str(data.get("case_id") or ""),
            apparatus_digest=str(data.get("apparatus_digest") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            subject_sha=str(data.get("subject_sha") or ""),
            subject_tree=str(data.get("subject_tree") or ""),
            evaluator_runtime_id=str(data.get("evaluator_runtime_id") or ""),
            subject_runtime_digest=str(data.get("subject_runtime_digest") or ""),
            model=str(data.get("model") or ""),
            reasoning_effort=str(data.get("reasoning_effort") or ""),
            max_attempts=int(data.get("max_attempts") or 0),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
            case_concurrency=int(data.get("case_concurrency") or 0),
            visible_verifier_command=str(data.get("visible_verifier_command") or ""),
            memory_digest=str(data.get("memory_digest") or ""),
            operator_role_skill_digest=str(
                data.get("operator_role_skill_digest") or ""
            ),
            execution_role_skill_digest=str(
                data.get("execution_role_skill_digest") or ""
            ),
            execution_mode=str(data.get("execution_mode") or ""),  # type: ignore[arg-type]
            task_worktree_root=str(data.get("task_worktree_root") or ""),
            output_root=str(data.get("output_root") or ""),
            opaque_budget_slot_ids=_tuple_strings(
                data.get("opaque_budget_slot_ids") or (),
                "opaque_budget_slot_ids",
            ),
            revision=int(data.get("revision") or 0),
            allowlist_digest=str(data.get("allowlist_digest") or ""),
            public_evidence_cutoff=str(data.get("public_evidence_cutoff") or ""),
            direct_sdk_in_process=data["direct_sdk_in_process"],
            outer_bubblewrap=data["outer_bubblewrap"],
            parent_candidate_sha=(
                str(data["parent_candidate_sha"])
                if data.get("parent_candidate_sha") is not None
                else None
            ),
            candidate_diff_digest=(
                str(data["candidate_diff_digest"])
                if data.get("candidate_diff_digest") is not None
                else None
            ),
            treatment_locked=data["treatment_locked"],
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-workspace-treatment-binding-v1",
                "study_id": self.study_id,
                "arm": self.arm,
                "stage": self.stage,
                "case_id": self.case_id,
                "apparatus_digest": self.apparatus_digest,
                "protocol_digest": self.protocol_digest,
                "subject_sha": self.subject_sha,
                "subject_tree": self.subject_tree,
                "evaluator_runtime_id": self.evaluator_runtime_id,
                "subject_runtime_digest": self.subject_runtime_digest,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "max_attempts": self.max_attempts,
                "timeout_seconds": self.timeout_seconds,
                "case_concurrency": self.case_concurrency,
                "visible_verifier_command": self.visible_verifier_command,
                "memory_digest": self.memory_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_role_skill_digest": self.execution_role_skill_digest,
                "execution_mode": self.execution_mode,
                "task_worktree_root": self.task_worktree_root,
                "output_root": self.output_root,
                "opaque_budget_slot_ids": list(self.opaque_budget_slot_ids),
                "revision": self.revision,
                "allowlist_digest": self.allowlist_digest,
                "public_evidence_cutoff": self.public_evidence_cutoff,
                "direct_sdk_in_process": self.direct_sdk_in_process,
                "outer_bubblewrap": self.outer_bubblewrap,
                "parent_candidate_sha": self.parent_candidate_sha,
                "candidate_diff_digest": self.candidate_diff_digest,
                "treatment_locked": self.treatment_locked,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2HoldoutBurnRecord:
    """Irreversible marker for any pre-lock Holdout exposure."""

    study_id: str
    reason: str
    exposure_evidence_digest: str
    h1_change_forbidden: bool = True
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        if not self.reason.strip():
            raise Exp2ContractError("Exp2 Holdout burn reason is required")
        _digest(self.exposure_evidence_digest, "exposure_evidence_digest")
        if self.h1_change_forbidden is not True:
            raise Exp2ContractError("Exp2 Holdout burn must forbid further H1 changes")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2HoldoutBurnRecord:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "reason",
                "exposure_evidence_digest",
                "h1_change_forbidden",
                "created_at",
                "record_digest",
            },
            "Exp2 Holdout burn",
        )
        if data.get("schema") != "autobugfix-exp2-holdout-burn-v1":
            raise Exp2ContractError("unsupported Exp2 Holdout burn schema")
        if type(data.get("h1_change_forbidden")) is not bool:
            raise Exp2ContractError("Exp2 Holdout burn flag must be boolean")
        return cls(
            study_id=str(data.get("study_id") or ""),
            reason=str(data.get("reason") or ""),
            exposure_evidence_digest=str(data.get("exposure_evidence_digest") or ""),
            h1_change_forbidden=data["h1_change_forbidden"],
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-holdout-burn-v1",
                "study_id": self.study_id,
                "reason": self.reason,
                "exposure_evidence_digest": self.exposure_evidence_digest,
                "h1_change_forbidden": self.h1_change_forbidden,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2StudyPlan:
    study_id: str
    calibration_protocol_path: str
    public_manifest_path: str
    h0_binding_path: str
    candidate_binding_path: str
    calibration_case_ids: tuple[str, ...]
    public_case_ids: tuple[str, ...]
    cohort_audit_path: str
    policy_path: str
    apparatus_receipt_path: str
    empty_memory_fixture_path: str
    execution_mode: Exp2ExecutionMode = "protected"
    disposable_root: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        if self.schema_version != 1:
            raise Exp2ContractError("unsupported Exp2 plan schema")
        _path(self.calibration_protocol_path, "calibration_protocol_path")
        _path(self.public_manifest_path, "public_manifest_path")
        _path(self.h0_binding_path, "h0_binding_path")
        _path(self.candidate_binding_path, "candidate_binding_path")
        for field_name in (
            "cohort_audit_path",
            "policy_path",
            "apparatus_receipt_path",
            "empty_memory_fixture_path",
        ):
            _path(getattr(self, field_name), field_name)
        if len(self.calibration_case_ids) not in {2, 3}:
            raise Exp2ContractError("Exp2 calibration cohort must contain 2 or 3 cases")
        if len(self.public_case_ids) != PUBLIC_CASE_COUNTS[16]:
            raise Exp2ContractError("Exp2 public cohort must contain exactly 10 cases")
        if len(set(self.calibration_case_ids)) != len(self.calibration_case_ids):
            raise Exp2ContractError("Exp2 calibration cases must be unique")
        if len(set(self.public_case_ids)) != len(self.public_case_ids):
            raise Exp2ContractError("Exp2 public cases must be unique")
        if set(self.calibration_case_ids) & set(self.public_case_ids):
            raise Exp2ContractError(
                "Exp2 calibration and public cases must be disjoint"
            )
        if self.execution_mode not in {"protected", "workspace_only"}:
            raise Exp2ContractError("unsupported Exp2 execution mode")
        if self.execution_mode == "workspace_only":
            if self.disposable_root is None:
                raise Exp2ContractError(
                    "workspace-only Exp2 plans require a disposable root"
                )
            _path(self.disposable_root, "disposable_root")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2StudyPlan:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "schema_version",
                "study_id",
                "calibration_protocol_path",
                "public_manifest_path",
                "h0_binding_path",
                "candidate_binding_path",
                "calibration_case_ids",
                "public_case_ids",
                "execution_mode",
                "disposable_root",
                "cohort_audit_path",
                "policy_path",
                "apparatus_receipt_path",
                "empty_memory_fixture_path",
                "record_digest",
            },
            "Exp2 study plan",
        )
        if data.get("schema") != "autobugfix-exp2-study-plan-v1":
            raise Exp2ContractError("unsupported Exp2 study plan schema")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            study_id=str(data.get("study_id") or ""),
            calibration_protocol_path=str(data.get("calibration_protocol_path") or ""),
            public_manifest_path=str(data.get("public_manifest_path") or ""),
            h0_binding_path=str(data.get("h0_binding_path") or ""),
            candidate_binding_path=str(data.get("candidate_binding_path") or ""),
            calibration_case_ids=_tuple_strings(
                data.get("calibration_case_ids") or (), "calibration_case_ids"
            ),
            public_case_ids=_tuple_strings(
                data.get("public_case_ids") or (), "public_case_ids"
            ),
            execution_mode=str(data.get("execution_mode") or "protected"),  # type: ignore[arg-type]
            disposable_root=(
                str(data["disposable_root"])
                if data.get("disposable_root") is not None
                else None
            ),
            cohort_audit_path=(
                str(data["cohort_audit_path"])
                if data.get("cohort_audit_path") is not None
                else None
            ),
            policy_path=(
                str(data["policy_path"])
                if data.get("policy_path") is not None
                else None
            ),
            apparatus_receipt_path=(
                str(data["apparatus_receipt_path"])
                if data.get("apparatus_receipt_path") is not None
                else None
            ),
            empty_memory_fixture_path=(
                str(data["empty_memory_fixture_path"])
                if data.get("empty_memory_fixture_path") is not None
                else None
            ),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> Exp2StudyPlan:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2ContractError("Exp2 plan must be a mapping")
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-study-plan-v1",
                "schema_version": self.schema_version,
                "study_id": self.study_id,
                "calibration_protocol_path": self.calibration_protocol_path,
                "public_manifest_path": self.public_manifest_path,
                "h0_binding_path": self.h0_binding_path,
                "candidate_binding_path": self.candidate_binding_path,
                "calibration_case_ids": list(self.calibration_case_ids),
                "public_case_ids": list(self.public_case_ids),
                "execution_mode": self.execution_mode,
                "disposable_root": self.disposable_root,
                "cohort_audit_path": self.cohort_audit_path,
                "policy_path": self.policy_path,
                "apparatus_receipt_path": self.apparatus_receipt_path,
                "empty_memory_fixture_path": self.empty_memory_fixture_path,
            }
        )

    @property
    def plan_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


def validate_wave_schedule(
    public_case_ids: Sequence[str],
) -> dict[int, Exp2BudgetAllocation]:
    values = tuple(safe_component(item, "public_case_ids") for item in public_case_ids)
    if len(values) != PUBLIC_CASE_COUNTS[16] or len(set(values)) != len(values):
        raise Exp2ContractError("Exp2 public schedule must contain 10 unique cases")
    return {
        wave: Exp2BudgetAllocation(wave, values[:count])
        for wave, count in PUBLIC_CASE_COUNTS.items()
    }


def projection_digest(projection: Exp2ResultProjection) -> str:
    return str(projection.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2PairedPublicSummary:
    study_id: str
    case_ids: tuple[str, ...]
    h0_resolved_count: int
    h1_resolved_count: int
    h0_harness_error_count: int
    h1_harness_error_count: int
    h1_minus_h0_resolved: int
    pair_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        if len(self.case_ids) != PUBLIC_CASE_COUNTS[16]:
            raise Exp2ContractError("paired public summary requires 10 cases")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise Exp2ContractError("paired public summary case IDs must be unique")
        if any(value < 0 for value in self.pair_counts.values()):
            raise Exp2ContractError("paired public summary counts must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-paired-public-summary-v1",
                "study_id": self.study_id,
                "case_ids": list(self.case_ids),
                "h0_resolved_count": self.h0_resolved_count,
                "h1_resolved_count": self.h1_resolved_count,
                "h0_harness_error_count": self.h0_harness_error_count,
                "h1_harness_error_count": self.h1_harness_error_count,
                "h1_minus_h0_resolved": self.h1_minus_h0_resolved,
                "rescue_count": self.rescue_count,
                "regression_count": self.regression_count,
                "invalid_count": self.invalid_count,
                "pair_counts": dict(self.pair_counts),
            }
        )

    @property
    def rescue_count(self) -> int:
        return int(self.pair_counts.get("h0_unresolved_h1_resolved", 0))

    @property
    def regression_count(self) -> int:
        return int(self.pair_counts.get("h0_resolved_h1_unresolved", 0))

    @property
    def invalid_count(self) -> int:
        return self.h0_harness_error_count + self.h1_harness_error_count


@dataclass(slots=True, frozen=True)
class Exp2PublicRegressionGate:
    """Treatment-lock receipt for the public engineering regression gate."""

    study_id: str
    paired_public_digest: str
    h1_subject_sha: str
    h1_binding_digest: str
    full_check_digest: str
    holdout_exposure_audit_digest: str
    revision_count: int
    h1_regression_count: int
    h1_minus_h0_resolved: int
    passed: bool
    treatment_locked: bool
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _digest(self.paired_public_digest, "paired_public_digest")
        _git_sha(self.h1_subject_sha, "h1_subject_sha")
        _digest(self.h1_binding_digest, "h1_binding_digest")
        _digest(self.full_check_digest, "full_check_digest")
        _digest(
            self.holdout_exposure_audit_digest,
            "holdout_exposure_audit_digest",
        )
        if self.revision_count != 2:
            raise Exp2ContractError(
                "Exp2 treatment lock requires exactly two H1 revisions"
            )
        if self.h1_regression_count < 0:
            raise Exp2ContractError("Exp2 regression count must be non-negative")
        if type(self.passed) is not bool or type(self.treatment_locked) is not bool:
            raise Exp2ContractError("Exp2 regression gate booleans are invalid")
        if self.treatment_locked is not self.passed:
            raise Exp2ContractError(
                "Exp2 treatment lock must agree with the public regression gate"
            )
        if self.passed and (
            self.h1_regression_count != 0 or self.h1_minus_h0_resolved < 0
        ):
            raise Exp2ContractError(
                "a passed Exp2 gate requires zero regressions and non-negative paired gain"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2PublicRegressionGate:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "paired_public_digest",
                "h1_subject_sha",
                "h1_binding_digest",
                "full_check_digest",
                "holdout_exposure_audit_digest",
                "revision_count",
                "h1_regression_count",
                "h1_minus_h0_resolved",
                "passed",
                "treatment_locked",
                "created_at",
                "record_digest",
            },
            "Exp2 public regression gate",
        )
        if data.get("schema") != "autobugfix-exp2-public-regression-gate-v1":
            raise Exp2ContractError("unsupported Exp2 public regression gate schema")
        for field_name in ("passed", "treatment_locked"):
            if type(data.get(field_name)) is not bool:
                raise Exp2ContractError(
                    f"Exp2 public regression gate field {field_name} must be boolean"
                )
        return cls(
            study_id=str(data.get("study_id") or ""),
            paired_public_digest=str(data.get("paired_public_digest") or ""),
            h1_subject_sha=str(data.get("h1_subject_sha") or ""),
            h1_binding_digest=str(data.get("h1_binding_digest") or ""),
            full_check_digest=str(data.get("full_check_digest") or ""),
            holdout_exposure_audit_digest=str(
                data.get("holdout_exposure_audit_digest") or ""
            ),
            revision_count=int(data.get("revision_count") or 0),
            h1_regression_count=int(data.get("h1_regression_count") or 0),
            h1_minus_h0_resolved=int(data.get("h1_minus_h0_resolved") or 0),
            passed=data["passed"],
            treatment_locked=data["treatment_locked"],
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-public-regression-gate-v1",
                "study_id": self.study_id,
                "paired_public_digest": self.paired_public_digest,
                "h1_subject_sha": self.h1_subject_sha,
                "h1_binding_digest": self.h1_binding_digest,
                "full_check_digest": self.full_check_digest,
                "holdout_exposure_audit_digest": self.holdout_exposure_audit_digest,
                "revision_count": self.revision_count,
                "h1_regression_count": self.h1_regression_count,
                "h1_minus_h0_resolved": self.h1_minus_h0_resolved,
                "passed": self.passed,
                "treatment_locked": self.treatment_locked,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


@dataclass(slots=True, frozen=True)
class Exp2SealedAggregate:
    """Guard-released aggregate; sealed identities and per-case labels are absent."""

    study_id: str
    treatment_lock_digest: str
    guard_metric_digest: str
    h0_resolved_count: int
    h1_resolved_count: int
    rescue_count: int
    regression_count: int
    invalid_count: int
    fixed_denominator: int = 6
    limitation_text: str = (
        "Six repository-unique Holdout cases are descriptive evidence only; "
        "zero observed regressions is not a population guarantee."
    )
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        safe_component(self.study_id, "study_id")
        _digest(self.treatment_lock_digest, "treatment_lock_digest")
        _digest(self.guard_metric_digest, "guard_metric_digest")
        if self.fixed_denominator != 6:
            raise Exp2ContractError("Exp2 sealed aggregate denominator must be six")
        for field_name in (
            "h0_resolved_count",
            "h1_resolved_count",
            "rescue_count",
            "regression_count",
            "invalid_count",
        ):
            value = getattr(self, field_name)
            if value < 0 or value > self.fixed_denominator:
                raise Exp2ContractError(
                    f"Exp2 sealed aggregate {field_name} is invalid"
                )
        if not self.limitation_text.strip():
            raise Exp2ContractError("Exp2 sealed aggregate limitation text is required")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Exp2SealedAggregate:
        _verify_exp2_record(data)
        _only_fields(
            data,
            {
                "schema",
                "study_id",
                "treatment_lock_digest",
                "guard_metric_digest",
                "h0_resolved_count",
                "h1_resolved_count",
                "rescue_count",
                "regression_count",
                "invalid_count",
                "fixed_denominator",
                "limitation_text",
                "created_at",
                "record_digest",
            },
            "Exp2 sealed aggregate",
        )
        if data.get("schema") != "autobugfix-exp2-sealed-aggregate-v1":
            raise Exp2ContractError("unsupported Exp2 sealed aggregate schema")
        return cls(
            study_id=str(data.get("study_id") or ""),
            treatment_lock_digest=str(data.get("treatment_lock_digest") or ""),
            guard_metric_digest=str(data.get("guard_metric_digest") or ""),
            h0_resolved_count=int(data.get("h0_resolved_count") or 0),
            h1_resolved_count=int(data.get("h1_resolved_count") or 0),
            rescue_count=int(data.get("rescue_count") or 0),
            regression_count=int(data.get("regression_count") or 0),
            invalid_count=int(data.get("invalid_count") or 0),
            fixed_denominator=int(data.get("fixed_denominator") or 0),
            limitation_text=str(data.get("limitation_text") or ""),
            created_at=str(data.get("created_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-sealed-aggregate-v1",
                "study_id": self.study_id,
                "treatment_lock_digest": self.treatment_lock_digest,
                "guard_metric_digest": self.guard_metric_digest,
                "h0_resolved_count": self.h0_resolved_count,
                "h1_resolved_count": self.h1_resolved_count,
                "rescue_count": self.rescue_count,
                "regression_count": self.regression_count,
                "invalid_count": self.invalid_count,
                "fixed_denominator": self.fixed_denominator,
                "limitation_text": self.limitation_text,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])


def reduce_paired_public(
    h0_projections: Sequence[Mapping[str, Any] | Exp2ResultProjection],
    h1_projections: Sequence[Mapping[str, Any] | Exp2ResultProjection],
) -> Exp2PairedPublicSummary:
    def normalize(
        values: Sequence[Mapping[str, Any] | Exp2ResultProjection],
    ) -> tuple[Exp2ResultProjection, ...]:
        return tuple(
            value
            if isinstance(value, Exp2ResultProjection)
            else Exp2ResultProjection.from_dict(value)
            for value in values
        )

    h0 = normalize(h0_projections)
    h1 = normalize(h1_projections)
    if len(h0) != PUBLIC_CASE_COUNTS[16] or len(h1) != PUBLIC_CASE_COUNTS[16]:
        raise Exp2ContractError("paired public reduction requires two ten-case arms")
    if any(item.arm != "H0" or item.stage != "H0_PUBLIC" for item in h0):
        raise Exp2ContractError("paired H0 projections have the wrong arm or stage")
    if any(item.arm != "H1" or item.stage != "PUBLIC_REPLAY" for item in h1):
        raise Exp2ContractError("paired H1 projections have the wrong arm or stage")
    h0_by_case = {item.case_id: item for item in h0}
    h1_by_case = {item.case_id: item for item in h1}
    if len(h0_by_case) != len(h0) or len(h1_by_case) != len(h1):
        raise Exp2ContractError("paired public reduction contains duplicate cases")
    if set(h0_by_case) != set(h1_by_case):
        raise Exp2ContractError("paired public arms do not share the same cases")
    study_ids = {item.study_id for item in (*h0, *h1)}
    if len(study_ids) != 1:
        raise Exp2ContractError("paired public arms belong to different Studies")
    pair_counts: dict[str, int] = {}
    for case_id in h0_by_case:
        left = h0_by_case[case_id].public_label
        right = h1_by_case[case_id].public_label
        key = f"h0_{left}_h1_{right}"
        pair_counts[key] = pair_counts.get(key, 0) + 1
    h0_resolved = sum(item.resolved and not item.harness_error for item in h0)
    h1_resolved = sum(item.resolved and not item.harness_error for item in h1)
    return Exp2PairedPublicSummary(
        study_id=next(iter(study_ids)),
        case_ids=tuple(sorted(h0_by_case)),
        h0_resolved_count=h0_resolved,
        h1_resolved_count=h1_resolved,
        h0_harness_error_count=sum(item.harness_error for item in h0),
        h1_harness_error_count=sum(item.harness_error for item in h1),
        h1_minus_h0_resolved=h1_resolved - h0_resolved,
        pair_counts=pair_counts,
    )
