from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from autobugfix.eval.benchmarks.exp2_resume import (
    Exp2RollbackAuthorization,
    Exp2SourceProjection,
    Exp2SourceProjectionBundle,
)
from autobugfix.eval.benchmarks.models import record_with_digest
from autobugfix.operator.models import digest_payload
from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from tests.test_operator_budget import (
    grant_wave_three,
    initialized_study,
    register_h0_metric,
)
from tests.test_operator_policy import (
    OperatorBackend,
    make_operator_repo,
    register_optimization_evidence,
    run,
    write_control_config,
    write_test_policy,
)


def verified_line_candidate(tmp_path: Path):
    root, service = initialized_study(tmp_path)
    return prepare_verified_line(root, service)


def prepare_verified_line(root: Path, service: OperatorGovernanceService):
    _, grant = grant_wave_three(service)
    evidence_reference = register_optimization_evidence(
        service,
        root,
        "budget-study",
    )
    triage = service.create_triage(
        triage_id="integration-triage",
        summary="Eval orchestration fails on visible benchmark evidence",
        suspected_layers=("eval",),
        evidence=(evidence_reference,),
        creator="operator",
        confidence="high",
    )
    request = service.create_request(
        request_id="integration-request",
        triage_id=triage.triage_id,
        summary="Repair Eval orchestration on the experiment line",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py",),
        validation_profiles=("eval",),
        experiment_line_id="budget-study",
        budget_grant_id=grant.grant_id,
        creator="operator",
    )
    service.start(request.request_id)
    service.start_writer(request.request_id)
    assert service.verify(request.request_id, mode="fast")["check_run"]["status"] == "PASSED"
    service.commit_candidate(request.request_id, message="Repair Eval orchestration")
    assert service.verify(request.request_id, mode="full")["check_run"]["status"] == "PASSED"
    assert service.projection(request.request_id).state == "VERIFIED"
    return root, service, request, grant


def verified_candidate_with_integration_guard(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    policy_path = write_test_policy(root, tmp_path)
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["validation_profiles"]["eval"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "external-integration-guard",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "raise SystemExit(9 if 'operator-line-worktrees' in str(Path.cwd()) else 0)",
                ],
            }
        ],
    }
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy_path,
        backend=OperatorBackend(),
    )
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    service.create_study(
        study_id="budget-study",
        purpose="Test integration validation failure",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref="main",
    )
    metric = register_h0_metric(service, root)
    service.initialize_experiment_line(
        "budget-study",
        metric_receipt_id=metric.metric_id,
    )
    _, grant = grant_wave_three(service)
    evidence_reference = register_optimization_evidence(
        service,
        root,
        "budget-study",
    )
    triage = service.create_triage(
        triage_id="integration-triage",
        summary="Eval orchestration fails on visible benchmark evidence",
        suspected_layers=("eval",),
        evidence=(evidence_reference,),
        creator="operator",
        confidence="high",
    )
    request = service.create_request(
        request_id="integration-request",
        triage_id=triage.triage_id,
        summary="Repair Eval orchestration on the experiment line",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py",),
        validation_profiles=("eval",),
        experiment_line_id="budget-study",
        budget_grant_id=grant.grant_id,
        creator="operator",
    )
    service.start(request.request_id)
    service.start_writer(request.request_id)
    assert service.verify(request.request_id, mode="fast")["check_run"]["status"] == "PASSED"
    service.commit_candidate(request.request_id, message="Repair Eval orchestration")
    assert service.verify(request.request_id, mode="full")["check_run"]["status"] == "PASSED"
    return service, request, grant


def test_verified_candidate_integrates_through_trusted_worktree_and_double_cas(tmp_path: Path):
    root, service, request, grant = verified_line_candidate(tmp_path)
    control_head = service.store.read_study("budget-study").base_subject_sha
    candidate_head = service._snapshot(request.request_id).head_sha

    result = service.integrate_candidate(
        request.request_id,
        grant_id=grant.grant_id,
        actor="operator",
    )

    integration = result["integration"]
    line = service.store.read_experiment_line("budget-study")
    assert integration["kind"] == "CANDIDATE"
    assert integration["candidate_head_sha"] == candidate_head
    assert integration["expected_head_sha"] == control_head
    assert integration["budget_grant_id"] == grant.grant_id
    assert integration["budget_digest"] == grant.grant_digest
    assert line.generation == 1
    assert line.head_sha == integration["result_head_sha"]
    assert service.projection(request.request_id).state == "CLOSED"
    assert service.projection(request.request_id).outcome == "integrated"
    assert (
        service.store.read_integrations("budget-study")[-1].integration_id
        == integration["integration_id"]
    )
    assert result["remote"] == {"requested": False, "pushed": False}
    assert not (
        service.config.operator.experiment_lines.root
        / "budget-study"
        / integration["integration_id"]
    ).exists()
    assert service._snapshot(request.request_id).head_sha == candidate_head
    assert service.project_root == root
    assert run(["git", "rev-parse", "HEAD"], root).stdout.strip() == control_head
    assert run(["git", "branch", "--show-current"], root).stdout.strip() == "main"


