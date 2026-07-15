from __future__ import annotations

from typing import Any, Mapping


STUDY_BINDING_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "study_id",
        "cohort_id",
        "line_id",
        "subject_sha",
        "subject_tree",
        "line_generation",
        "line_status",
        "manifest_digest",
        "success_contract_digest",
        "harness_sha",
        "policy_digest",
        "role_config_digest",
        "memory_digest",
        "primary_model",
        "target_checkpoint_name",
        "budget_grant_id",
        "budget_digest",
        "wave",
        "record_digest",
    }
)


class StudyBindingError(RuntimeError):
    pass


def validate_study_binding_shape(record: Mapping[str, Any]) -> None:
    if set(record) != STUDY_BINDING_FIELDS:
        missing = sorted(STUDY_BINDING_FIELDS - set(record))
        extra = sorted(set(record) - STUDY_BINDING_FIELDS)
        raise StudyBindingError(
            f"Study binding fields differ from the shared schema; missing={missing}, extra={extra}"
        )
    if record.get("schema") != "autobugfix-guard-study-binding-v1":
        raise StudyBindingError("unsupported Study binding schema")
    expected_status = {
        "BASELINE": "NOT_INITIALIZED",
        "OPTIMIZATION": "OPEN",
        "CANDIDATE": "CLOSED",
    }.get(str(record.get("kind") or ""))
    if expected_status is None or record.get("line_status") != expected_status:
        raise StudyBindingError("Study binding lifecycle state is invalid")
