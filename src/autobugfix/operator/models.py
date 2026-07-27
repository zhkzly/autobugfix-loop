from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from autobugfix.models import utc_now

LayerName = Literal["execution", "memory", "eval", "operator", "shared_runtime", "docs_skills"]
RiskLevel = Literal["low", "medium", "high", "constitutional"]
Confidence = Literal["low", "medium", "high"]
ApprovalKind = Literal["reviewer", "human_signed", "github", "interactive"]
ApprovalStage = Literal["scope", "merge"]
ApprovalDecision = Literal["approve", "request_changes", "reject", "revoke"]
RequestPhase = Literal["REQUESTED", "ACTIVE", "VERIFIED", "CLOSED"]
WriterRunStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"]
CheckRunStatus = Literal["PENDING", "RUNNING", "PASSED", "FAILED", "ERROR", "CANCELLED"]
GateStatus = Literal["PASS", "FAIL", "PENDING", "SKIPPED", "STALE"]
ScopeRevisionStatus = Literal["PROPOSED", "APPROVED", "REJECTED", "SUPERSEDED"]
ExperimentStatus = Literal["CREATED", "RUNNING", "COMPLETED", "FAILED", "CLOSED"]
PromotionStatus = Literal["PREPARED", "PR_OPEN", "MERGED", "CANARY", "ACTIVE", "FAILED", "ROLLED_BACK"]
ExperimentLineStatus = Literal["OPEN", "CLOSED"]
IntegrationKind = Literal["CANDIDATE", "ROLLBACK", "RECONCILIATION"]
CheckpointName = Literal["H0", "H_bug", "H_general"]
UsageStatus = Literal["RESERVED", "COMPLETED", "INDETERMINATE"]
StudyMetricKind = Literal["BASELINE", "CANDIDATE"]

VALID_LAYERS: tuple[str, ...] = ("execution", "memory", "eval", "operator", "shared_runtime", "docs_skills")
VALID_RISKS: tuple[str, ...] = ("low", "medium", "high", "constitutional")
VALID_CONFIDENCE: tuple[str, ...] = ("low", "medium", "high")
VALID_APPROVAL_KINDS: tuple[str, ...] = ("reviewer", "human_signed", "github", "interactive")
VALID_APPROVAL_STAGES: tuple[str, ...] = ("scope", "merge")
VALID_APPROVAL_DECISIONS: tuple[str, ...] = ("approve", "request_changes", "reject", "revoke")
VALID_EXPERIMENT_LINE_STATUSES: tuple[str, ...] = ("OPEN", "CLOSED")
VALID_INTEGRATION_KINDS: tuple[str, ...] = ("CANDIDATE", "ROLLBACK", "RECONCILIATION")
VALID_CHECKPOINT_NAMES: tuple[str, ...] = ("H0", "H_bug", "H_general")
VALID_BUDGET_WAVES: tuple[int, ...] = (3, 8, 16)
VALID_USAGE_STATUSES: tuple[str, ...] = ("RESERVED", "COMPLETED", "INDETERMINATE")
VALID_STUDY_METRIC_KINDS: tuple[str, ...] = ("BASELINE", "CANDIDATE")