def test_exp2_transition_and_rollback_are_exported_from_trusted_state(
    tmp_path: Path,
) -> None:
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    policy_path = write_test_policy(root, tmp_path)
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy_path,
        backend=OperatorBackend(),
    )
    subject_runtime = record_with_digest(
        {
            "schema": "autobugfix-swe-subject-runtime-v1",
            "treatment_contract_digest": "d" * 64,
            "treatment": {},
        }
    )
    manifest_record = record_with_digest(
        {
            "schema": "autobugfix-swe-sealed-manifest-v2",
            "subject_runtime": subject_runtime,
        }
    )
    manifest = root / "exp2-transition-manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(manifest_record, sort_keys=False),
        encoding="utf-8",
    )
    study = service.create_study(
        study_id="budget-study",
        purpose="Test Exp2 candidate transition",
        manifest_path=manifest,
        success_contract={"net_paired_gain": ">=0"},
        base_ref="main",
        target_checkpoint_name="H_general",
    )
    exported_binding = service.export_exp2_h0_binding(study.study_id)
    binding = {
        key: value
        for key, value in exported_binding.items()
        if key != "artifact_path"
    }
    bundle = Exp2SourceProjectionBundle(
        study_id="exp2-pilot-v2",
        h0_receipt_digest="a" * 64,
        feasibility="passed",
        projections=(
            Exp2SourceProjection(
                "astropy__astropy-13398",
                "b" * 64,
                "unresolved",
                "visible_verifier",
            ),
            Exp2SourceProjection(
                "django__django-10097",
                "c" * 64,
                "resolved",
                "unknown",
            ),
        ),
    )
    source_path = (
        service.config.eval.benchmarks.trusted_case_root
        / "exp2/transition-source.yaml"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        yaml.safe_dump(bundle.to_dict(), sort_keys=False),
        encoding="utf-8",
    )
    baseline_payload = {
        "schema": "autobugfix-study-baseline-v1",
        "study_id": binding["study_id"],
        "line_id": binding["line_id"],
        "subject_sha": binding["subject_sha"],
        "manifest_digest": binding["manifest_digest"],
        "success_contract_digest": binding["success_contract_digest"],
        "metrics": {
            "apparatus_valid": True,
            "h0_terminal_coverage": 1.0,
            "adaptation_feasible": True,
        },
        "guard_run_id": "exp2-pilot-v2",
        "evidence_digest": bundle.record_digest,
    }
    baseline = {
        **baseline_payload,
        "receipt_digest": digest_payload(baseline_payload),
    }
    baseline_path = source_path.with_name("transition-baseline.yaml")
    baseline_path.write_text(
        yaml.safe_dump(baseline, sort_keys=False),
        encoding="utf-8",
    )
    handoff = service.register_exp2_h0_handoff(
        study.study_id,
        binding_path=Path(exported_binding["artifact_path"]),
        metric_path=baseline_path,
        source_projection_path=source_path,
    )
    attribution_export = service.export_exp2_attribution(
        exp2_study_id="exp2-pilot-v2",
        operator_study_id=study.study_id,
        evidence_id=handoff["evidence"]["evidence_id"],
        expected_mechanism="Preserve visible verifier feedback",
        execution_scope=("src/autobugfix/eval/runner.py",),
        validation_plan=("source", "transfer"),
        hypothesis="Visible verifier feedback is dropped.",
    )
    _, grant = grant_wave_three(service)
    triage = service.create_triage(
        triage_id="exp2-transition-triage",
        summary="Visible verifier feedback is dropped",
        suspected_layers=("eval",),
        evidence=(handoff["evidence_reference"],),
        creator="operator",
        confidence="high",
    )
    request = service.create_request(
        request_id="exp2-transition-request",
        triage_id=triage.triage_id,
        summary="Preserve visible verifier feedback",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py",),
        validation_profiles=("eval",),
        experiment_line_id=study.line_id,
        budget_grant_id=grant.grant_id,
        creator="operator",
    )
    service.start(request.request_id)
    service.start_writer(request.request_id)
    assert service.verify(request.request_id, mode="fast")["check_run"]["status"] == "PASSED"
    service.commit_candidate(request.request_id, message="Preserve verifier feedback")
    assert service.verify(request.request_id, mode="full")["check_run"]["status"] == "PASSED"
    service.integrate_candidate(
        request.request_id,
        grant_id=grant.grant_id,
        actor="operator",
    )

    exported = service.export_exp2_candidate_transition(
        operator_study_id="budget-study",
        request_id=request.request_id,
        attribution_digest=str(attribution_export["record_digest"]),
    )
    transition = {
        key: value for key, value in exported.items() if key != "artifact_path"
    }

    assert service.verify_exp2_candidate_transition(transition) == transition
    assert transition["requested_paths"] == ["src/autobugfix/eval/runner.py"]
    assert transition["actual_paths"] == ["src/autobugfix/eval/runner.py"]
    assert service.store.read_experiment_line("budget-study").status == "CLOSED"

    authorization = Exp2RollbackAuthorization(
        study_id="exp2-pilot-v2",
        issuer="exp2-eval-coordinator-v2",
        candidate_transition_digest=str(transition["record_digest"]),
        transfer_metrics_digest="e" * 64,
    )
    authorization_path = (
        service.config.eval.benchmarks.trusted_case_root
        / "exp2/exp2-pilot-v2/rollback-authorization.yaml"
    )
    authorization_path.parent.mkdir(parents=True, exist_ok=True)
    authorization_path.write_text(
        yaml.safe_dump(authorization.to_dict(), sort_keys=False),
        encoding="utf-8",
    )
    rolled_back = service.export_exp2_rollback_receipt(
        transition,
        rollback_authorization_path=authorization_path,
        reason="Exp2 transfer regression",
        actor="operator",
    )
    rollback = {
        key: value for key, value in rolled_back.items() if key != "artifact_path"
    }
    assert service.verify_exp2_rollback_receipt(rollback) == rollback
    assert rollback["candidate_transition_digest"] == transition["record_digest"]


