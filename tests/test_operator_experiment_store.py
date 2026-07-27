from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from autobugfix.models import utc_now
from autobugfix.operator.models import (
    BudgetGrantRecord,
    CheckpointRecord,
    ExperimentLineRecord,
    IntegrationRecord,
    OperatorModelError,
    OperatorRequest,
    StudyRecord,
    UsageEntryRecord,
    digest_payload,
)
from autobugfix.operator.store import OperatorStore, OperatorStoreError


def make_store(tmp_path: Path) -> OperatorStore:
    return OperatorStore(
        tmp_path,
        state_root=tmp_path / "authority",
        artifact_root=tmp_path / "artifacts",
    )


def make_study(study_id: str = "bugfix-study", line_id: str = "bugfix-line") -> StudyRecord:
    return StudyRecord(
        study_id=study_id,
        purpose="Measure governed bugfix harness improvement",
        base_checkpoint_id=f"{study_id}-h0",
        base_subject_sha="a" * 40,
        harness_sha="a" * 40,
        policy_digest="policy-digest",
        line_id=line_id,
        primary_model="gpt-5.4-mini",
        target_checkpoint_name="H_bug",
        manifest_digest="manifest-digest",
        role_config_digest="role-digest",
        memory_digest="memory-digest",
        success_contract={"visible_net_gain": ">0", "holdout_regressions": 0},
    )


def make_line(study: StudyRecord, branch: str = "experiment/bugfix-main") -> ExperimentLineRecord:
    return ExperimentLineRecord(
        line_id=study.line_id,
        study_id=study.study_id,
        branch=branch,
        base_sha=study.base_subject_sha,
        head_sha=study.base_subject_sha,
        remote="origin",
    )


def make_h0(study: StudyRecord, line: ExperimentLineRecord) -> CheckpointRecord:
    return CheckpointRecord(
        checkpoint_id=study.base_checkpoint_id,
        study_id=study.study_id,
        line_id=line.line_id,
        name="H0",
        subject_sha=line.head_sha,
        tree_sha="tree-h0",
        harness_sha=study.harness_sha,
        policy_digest=study.policy_digest,
        config_digest="config-digest",
        model_digest="model-digest",
        skills_digest="skills-digest",
        memory_digest=study.memory_digest,
        manifest_digest=study.manifest_digest,
        budget_digest="no-budget",
        metric_digest="metric-h0",
        release_path="/trusted/releases/h0",
    )


def make_grant(study: StudyRecord) -> BudgetGrantRecord:
    return BudgetGrantRecord(
        grant_id=f"{study.study_id}-wave-3",
        budget_request_id=f"{study.study_id}-wave-3-request",
        budget_request_digest="budget-request-digest",
        study_id=study.study_id,
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        model="gpt-5.4-mini",
        max_calls=30,
        max_writer_attempts=2,
        max_operator_revisions=2,
        wall_time_seconds=7200,
        case_concurrency=1,
        approved_by="human",
        approval_kind="interactive",
    )


def initialize_records(tmp_path: Path) -> tuple[
    OperatorStore,
    StudyRecord,
    ExperimentLineRecord,
]:
    store = make_store(tmp_path)
    study = make_study()
    line = make_line(study)
    store.write_study(study)
    store.write_experiment_line(line)
    return store, study, line


def test_additive_schema_migration_preserves_existing_database_rows(tmp_path: Path):
    store = make_store(tmp_path)
    store.root.mkdir(parents=True)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "CREATE TABLE requests (request_id TEXT PRIMARY KEY, data TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO requests (request_id,data,created_at) VALUES (?,?,?)",
            ("legacy", '{"legacy":true}', utc_now()),
        )
        connection.execute("PRAGMA user_version = 0")

    store.init()

    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute(
            "SELECT data FROM requests WHERE request_id = 'legacy'"
        ).fetchone()[0] == '{"legacy":true}'
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "studies",
        "study_metrics",
        "experiment_lines",
        "integrations",
        "checkpoints",
        "budget_requests",
        "budget_grants",
        "usage_entries",
    } <= tables


def test_store_rejects_newer_schema_version(tmp_path: Path):
    store = make_store(tmp_path)
    store.root.mkdir(parents=True)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("PRAGMA user_version = 999")

    with pytest.raises(OperatorStoreError, match="newer than supported"):
        store.init()


