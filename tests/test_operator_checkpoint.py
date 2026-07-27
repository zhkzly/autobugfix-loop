from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
import yaml

from autobugfix.operator.models import digest_payload
from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from autobugfix.operator.store import OperatorStoreError
from test_operator_budget import register_h0_metric
from test_operator_integration import prepare_verified_line, verified_line_candidate
from test_operator_policy import (
    OperatorBackend,
    make_operator_repo,
    run,
    write_control_config,
    write_test_policy,
)


def write_metric_receipt_source(
    service,
    line_id: str,
    path: Path,
    *,
    success: bool = True,
) -> Path:
    line = service.store.read_experiment_line(line_id)
    study = service.store.read_study(line.study_id)
    grant = service.store.read_budget_grants(study.study_id)[-1]
    payload = {
        "schema": "autobugfix-study-metric-v1",
        "study_id": study.study_id,
        "line_id": line.line_id,
        "subject_sha": line.head_sha,
        "wave": grant.wave,
        "manifest_digest": study.manifest_digest,
        "success_contract_digest": digest_payload(study.success_contract),
        "budget_grant_id": grant.grant_id,
        "budget_digest": grant.grant_digest,
        "success_contract_passed": success,
        "metrics": {"visible_net_gain": 1, "holdout_regressions": 0},
    }
    path.write_text(
        yaml.safe_dump(
            {**payload, "receipt_digest": digest_payload(payload)},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def register_metric_receipt(service, line_id: str, path: Path):
    write_metric_receipt_source(service, line_id, path)
    study_id = service.store.read_experiment_line(line_id).study_id
    return service.register_guard_metric_receipt(
        study_id,
        receipt_path=path,
        kind="CANDIDATE",
    )


def integrated_study(tmp_path: Path):
    root, service, request, grant = verified_line_candidate(tmp_path)
    service.integrate_candidate(request.request_id, grant_id=grant.grant_id)
    return root, service, request, grant


def test_checkpoint_freezes_release_digests_and_h0_lineage(tmp_path: Path):
    _, service, _, _ = integrated_study(tmp_path)
    metric = register_metric_receipt(
        service,
        "budget-study",
        tmp_path / "h-bug-metric.yaml",
    )

    with pytest.raises(OperatorGovernanceError, match="target checkpoint is H_bug"):
        service.create_checkpoint(
            "budget-study",
            metric_receipt_id=metric.metric_id,
            checkpoint_name="H_general",
        )
    result = service.create_checkpoint(
        "budget-study",
        metric_receipt_id=metric.metric_id,
    )

    checkpoint = service.store.read_checkpoint("budget-study-H_bug")
    h0 = service.store.read_checkpoint("budget-study-H0")
    line = service.store.read_experiment_line("budget-study")
    release = Path(checkpoint.release_path)
    active = service.config.operator.experiment_lines.active_release_root / "budget-study"
    assert result["checkpoint"]["name"] == "H_bug"
    assert checkpoint.parent_checkpoint_id == h0.checkpoint_id
    assert checkpoint.parent_subject_sha == h0.subject_sha
    assert checkpoint.subject_sha == line.head_sha
    assert checkpoint.tree_sha == run(
        ["git", "rev-parse", f"{line.head_sha}^{{tree}}"],
        service.project_root,
    ).stdout.strip()
    assert line.active_checkpoint_id == checkpoint.checkpoint_id
    assert line.generation == 2
    assert release.is_dir()
    assert not (release / ".git").exists()
    assert not (release.stat().st_mode & stat.S_IWUSR)
    assert active.is_symlink()
    assert active.resolve() == release.resolve()


def test_checkpoint_rejects_forged_or_failed_metric_receipt(tmp_path: Path):
    _, service, _, _ = integrated_study(tmp_path)
    metric = write_metric_receipt_source(
        service,
        "budget-study",
        tmp_path / "metric.yaml",
    )
    data = yaml.safe_load(metric.read_text(encoding="utf-8"))
    data["success_contract_passed"] = False
    metric.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(OperatorGovernanceError, match="digest mismatch"):
        service.register_guard_metric_receipt(
            "budget-study",
            receipt_path=metric,
            kind="CANDIDATE",
        )

    payload = {key: value for key, value in data.items() if key != "receipt_digest"}
    data["receipt_digest"] = digest_payload(payload)
    metric.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(OperatorGovernanceError, match="did not pass"):
        service.register_guard_metric_receipt(
            "budget-study",
            receipt_path=metric,
            kind="CANDIDATE",
        )


def test_checkpoint_rejects_tampered_registered_metric_artifact(tmp_path: Path):
    _, service, _, _ = integrated_study(tmp_path)
    metric = register_metric_receipt(service, "budget-study", tmp_path / "metric.yaml")
    artifact = Path(metric.artifact_path)
    artifact.write_text("forged: true\n", encoding="utf-8")

    with pytest.raises(OperatorStoreError, match="artifact digest mismatch"):
        service.create_checkpoint(
            "budget-study",
            metric_receipt_id=metric.metric_id,
        )


def test_guard_metric_registration_rejects_case_level_payload(tmp_path: Path):
    _, service, _, _ = integrated_study(tmp_path)
    path = write_metric_receipt_source(
        service,
        "budget-study",
        tmp_path / "case-level-metric.yaml",
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["metrics"] = {"case_results": {"case-1": "pass"}}
    payload = {key: value for key, value in data.items() if key != "receipt_digest"}
    data["receipt_digest"] = digest_payload(payload)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(OperatorGovernanceError, match="aggregate scalar values"):
        service.register_guard_metric_receipt(
            "budget-study",
            receipt_path=path,
            kind="CANDIDATE",
        )


def test_rollback_creates_new_history_commit_with_checkpoint_tree(tmp_path: Path):
    root, service, request, _ = integrated_study(tmp_path)
    metric = register_metric_receipt(service, "budget-study", tmp_path / "metric.yaml")
    service.create_checkpoint("budget-study", metric_receipt_id=metric.metric_id)
    line_before = service.store.read_experiment_line("budget-study")
    h0 = service.store.read_checkpoint("budget-study-H0")

    result = service.rollback_experiment_line(
        "budget-study",
        h0.checkpoint_id,
        reason="Holdout regression exceeded the frozen success contract",
        actor="operator",
    )

    line_after = service.store.read_experiment_line("budget-study")
    parent = run(["git", "rev-parse", f"{line_after.head_sha}^"], root).stdout.strip()
    active = service.config.operator.experiment_lines.active_release_root / "budget-study"
    assert result["integration"]["kind"] == "ROLLBACK"
    assert line_after.generation == line_before.generation + 1
    assert line_after.head_sha != h0.subject_sha
    assert parent == line_before.head_sha
    assert run(["git", "rev-parse", f"{line_after.head_sha}^{{tree}}"], root).stdout.strip() == (
        h0.tree_sha
    )
    assert line_after.active_checkpoint_id == h0.checkpoint_id
    assert active.resolve() == Path(h0.release_path).resolve()
    assert service.projection(request.request_id).state == "CLOSED"
    assert service.store.read_integrations("budget-study")[-1].kind == "ROLLBACK"
    assert run(["git", "branch", "--show-current"], root).stdout.strip() == "main"


def test_failed_rollback_validation_preserves_line_and_active_release(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    policy_path = write_test_policy(root, tmp_path)
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["validation_profiles"]["full"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "reject-rollback-worktree",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "raise SystemExit(9 if 'rollback-' in str(Path.cwd()) else 0)",
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
        purpose="Test rollback failure containment",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref="main",
    )
    metric_h0 = register_h0_metric(service, root)
    service.initialize_experiment_line(
        "budget-study",
        metric_receipt_id=metric_h0.metric_id,
    )
    _, service, request, grant = prepare_verified_line(root, service)
    service.integrate_candidate(request.request_id, grant_id=grant.grant_id)
    metric = register_metric_receipt(service, "budget-study", tmp_path / "metric.yaml")
    service.create_checkpoint("budget-study", metric_receipt_id=metric.metric_id)
    line_before = service.store.read_experiment_line("budget-study")
    active = service.config.operator.experiment_lines.active_release_root / "budget-study"
    active_before = active.resolve()
    h0 = service.store.read_checkpoint("budget-study-H0")

    with pytest.raises(OperatorGovernanceError, match="rollback validation failed"):
        service.rollback_experiment_line(
            "budget-study",
            h0.checkpoint_id,
            reason="Exercise deterministic rollback failure",
        )

    assert service.store.read_experiment_line("budget-study") == line_before
    assert run(
        ["git", "rev-parse", "refs/heads/experiment/budget-study-main"],
        root,
    ).stdout.strip() == line_before.head_sha
    assert active.resolve() == active_before
    assert service.store.read_integrations("budget-study")[-1].kind == "CANDIDATE"


def test_rollback_rejects_tampered_checkpoint_release(tmp_path: Path):
    _, service, _, _ = integrated_study(tmp_path)
    metric = register_metric_receipt(service, "budget-study", tmp_path / "metric.yaml")
    service.create_checkpoint("budget-study", metric_receipt_id=metric.metric_id)
    line_before = service.store.read_experiment_line("budget-study")
    h0 = service.store.read_checkpoint("budget-study-H0")
    release_file = Path(h0.release_path) / "src/autobugfix/eval/runner.py"
    release_file.chmod(0o600)
    release_file.write_text("# forged release\n", encoding="utf-8")
    active = service.config.operator.experiment_lines.active_release_root / "budget-study"
    active_before = active.resolve()

    with pytest.raises(OperatorGovernanceError, match="does not match its Git tree"):
        service.rollback_experiment_line(
            "budget-study",
            h0.checkpoint_id,
            reason="Exercise release tamper detection",
        )

    assert service.store.read_experiment_line("budget-study") == line_before
    assert active.resolve() == active_before
