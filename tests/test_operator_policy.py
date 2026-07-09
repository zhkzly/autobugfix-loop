from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from autobugfix.cli import main
from autobugfix.operator.metrics import compare_baseline, record_baseline
from autobugfix.operator.models import OperatorRequest, OperatorReview
from autobugfix.operator.policy import evaluate_policy


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def make_operator_repo(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    root.mkdir()
    run(["git", "init", "-b", "main"], root)
    run(["git", "config", "user.email", "operator@example.com"], root)
    run(["git", "config", "user.name", "Operator User"], root)
    (root / ".gitignore").write_text(
        ".autobugfix/\n.autobugfix-evals/\n.autobugfix-experiments/\n",
        encoding="utf-8",
    )
    (root / "src/autobugfix/eval").mkdir(parents=True)
    (root / "src/autobugfix").mkdir(parents=True, exist_ok=True)
    (root / "src/autobugfix/service.py").write_text("CodexSDKBackend\n", encoding="utf-8")
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner\n", encoding="utf-8")
    (root / ".trellis/spec/backend").mkdir(parents=True)
    (root / ".trellis/spec/backend/autobugfix-loop-harness-contract.md").write_text(
        "# Constitution\n",
        encoding="utf-8",
    )
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "base"], root)
    return root


def test_operator_policy_allows_declared_layer_on_non_main_branch(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    run(["git", "switch", "-c", "agent/eval-fix"], root)
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner changed\n", encoding="utf-8")
    request = OperatorRequest(request_id="op-1", summary="Fix eval harness", primary_layer="eval")

    decision = evaluate_policy(root, request, [], base_ref="HEAD")

    assert decision.allowed
    assert decision.changed_layers["eval"] == ["src/autobugfix/eval/runner.py"]


def test_operator_policy_rejects_patch_on_main(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner changed\n", encoding="utf-8")
    request = OperatorRequest(request_id="op-2", summary="Fix eval harness", primary_layer="eval")

    decision = evaluate_policy(root, request, [], base_ref="HEAD")

    assert not decision.allowed
    assert any("protected branch" in item for item in decision.violations)


def test_operator_policy_requires_review_for_cross_layer_request(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    run(["git", "switch", "-c", "agent/eval-config-fix"], root)
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner changed\n", encoding="utf-8")
    (root / "src/autobugfix/config.py").write_text("# config changed\n", encoding="utf-8")
    request = OperatorRequest(
        request_id="op-3",
        summary="Fix eval config propagation",
        primary_layer="eval",
        secondary_layers=["shared_runtime"],
        risk="medium",
    )

    blocked = evaluate_policy(root, request, [], base_ref="HEAD")
    approved = evaluate_policy(
        root,
        request,
        [
            OperatorReview(
                request_id="op-3",
                reviewer="scope-reviewer",
                reviewer_kind="agent",
                decision="approve",
                reason="eval runner consumes isolated config generation",
            )
        ],
        base_ref="HEAD",
    )

    assert not blocked.allowed
    assert any("approved review" in item for item in blocked.violations)
    assert approved.allowed


def test_operator_policy_requires_human_for_constitution_change(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    run(["git", "switch", "-c", "agent/constitution-change"], root)
    constitution = root / ".trellis/spec/backend/autobugfix-loop-harness-contract.md"
    constitution.write_text("# Constitution changed\n", encoding="utf-8")
    request = OperatorRequest(
        request_id="op-4",
        summary="Change project constitution",
        primary_layer="docs_skills",
        risk="architecture",
    )
    agent_review = OperatorReview(
        request_id="op-4",
        reviewer="scope-reviewer",
        reviewer_kind="agent",
        decision="approve",
        reason="agent cannot approve constitution changes",
    )
    human_review = OperatorReview(
        request_id="op-4",
        reviewer="human-owner",
        reviewer_kind="human",
        decision="approve",
        reason="explicit constitution change approval",
    )

    blocked = evaluate_policy(root, request, [agent_review], base_ref="HEAD")
    approved = evaluate_policy(root, request, [agent_review, human_review], base_ref="HEAD")

    assert not blocked.allowed
    assert any("human approval" in item for item in blocked.violations)
    assert approved.allowed
    assert approved.protected_files == [".trellis/spec/backend/autobugfix-loop-harness-contract.md"]


def test_operator_cli_writes_records_and_validates_policy(tmp_path: Path, monkeypatch):
    root = make_operator_repo(tmp_path)
    run(["git", "switch", "-c", "agent/operator-cli"], root)
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner changed\n", encoding="utf-8")
    monkeypatch.chdir(root)

    assert main(
        [
            "operator",
            "triage",
            "--triage-id",
            "triage-1",
            "--summary",
            "Eval harness report is incomplete",
            "--suspected-layer",
            "eval",
            "--confidence",
            "medium",
            "--evidence",
            ".autobugfix-evals/run/case/report.yaml",
        ]
    ) == 0
    assert main(
        [
            "operator",
            "request",
            "--request-id",
            "request-1",
            "--summary",
            "Fix eval harness artifact capture",
            "--primary-layer",
            "eval",
            "--triage-id",
            "triage-1",
            "--validation-command",
            "python -c 'print(1)'",
        ]
    ) == 0
    assert main(["operator", "validate", "--request-id", "request-1"]) == 0
    request_path = root / ".autobugfix/operator/requests/request-1.yaml"
    assert yaml.safe_load(request_path.read_text(encoding="utf-8"))["primary_layer"] == "eval"


def test_operator_baseline_compare_detects_regression(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    record_baseline(root, "toy-e2e", {"pass_rate": 1.0, "runtime_seconds": 10.0})

    passing = compare_baseline(
        root,
        "toy-e2e",
        {"pass_rate": 1.0, "runtime_seconds": 11.0},
        max_regression_percent={"runtime_seconds": 20.0},
        min_metrics={"pass_rate": 1.0},
    )
    failing = compare_baseline(
        root,
        "toy-e2e",
        {"pass_rate": 0.0, "runtime_seconds": 15.0},
        max_regression_percent={"runtime_seconds": 20.0},
        min_metrics={"pass_rate": 1.0},
    )

    assert passing["ok"]
    assert not failing["ok"]
    assert len(failing["failures"]) == 2
