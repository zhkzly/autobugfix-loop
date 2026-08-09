from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autobugfix.eval.benchmarks.exp2_coordinator import (
    Exp2Coordinator,
    Exp2CoordinatorError,
)
from autobugfix.eval.benchmarks.exp2_records import (
    Exp2ApparatusReceipt,
    Exp2AttributionRecord,
    Exp2BudgetAllocation,
    Exp2CohortAudit,
    Exp2ContractError,
    Exp2EmptyMemoryFixture,
    Exp2PolicyRecord,
    Exp2PublicRegressionGate,
    Exp2ResultProjection,
    Exp2SealedAggregate,
    Exp2StudyPlan,
    Exp2WorkspaceTreatmentBinding,
    opaque_budget_slot_ids,
    reduce_paired_public,
)
from autobugfix.eval.benchmarks.models import record_with_digest

SUBJECT_H0 = "a" * 40
SUBJECT_H1 = "b" * 40
SUBJECT_H1_NEXT = "c" * 40


def official(case_id: str, *, resolved: bool = True) -> dict[str, object]:
    return record_with_digest(
        {
            "schema": "autobugfix-swe-official-result-v1",
            "adapter": "swebench_verified",
            "instance_id": case_id,
            "run_id": f"run-{case_id}",
            "resolved": resolved,
            "passed": resolved,
            "harness_error": "",
            "image": "image",
            "image_id": "sha256:" + "1" * 64,
            "command": {"argv": ["official"]},
            "report_path": "/tmp/report.yaml",
            "report_sha256": "2" * 64,
            "output_root": "/tmp/output",
            "started_at": "2026-08-09T00:00:00Z",
            "finished_at": "2026-08-09T00:00:01Z",
        }
    )


def report(
    case_id: str,
    subject_sha: str,
    *,
    resolved: bool = True,
    binding_digest: str = "4" * 64,
) -> dict[str, object]:
    official_record = official(case_id, resolved=resolved)
    execution_receipt = record_with_digest(
        {
            "schema": "autobugfix-exp2-execution-receipt-v1",
            "execution_mode": "protected",
            "direct_sdk_in_process": False,
            "outer_bubblewrap": True,
            "broker_command_digest": "8" * 64,
            "broker_result_digest": "9" * 64,
            "task_worktree_path": "/tmp/task-worktree",
            "workspace_only_preflight_digest": None,
        }
    )
    return record_with_digest(
        {
            "schema": "autobugfix-swe-formal-case-v2",
            "protocol_digest": "1" * 64,
            "codex_runtime": {
                "model": "gpt-5.4-mini",
                "reasoning_effort": "low",
                "service_tier": None,
                "sdk_package": "openai-codex",
                "sdk_version": "0.144.4",
                "cli_package": "openai-codex-cli-bin",
                "cli_version": "0.144.4",
                "max_attempts": 2,
                "timeout_seconds": 900,
            },
            "subject_runtime_contract_digest": "2" * 64,
            "subject_runtime_digest": "3" * 64,
            "evaluator_runtime_id": "eval-runtime",
            "memory_digest": "5" * 64,
            "role_config_digest": "6" * 64,
            "policy_digest": "7" * 64,
            "case_token": case_id,
            "executed_subject_sha": subject_sha,
            "submission_digest": "3" * 64,
            "study_binding_digest": binding_digest,
            "official_result": official_record,
            "execution_receipt": execution_receipt,
            "noninterference": record_with_digest(
                {
                    "schema": "autobugfix-swe-noninterference-v1",
                    "case_token": case_id,
                    "submission_digest": "3" * 64,
                    "official_result_digest": official_record["record_digest"],
                    "unchanged": True,
                    "before": {},
                    "after": {},
                    "checked_at": "2026-08-09T00:00:02Z",
                }
            ),
            "harness_error": "",
        }
    )