class OperatorModelError(ValueError):
    pass


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_payload(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _strings(value: Any, name: str, *, required: bool = False) -> tuple[str, ...]:
    if value is None:
        items: tuple[str, ...] = ()
    elif isinstance(value, (list, tuple)):
        items = tuple(str(item) for item in value if str(item).strip())
    else:
        raise OperatorModelError(f"{name} must be a list")
    if required and not items:
        raise OperatorModelError(f"{name} must not be empty")
    return items


def _choice(value: str, choices: tuple[str, ...], name: str) -> str:
    if value not in choices:
        raise OperatorModelError(f"{name} must be one of {', '.join(choices)}")
    return value


def _required(value: str, name: str) -> str:
    if not value.strip():
        raise OperatorModelError(f"{name} is required")
    return value


def is_expired(timestamp: str | None, now: datetime | None = None) -> bool:
    if not timestamp:
        return False
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return value <= (now or datetime.now(UTC))


@dataclass(slots=True, frozen=True)
class OperatorTriage:
    triage_id: str
    summary: str
    suspected_layers: tuple[str, ...]
    evidence: tuple[str, ...]
    creator: str
    confidence: str = "low"
    next_actions: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _required(self.triage_id, "triage_id")
        _required(self.summary, "summary")
        _required(self.creator, "creator")
        if not self.suspected_layers:
            raise OperatorModelError("suspected_layers must not be empty")
        if not self.evidence:
            raise OperatorModelError("evidence must not be empty")
        for layer in self.suspected_layers:
            _choice(layer, VALID_LAYERS, "suspected_layers")
        _choice(self.confidence, VALID_CONFIDENCE, "confidence")

    def payload(self) -> dict[str, Any]:
        return {
            "triage_id": self.triage_id,
            "summary": self.summary,
            "suspected_layers": list(self.suspected_layers),
            "evidence": list(self.evidence),
            "creator": self.creator,
            "confidence": self.confidence,
            "next_actions": list(self.next_actions),
            "created_at": self.created_at,
        }

    @property
    def triage_digest(self) -> str:
        return digest_payload(self.payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "triage_digest": self.triage_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperatorTriage":
        item = cls(
            triage_id=str(data["triage_id"]),
            summary=str(data["summary"]),
            suspected_layers=_strings(data.get("suspected_layers"), "suspected_layers", required=True),
            evidence=_strings(data.get("evidence"), "evidence", required=True),
            creator=str(data.get("creator") or "unknown"),
            confidence=str(data.get("confidence", "low")),
            next_actions=_strings(data.get("next_actions"), "next_actions"),
            created_at=str(data.get("created_at") or utc_now()),
        )
        stored = data.get("triage_digest")
        if stored and stored != item.triage_digest:
            raise OperatorModelError("triage digest mismatch")
        return item


@dataclass(slots=True, frozen=True)
class OperatorRequest:
    request_id: str
    summary: str
    primary_layer: str
    triage_id: str
    triage_digest: str
    evidence: tuple[str, ...]
    validation_profiles: tuple[str, ...]
    branch: str
    base_sha: str
    creator: str
    secondary_layers: tuple[str, ...] = ()
    requested_risk: str = "low"
    performance_baseline: str | None = None
    planned_paths: tuple[str, ...] = ()
    constitution_digest: str | None = None
    experiment_line_id: str | None = None
    experiment_line_generation: int | None = None
    budget_grant_id: str | None = None
    budget_grant_digest: str | None = None
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    persisted_request_digest: str | None = field(default=None, repr=False, compare=False)
    persisted_request_digest_schema: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.summary, "summary"),
            (self.triage_id, "triage_id"),
            (self.triage_digest, "triage_digest"),
            (self.branch, "branch"),
            (self.base_sha, "base_sha"),
            (self.creator, "creator"),
        ):
            _required(value, name)
        _choice(self.primary_layer, VALID_LAYERS, "primary_layer")
        _choice(self.requested_risk, VALID_RISKS, "requested_risk")
        for layer in self.secondary_layers:
            _choice(layer, VALID_LAYERS, "secondary_layers")
        if self.primary_layer in self.secondary_layers:
            raise OperatorModelError("primary_layer must not be repeated in secondary_layers")
        if not self.evidence:
            raise OperatorModelError("evidence must not be empty")
        if not self.validation_profiles:
            raise OperatorModelError("validation_profiles must not be empty")
        if not self.planned_paths:
            raise OperatorModelError("planned_paths must not be empty")
        for path in self.planned_paths:
            _required(path, "planned_paths")
            normalized = path.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise OperatorModelError(f"planned path must be repository-relative: {path!r}")
        if self.expires_at:
            datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if bool(self.experiment_line_id) != (self.experiment_line_generation is not None):
            raise OperatorModelError(
                "experiment_line_id and experiment_line_generation must be provided together"
            )
        if self.experiment_line_generation is not None and self.experiment_line_generation < 0:
            raise OperatorModelError("experiment_line_generation must not be negative")
        if bool(self.budget_grant_id) != bool(self.budget_grant_digest):
            raise OperatorModelError(
                "budget_grant_id and budget_grant_digest must be provided together"
            )
        if self.budget_grant_id and not self.experiment_line_id:
            raise OperatorModelError("budget grant binding requires an experiment line")
        if self.experiment_line_id and not self.budget_grant_id:
            raise OperatorModelError("experiment line binding requires a budget grant")

    @property
    def declared_layers(self) -> set[str]:
        return {self.primary_layer, *self.secondary_layers}

    def payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "summary": self.summary,
            "primary_layer": self.primary_layer,
            "secondary_layers": list(self.secondary_layers),
            "requested_risk": self.requested_risk,
            "triage_id": self.triage_id,
            "triage_digest": self.triage_digest,
            "evidence": list(self.evidence),
            "validation_profiles": list(self.validation_profiles),
            "performance_baseline": self.performance_baseline,
            "planned_paths": list(self.planned_paths),
            "constitution_digest": self.constitution_digest,
            "experiment_line_id": self.experiment_line_id,
            "experiment_line_generation": self.experiment_line_generation,
            "budget_grant_id": self.budget_grant_id,
            "budget_grant_digest": self.budget_grant_digest,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "creator": self.creator,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }

    def _request_digest_payload(self) -> dict[str, Any]:
        payload = self.payload()
        schema = self.persisted_request_digest_schema
        if schema == "without_budget_binding":
            payload.pop("budget_grant_id")
            payload.pop("budget_grant_digest")
        elif schema == "without_experiment_line":
            for key in (
                "experiment_line_id",
                "experiment_line_generation",
                "budget_grant_id",
                "budget_grant_digest",
            ):
                payload.pop(key)
        elif schema not in {None, "current"}:
            raise OperatorModelError(f"unsupported request digest schema: {schema}")
        return payload

    @property
    def request_digest(self) -> str:
        return digest_payload(self._request_digest_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "request_digest": self.request_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperatorRequest":
        risk = str(data.get("requested_risk", data.get("risk", "low")))
        if risk == "architecture":
            risk = "constitutional"
        item = cls(
            request_id=str(data["request_id"]),
            summary=str(data["summary"]),
            primary_layer=str(data["primary_layer"]),
            secondary_layers=_strings(data.get("secondary_layers"), "secondary_layers"),
            requested_risk=risk,
            triage_id=str(data["triage_id"]),
            triage_digest=str(data["triage_digest"]),
            evidence=_strings(data.get("evidence"), "evidence", required=True),
            validation_profiles=_strings(data.get("validation_profiles"), "validation_profiles", required=True),
            performance_baseline=data.get("performance_baseline"),
            planned_paths=_strings(data.get("planned_paths"), "planned_paths"),
            constitution_digest=data.get("constitution_digest"),
            experiment_line_id=data.get("experiment_line_id"),
            experiment_line_generation=(
                int(data["experiment_line_generation"])
                if data.get("experiment_line_generation") is not None
                else None
            ),
            budget_grant_id=data.get("budget_grant_id"),
            budget_grant_digest=data.get("budget_grant_digest"),
            branch=str(data["branch"]),
            base_sha=str(data["base_sha"]),
            creator=str(data["creator"]),
            expires_at=data.get("expires_at"),
            created_at=str(data.get("created_at") or utc_now()),
        )
        stored = data.get("request_digest")
        if stored:
            computed = digest_payload(item.payload())
            pre_budget = item.payload()
            pre_budget.pop("budget_grant_id")
            pre_budget.pop("budget_grant_digest")
            legacy = item.payload()
            for key in (
                "experiment_line_id",
                "experiment_line_generation",
                "budget_grant_id",
                "budget_grant_digest",
            ):
                legacy.pop(key)
            if stored == computed:
                schema = "current"
            elif item.budget_grant_id is None and stored == digest_payload(pre_budget):
                schema = "without_budget_binding"
            elif (
                item.experiment_line_id is None
                and item.budget_grant_id is None
                and stored == digest_payload(legacy)
            ):
                schema = "without_experiment_line"
            else:
                raise OperatorModelError("request digest mismatch")
            object.__setattr__(item, "persisted_request_digest", str(stored))
            object.__setattr__(item, "persisted_request_digest_schema", schema)
        return item


@dataclass(slots=True, frozen=True)
class OperatorApproval:
    approval_id: str
    request_id: str
    request_digest: str
    base_sha: str
    approver: str
    kind: str
    stage: str
    decision: str
    reason: str
    allowed_layers: tuple[str, ...]
    scope_version: int = 1
    allowed_paths: tuple[str, ...] = ()
    expires_at: str | None = None
    patch_digest: str | None = None
    head_sha: str | None = None
    proof: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.approval_id, "approval_id"),
            (self.request_id, "request_id"),
            (self.request_digest, "request_digest"),
            (self.base_sha, "base_sha"),
            (self.approver, "approver"),
            (self.reason, "reason"),
        ):
            _required(value, name)
        _choice(self.kind, VALID_APPROVAL_KINDS, "kind")
        _choice(self.stage, VALID_APPROVAL_STAGES, "stage")
        _choice(self.decision, VALID_APPROVAL_DECISIONS, "decision")
        if not self.allowed_layers:
            raise OperatorModelError("allowed_layers must not be empty")
        if self.scope_version < 1:
            raise OperatorModelError("scope_version must be positive")
        for layer in self.allowed_layers:
            _choice(layer, VALID_LAYERS, "allowed_layers")

    @property
    def human_verified_kind(self) -> bool:
        return self.kind in {"human_signed", "github"}

    @property
    def approved(self) -> bool:
        return self.decision == "approve" and not is_expired(self.expires_at)

    def payload(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "base_sha": self.base_sha,
            "approver": self.approver,
            "kind": self.kind,
            "stage": self.stage,
            "decision": self.decision,
            "reason": self.reason,
            "allowed_layers": list(self.allowed_layers),
            "scope_version": self.scope_version,
            "allowed_paths": list(self.allowed_paths),
            "expires_at": self.expires_at,
            "patch_digest": self.patch_digest,
            "head_sha": self.head_sha,
            "proof": self.proof,
            "created_at": self.created_at,
        }

    @property
    def approval_digest(self) -> str:
        return digest_payload(self.payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "approval_digest": self.approval_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperatorApproval":
        item = cls(
            approval_id=str(data["approval_id"]),
            request_id=str(data["request_id"]),
            request_digest=str(data["request_digest"]),
            base_sha=str(data["base_sha"]),
            approver=str(data["approver"]),
            kind=str(data["kind"]),
            stage=str(data.get("stage", "scope")),
            decision=str(data["decision"]),
            reason=str(data["reason"]),
            allowed_layers=_strings(data.get("allowed_layers"), "allowed_layers", required=True),
            scope_version=int(data.get("scope_version", 1)),
            allowed_paths=_strings(data.get("allowed_paths"), "allowed_paths"),
            expires_at=data.get("expires_at"),
            patch_digest=data.get("patch_digest"),
            head_sha=data.get("head_sha"),
            proof=dict(data.get("proof") or {}),
            created_at=str(data.get("created_at") or utc_now()),
        )
        stored = data.get("approval_digest")
        if stored and stored != item.approval_digest:
            raise OperatorModelError("approval digest mismatch")
        return item


@dataclass(slots=True, frozen=True)
class OperatorEvent:
    event_id: str
    request_id: str
    kind: str
    actor: str
    payload: dict[str, Any]
    previous_hash: str | None
    created_at: str = field(default_factory=utc_now)
    event_hash: str | None = None

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "kind": self.kind,
            "actor": self.actor,
            "payload": self.payload,
            "previous_hash": self.previous_hash,
            "created_at": self.created_at,
        }

    @property
    def computed_hash(self) -> str:
        return digest_payload(self.unsigned_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_payload(), "event_hash": self.computed_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperatorEvent":
        item = cls(
            event_id=str(data["event_id"]),
            request_id=str(data["request_id"]),
            kind=str(data["kind"]),
            actor=str(data["actor"]),
            payload=dict(data.get("payload") or {}),
            previous_hash=data.get("previous_hash"),
            created_at=str(data.get("created_at") or utc_now()),
            event_hash=data.get("event_hash"),
        )
        if item.event_hash and item.event_hash != item.computed_hash:
            raise OperatorModelError("operator event hash mismatch")
        return item


@dataclass(slots=True)
class OperatorProjection:
    request_id: str
    state: str = "REQUESTED"
    last_event_hash: str | None = None
    workspace_path: str | None = None
    patch_digest: str | None = None
    head_sha: str | None = None
    validation_id: str | None = None
    active_writer_run_id: str | None = None
    active_check_run_id: str | None = None
    scope_version: int = 1
    outcome: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "last_event_hash": self.last_event_hash,
            "workspace_path": self.workspace_path,
            "patch_digest": self.patch_digest,
            "head_sha": self.head_sha,
            "validation_id": self.validation_id,
            "active_writer_run_id": self.active_writer_run_id,
            "active_check_run_id": self.active_check_run_id,
            "scope_version": self.scope_version,
            "outcome": self.outcome,
            "blocked_by": self.blocked_by,
            "violations": self.violations,
        }


