from __future__ import annotations

from autobugfix.operator.models import OperatorEvent, OperatorProjection


class OperatorProjectionError(RuntimeError):
    pass


_PHASE_EVENTS: dict[str, str] = {
    "request_created": "REQUESTED",
    "request_activated": "ACTIVE",
    "verification_accepted": "VERIFIED",
    "request_reopened": "ACTIVE",
    "request_closed": "CLOSED",
}

_ALLOWED_PHASE_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"REQUESTED"},
    "REQUESTED": {"REQUESTED", "ACTIVE", "CLOSED"},
    "ACTIVE": {"ACTIVE", "VERIFIED", "CLOSED"},
    "VERIFIED": {"VERIFIED", "ACTIVE", "CLOSED"},
    "CLOSED": {"CLOSED"},
}


def project_request(request_id: str, events: list[OperatorEvent]) -> OperatorProjection:
    projection = OperatorProjection(request_id=request_id)
    current: str | None = None
    for event in events:
        next_phase = _PHASE_EVENTS.get(event.kind)
        if next_phase is not None:
            if next_phase not in _ALLOWED_PHASE_TRANSITIONS.get(current, set()):
                raise OperatorProjectionError(
                    f"invalid governance phase transition {current or 'NONE'} -> {next_phase} via {event.kind}"
                )
            current = next_phase
            projection.state = next_phase

        projection.last_event_hash = event.computed_hash
        if event.kind == "request_activated":
            projection.workspace_path = str(event.payload.get("workspace_path") or "") or None
        elif event.kind == "writer_started":
            projection.active_writer_run_id = str(event.payload.get("run_id") or "") or None
        elif event.kind in {"writer_completed", "writer_failed", "writer_timed_out", "writer_cancelled"}:
            projection.active_writer_run_id = None
        elif event.kind == "check_started":
            projection.active_check_run_id = str(event.payload.get("check_id") or "") or None
        elif event.kind in {"check_passed", "check_failed", "check_error", "check_cancelled"}:
            projection.active_check_run_id = None
            projection.validation_id = str(event.payload.get("check_id") or "") or projection.validation_id
            projection.violations = [str(item) for item in event.payload.get("failures") or []]
        elif event.kind == "patch_observed":
            projection.patch_digest = str(event.payload.get("patch_digest") or "") or None
            projection.head_sha = str(event.payload.get("head_sha") or "") or None
        elif event.kind == "scope_revision_approved":
            projection.scope_version = int(event.payload.get("version") or projection.scope_version)
        elif event.kind == "gate_updated":
            projection.blocked_by = [str(item) for item in event.payload.get("blocked_by") or []]
        elif event.kind == "verification_accepted":
            projection.validation_id = str(event.payload.get("check_id") or "") or projection.validation_id
            projection.patch_digest = str(event.payload.get("patch_digest") or "") or projection.patch_digest
            projection.head_sha = str(event.payload.get("head_sha") or "") or projection.head_sha
            projection.blocked_by = []
            projection.violations = []
        elif event.kind == "request_reopened":
            projection.blocked_by = [str(event.payload.get("reason") or "candidate_changed")]
        elif event.kind == "request_closed":
            projection.outcome = str(event.payload.get("outcome") or "closed")

    if current is None:
        raise OperatorProjectionError(f"request {request_id} has no request_created event")
    return projection