def test_exp2_h0_handoff_exposes_only_source_evidence_and_binds_attribution(
    tmp_path: Path,
) -> None:
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    policy_path = write_test_policy(root, tmp_path)
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy_path,
        backend=OperatorBackend(),
    )
    manifest = root / "exp2-manifest.yaml"
    manifest.write_text("cases: [source-1, source-2]\n", encoding="utf-8")
    study = service.create_study(
        study_id="exp2-operator-study",
        purpose="Test source-only Exp2 handoff",
        manifest_path=manifest,
        success_contract={"net_paired_gain": ">=0"},
        base_ref="main",
        target_checkpoint_name="H_general",
    )
    exported_binding = service.export_exp2_h0_binding(study.study_id)
    binding_path = Path(exported_binding["artifact_path"])
    bundle = Exp2SourceProjectionBundle(
        study_id="exp2-pilot-v2",
        h0_receipt_digest="a" * 64,
        feasibility="passed",
        projections=(
            Exp2SourceProjection(
                case_id="astropy__astropy-13398",
                receipt_digest="b" * 64,
                terminal_label="unresolved",
                failure_stage="visible_verifier",
            ),
            Exp2SourceProjection(
                case_id="django__django-10097",
                receipt_digest="c" * 64,
                terminal_label="resolved",
                failure_stage="unknown",
            ),
        ),
    )
    source_path = (
        service.config.eval.benchmarks.trusted_case_root
        / "exp2/source-projection-bundle.yaml"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        yaml.safe_dump(bundle.to_dict(), sort_keys=False),
        encoding="utf-8",
    )
    binding = {
        key: value
        for key, value in exported_binding.items()
        if key != "artifact_path"
    }
    metric_payload = {
        "schema": "autobugfix-study-baseline-v1",
        "study_id": binding["study_id"],
        "line_id": binding["line_id"],
        "subject_sha": binding["subject_sha"],
        "manifest_digest": binding["manifest_digest"],
        "success_contract_digest": binding["success_contract_digest"],
        "metrics": {
            "apparatus_valid": True,
            "h0_terminal_coverage": 1.0,
            "adaptation_feasible": True,
        },
        "guard_run_id": "exp2-pilot-v2",
        "evidence_digest": bundle.record_digest,
    }
    metric = {
        **metric_payload,
        "receipt_digest": digest_payload(metric_payload),
    }
    metric_path = source_path.with_name("operator-h0-metric.yaml")
    metric_path.write_text(
        yaml.safe_dump(metric, sort_keys=False),
        encoding="utf-8",
    )

    handoff = service.register_exp2_h0_handoff(
        study.study_id,
        binding_path=binding_path,
        metric_path=metric_path,
        source_projection_path=source_path,
    )
    evidence_id = handoff["evidence"]["evidence_id"]
    exported = service.export_exp2_attribution(
        exp2_study_id="exp2-pilot-v2",
        operator_study_id=study.study_id,
        evidence_id=evidence_id,
        expected_mechanism="Preserve visible verifier feedback between attempts",
        execution_scope=("src/autobugfix/runner.py",),
        validation_plan=("Run source replay", "Run transfer cases"),
        hypothesis="The loop drops useful verifier feedback before retry.",
    )
    attribution = {
        key: value for key, value in exported.items() if key != "artifact_path"
    }

    assert handoff["evidence"]["source_kind"] == "exp2_source_projection"
    assert service.verify_exp2_attribution(attribution) == attribution
    assert attribution["supporting_receipt_digests"] == ["b" * 64, "c" * 64]


