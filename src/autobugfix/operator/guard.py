from __future__ import annotations

import fnmatch
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from autobugfix.operator.approvals import (
    OperatorApprovalError,
    approval_matches,
    effective_approvals,
    verify_external_approval,
)
from autobugfix.operator.models import OperatorApproval, OperatorRequest, ScopeRevision
from autobugfix.operator.policy import layers_for_file


class TransitionGuardError(RuntimeError):
    pass


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "constitutional": 3}


@dataclass(slots=True, frozen=True)
class ScopeAuthority:
    allowed: bool
    risk: str
    permission_class: str
    human_required: bool
    review_required: bool
    violations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk": self.risk,
            "permission_class": self.permission_class,
            "human_required": self.human_required,
            "review_required": self.review_required,
            "violations": list(self.violations),
        }


def max_risk(*values: str) -> str:
    return max(values, key=lambda value: RISK_ORDER[value])


def effective_request(request: OperatorRequest, revisions: Iterable[ScopeRevision]) -> tuple[OperatorRequest, int]:
    approved = [revision for revision in revisions if revision.status == "APPROVED"]
    if not approved:
        return request, 1
    revision = max(approved, key=lambda item: item.version)
    layers = list(dict.fromkeys(revision.layers))
    if not layers:
        raise TransitionGuardError("approved scope revision has no layers")
    return (
        replace(
            request,
            primary_layer=layers[0],
            secondary_layers=tuple(layers[1:]),
            planned_paths=revision.paths,
            requested_risk=max_risk(request.requested_risk, revision.requested_risk),
        ),
        revision.version,
    )


def _literal_prefix(pattern: str) -> str:
    normalized = pattern.replace("\\", "/")
    marker_positions = [position for marker in "*?[" if (position := normalized.find(marker)) >= 0]
    if not marker_positions:
        return normalized
    prefix = normalized[: min(marker_positions)]
    return prefix.rsplit("/", 1)[0] if "/" in prefix else ""


def _patterns_may_overlap(left: str, right: str) -> bool:
    left_glob = any(marker in left for marker in "*?[")
    right_glob = any(marker in right for marker in "*?[")
    if not left_glob:
        return fnmatch.fnmatch(left, right)
    if not right_glob:
        return fnmatch.fnmatch(right, left)
    left_prefix = _literal_prefix(left).rstrip("/")
    right_prefix = _literal_prefix(right).rstrip("/")
    if not left_prefix or not right_prefix:
        return True
    return (
        left_prefix == right_prefix
        or left_prefix.startswith(f"{right_prefix}/")
        or right_prefix.startswith(f"{left_prefix}/")
    )


def compute_scope_risk(request: OperatorRequest, constitution: Mapping[str, Any]) -> tuple[str, list[str]]:
    violations: list[str] = []
    protected_patterns = [str(item) for item in constitution.get("protected_paths") or []]
    inferred_layers: set[str] = set()
    protected = False
    for path in request.planned_paths:
        layers = layers_for_file(constitution, path)
        if not layers:
            violations.append(f"planned path is not classified by trusted constitution: {path}")
            continue
        inferred_layers.update(layers)
        if not set(layers) & request.declared_layers:
            violations.append(f"planned path is outside declared layers: {path}")
        if any(_patterns_may_overlap(path, pattern) for pattern in protected_patterns):
            protected = True
    missing_layers = sorted(request.declared_layers - inferred_layers)
    for layer in missing_layers:
        violations.append(f"declared layer has no planned path: {layer}")
    if protected:
        computed = "constitutional"
    elif len(request.declared_layers | inferred_layers) > 1:
        computed = "medium"
    else:
        computed = "low"
    return max_risk(request.requested_risk, computed), violations


def check_scope_authority(
    request: OperatorRequest,
    approvals: Iterable[OperatorApproval],
    constitution: Mapping[str, Any],
    *,
    allowed_signers: Path | None = None,
    scope_version: int = 1,
) -> ScopeAuthority:
    risk, violations = compute_scope_risk(request, constitution)
    permission_class = (
        "constitutional" if risk == "constitutional" else "cross_layer" if risk in {"medium", "high"} else "layer_local"
    )
    human_required = permission_class == "constitutional"
    review_required = permission_class != "layer_local"
    valid: list[OperatorApproval] = []
    for approval in effective_approvals(approvals):
        if approval.human_verified_kind:
            try:
                verify_external_approval(approval, constitution, allowed_signers=allowed_signers)
            except OperatorApprovalError as exc:
                violations.append(f"invalid external approval {approval.approval_id}: {exc}")
                continue
        valid.append(approval)
    if review_required and not any(
        approval_matches(
            approval,
            request,
            files=request.planned_paths,
            require_human=human_required,
            stage="scope",
            scope_version=scope_version,
        )
        for approval in valid
    ):
        violations.append(
            "constitutional scope requires verified human approval"
            if human_required
            else "cross-layer scope requires independent reviewer approval"
        )
    return ScopeAuthority(
        allowed=not violations,
        risk=risk,
        permission_class=permission_class,
        human_required=human_required,
        review_required=review_required,
        violations=tuple(violations),
    )


class TransitionGuard:
    def require_phase(self, actual: str, *allowed: str) -> None:
        if actual not in allowed:
            raise TransitionGuardError(f"transition requires phase {', '.join(allowed)}; current phase is {actual}")

    def require_no_active_run(self, writer_run_id: str | None, check_run_id: str | None) -> None:
        if writer_run_id:
            raise TransitionGuardError(f"writer run is already active: {writer_run_id}")
        if check_run_id:
            raise TransitionGuardError(f"check run is already active: {check_run_id}")

    def feedback_actions(self, failures: Iterable[str]) -> tuple[str, ...]:
        text = "\n".join(failures).lower()
        if "outside declared" in text or "not classified" in text or "scope approval" in text:
            return ("revert_candidate_change", "request_scope_change", "inspect")
        if "approval" in text:
            return ("request_approval", "inspect", "abandon")
        if "baseline" in text:
            return ("request_scope_change", "inspect", "abandon")
        if "semantic" in text:
            return ("inspect", "request_review", "abandon")
        return ("retry_writer", "inspect", "abandon")
