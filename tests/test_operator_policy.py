from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from autobugfix.cli import main
from autobugfix.operator.bundle import validate_bundle
from autobugfix.operator.metrics import compare_baseline, record_baseline
from autobugfix.operator.policy import layers_for_file
from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from autobugfix.operator.store import OperatorStore, OperatorStoreError
from autobugfix.operator.trusted import load_trusted_policy


PACKAGE_POLICY = Path(__file__).parents[1] / "src/autobugfix/operator/constitution.yaml"


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
    (root / "evidence").mkdir()
    (root / "evidence/report.yaml").write_text("failure: true\n", encoding="utf-8")
    (root / "src/autobugfix/eval").mkdir(parents=True)
    (root / "src/autobugfix/operator").mkdir(parents=True)
    (root / "src/autobugfix/service.py").write_text("CodexSDKBackend\n", encoding="utf-8")
    (root / "src/autobugfix/config.py").write_text("# config\n", encoding="utf-8")
    (root / "src/autobugfix/eval/runner.py").write_text("# eval runner\n", encoding="utf-8")
    (root / "src/autobugfix/operator/constitution.yaml").write_text(
        PACKAGE_POLICY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "base"], root)
    return root


def service_for(root: Path, *, policy: Path = PACKAGE_POLICY, allowed_signers: Path | None = None):
    return OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy,
        allowed_signers=allowed_signers,
    )


def create_request(
    service: OperatorGovernanceService,
    *,
    request_id: str,
    primary: str = "eval",
    secondary: tuple[str, ...] = (),
    risk: str = "low",
    baseline: str | None = None,
    creator: str = "operator-agent",
):
    triage = service.create_triage(
        triage_id=f"triage-{request_id}",
        summary="Observed a reproducible harness failure",
        suspected_layers=(primary, *secondary),
        evidence=("evidence/report.yaml",),
        creator=creator,
        confidence="high",
    )
    return service.create_request(
        request_id=request_id,
        triage_id=triage.triage_id,
        summary="Repair the diagnosed subsystem",
        primary_layer=primary,
        secondary_layers=secondary,
        requested_risk=risk,
        validation_profiles=(primary,),
        performance_baseline=baseline,
        creator=creator,
    )


def test_request_is_immutable_and_event_chain_detects_tampering(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root)
    create_request(service, request_id="immutable")

    with pytest.raises(OperatorStoreError, match="already exists"):
        create_request(service, request_id="immutable")

    event_path = OperatorStore(root).event_path("immutable")
    row = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    row["payload"]["base_sha"] = "forged"
    event_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(OperatorStoreError, match="event hash"):
        OperatorStore(root).read_events("immutable")


def test_cross_layer_request_requires_independent_reviewer(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root)
    create_request(service, request_id="cross", secondary=("shared_runtime",), risk="medium")

    blocked = service.preflight("cross")
    assert not blocked["allowed"]
    with pytest.raises(OperatorGovernanceError, match="cannot independently review"):
        service.add_reviewer_decision(
            "cross", reviewer="operator-agent", decision="approve", reason="self approval"
        )

    service.add_reviewer_decision(
        "cross", reviewer="reviewer-agent", decision="approve", reason="config is consumed by eval"
    )
    assert service.preflight("cross")["allowed"]


def test_signed_human_scope_approval_is_verified(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    key = tmp_path / "human-key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], root)
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text(f"alice {key.with_suffix('.pub').read_text(encoding='utf-8')}", encoding="utf-8")
    service = service_for(root, allowed_signers=allowed_signers)
    create_request(service, request_id="signed", primary="operator", risk="constitutional")
    payload = tmp_path / "approval.json"
    service.create_approval_payload(
        "signed", payload, approver="alice", stage="scope", reason="authorize governance repair"
    )
    run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "autobugfix-operator", str(payload)], root)
    service.import_signed_approval(
        "signed", payload_path=payload, signature_path=Path(f"{payload}.sig")
    )

    assert service.preflight("signed")["allowed"]


def test_candidate_cannot_weaken_its_own_constitution(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root)
    create_request(service, request_id="self-amend", primary="operator", risk="low")
    workspace = service.create_workspace("self-amend")
    candidate_policy = Path(workspace["path"]) / "src/autobugfix/operator/constitution.yaml"
    candidate_policy.write_text("version: 2\nlayers: {}\nprotected_paths: []\nvalidation_profiles: {}\nmetrics: {}\n", encoding="utf-8")

    decision = service.postflight("self-amend")

    assert not decision["allowed"]
    assert decision["computed_risk"] == "constitutional"
    assert any("scope approval" in item for item in decision["violations"])