def test_integration_rejects_dirty_or_stale_verified_candidate(tmp_path: Path):
    _, service, request, grant = verified_line_candidate(tmp_path)
    workspace = Path(service.store.read_workspace(request.request_id)["path"])
    target = workspace / "src/autobugfix/eval/runner.py"
    target.write_text(target.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")

    with pytest.raises(OperatorGovernanceError, match="clean worktree"):
        service.integrate_candidate(request.request_id, grant_id=grant.grant_id)

    line = service.store.read_experiment_line("budget-study")
    assert line.generation == 0
    assert service.projection(request.request_id).state == "VERIFIED"


def test_integration_rejects_unbound_or_wrong_budget(tmp_path: Path):
    _, service, request, grant = verified_line_candidate(tmp_path)
    other_request = service.create_budget_request(
        "budget-study",
        wave=8,
        case_ids=tuple(f"case-{index}" for index in range(1, 9)),
        reason="Prepare but do not approve a later wave",
    )
    assert other_request.wave == 8
    other_grant = service.approve_budget_grant(
        other_request.budget_request_id,
        approver="human",
        confirm_request_digest=other_request.budget_request_digest,
    )

    with pytest.raises(OperatorGovernanceError, match="frozen request budget"):
        service.integrate_candidate(request.request_id, grant_id=other_grant.grant_id)

    assert grant.grant_id == request.budget_grant_id


def test_failed_integration_validation_keeps_line_and_request_unchanged(tmp_path: Path):
    service, request, grant = verified_candidate_with_integration_guard(tmp_path)
    line_before = service.store.read_experiment_line("budget-study")

    with pytest.raises(OperatorGovernanceError, match="validation failed"):
        service.integrate_candidate(request.request_id, grant_id=grant.grant_id)

    line_after = service.store.read_experiment_line("budget-study")
    assert line_after == line_before
    assert service.projection(request.request_id).state == "VERIFIED"
    logs = [
        artifact
        for artifact in service.store.read_artifacts(request.request_id)
        if artifact["kind"] == "integration-check-log"
    ]
    assert logs


def test_store_failure_after_git_cas_reverts_line_ref(
    tmp_path: Path,
    monkeypatch,
):
    root, service, request, grant = verified_line_candidate(tmp_path)
    line_before = service.store.read_experiment_line("budget-study")

    def fail_store_update(*_args, **_kwargs):
        raise RuntimeError("simulated SQLite commit failure")

    monkeypatch.setattr(service.store, "advance_experiment_line", fail_store_update)
    with pytest.raises(RuntimeError, match="SQLite commit failure"):
        service.integrate_candidate(request.request_id, grant_id=grant.grant_id)

    assert service.store.read_experiment_line("budget-study") == line_before
    assert run(
        ["git", "rev-parse", "refs/heads/experiment/budget-study-main"],
        root,
    ).stdout.strip() == line_before.head_sha
    assert service.projection(request.request_id).state == "VERIFIED"
