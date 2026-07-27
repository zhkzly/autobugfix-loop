from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from test_operator_budget import (
    grant_wave_three,
    initialized_study,
    register_h0_metric,
)
from test_operator_policy import (
    OperatorBackend,
    make_operator_repo,
    run,
    service_for,
    write_control_config,
    write_test_policy,
)


def verified_line_candidate(tmp_path: Path):
    root, service = initialized_study(tmp_path)
    return prepare_verified_line(root, service)


def prepare_verified_line(root: Path, service: OperatorGovernanceService):
    _, grant = grant_wave_three(service)
    triage = service.create_triage(
        triage_id="integration-triage",
        summary="Eval orchestration fails on visible benchmark evidence",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
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
    triage = service.create_triage(
        triage_id="integration-triage",
        summary="Eval orchestration fails on visible benchmark evidence",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
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


def test_observed_study_metrics_are_derived_from_real_baseline_and_experiment_receipts(
    tmp_path: Path,
):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    baseline = service.capture_baseline("observed-h0", profile="smoke")
    run(["git", "add", ".autobugfix-baselines/observed-h0.yaml"], root)
    run(["git", "commit", "-m", "Record observed H0 baseline"], root)
    h0_sha = run(["git", "rev-parse", "main"], root).stdout.strip()
    manifest = root / "observed-study-manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    study = service.create_study(
        study_id="observed-study",
        purpose="Derive checkpoint metrics from trusted execution evidence",
        manifest_path=manifest,
        success_contract={
            "observed_guard": {
                "require_all_commands_pass": True,
                "metrics": {
                    "pass_rate": {"operator": "eq", "value": 1.0},
                    "artifact_completeness": {"operator": "eq", "value": 1.0},
                },
                "protected_ref": "main",
            }
        },
        base_ref=h0_sha,
    )
    h0_metric = service.record_observed_baseline_metric(
        study.study_id,
        baseline_name="observed-h0",
    )
    _, h0_receipt = service._registered_metric_receipt(h0_metric.metric_id)
    assert h0_receipt["metrics"] == baseline["baseline"]["metrics"]
    assert h0_receipt["guard_run_id"].startswith("baseline:")
    assert h0_receipt["evidence_digest"]
    service.initialize_experiment_line(study.study_id, metric_receipt_id=h0_metric.metric_id)
    budget_request = service.create_budget_request(
        study.study_id,
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        reason="Run the first observed study wave",
        requester="operator",
    )
    grant = service.approve_budget_grant(
        budget_request.budget_request_id,
        approver="human",
        confirm_request_digest=budget_request.budget_request_digest,
    )
    triage = service.create_triage(
        triage_id="observed-study-triage",
        summary="Eval orchestration needs an evidence-bound repair",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
        creator="operator",
        confidence="high",
    )
    request = service.create_request(
        request_id="observed-study-request",
        triage_id=triage.triage_id,
        summary="Repair Eval orchestration on the observed study line",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py",),
        validation_profiles=("eval",),
        performance_baseline="observed-h0",
        experiment_line_id=study.line_id,
        budget_grant_id=grant.grant_id,
        creator="operator",
    )
    service.start(request.request_id)
    service.start_writer(request.request_id)
    assert service.verify(request.request_id, mode="fast")["check_run"]["status"] == "PASSED"
    service.commit_candidate(request.request_id, message="Repair observed study Eval orchestration")
    experiment = service.run_experiment(request.request_id, profile="smoke")
    assert experiment["status"] == "COMPLETED"
    assert experiment["passed"] is True
    assert service.verify(request.request_id, mode="full")["check_run"]["status"] == "PASSED"
    service.integrate_candidate(request.request_id, grant_id=grant.grant_id)

    candidate_metric = service.record_observed_candidate_metric(study.study_id)

    _, candidate_receipt = service._registered_metric_receipt(candidate_metric.metric_id)
    assert candidate_receipt["metrics"] == experiment["metric_receipt"]["metrics"]
    assert candidate_receipt["guard_run_id"] == f"experiment:{experiment['experiment_id']}"
    assert candidate_receipt["evidence_digest"]
    checkpoint = service.create_checkpoint(
        study.line_id,
        metric_receipt_id=candidate_metric.metric_id,
    )
    assert checkpoint["checkpoint"]["name"] == "H_bug"
    assert run(["git", "rev-parse", "main"], root).stdout.strip() == h0_sha