def test_committed_changes_remain_visible_from_frozen_base(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root)
    request = create_request(service, request_id="committed")
    workspace = service.create_workspace(request.request_id)
    candidate = Path(workspace["path"])
    (candidate / "src/autobugfix/eval/runner.py").write_text("# committed fix\n", encoding="utf-8")
    run(["git", "add", "src/autobugfix/eval/runner.py"], candidate)
    run(["git", "commit", "-m", "fix eval harness"], candidate)

    decision = service.postflight(request.request_id)

    assert decision["allowed"]
    assert decision["changed_files"] == ["src/autobugfix/eval/runner.py"]
    assert decision["base_sha"] == request.base_sha
    assert decision["head_sha"] != request.base_sha


def test_baseline_contract_rejects_missing_and_regressed_metrics(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    record_baseline(
        root,
        "eval-smoke",
        {"pass_rate": 1.0, "artifact_completeness": 1.0, "runtime_seconds": 10.0},
        base_sha="abc123",
    )
    contract = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))["metrics"]

    missing = compare_baseline(root, "eval-smoke", {"pass_rate": 1.0}, contract)
    regressed = compare_baseline(
        root,
        "eval-smoke",
        {"pass_rate": 0.0, "artifact_completeness": 1.0, "runtime_seconds": 20.0},
        contract,
    )

    assert not missing["ok"]
    assert any("artifact_completeness" in item for item in missing["failures"])
    assert not regressed["ok"]
    assert any("pass_rate" in item for item in regressed["failures"])
    assert any("runtime_seconds" in item for item in regressed["failures"])


def test_real_worktree_validation_and_bundle_round_trip(tmp_path: Path, monkeypatch):
    root = make_operator_repo(tmp_path)
    policy_data = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))
    policy_data["validation_profiles"]["eval"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "real-process",
                "argv": [
                    sys.executable,
                    "-c",
                    "import os; assert 'GH_TOKEN' not in os.environ; print('validated')",
                ],
            }
        ],
    }
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-candidate")
    policy = tmp_path / "trusted-policy.yaml"
    policy.write_text(yaml.safe_dump(policy_data, sort_keys=False), encoding="utf-8")
    service = service_for(root, policy=policy)
    request = create_request(service, request_id="bundle")
    workspace = service.create_workspace(request.request_id)
    candidate = Path(workspace["path"])
    (candidate / "src/autobugfix/eval/runner.py").write_text("# validated fix\n", encoding="utf-8")
    assert service.postflight(request.request_id)["allowed"]

    report = service.validate(request.request_id)
    assert report["policy"]["allowed"]
    assert report["command_results"][0]["argv"][0] == sys.executable
    assert Path(report["command_results"][0]["stdout_path"]).read_text(encoding="utf-8").strip() == "validated"
    assert service.projection(request.request_id).state == "MERGE_READY"

    bundle_path = service.export_bundle(request.request_id)
    trusted = load_trusted_policy(root, trusted_ref=None, trusted_file=policy)
    bundle_report = validate_bundle(bundle_path, candidate, trusted, run_profiles=False)
    assert bundle_report["allowed"]
    assert bundle_report["policy"]["metadata_files"] == [
        ".autobugfix-governance/bundle/bundle.yaml"
    ]


def test_cli_has_no_unverified_human_review_switch(tmp_path: Path, monkeypatch):
    root = make_operator_repo(tmp_path)
    monkeypatch.chdir(root)
    trusted = str(PACKAGE_POLICY)
    assert main(
        [
            "operator",
            "triage",
            "--trusted-file",
            trusted,
            "--triage-id",
            "triage-cli",
            "--summary",
            "eval artifact is incomplete",
            "--suspected-layer",
            "eval",
            "--evidence",
            "evidence/report.yaml",
            "--creator",
            "operator-agent",
        ]
    ) == 0
    assert main(
        [
            "operator",
            "request",
            "--trusted-file",
            trusted,
            "--request-id",
            "request-cli",
            "--triage-id",
            "triage-cli",
            "--summary",
            "fix eval artifact capture",
            "--primary-layer",
            "eval",
            "--creator",
            "operator-agent",
        ]
    ) == 0
    with pytest.raises(SystemExit):
        main(
            [
                "operator",
                "review",
                "--trusted-file",
                trusted,
                "request-cli",
                "--reviewer",
                "operator-agent",
                "--kind",
                "human",
                "--decision",
                "approve",
                "--reason",
                "forged",
            ]
        )


def test_constitution_classifies_every_governed_source_path():
    root = Path(__file__).parents[1]
    constitution = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))
    tracked = run(["git", "ls-files"], root).stdout.splitlines()
    tracked.extend(run(["git", "ls-files", "--others", "--exclude-standard"], root).stdout.splitlines())
    prefixes = (
        "src/",
        "tests/",
        "scripts/",
        ".github/",
        ".agents/",
        ".trellis/spec/",
        ".trellis/tasks/",
        "docs/",
    )
    missing = sorted(
        path for path in tracked if path.startswith(prefixes) and not layers_for_file(constitution, path)
    )
    assert missing == []