def plan(tmp_path: Path) -> Exp2StudyPlan:
    fixture_path = tmp_path / "empty-memory.yaml"
    fixture_path.write_text(
        "\n".join(
            (
                "schema: autobugfix-exp2-empty-memory-fixture-spec-v1",
                "fixture_id: exp2-empty-memory-v1",
                "active_entries: []",
                "approved_skill_entries: []",
                "",
            )
        ),
        encoding="utf-8",
    )
    fixture = Exp2EmptyMemoryFixture.from_yaml(fixture_path)
    policy = Exp2PolicyRecord(
        study_id="study-exp2",
        memory_fixture_digest=fixture.record_digest,
        operator_role_skill_digest="2" * 64,
        execution_allowlist=("src/autobugfix/eval/benchmarks",),
    )
    audit = Exp2CohortAudit(
        study_id="study-exp2",
        protocol_digest="1" * 64,
        calibration_case_ids=("cal-01", "cal-02"),
        public_case_ids=tuple(f"public-{index:02d}" for index in range(1, 11)),
        calibration_repositories=("external-a", "external-b"),
        public_repositories=(
            "astropy",
            "django",
            "sympy",
            "xarray",
            "sklearn",
            "pytest",
        ),
        calibration_exclusion_digest="3" * 64,
    )
    apparatus = Exp2ApparatusReceipt(
        study_id="study-exp2",
        apparatus_sha="a" * 40,
        apparatus_tree="b" * 40,
        protocol_digest="1" * 64,
        evaluator_runtime_digest="4" * 64,
        subject_runtime_contract_digest="5" * 64,
        scorer_digest="6" * 64,
        projection_digest="7" * 64,
        reporting_digest="8" * 64,
        memory_fixture_digest=fixture.record_digest,
        operator_role_skill_digest=policy.operator_role_skill_digest,
        execution_mode="protected",
        preflight_digest="9" * 64,
    )
    (tmp_path / "cohort-audit.yaml").write_text(
        yaml.safe_dump(audit.to_dict(), sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "policy.yaml").write_text(
        yaml.safe_dump(policy.to_dict(), sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "apparatus.yaml").write_text(
        yaml.safe_dump(apparatus.to_dict(), sort_keys=False), encoding="utf-8"
    )
    return Exp2StudyPlan(
        study_id="study-exp2",
        calibration_protocol_path=str(
            (tmp_path / "calibration-protocol.yaml").resolve()
        ),
        public_manifest_path=str((tmp_path / "manifest.yaml").resolve()),
        h0_binding_path=str((tmp_path / "h0.yaml").resolve()),
        candidate_binding_path=str((tmp_path / "candidate.yaml").resolve()),
        calibration_case_ids=("cal-01", "cal-02"),
        public_case_ids=tuple(f"public-{index:02d}" for index in range(1, 11)),
        cohort_audit_path=str((tmp_path / "cohort-audit.yaml").resolve()),
        policy_path=str((tmp_path / "policy.yaml").resolve()),
        apparatus_receipt_path=str((tmp_path / "apparatus.yaml").resolve()),
        empty_memory_fixture_path=str((tmp_path / "empty-memory.yaml").resolve()),
    )


def test_exp2_budget_slots_preserve_operator_wave_shape() -> None:
    wave = Exp2BudgetAllocation(3, ("public-01", "public-02"))

    assert wave.operator_case_ids == (
        "public-01",
        "public-02",
        "exp2-budget-slot-3-01",
    )


def test_exp2_frozen_identity_records_bind_empty_memory_and_direct_mode(
    tmp_path: Path,
) -> None:
    fixture_path = tmp_path / "empty-memory.yaml"
    fixture_path.write_text(
        "\n".join(
            (
                "schema: autobugfix-exp2-empty-memory-fixture-spec-v1",
                "fixture_id: exp2-empty-memory-v1",
                "active_entries: []",
                "approved_skill_entries: []",
                "",
            )
        ),
        encoding="utf-8",
    )
    fixture = Exp2EmptyMemoryFixture.from_yaml(fixture_path)
    policy = Exp2PolicyRecord(
        study_id="study-exp2",
        memory_fixture_digest=fixture.record_digest,
        operator_role_skill_digest="1" * 64,
        execution_allowlist=("src/autobugfix/eval/benchmarks",),
    )
    audit = Exp2CohortAudit(
        study_id="study-exp2",
        protocol_digest="2" * 64,
        calibration_case_ids=("cal-01", "cal-02"),
        public_case_ids=tuple(f"public-{index:02d}" for index in range(1, 11)),
        calibration_repositories=("external-a", "external-b"),
        public_repositories=(
            "astropy",
            "django",
            "sympy",
            "xarray",
            "sklearn",
            "pytest",
        ),
        calibration_exclusion_digest="3" * 64,
    )
    apparatus = Exp2ApparatusReceipt(
        study_id="study-exp2",
        apparatus_sha="a" * 40,
        apparatus_tree="b" * 40,
        protocol_digest="2" * 64,
        evaluator_runtime_digest="4" * 64,
        subject_runtime_contract_digest="5" * 64,
        scorer_digest="6" * 64,
        projection_digest="7" * 64,
        reporting_digest="8" * 64,
        memory_fixture_digest=fixture.record_digest,
        operator_role_skill_digest=policy.operator_role_skill_digest,
        execution_mode="workspace_only",
        preflight_digest="9" * 64,
    )
    binding = Exp2WorkspaceTreatmentBinding(
        study_id="study-exp2",
        arm="H1",
        stage="H1A_PUBLIC",
        case_id="public-01",
        apparatus_digest=apparatus.record_digest,
        protocol_digest="2" * 64,
        subject_sha="c" * 40,
        subject_tree="d" * 40,
        evaluator_runtime_id="eval-runtime",
        subject_runtime_digest="5" * 64,
        model="gpt-5.4-mini",
        reasoning_effort="low",
        max_attempts=2,
        timeout_seconds=900,
        case_concurrency=1,
        visible_verifier_command="swe-visible-v1",
        memory_digest=fixture.record_digest,
        operator_role_skill_digest=policy.operator_role_skill_digest,
        execution_role_skill_digest="e" * 64,
        execution_mode="workspace_only",
        task_worktree_root=str((tmp_path / "task").resolve()),
        output_root=str((tmp_path / "output").resolve()),
        opaque_budget_slot_ids=("exp2-budget-slot-3-01",),
        revision=1,
        allowlist_digest="f" * 64,
        public_evidence_cutoff="2026-08-09T00:00:00Z",
        direct_sdk_in_process=True,
        outer_bubblewrap=False,
        parent_candidate_sha="a" * 40,
        candidate_diff_digest="1" * 64,
    )

    assert (
        Exp2PolicyRecord.from_dict(policy.to_dict()).record_digest
        == policy.record_digest
    )
    assert (
        Exp2CohortAudit.from_dict(audit.to_dict()).record_digest == audit.record_digest
    )
    assert (
        Exp2ApparatusReceipt.from_dict(apparatus.to_dict()).record_digest
        == apparatus.record_digest
    )
    assert (
        Exp2WorkspaceTreatmentBinding.from_dict(binding.to_dict()).record_digest
        == binding.record_digest
    )
    assert len(opaque_budget_slot_ids(8)) == 3
    assert (
        len(
            Exp2BudgetAllocation(
                16, tuple(f"case-{i}" for i in range(10))
            ).operator_case_ids
        )
        == 16
    )


def test_exp2_plan_round_trip_and_workspace_mode_requires_disposable_root(
    tmp_path: Path,
) -> None:
    baseline = plan(tmp_path).to_dict()
    assert Exp2StudyPlan.from_dict(baseline).plan_digest == baseline["record_digest"]

    baseline = record_with_digest(
        {
            **{key: value for key, value in baseline.items() if key != "record_digest"},
            "execution_mode": "workspace_only",
        }
    )
    with pytest.raises(Exp2ContractError, match="disposable root"):
        Exp2StudyPlan.from_dict(baseline)


def test_result_projection_exposes_only_terminal_public_label() -> None:
    item = Exp2ResultProjection.from_report(
        report("public-01", SUBJECT_H1),
        study_id="study-exp2",
        arm="H1",
        stage="H1A_PUBLIC",
    ).to_dict()

    assert item["public_label"] == "resolved"
    assert item["same_case_retry_forbidden"] is True
    assert "official_result" not in item
    assert "gold_patch" not in item


def test_paired_public_reducer_compares_h0_and_h1_labels_only() -> None:
    h0 = [
        Exp2ResultProjection.from_report(
            report(f"public-{index:02d}", SUBJECT_H0, resolved=index != 1),
            study_id="study-exp2",
            arm="H0",
            stage="H0_PUBLIC",
        )
        for index in range(1, 11)
    ]
    h1 = [
        Exp2ResultProjection.from_report(
            report(f"public-{index:02d}", SUBJECT_H1, resolved=True),
            study_id="study-exp2",
            arm="H1",
            stage="PUBLIC_REPLAY",
        )
        for index in range(1, 11)
    ]

    summary = reduce_paired_public(h0, h1).to_dict()

    assert summary["h0_resolved_count"] == 9
    assert summary["h1_resolved_count"] == 10
    assert summary["h1_minus_h0_resolved"] == 1
    assert "official_result" not in summary


def test_attribution_is_hypothesis_with_one_allowlisted_scope() -> None:
    record = Exp2AttributionRecord(
        study_id="study-exp2",
        arm="H1",
        stage="H1A_PUBLIC",
        source_projection_digest="4" * 64,
        failure_stage="visible_verifier",
        hypothesis="The retry prompt omits the visible verifier failure summary.",
        confidence=0.7,
        supporting_evidence_digests=("5" * 64,),
        expected_mechanism="A bounded retry receives less actionable feedback.",
        change_scope="execution_harness",
        validation_plan=("run targeted verifier", "run full regression"),
        parent_candidate_sha=SUBJECT_H1,
        revision=1,
        author="operator-supervisor",
        approver="trusted-coordinator",
    )

    assert (
        Exp2AttributionRecord.from_dict(record.to_dict()).record_digest
        == record.record_digest
    )

    tampered = record_with_digest(
        {
            **{
                key: value
                for key, value in record.to_dict().items()
                if key != "record_digest"
            },
            "change_scope": "operator_policy",
        }
    )
    with pytest.raises(Exp2ContractError, match="not allowlisted"):
        Exp2AttributionRecord.from_dict(tampered)


def test_coordinator_requires_attribution_and_preserves_wave_order(
    tmp_path: Path,
) -> None:
    current_plan = plan(tmp_path)
    coordinator = Exp2Coordinator(tmp_path / "state", current_plan.study_id)
    coordinator.initialize(current_plan)

    h0 = coordinator.record_stage(
        stage="H0_CALIBRATION",
        reports=[report("cal-01", SUBJECT_H0), report("cal-02", SUBJECT_H0)],
        subject_sha=SUBJECT_H0,
        execution_mode="protected",
    )
    assert h0["state"] == "H0_CALIBRATED"

    h0_public = coordinator.record_stage(
        stage="H0_PUBLIC",
        reports=[report(f"public-{index:02d}", SUBJECT_H0) for index in range(1, 11)],
        subject_sha=SUBJECT_H0,
        execution_mode="protected",
    )
    assert h0_public["state"] == "H0_COMPLETE"

    h1a = coordinator.record_stage(
        stage="H1A_PUBLIC",
        reports=[
            report("public-01", SUBJECT_H1, binding_digest="6" * 64),
            report("public-02", SUBJECT_H1, binding_digest="6" * 64),
        ],
        subject_sha=SUBJECT_H1,
        execution_mode="protected",
    )
    assert h1a["state"] == "ATTRIBUTION_AWAITING"
    assert coordinator.resume()["status"] == "blocked"

    stage_receipt = h1a["stage_receipt"]
    attribution = Exp2AttributionRecord(
        study_id=current_plan.study_id,
        arm="H1",
        stage="H1A_PUBLIC",
        source_projection_digest=stage_receipt["projection_digests"][0],
        failure_stage="official_eval",
        hypothesis="The candidate changes the wrong execution surface.",
        confidence=0.5,
        supporting_evidence_digests=("6" * 64,),
        expected_mechanism="The visible patch does not address the reported behavior.",
        change_scope="execution_role_skill",
        validation_plan=("run targeted case",),
        parent_candidate_sha=SUBJECT_H1,
        revision=1,
        author="operator-supervisor",
        approver="trusted-coordinator",
    )
    assert coordinator.record_attribution(attribution)["state"] == "H1B_LOCKED"

    with pytest.raises(Exp2ContractError, match="frozen case schedule"):
        coordinator.record_stage(
            stage="H1B_PUBLIC",
            reports=[
                report("public-02", SUBJECT_H1_NEXT),
                report("public-03", SUBJECT_H1_NEXT),
                report("public-04", SUBJECT_H1_NEXT),
            ],
            subject_sha=SUBJECT_H1_NEXT,
            execution_mode="protected",
        )

    result = coordinator.record_stage(
        stage="H1B_PUBLIC",
        reports=[
            report("public-03", SUBJECT_H1_NEXT, binding_digest="5" * 64),
            report("public-04", SUBJECT_H1_NEXT, binding_digest="5" * 64),
            report("public-05", SUBJECT_H1_NEXT, binding_digest="5" * 64),
        ],
        subject_sha=SUBJECT_H1_NEXT,
        execution_mode="protected",
    )
    assert result["state"] == "ATTRIBUTION_AWAITING"

    h1b_attribution = Exp2AttributionRecord(
        study_id=current_plan.study_id,
        arm="H1",
        stage="H1B_PUBLIC",
        source_projection_digest=result["stage_receipt"]["projection_digests"][0],
        failure_stage="official_eval",
        hypothesis="The second candidate needs no further change after the unseen cases.",
        confidence=0.5,
        supporting_evidence_digests=("7" * 64,),
        expected_mechanism="The candidate is now locked for public replay.",
        change_scope="execution_harness",
        validation_plan=("run the complete deterministic suite",),
        parent_candidate_sha=SUBJECT_H1_NEXT,
        revision=2,
        author="operator-supervisor",
        approver="trusted-coordinator",
    )
    assert coordinator.record_attribution(h1b_attribution)["state"] == "H1C_LOCKED"

    replay = coordinator.record_stage(
        stage="PUBLIC_REPLAY",
        reports=[
            report(f"public-{index:02d}", SUBJECT_H1_NEXT, binding_digest="5" * 64)
            for index in range(1, 11)
        ],
        subject_sha=SUBJECT_H1_NEXT,
        execution_mode="protected",
    )
    assert replay["state"] == "PUBLIC_GATE_AWAITING"
    summary = coordinator.paired_public_summary()
    gate = Exp2PublicRegressionGate(
        study_id=current_plan.study_id,
        paired_public_digest=summary["record_digest"],
        h1_subject_sha=SUBJECT_H1_NEXT,
        h1_binding_digest="5" * 64,
        full_check_digest="8" * 64,
        holdout_exposure_audit_digest="9" * 64,
        revision_count=2,
        h1_regression_count=0,
        h1_minus_h0_resolved=0,
        passed=True,
        treatment_locked=True,
    )
    assert coordinator.record_public_regression_gate(gate)["state"] == "SEALED_UNLOCKED"
    aggregate = Exp2SealedAggregate(
        study_id=current_plan.study_id,
        treatment_lock_digest=gate.record_digest,
        guard_metric_digest="a" * 64,
        h0_resolved_count=0,
        h1_resolved_count=0,
        rescue_count=0,
        regression_count=0,
        invalid_count=0,
    )
    assert (
        coordinator.record_sealed_aggregate(aggregate)["state"]
        == "HOLDOUT_COMPLETE"
    )


def test_coordinator_reconciles_journal_append_after_ledger_write_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_plan = plan(tmp_path)
    state_root = tmp_path / "state"
    coordinator = Exp2Coordinator(state_root, current_plan.study_id)
    coordinator.initialize(current_plan)

    def interrupted(_: object) -> None:
        raise RuntimeError("simulated ledger write interruption")

    monkeypatch.setattr(coordinator, "_save_ledger", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        coordinator.record_stage(
            stage="H0_CALIBRATION",
            reports=[report("cal-01", SUBJECT_H0), report("cal-02", SUBJECT_H0)],
            subject_sha=SUBJECT_H0,
            execution_mode="protected",
        )

    recovered = Exp2Coordinator(state_root, current_plan.study_id)
    assert recovered.status()["state"] == "H0_CALIBRATED"
    assert recovered.status()["receipt_digests"]


def test_coordinator_rejects_tampered_event_payload(tmp_path: Path) -> None:
    current_plan = plan(tmp_path)
    coordinator = Exp2Coordinator(tmp_path / "state", current_plan.study_id)
    coordinator.initialize(current_plan)
    coordinator.record_stage(
        stage="H0_CALIBRATION",
        reports=[report("cal-01", SUBJECT_H0), report("cal-02", SUBJECT_H0)],
        subject_sha=SUBJECT_H0,
        execution_mode="protected",
    )

    event_path = tmp_path / "state" / "events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["payload"]["stage_receipt"]["case_ids"] = ["tampered"]
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(
        Exp2CoordinatorError, match="event journal line 1 digest is invalid"
    ):
        Exp2Coordinator(tmp_path / "state", current_plan.study_id).status()