def test_new_authority_records_require_intact_digest(tmp_path: Path):
    store, study, _ = initialize_records(tmp_path)
    tampered = study.to_dict()
    tampered["purpose"] = "forged purpose"
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE studies SET data = ? WHERE study_id = ?",
            (json.dumps(tampered), study.study_id),
        )

    with pytest.raises(OperatorModelError, match="record digest mismatch"):
        store.read_study(study.study_id)

    tampered.pop("record_digest")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE studies SET data = ? WHERE study_id = ?",
            (json.dumps(tampered), study.study_id),
        )
    with pytest.raises(OperatorModelError, match="record digest is required"):
        store.read_study(study.study_id)


def test_legacy_request_digest_remains_stable_without_line_binding_fields():
    data = {
        "request_id": "legacy-request",
        "summary": "Legacy request",
        "primary_layer": "eval",
        "secondary_layers": [],
        "requested_risk": "low",
        "triage_id": "legacy-triage",
        "triage_digest": "triage-digest",
        "evidence": ["note:evidence"],
        "validation_profiles": ["eval"],
        "performance_baseline": None,
        "planned_paths": ["src/autobugfix/eval/**"],
        "constitution_digest": "policy-digest",
        "branch": "operator/experiment/legacy",
        "base_sha": "a" * 40,
        "creator": "operator",
        "expires_at": None,
        "created_at": utc_now(),
    }
    legacy_digest = digest_payload(data)
    request = OperatorRequest.from_dict({**data, "request_digest": legacy_digest})

    assert request.experiment_line_id is None
    assert request.experiment_line_generation is None
    assert request.request_digest == legacy_digest
    assert OperatorRequest.from_dict(request.to_dict()).request_digest == legacy_digest


def test_legacy_request_digest_recomputes_for_scope_revision():
    data = {
        "request_id": "legacy-scope-request",
        "summary": "Legacy scope request",
        "primary_layer": "eval",
        "secondary_layers": [],
        "requested_risk": "low",
        "triage_id": "legacy-triage",
        "triage_digest": "triage-digest",
        "evidence": ["note:evidence"],
        "validation_profiles": ["eval"],
        "performance_baseline": None,
        "planned_paths": ["src/autobugfix/eval/**"],
        "constitution_digest": "policy-digest",
        "branch": "operator/experiment/legacy",
        "base_sha": "a" * 40,
        "creator": "operator",
        "expires_at": None,
        "created_at": utc_now(),
    }
    original_digest = digest_payload(data)
    request = OperatorRequest.from_dict({**data, "request_digest": original_digest})
    revised = replace(
        request,
        primary_layer="operator",
        secondary_layers=("eval",),
        requested_risk="constitutional",
        planned_paths=(
            "src/autobugfix/operator/models.py",
            "src/autobugfix/eval/adapters.py",
        ),
    )
    expected = {
        **data,
        "primary_layer": "operator",
        "secondary_layers": ["eval"],
        "requested_risk": "constitutional",
        "planned_paths": [
            "src/autobugfix/operator/models.py",
            "src/autobugfix/eval/adapters.py",
        ],
    }

    assert revised.request_digest == digest_payload(expected)
    assert revised.request_digest != original_digest


def test_study_line_checkpoint_budget_and_usage_round_trip(tmp_path: Path):
    store, study, line = initialize_records(tmp_path)
    checkpoint = make_h0(study, line)
    grant = make_grant(study)
    usage = UsageEntryRecord(
        usage_id="usage-1",
        grant_id=grant.grant_id,
        study_id=study.study_id,
        call_key="writer-case-1-attempt-1",
        execution_id="h0-case-1",
        case_id="case-1",
        role="writer",
        model="gpt-5.4-mini",
        status="RESERVED",
        attempt=1,
        revision=0,
    )
    store.write_checkpoint(checkpoint)
    store.write_budget_grant(grant)
    store.write_usage_entry(usage)

    assert store.read_study(study.study_id) == study
    assert store.read_experiment_line(line.line_id) == line
    assert store.read_checkpoint(checkpoint.checkpoint_id) == checkpoint
    assert store.read_budget_grant(grant.grant_id) == grant
    assert store.read_usage_entry(usage.usage_id) == usage
    assert grant.grant_digest == grant.to_dict()["record_digest"]

    completed = replace(
        usage,
        status="COMPLETED",
        result_id="codex-result-1",
        raw_log_path="/trusted/artifacts/codex-result-1.jsonl",
        input_tokens=100,
        output_tokens=25,
        duration_seconds=4.5,
        finished_at=utc_now(),
    )
    assert store.finalize_usage_entry(completed) == completed
    with pytest.raises(OperatorStoreError, match="already finalized"):
        store.finalize_usage_entry(completed)