def _record_dict(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    return {**body, "record_digest": digest_payload(body)}


def _verify_record(data: Mapping[str, Any]) -> None:
    stored = data.get("record_digest")
    payload = {key: value for key, value in data.items() if key != "record_digest"}
    if stored and stored != digest_payload(payload):
        raise OperatorModelError("record digest mismatch")


def _verify_required_record(data: Mapping[str, Any]) -> None:
    if not data.get("record_digest"):
        raise OperatorModelError("record digest is required")
    _verify_record(data)


@dataclass(slots=True, frozen=True)
class WriterRun:
    run_id: str
    request_id: str
    attempt: int
    status: str
    role_digest: str
    scope_version: int
    input_digest: str
    base_sha: str
    candidate_before_head_sha: str | None = None
    candidate_before_patch_digest: str | None = None
    candidate_before_content_digest: str | None = None
    head_sha: str | None = None
    patch_digest: str | None = None
    candidate_after_content_digest: str | None = None
    staging_patch_digest: str | None = None
    application_artifact_id: str | None = None
    feedback_ids: tuple[str, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in {"QUEUED", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}:
            raise OperatorModelError(f"invalid writer run status: {self.status}")
        if self.attempt < 1:
            raise OperatorModelError("writer attempt must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "run_id": self.run_id,
            "request_id": self.request_id,
            "attempt": self.attempt,
            "status": self.status,
            "role_digest": self.role_digest,
            "scope_version": self.scope_version,
            "input_digest": self.input_digest,
            "base_sha": self.base_sha,
            "candidate_before_head_sha": self.candidate_before_head_sha,
            "candidate_before_patch_digest": self.candidate_before_patch_digest,
            "candidate_before_content_digest": self.candidate_before_content_digest,
            "head_sha": self.head_sha,
            "patch_digest": self.patch_digest,
            "candidate_after_content_digest": self.candidate_after_content_digest,
            "staging_patch_digest": self.staging_patch_digest,
            "application_artifact_id": self.application_artifact_id,
            "feedback_ids": list(self.feedback_ids),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WriterRun":
        _verify_record(data)
        return cls(
            run_id=str(data["run_id"]), request_id=str(data["request_id"]), attempt=int(data["attempt"]),
            status=str(data["status"]), role_digest=str(data["role_digest"]),
            scope_version=int(data["scope_version"]), input_digest=str(data["input_digest"]),
            base_sha=str(data["base_sha"]),
            candidate_before_head_sha=data.get("candidate_before_head_sha"),
            candidate_before_patch_digest=data.get("candidate_before_patch_digest"),
            candidate_before_content_digest=data.get("candidate_before_content_digest"),
            head_sha=data.get("head_sha"),
            patch_digest=data.get("patch_digest"),
            candidate_after_content_digest=data.get("candidate_after_content_digest"),
            staging_patch_digest=data.get("staging_patch_digest"),
            application_artifact_id=data.get("application_artifact_id"),
            feedback_ids=_strings(data.get("feedback_ids"), "feedback_ids"), started_at=data.get("started_at"),
            finished_at=data.get("finished_at"), error=data.get("error"), created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class CheckRun:
    check_id: str
    request_id: str
    writer_run_id: str | None
    status: str
    mode: str
    base_sha: str
    head_sha: str
    patch_digest: str
    scope_version: int
    profile_names: tuple[str, ...]
    command_results: tuple[dict[str, Any], ...] = ()
    failures: tuple[str, ...] = ()
    semantic_verdict_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in {"PENDING", "RUNNING", "PASSED", "FAILED", "ERROR", "CANCELLED"}:
            raise OperatorModelError(f"invalid check run status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "check_id": self.check_id, "request_id": self.request_id, "writer_run_id": self.writer_run_id,
            "status": self.status, "mode": self.mode, "base_sha": self.base_sha, "head_sha": self.head_sha,
            "patch_digest": self.patch_digest, "scope_version": self.scope_version,
            "profile_names": list(self.profile_names), "command_results": list(self.command_results),
            "failures": list(self.failures), "semantic_verdict_id": self.semantic_verdict_id,
            "started_at": self.started_at, "finished_at": self.finished_at, "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckRun":
        _verify_record(data)
        return cls(
            check_id=str(data["check_id"]), request_id=str(data["request_id"]),
            writer_run_id=data.get("writer_run_id"), status=str(data["status"]), mode=str(data["mode"]),
            base_sha=str(data["base_sha"]), head_sha=str(data["head_sha"]), patch_digest=str(data["patch_digest"]),
            scope_version=int(data["scope_version"]), profile_names=_strings(data.get("profile_names"), "profile_names"),
            command_results=tuple(dict(item) for item in data.get("command_results") or []),
            failures=_strings(data.get("failures"), "failures"), semantic_verdict_id=data.get("semantic_verdict_id"),
            started_at=data.get("started_at"), finished_at=data.get("finished_at"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class GateSnapshot:
    request_id: str
    patch_digest: str
    scope_version: int
    scope: str = "PENDING"
    tests: str = "PENDING"
    semantic: str = "PENDING"
    approval: str = "PENDING"
    merge: str = "PENDING"
    check_run_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "request_id": self.request_id, "patch_digest": self.patch_digest,
            "scope_version": self.scope_version, "scope": self.scope, "tests": self.tests,
            "semantic": self.semantic, "approval": self.approval, "merge": self.merge,
            "check_run_id": self.check_run_id, "created_at": self.created_at,
        })


@dataclass(slots=True, frozen=True)
class FeedbackPacket:
    feedback_id: str
    request_id: str
    category: str
    summary: str
    patch_digest: str | None
    writer_run_id: str | None = None
    check_run_id: str | None = None
    failures: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "feedback_id": self.feedback_id, "request_id": self.request_id, "category": self.category,
            "summary": self.summary, "patch_digest": self.patch_digest, "writer_run_id": self.writer_run_id,
            "check_run_id": self.check_run_id, "failures": list(self.failures),
            "artifact_ids": list(self.artifact_ids), "allowed_actions": list(self.allowed_actions),
            "created_at": self.created_at,
        })


@dataclass(slots=True, frozen=True)
class ScopeRevision:
    revision_id: str
    request_id: str
    version: int
    status: str
    layers: tuple[str, ...]
    paths: tuple[str, ...]
    requested_risk: str
    reason: str
    creator: str
    approval_ids: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "revision_id": self.revision_id, "request_id": self.request_id, "version": self.version,
            "status": self.status, "layers": list(self.layers), "paths": list(self.paths),
            "requested_risk": self.requested_risk, "reason": self.reason, "creator": self.creator,
            "approval_ids": list(self.approval_ids), "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopeRevision":
        _verify_record(data)
        return cls(
            revision_id=str(data["revision_id"]),
            request_id=str(data["request_id"]),
            version=int(data["version"]),
            status=str(data["status"]),
            layers=_strings(data.get("layers"), "layers", required=True),
            paths=_strings(data.get("paths"), "paths"),
            requested_risk=str(data["requested_risk"]),
            reason=str(data["reason"]),
            creator=str(data["creator"]),
            approval_ids=_strings(data.get("approval_ids"), "approval_ids"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class ArtifactReference:
    artifact_id: str
    request_id: str
    producer: str
    trust_class: str
    kind: str
    path: str
    sha256: str
    writer_run_id: str | None = None
    check_run_id: str | None = None
    patch_digest: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "artifact_id": self.artifact_id, "request_id": self.request_id, "producer": self.producer,
            "trust_class": self.trust_class, "kind": self.kind, "path": self.path, "sha256": self.sha256,
            "writer_run_id": self.writer_run_id, "check_run_id": self.check_run_id,
            "patch_digest": self.patch_digest, "created_at": self.created_at,
        })


@dataclass(slots=True, frozen=True)
class ExperimentRecord:
    experiment_id: str
    request_id: str
    status: str
    profile: str
    trusted_base_sha: str
    candidate_branch: str
    candidate_worktree: str
    shadow_state_root: str
    dataset_digest: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "experiment_id": self.experiment_id, "request_id": self.request_id, "status": self.status,
            "profile": self.profile, "trusted_base_sha": self.trusted_base_sha,
            "candidate_branch": self.candidate_branch, "candidate_worktree": self.candidate_worktree,
            "shadow_state_root": self.shadow_state_root, "dataset_digest": self.dataset_digest,
            "created_at": self.created_at,
        })


@dataclass(slots=True, frozen=True)
class PromotionRecord:
    promotion_id: str
    request_id: str
    status: str
    before_sha: str
    candidate_head_sha: str
    patch_digest: str
    policy_digest: str
    check_run_ids: tuple[str, ...]
    previous_active_release: str | None = None
    candidate_release: str | None = None
    pull_request: int | None = None
    merge_sha: str | None = None
    rollback_reason: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "promotion_id": self.promotion_id, "request_id": self.request_id, "status": self.status,
            "before_sha": self.before_sha, "candidate_head_sha": self.candidate_head_sha,
            "patch_digest": self.patch_digest, "policy_digest": self.policy_digest,
            "check_run_ids": list(self.check_run_ids), "previous_active_release": self.previous_active_release,
            "candidate_release": self.candidate_release, "pull_request": self.pull_request,
            "merge_sha": self.merge_sha, "rollback_reason": self.rollback_reason, "created_at": self.created_at,
        })


