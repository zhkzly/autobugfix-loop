from __future__ import annotations

from autobugfix.operator.models import OperatorEvent, OperatorProjection


class OperatorProjectionError(RuntimeError):
    pass


_EVENT_STATES: dict[str, str] = {
    "request_created": "TRIAGED",
    "review_required": "REVIEW_PENDING",
    "authorized": "AUTHORIZED",
    "workspace_created": "PATCHING",
    "postflight_completed": "PATCHED",
    "validation_started": "VALIDATING",
    "validation_passed": "VALIDATED",
    "validation_failed": "VALIDATION_FAILED",
    "merge_ready": "MERGE_READY",
    "rejected": "REJECTED",
    "expired": "EXPIRED",
    "revoked": "REVOKED",
}

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "TRIAGED": {"TRIAGED", "REVIEW_PENDING", "AUTHORIZED", "REJECTED", "EXPIRED", "REVOKED"},
    "REVIEW_PENDING": {"REVIEW_PENDING", "AUTHORIZED", "REJECTED", "EXPIRED", "REVOKED"},
    "AUTHORIZED": {"AUTHORIZED", "REVIEW_PENDING", "PATCHING", "REJECTED", "EXPIRED", "REVOKED"},
    "PATCHING": {"PATCHING", "PATCHED", "REJECTED", "EXPIRED", "REVOKED"},
    "PATCHED": {"PATCHED", "VALIDATING", "REJECTED", "EXPIRED", "REVOKED"},
    "VALIDATING": {"VALIDATING", "VALIDATED", "VALIDATION_FAILED", "REJECTED", "EXPIRED", "REVOKED"},
    "VALIDATED": {"VALIDATED", "MERGE_READY", "VALIDATING", "REJECTED", "EXPIRED", "REVOKED"},
    "VALIDATION_FAILED": {"VALIDATION_FAILED", "PATCHING", "VALIDATING", "REJECTED", "REVOKED"},
    "MERGE_READY": {"MERGE_READY", "REVOKED", "EXPIRED"},
    "REJECTED": {"REJECTED"},
    "EXPIRED": {"EXPIRED"},
    "REVOKED": {"REVOKED"},
}


def project_request(request_id: str, events: list[OperatorEvent]) -> OperatorProjection:
    projection = OperatorProjection(request_id=request_id)
    current = projection.state
    for event in events:
        next_state = _EVENT_STATES.get(event.kind, current)
        if next_state not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise OperatorProjectionError(f"invalid governance transition {current} -> {next_state} via {event.kind}")
        current = next_state
        projection.state = current
        projection.last_event_hash = event.computed_hash
        if event.kind == "workspace_created":
            projection.workspace_path = str(event.payload.get("path") or "") or None
        elif event.kind == "postflight_completed":
            projection.patch_digest = str(event.payload.get("patch_digest") or "") or None
            projection.head_sha = str(event.payload.get("head_sha") or "") or None
        elif event.kind in {"validation_failed", "validation_passed", "merge_ready"}:
            projection.validation_id = str(event.payload.get("validation_id") or "") or None
            projection.violations = [str(item) for item in event.payload.get("violations") or []]
    return projection