def test_duplicate_checkpoint_name_and_call_key_are_rejected(tmp_path: Path):
    store, study, line = initialize_records(tmp_path)
    store.write_checkpoint(make_h0(study, line))
    duplicate = replace(make_h0(study, line), checkpoint_id="another-h0")
    with pytest.raises(OperatorStoreError, match="immutable operator record already exists"):
        store.write_checkpoint(duplicate)

    grant = make_grant(study)
    store.write_budget_grant(grant)
    first = UsageEntryRecord(
        usage_id="usage-1",
        grant_id=grant.grant_id,
        study_id=study.study_id,
        call_key="same-call",
        execution_id="h0-case-1",
        case_id="case-1",
        role="writer",
        model=grant.model,
        status="RESERVED",
        attempt=1,
        revision=0,
    )
    store.write_usage_entry(first)
    with pytest.raises(OperatorStoreError, match="immutable operator record already exists"):
        store.write_usage_entry(replace(first, usage_id="usage-2"))


def test_line_and_integration_advance_in_one_compare_and_swap(tmp_path: Path):
    store, study, line = initialize_records(tmp_path)
    updated = replace(line, head_sha="b" * 40, generation=1)
    integration = IntegrationRecord(
        integration_id="integration-1",
        study_id=study.study_id,
        line_id=line.line_id,
        kind="ROLLBACK",
        expected_head_sha=line.head_sha,
        expected_generation=0,
        candidate_head_sha="candidate-sha",
        result_head_sha=updated.head_sha,
        result_tree_sha="result-tree",
        patch_digest="patch-digest",
        policy_digest=study.policy_digest,
        actor="guard",
        artifact_ids=("artifact-1",),
    )

    assert store.advance_experiment_line(updated, integration) == updated
    assert store.read_experiment_line(line.line_id) == updated
    assert store.read_integrations(line.line_id) == [integration]

    stale_line = replace(updated, head_sha="c" * 40, generation=1)
    stale_integration = replace(
        integration,
        integration_id="integration-stale",
        result_head_sha=stale_line.head_sha,
    )
    with pytest.raises(OperatorStoreError, match="stale experiment line"):
        store.advance_experiment_line(stale_line, stale_integration)
    assert [item.integration_id for item in store.read_integrations(line.line_id)] == [
        "integration-1"
    ]


def test_concurrent_line_compare_and_swap_has_one_winner(tmp_path: Path):
    store, _, line = initialize_records(tmp_path)
    stores = [
        store,
        OperatorStore(
            tmp_path,
            state_root=store.root,
            artifact_root=store.artifact_root,
        ),
    ]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def update(candidate: OperatorStore, suffix: str) -> None:
        next_line = replace(line, head_sha=suffix * 40, generation=1)
        barrier.wait(timeout=5)
        try:
            candidate.compare_and_swap_experiment_line(
                next_line,
                expected_head_sha=line.head_sha,
                expected_generation=0,
            )
            outcomes.append("won")
        except OperatorStoreError:
            outcomes.append("stale")

    threads = [
        threading.Thread(target=update, args=(stores[0], "b")),
        threading.Thread(target=update, args=(stores[1], "c")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["stale", "won"]
    assert store.read_experiment_line(line.line_id).generation == 1


def test_line_lease_is_reentrant_but_excludes_another_store(tmp_path: Path):
    store, _, line = initialize_records(tmp_path)
    other = OperatorStore(
        tmp_path,
        state_root=store.root,
        artifact_root=store.artifact_root,
    )

    with store.experiment_line_lease(line.line_id):
        with store.experiment_line_lease(line.line_id):
            with pytest.raises(OperatorStoreError, match="locked by another command"):
                with other.experiment_line_lease(line.line_id):
                    pass

    with other.experiment_line_lease(line.line_id):
        pass
