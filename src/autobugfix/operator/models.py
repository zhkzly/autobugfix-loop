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

VALID_LAYERS: tuple[str, ...] = ("execution", "memory", "eval", "operator", "shared_runtime", "docs_skills")
VALID_RISKS: tuple[str, ...] = ("low", "medium", "high", "constitutional")
VALID_CONFIDENCE: tuple[str, ...] = ("low", "medium", "high")
VALID_APPROVAL_KINDS: tuple[str, ...] = ("reviewer", "human_signed", "github", "interactive")
VALID_APPROVAL_STAGES: tuple[str, ...] = ("scope", "merge")
VALID_APPROVAL_DECISIONS: tuple[str, ...] = ("approve", "request_changes", "reject", "revoke")


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
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)

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
        if self.expires_at:
            datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))

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
            "branch": self.branch,
            "base_sha": self.base_sha,
            "creator": self.creator,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }

    @property
    def request_digest(self) -> str:
        return digest_payload(self.payload())

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
            branch=str(data["branch"]),
            base_sha=str(data["base_sha"]),
            creator=str(data["creator"]),
            expires_at=data.get("expires_at"),
            created_at=str(data.get("created_at") or utc_now()),
        )
        stored = data.get("request_digest")
        if stored and stored != item.request_digest:
            raise OperatorModelError("request digest mismatch")
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
    head_sha: str | None = None
    patch_digest: str | None = None
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
            "head_sha": self.head_sha,
            "patch_digest": self.patch_digest,
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
            base_sha=str(data["base_sha"]), head_sha=data.get("head_sha"), patch_digest=data.get("patch_digest"),
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