@dataclass(slots=True, frozen=True)
class StudyRecord:
    study_id: str
    purpose: str
    base_checkpoint_id: str
    base_subject_sha: str
    harness_sha: str
    policy_digest: str
    line_id: str
    primary_model: str
    target_checkpoint_name: str
    manifest_digest: str
    role_config_digest: str
    memory_digest: str
    success_contract: dict[str, Any]
    cohort_id: str | None = None
    base_config_digest: str | None = None
    base_model_digest: str | None = None
    base_skills_digest: str | None = None
    memory_snapshot_path: str | None = None
    manifest_snapshot_path: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.study_id, "study_id"),
            (self.purpose, "purpose"),
            (self.base_checkpoint_id, "base_checkpoint_id"),
            (self.base_subject_sha, "base_subject_sha"),
            (self.harness_sha, "harness_sha"),
            (self.policy_digest, "policy_digest"),
            (self.line_id, "line_id"),
            (self.primary_model, "primary_model"),
            (self.manifest_digest, "manifest_digest"),
            (self.role_config_digest, "role_config_digest"),
            (self.memory_digest, "memory_digest"),
        ):
            _required(value, name)
        if self.target_checkpoint_name not in {"H_bug", "H_general"}:
            raise OperatorModelError(
                "target_checkpoint_name must be H_bug or H_general"
            )
        if not self.success_contract:
            raise OperatorModelError("success_contract must not be empty")
        frozen_h0 = (
            self.cohort_id,
            self.base_config_digest,
            self.base_model_digest,
            self.base_skills_digest,
        )
        if any(frozen_h0) != all(frozen_h0):
            raise OperatorModelError(
                "cohort_id and all frozen H0 digests must be provided together"
            )

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "study_id": self.study_id,
            "purpose": self.purpose,
            "base_checkpoint_id": self.base_checkpoint_id,
            "base_subject_sha": self.base_subject_sha,
            "harness_sha": self.harness_sha,
            "policy_digest": self.policy_digest,
            "line_id": self.line_id,
            "primary_model": self.primary_model,
            "target_checkpoint_name": self.target_checkpoint_name,
            "manifest_digest": self.manifest_digest,
            "role_config_digest": self.role_config_digest,
            "memory_digest": self.memory_digest,
            "memory_snapshot_path": self.memory_snapshot_path,
            "manifest_snapshot_path": self.manifest_snapshot_path,
            "success_contract": self.success_contract,
            "cohort_id": self.cohort_id,
            "base_config_digest": self.base_config_digest,
            "base_model_digest": self.base_model_digest,
            "base_skills_digest": self.base_skills_digest,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudyRecord":
        _verify_required_record(data)
        success_contract = data.get("success_contract")
        if not isinstance(success_contract, Mapping):
            raise OperatorModelError("success_contract must be a mapping")
        return cls(
            study_id=str(data["study_id"]),
            purpose=str(data["purpose"]),
            base_checkpoint_id=str(data["base_checkpoint_id"]),
            base_subject_sha=str(data["base_subject_sha"]),
            harness_sha=str(data["harness_sha"]),
            policy_digest=str(data["policy_digest"]),
            line_id=str(data["line_id"]),
            primary_model=str(data["primary_model"]),
            target_checkpoint_name=str(data["target_checkpoint_name"]),
            manifest_digest=str(data["manifest_digest"]),
            role_config_digest=str(data["role_config_digest"]),
            memory_digest=str(data["memory_digest"]),
            memory_snapshot_path=data.get("memory_snapshot_path"),
            manifest_snapshot_path=data.get("manifest_snapshot_path"),
            success_contract=dict(success_contract),
            cohort_id=data.get("cohort_id"),
            base_config_digest=data.get("base_config_digest"),
            base_model_digest=data.get("base_model_digest"),
            base_skills_digest=data.get("base_skills_digest"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class StudyMetricRecord:
    metric_id: str
    study_id: str
    line_id: str
    kind: str
    subject_sha: str
    manifest_digest: str
    success_contract_digest: str
    producer: str
    artifact_path: str
    artifact_sha256: str
    receipt_digest: str
    budget_grant_id: str | None = None
    budget_digest: str | None = None
    wave: int | None = None
    success_contract_passed: bool | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.metric_id, "metric_id"),
            (self.study_id, "study_id"),
            (self.line_id, "line_id"),
            (self.subject_sha, "subject_sha"),
            (self.manifest_digest, "manifest_digest"),
            (self.success_contract_digest, "success_contract_digest"),
            (self.producer, "producer"),
            (self.artifact_path, "artifact_path"),
            (self.artifact_sha256, "artifact_sha256"),
            (self.receipt_digest, "receipt_digest"),
        ):
            _required(value, name)
        _choice(self.kind, VALID_STUDY_METRIC_KINDS, "kind")
        if self.kind == "BASELINE":
            if self.budget_grant_id or self.budget_digest or self.wave is not None:
                raise OperatorModelError("baseline metric must not bind a budget grant")
        else:
            if not self.budget_grant_id or not self.budget_digest or self.wave is None:
                raise OperatorModelError("candidate metric requires a budget grant and wave")
            if self.wave not in VALID_BUDGET_WAVES:
                raise OperatorModelError("candidate metric wave is invalid")
            if self.success_contract_passed is not True:
                raise OperatorModelError("candidate metric must pass the success contract")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "metric_id": self.metric_id,
            "study_id": self.study_id,
            "line_id": self.line_id,
            "kind": self.kind,
            "subject_sha": self.subject_sha,
            "manifest_digest": self.manifest_digest,
            "success_contract_digest": self.success_contract_digest,
            "producer": self.producer,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "receipt_digest": self.receipt_digest,
            "budget_grant_id": self.budget_grant_id,
            "budget_digest": self.budget_digest,
            "wave": self.wave,
            "success_contract_passed": self.success_contract_passed,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudyMetricRecord":
        _verify_required_record(data)
        return cls(
            metric_id=str(data["metric_id"]),
            study_id=str(data["study_id"]),
            line_id=str(data["line_id"]),
            kind=str(data["kind"]),
            subject_sha=str(data["subject_sha"]),
            manifest_digest=str(data["manifest_digest"]),
            success_contract_digest=str(data["success_contract_digest"]),
            producer=str(data["producer"]),
            artifact_path=str(data["artifact_path"]),
            artifact_sha256=str(data["artifact_sha256"]),
            receipt_digest=str(data["receipt_digest"]),
            budget_grant_id=data.get("budget_grant_id"),
            budget_digest=data.get("budget_digest"),
            wave=int(data["wave"]) if data.get("wave") is not None else None,
            success_contract_passed=data.get("success_contract_passed"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class ExperimentLineRecord:
    line_id: str
    study_id: str
    branch: str
    base_sha: str
    head_sha: str
    generation: int = 0
    active_checkpoint_id: str | None = None
    status: str = "OPEN"
    remote: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.line_id, "line_id"),
            (self.study_id, "study_id"),
            (self.branch, "branch"),
            (self.base_sha, "base_sha"),
            (self.head_sha, "head_sha"),
        ):
            _required(value, name)
        _choice(self.status, VALID_EXPERIMENT_LINE_STATUSES, "status")
        if self.generation < 0:
            raise OperatorModelError("generation must not be negative")
        if self.remote is not None:
            _required(self.remote, "remote")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "line_id": self.line_id,
            "study_id": self.study_id,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "generation": self.generation,
            "active_checkpoint_id": self.active_checkpoint_id,
            "status": self.status,
            "remote": self.remote,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExperimentLineRecord":
        _verify_required_record(data)
        return cls(
            line_id=str(data["line_id"]),
            study_id=str(data["study_id"]),
            branch=str(data["branch"]),
            base_sha=str(data["base_sha"]),
            head_sha=str(data["head_sha"]),
            generation=int(data.get("generation", 0)),
            active_checkpoint_id=data.get("active_checkpoint_id"),
            status=str(data.get("status", "OPEN")),
            remote=data.get("remote"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class IntegrationRecord:
    integration_id: str
    study_id: str
    line_id: str
    kind: str
    expected_head_sha: str
    expected_generation: int
    candidate_head_sha: str
    result_head_sha: str
    result_tree_sha: str
    patch_digest: str
    policy_digest: str
    actor: str
    request_id: str | None = None
    check_run_id: str | None = None
    budget_grant_id: str | None = None
    budget_digest: str | None = None
    artifact_ids: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.integration_id, "integration_id"),
            (self.study_id, "study_id"),
            (self.line_id, "line_id"),
            (self.expected_head_sha, "expected_head_sha"),
            (self.candidate_head_sha, "candidate_head_sha"),
            (self.result_head_sha, "result_head_sha"),
            (self.result_tree_sha, "result_tree_sha"),
            (self.patch_digest, "patch_digest"),
            (self.policy_digest, "policy_digest"),
            (self.actor, "actor"),
        ):
            _required(value, name)
        _choice(self.kind, VALID_INTEGRATION_KINDS, "kind")
        if self.expected_generation < 0:
            raise OperatorModelError("expected_generation must not be negative")
        if self.kind == "CANDIDATE" and (not self.request_id or not self.check_run_id):
            raise OperatorModelError("candidate integration requires request_id and check_run_id")
        if bool(self.budget_grant_id) != bool(self.budget_digest):
            raise OperatorModelError("budget_grant_id and budget_digest must be provided together")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "integration_id": self.integration_id,
            "study_id": self.study_id,
            "line_id": self.line_id,
            "kind": self.kind,
            "expected_head_sha": self.expected_head_sha,
            "expected_generation": self.expected_generation,
            "candidate_head_sha": self.candidate_head_sha,
            "result_head_sha": self.result_head_sha,
            "result_tree_sha": self.result_tree_sha,
            "patch_digest": self.patch_digest,
            "policy_digest": self.policy_digest,
            "actor": self.actor,
            "request_id": self.request_id,
            "check_run_id": self.check_run_id,
            "budget_grant_id": self.budget_grant_id,
            "budget_digest": self.budget_digest,
            "artifact_ids": list(self.artifact_ids),
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntegrationRecord":
        _verify_required_record(data)
        return cls(
            integration_id=str(data["integration_id"]),
            study_id=str(data["study_id"]),
            line_id=str(data["line_id"]),
            kind=str(data["kind"]),
            expected_head_sha=str(data["expected_head_sha"]),
            expected_generation=int(data["expected_generation"]),
            candidate_head_sha=str(data["candidate_head_sha"]),
            result_head_sha=str(data["result_head_sha"]),
            result_tree_sha=str(data["result_tree_sha"]),
            patch_digest=str(data["patch_digest"]),
            policy_digest=str(data["policy_digest"]),
            actor=str(data["actor"]),
            request_id=data.get("request_id"),
            check_run_id=data.get("check_run_id"),
            budget_grant_id=data.get("budget_grant_id"),
            budget_digest=data.get("budget_digest"),
            artifact_ids=_strings(data.get("artifact_ids"), "artifact_ids"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    study_id: str
    line_id: str
    name: str
    subject_sha: str
    tree_sha: str
    harness_sha: str
    policy_digest: str
    config_digest: str
    model_digest: str
    skills_digest: str
    memory_digest: str
    manifest_digest: str
    budget_digest: str
    metric_digest: str
    release_path: str
    parent_checkpoint_id: str | None = None
    parent_subject_sha: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.checkpoint_id, "checkpoint_id"),
            (self.study_id, "study_id"),
            (self.line_id, "line_id"),
            (self.subject_sha, "subject_sha"),
            (self.tree_sha, "tree_sha"),
            (self.harness_sha, "harness_sha"),
            (self.policy_digest, "policy_digest"),
            (self.config_digest, "config_digest"),
            (self.model_digest, "model_digest"),
            (self.skills_digest, "skills_digest"),
            (self.memory_digest, "memory_digest"),
            (self.manifest_digest, "manifest_digest"),
            (self.budget_digest, "budget_digest"),
            (self.metric_digest, "metric_digest"),
            (self.release_path, "release_path"),
        ):
            _required(value, name)
        _choice(self.name, VALID_CHECKPOINT_NAMES, "name")
        if self.name == "H0" and (self.parent_checkpoint_id or self.parent_subject_sha):
            raise OperatorModelError("H0 must not declare a parent checkpoint")
        if self.name != "H0" and (not self.parent_checkpoint_id or not self.parent_subject_sha):
            raise OperatorModelError(f"{self.name} requires an H0 parent binding")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "checkpoint_id": self.checkpoint_id,
            "study_id": self.study_id,
            "line_id": self.line_id,
            "name": self.name,
            "subject_sha": self.subject_sha,
            "tree_sha": self.tree_sha,
            "harness_sha": self.harness_sha,
            "policy_digest": self.policy_digest,
            "config_digest": self.config_digest,
            "model_digest": self.model_digest,
            "skills_digest": self.skills_digest,
            "memory_digest": self.memory_digest,
            "manifest_digest": self.manifest_digest,
            "budget_digest": self.budget_digest,
            "metric_digest": self.metric_digest,
            "release_path": self.release_path,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "parent_subject_sha": self.parent_subject_sha,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckpointRecord":
        _verify_required_record(data)
        return cls(
            checkpoint_id=str(data["checkpoint_id"]),
            study_id=str(data["study_id"]),
            line_id=str(data["line_id"]),
            name=str(data["name"]),
            subject_sha=str(data["subject_sha"]),
            tree_sha=str(data["tree_sha"]),
            harness_sha=str(data["harness_sha"]),
            policy_digest=str(data["policy_digest"]),
            config_digest=str(data["config_digest"]),
            model_digest=str(data["model_digest"]),
            skills_digest=str(data["skills_digest"]),
            memory_digest=str(data["memory_digest"]),
            manifest_digest=str(data["manifest_digest"]),
            budget_digest=str(data["budget_digest"]),
            metric_digest=str(data["metric_digest"]),
            release_path=str(data["release_path"]),
            parent_checkpoint_id=data.get("parent_checkpoint_id"),
            parent_subject_sha=data.get("parent_subject_sha"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class BudgetRequestRecord:
    budget_request_id: str
    study_id: str
    wave: int
    case_ids: tuple[str, ...]
    model: str
    max_calls: int
    max_writer_attempts: int
    max_operator_revisions: int
    wall_time_seconds: int
    case_concurrency: int
    reason: str
    requester: str
    previous_grant_id: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.budget_request_id, "budget_request_id"),
            (self.study_id, "study_id"),
            (self.model, "model"),
            (self.reason, "reason"),
            (self.requester, "requester"),
        ):
            _required(value, name)
        if self.wave not in VALID_BUDGET_WAVES:
            raise OperatorModelError("budget wave must be one of 3, 8, 16")
        if len(self.case_ids) != self.wave or len(set(self.case_ids)) != self.wave:
            raise OperatorModelError("case_ids must contain exactly wave unique cases")
        if any(not item.strip() for item in self.case_ids):
            raise OperatorModelError("case_ids must not contain empty values")
        for value, name in (
            (self.max_calls, "max_calls"),
            (self.max_writer_attempts, "max_writer_attempts"),
            (self.max_operator_revisions, "max_operator_revisions"),
            (self.wall_time_seconds, "wall_time_seconds"),
            (self.case_concurrency, "case_concurrency"),
        ):
            if value < 1:
                raise OperatorModelError(f"{name} must be positive")

    @property
    def budget_request_digest(self) -> str:
        return self.to_dict()["record_digest"]

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "budget_request_id": self.budget_request_id,
            "study_id": self.study_id,
            "wave": self.wave,
            "case_ids": list(self.case_ids),
            "model": self.model,
            "max_calls": self.max_calls,
            "max_writer_attempts": self.max_writer_attempts,
            "max_operator_revisions": self.max_operator_revisions,
            "wall_time_seconds": self.wall_time_seconds,
            "case_concurrency": self.case_concurrency,
            "reason": self.reason,
            "requester": self.requester,
            "previous_grant_id": self.previous_grant_id,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BudgetRequestRecord":
        _verify_required_record(data)
        return cls(
            budget_request_id=str(data["budget_request_id"]),
            study_id=str(data["study_id"]),
            wave=int(data["wave"]),
            case_ids=_strings(data.get("case_ids"), "case_ids", required=True),
            model=str(data["model"]),
            max_calls=int(data["max_calls"]),
            max_writer_attempts=int(data["max_writer_attempts"]),
            max_operator_revisions=int(data["max_operator_revisions"]),
            wall_time_seconds=int(data["wall_time_seconds"]),
            case_concurrency=int(data["case_concurrency"]),
            reason=str(data["reason"]),
            requester=str(data["requester"]),
            previous_grant_id=data.get("previous_grant_id"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class BudgetGrantRecord:
    grant_id: str
    budget_request_id: str
    budget_request_digest: str
    study_id: str
    wave: int
    case_ids: tuple[str, ...]
    model: str
    max_calls: int
    max_writer_attempts: int
    max_operator_revisions: int
    wall_time_seconds: int
    case_concurrency: int
    approved_by: str
    approval_kind: str
    previous_grant_id: str | None = None
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.grant_id, "grant_id"),
            (self.budget_request_id, "budget_request_id"),
            (self.budget_request_digest, "budget_request_digest"),
            (self.study_id, "study_id"),
            (self.model, "model"),
            (self.approved_by, "approved_by"),
            (self.approval_kind, "approval_kind"),
        ):
            _required(value, name)
        if self.wave not in VALID_BUDGET_WAVES:
            raise OperatorModelError("budget wave must be one of 3, 8, 16")
        if len(self.case_ids) != self.wave or len(set(self.case_ids)) != self.wave:
            raise OperatorModelError("case_ids must contain exactly wave unique cases")
        if any(not item.strip() for item in self.case_ids):
            raise OperatorModelError("case_ids must not contain empty values")
        for value, name in (
            (self.max_calls, "max_calls"),
            (self.max_writer_attempts, "max_writer_attempts"),
            (self.max_operator_revisions, "max_operator_revisions"),
            (self.wall_time_seconds, "wall_time_seconds"),
            (self.case_concurrency, "case_concurrency"),
        ):
            if value < 1:
                raise OperatorModelError(f"{name} must be positive")
        if self.expires_at:
            datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))

    @property
    def grant_digest(self) -> str:
        return digest_payload({
            key: value for key, value in self.to_dict().items() if key != "record_digest"
        })

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "grant_id": self.grant_id,
            "budget_request_id": self.budget_request_id,
            "budget_request_digest": self.budget_request_digest,
            "study_id": self.study_id,
            "wave": self.wave,
            "case_ids": list(self.case_ids),
            "model": self.model,
            "max_calls": self.max_calls,
            "max_writer_attempts": self.max_writer_attempts,
            "max_operator_revisions": self.max_operator_revisions,
            "wall_time_seconds": self.wall_time_seconds,
            "case_concurrency": self.case_concurrency,
            "approved_by": self.approved_by,
            "approval_kind": self.approval_kind,
            "previous_grant_id": self.previous_grant_id,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BudgetGrantRecord":
        _verify_required_record(data)
        return cls(
            grant_id=str(data["grant_id"]),
            budget_request_id=str(data["budget_request_id"]),
            budget_request_digest=str(data["budget_request_digest"]),
            study_id=str(data["study_id"]),
            wave=int(data["wave"]),
            case_ids=_strings(data.get("case_ids"), "case_ids", required=True),
            model=str(data["model"]),
            max_calls=int(data["max_calls"]),
            max_writer_attempts=int(data["max_writer_attempts"]),
            max_operator_revisions=int(data["max_operator_revisions"]),
            wall_time_seconds=int(data["wall_time_seconds"]),
            case_concurrency=int(data["case_concurrency"]),
            approved_by=str(data["approved_by"]),
            approval_kind=str(data["approval_kind"]),
            previous_grant_id=data.get("previous_grant_id"),
            expires_at=data.get("expires_at"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True, frozen=True)
class UsageEntryRecord:
    usage_id: str
    grant_id: str
    study_id: str
    call_key: str
    execution_id: str
    role: str
    model: str
    status: str
    attempt: int
    revision: int
    case_id: str | None = None
    result_id: str | None = None
    raw_log_path: str | None = None
    stderr_log_path: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    duration_seconds: float | None = None
    error: str | None = None
    reserved_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.usage_id, "usage_id"),
            (self.grant_id, "grant_id"),
            (self.study_id, "study_id"),
            (self.call_key, "call_key"),
            (self.execution_id, "execution_id"),
            (self.role, "role"),
            (self.model, "model"),
        ):
            _required(value, name)
        _choice(self.status, VALID_USAGE_STATUSES, "status")
        if self.attempt < 0 or self.revision < 0:
            raise OperatorModelError("attempt and revision must not be negative")
        for value, name in (
            (self.input_tokens, "input_tokens"),
            (self.cached_input_tokens, "cached_input_tokens"),
            (self.output_tokens, "output_tokens"),
        ):
            if value is not None and value < 0:
                raise OperatorModelError(f"{name} must not be negative")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise OperatorModelError("duration_seconds must not be negative")
        if self.status == "RESERVED" and self.finished_at is not None:
            raise OperatorModelError("reserved usage must not have finished_at")
        if self.status != "RESERVED" and self.finished_at is None:
            raise OperatorModelError("terminal usage requires finished_at")

    def to_dict(self) -> dict[str, Any]:
        return _record_dict({
            "usage_id": self.usage_id,
            "grant_id": self.grant_id,
            "study_id": self.study_id,
            "call_key": self.call_key,
            "execution_id": self.execution_id,
            "case_id": self.case_id,
            "role": self.role,
            "model": self.model,
            "status": self.status,
            "attempt": self.attempt,
            "revision": self.revision,
            "result_id": self.result_id,
            "raw_log_path": self.raw_log_path,
            "stderr_log_path": self.stderr_log_path,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "reserved_at": self.reserved_at,
            "finished_at": self.finished_at,
        })

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UsageEntryRecord":
        _verify_required_record(data)
        return cls(
            usage_id=str(data["usage_id"]),
            grant_id=str(data["grant_id"]),
            study_id=str(data["study_id"]),
            call_key=str(data["call_key"]),
            execution_id=str(data["execution_id"]),
            case_id=data.get("case_id"),
            role=str(data["role"]),
            model=str(data["model"]),
            status=str(data["status"]),
            attempt=int(data.get("attempt", 0)),
            revision=int(data.get("revision", 0)),
            result_id=data.get("result_id"),
            raw_log_path=data.get("raw_log_path"),
            stderr_log_path=data.get("stderr_log_path"),
            input_tokens=(int(data["input_tokens"]) if data.get("input_tokens") is not None else None),
            cached_input_tokens=(
                int(data["cached_input_tokens"])
                if data.get("cached_input_tokens") is not None
                else None
            ),
            output_tokens=(int(data["output_tokens"]) if data.get("output_tokens") is not None else None),
            duration_seconds=(
                float(data["duration_seconds"])
                if data.get("duration_seconds") is not None
                else None
            ),
            error=data.get("error"),
            reserved_at=str(data.get("reserved_at") or utc_now()),
            finished_at=data.get("finished_at"),
        )
