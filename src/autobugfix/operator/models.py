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
GovernanceState = Literal[
    "TRIAGED",
    "REVIEW_PENDING",
    "AUTHORIZED",
    "PATCHING",
    "PATCHED",
    "VALIDATING",
    "VALIDATED",
    "MERGE_READY",
    "REJECTED",
    "EXPIRED",
    "REVOKED",
    "VALIDATION_FAILED",
]

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
    state: str = "TRIAGED"
    last_event_hash: str | None = None
    workspace_path: str | None = None
    patch_digest: str | None = None
    head_sha: str | None = None
    validation_id: str | None = None
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
            "violations": self.violations,
        }
