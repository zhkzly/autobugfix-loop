from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from autobugfix.models import utc_now

LayerName = Literal["execution", "memory", "eval", "operator", "shared_runtime", "docs_skills"]
RiskLevel = Literal["low", "medium", "high", "architecture"]
Confidence = Literal["low", "medium", "high"]
ReviewerKind = Literal["agent", "human", "script", "operator"]
ReviewDecision = Literal["approve", "request_changes", "require_human", "reject"]

VALID_LAYERS: tuple[str, ...] = ("execution", "memory", "eval", "operator", "shared_runtime", "docs_skills")
VALID_RISKS: tuple[str, ...] = ("low", "medium", "high", "architecture")
VALID_CONFIDENCE: tuple[str, ...] = ("low", "medium", "high")
VALID_REVIEW_KINDS: tuple[str, ...] = ("agent", "human", "script", "operator")
VALID_REVIEW_DECISIONS: tuple[str, ...] = ("approve", "request_changes", "require_human", "reject")


class OperatorModelError(ValueError):
    pass


def _list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OperatorModelError(f"{field} must be a list")
    return [str(item) for item in value]


def _require_choice(value: str, choices: tuple[str, ...], field: str) -> str:
    if value not in choices:
        raise OperatorModelError(f"{field} must be one of {', '.join(choices)}")
    return value


@dataclass(slots=True)
class OperatorTriage:
    triage_id: str
    summary: str
    suspected_layers: list[str]
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.summary:
            raise OperatorModelError("summary is required")
        if not self.suspected_layers:
            raise OperatorModelError("at least one suspected layer is required")
        for layer in self.suspected_layers:
            _require_choice(layer, VALID_LAYERS, "suspected_layers")
        _require_choice(self.confidence, VALID_CONFIDENCE, "confidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "triage_id": self.triage_id,
            "summary": self.summary,
            "suspected_layers": self.suspected_layers,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "next_actions": self.next_actions,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorTriage":
        return cls(
            triage_id=str(data["triage_id"]),
            summary=str(data["summary"]),
            suspected_layers=_list(data.get("suspected_layers"), "suspected_layers"),
            confidence=str(data.get("confidence", "low")),
            evidence=_list(data.get("evidence"), "evidence"),
            next_actions=_list(data.get("next_actions"), "next_actions"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True)
class OperatorRequest:
    request_id: str
    summary: str
    primary_layer: str
    secondary_layers: list[str] = field(default_factory=list)
    risk: str = "low"
    triage_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    performance_baseline: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.summary:
            raise OperatorModelError("summary is required")
        _require_choice(self.primary_layer, VALID_LAYERS, "primary_layer")
        for layer in self.secondary_layers:
            _require_choice(layer, VALID_LAYERS, "secondary_layers")
        _require_choice(self.risk, VALID_RISKS, "risk")

    @property
    def declared_layers(self) -> set[str]:
        return {self.primary_layer, *self.secondary_layers}

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "summary": self.summary,
            "primary_layer": self.primary_layer,
            "secondary_layers": self.secondary_layers,
            "risk": self.risk,
            "triage_id": self.triage_id,
            "evidence": self.evidence,
            "validation_commands": self.validation_commands,
            "performance_baseline": self.performance_baseline,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorRequest":
        return cls(
            request_id=str(data["request_id"]),
            summary=str(data["summary"]),
            primary_layer=str(data["primary_layer"]),
            secondary_layers=_list(data.get("secondary_layers"), "secondary_layers"),
            risk=str(data.get("risk", "low")),
            triage_id=data.get("triage_id"),
            evidence=_list(data.get("evidence"), "evidence"),
            validation_commands=_list(data.get("validation_commands"), "validation_commands"),
            performance_baseline=data.get("performance_baseline"),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(slots=True)
class OperatorReview:
    request_id: str
    reviewer: str
    reviewer_kind: str
    decision: str
    reason: str
    approved_paths: list[str] = field(default_factory=list)
    required_validation: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.reviewer:
            raise OperatorModelError("reviewer is required")
        _require_choice(self.reviewer_kind, VALID_REVIEW_KINDS, "reviewer_kind")
        _require_choice(self.decision, VALID_REVIEW_DECISIONS, "decision")
        if not self.reason:
            raise OperatorModelError("reason is required")

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    @property
    def human(self) -> bool:
        return self.reviewer_kind == "human"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "reviewer": self.reviewer,
            "reviewer_kind": self.reviewer_kind,
            "decision": self.decision,
            "reason": self.reason,
            "approved_paths": self.approved_paths,
            "required_validation": self.required_validation,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorReview":
        return cls(
            request_id=str(data["request_id"]),
            reviewer=str(data["reviewer"]),
            reviewer_kind=str(data["reviewer_kind"]),
            decision=str(data["decision"]),
            reason=str(data["reason"]),
            approved_paths=_list(data.get("approved_paths"), "approved_paths"),
            required_validation=_list(data.get("required_validation"), "required_validation"),
            created_at=str(data.get("created_at") or utc_now()),
        )
