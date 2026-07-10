from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from autobugfix.cli import main
from autobugfix.models import CodexRequest, CodexResult
from autobugfix.operator.bundle import OperatorBundleError, validate_bundle
from autobugfix.operator.metrics import compare_baseline, record_baseline
from autobugfix.operator.policy import layers_for_file, static_constitution_violations
from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from autobugfix.operator.store import OperatorStoreError
from autobugfix.operator.trusted import load_trusted_policy
from autobugfix.operator.workspace import create_operator_workspace


PACKAGE_POLICY = Path(__file__).parents[1] / "src/autobugfix/operator/constitution.yaml"
PROJECT_ROOT = Path(__file__).parents[1]


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


class OperatorBackend:
    def __init__(self, actions: list[object] | None = None) -> None:
        self.actions = list(actions or [])
        self.calls: list[CodexRequest] = []

    def run(self, request: CodexRequest) -> CodexResult:
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text(json.dumps({"role": request.role}) + "\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        if request.role == "operator_writer":
            action = self.actions.pop(0) if self.actions else "edit"
            if isinstance(action, Exception):
                raise action
            if action == "edit":
                target = request.cwd / "src/autobugfix/eval/runner.py"
                target.write_text("# repaired eval runner\n", encoding="utf-8")
            return CodexResult(text="Writer completed the bounded candidate repair.")
        if request.role == "operator_verifier":
            return CodexResult(text="decision: pass\nreason: patch matches the constitution\n")
        if request.role == "operator_supervisor":
            return CodexResult(
                text="affected_loop: eval\ndiagnosis: verifier evidence is incomplete\n"
                "recommended_action: writer_start\nreason: request is active\n"
            )
        raise AssertionError(f"unexpected role: {request.role}")


class BlockingOperatorBackend(OperatorBackend):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, request: CodexRequest) -> CodexResult:
        if request.role != "operator_writer":
            return super().run(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text("{}\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        self.started.set()
        assert self.release.wait(timeout=10)
        return CodexResult(text="cancelled run returned")


def make_operator_repo(tmp_path: Path) -> Path:
    root = tmp_path / "control"
    root.mkdir()
    run(["git", "init", "-b", "main"], root)
    run(["git", "config", "user.email", "operator@example.com"], root)
    run(["git", "config", "user.name", "Operator User"], root)
    (root / ".gitignore").write_text(
        ".venv/\n__pycache__/\n*.pyc\n.autobugfix/\n.autobugfix-evals/\n.autobugfix-experiments/\n",
        encoding="utf-8",
    )
    (root / "evidence").mkdir()
    (root / "evidence/report.yaml").write_text("failure: true\n", encoding="utf-8")
    (root / "src/autobugfix/eval").mkdir(parents=True)
    (root / "src/autobugfix/operator").mkdir(parents=True)
    (root / "src/autobugfix/service.py").write_text("CodexSDKBackend\n", encoding="utf-8")
    shutil.copy2(PROJECT_ROOT / "src/autobugfix/config.py", root / "src/autobugfix/config.py")
    shutil.copy2(PROJECT_ROOT / "src/autobugfix/codex_sdk.py", root / "src/autobugfix/codex_sdk.py")
    (root / "src/autobugfix/git_utils.py").write_text("# git helpers\n", encoding="utf-8")
    (root / "src/autobugfix/eval/runner.py").write_text("# broken eval runner\n", encoding="utf-8")
    (root / "src/autobugfix/operator/constitution.yaml").write_text(
        PACKAGE_POLICY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for relative in (
        ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
        ".agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md",
        ".agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md",
        ".agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, destination)
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "base"], root)
    return root


def write_control_config(root: Path) -> None:
    path = root / ".autobugfix/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "codex": {"role_runtime": {"skill_guard": False, "strict_skill_guard": False}},
                "operator": {
                    "verification": {
                        "fast_profiles": ["eval"],
                        "full_profiles": ["eval"],
                        "require_semantic_verifier": False,
                        "process_sandbox": "auto",
                        "require_process_sandbox": True,
                        "network_access": False,
                    },
                    "experiments": {
                        "enabled": True,
                        "trusted_ref": "main",
                        "default_profile": "smoke",
                        "profiles": {
                            "smoke": {
                                "commands": [
                                    {
                                        "name": "write-shadow-result",
                                        "argv": [
                                            sys.executable,
                                            "-c",
                                            "from pathlib import Path; "
                                            "p=Path(r'{shadow_state_root}')/'result.txt'; "
                                            "p.write_text('passed'); print(p)",
                                        ],
                                    }
                                ]
                            }
                        },
                    },
                    "promotion": {"canary_profiles": ["canary"]},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def write_test_policy(root: Path, tmp_path: Path, *, canary_passes: bool = True) -> Path:
    policy = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))
    state_db = root / ".autobugfix/operator-v3/governance.sqlite3"
    policy["validation_profiles"]["eval"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "sandboxed-real-process",
                "argv": [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; assert not Path({str(state_db)!r}).exists(); print('validated')",
                ],
            }
        ],
    }
    policy["validation_profiles"]["canary"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "canary",
                "argv": [sys.executable, "-c", "print('canary')" if canary_passes else "raise SystemExit(9)"],
            }
        ],
    }
    path = tmp_path / ("trusted-policy-pass.yaml" if canary_passes else "trusted-policy-fail.yaml")
    path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return path


def service_for(
    root: Path,
    tmp_path: Path,
    *,
    backend: OperatorBackend | None = None,
    policy: Path | None = None,
    allowed_signers: Path | None = None,
) -> OperatorGovernanceService:
    write_control_config(root)
    return OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy or write_test_policy(root, tmp_path),
        allowed_signers=allowed_signers,
        backend=backend or OperatorBackend(),
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


def complete_request(service: OperatorGovernanceService, request_id: str = "complete"):
    request = create_request(service, request_id=request_id)
    assert service.advance(request_id)["action"] == "start"
    assert service.advance(request_id)["action"] == "writer_start"
    fast = service.advance(request_id)
    assert fast["action"] == "verify_fast"
    assert fast["result"]["check_run"]["status"] == "PASSED"
    assert service.projection(request_id).state == "ACTIVE"
    assert service.advance(request_id)["action"] == "candidate_commit"
    full = service.advance(request_id)
    assert full["action"] == "verify_full"
    assert full["result"]["check_run"]["status"] == "PASSED"
    assert service.projection(request_id).state == "VERIFIED"
    return request, Path(service.store.read_workspace(request_id)["path"])


def test_sqlite_authority_is_immutable_and_event_chain_detects_tampering(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    create_request(service, request_id="immutable")
    with pytest.raises(OperatorStoreError, match="already exists"):
        create_request(service, request_id="immutable")

    with sqlite3.connect(service.store.db_path) as connection:
        row = connection.execute(
            "SELECT seq,data FROM events WHERE request_id = ? ORDER BY seq LIMIT 1", ("immutable",)
        ).fetchone()
        data = json.loads(row[1])
        data["payload"]["base_sha"] = "forged"
        connection.execute("UPDATE events SET data = ? WHERE seq = ?", (json.dumps(data), row[0]))
    with pytest.raises((OperatorStoreError, ValueError), match="hash"):
        service.store.read_events("immutable")


def test_request_lease_blocks_competing_transition_and_start_recovers_worktree(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="lease")
    competitor = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=service.trusted_file,
        backend=OperatorBackend(),
    )
    with service.store.request_lease(request.request_id):
        with pytest.raises(OperatorStoreError, match="locked by another command"):
            competitor.start(request.request_id)
    orphan = create_operator_workspace(
        root,
        request,
        service.policy().data,
        worktree_root=service.config.operator.worktrees.root,
    )
    started = service.start(request.request_id)
    assert started["workspace"]["path"] == orphan["path"]
    assert started["workspace"]["recovered"] is True


def test_scope_revision_requires_version_bound_independent_review(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    create_request(service, request_id="scope")
    service.add_reviewer_decision(
        "scope", reviewer="reviewer-agent", decision="approve", reason="initial scope review"
    )
    proposed = service.request_scope_change(
        "scope",
        add_layers=("shared_runtime",),
        add_paths=("src/autobugfix/git_utils.py",),
        requested_risk="medium",
        reason="diagnosis proves config participates in the failure",
    )
    assert proposed["revision"]["status"] == "PROPOSED"
    revision_id = proposed["revision"]["revision_id"]
    with pytest.raises(OperatorGovernanceError, match="authority missing"):
        service.activate_scope_revision("scope", revision_id)
    approval = service.add_reviewer_decision(
        "scope",
        reviewer="second-reviewer",
        decision="approve",
        reason="cross-layer data flow verified",
        scope_revision_id=revision_id,
    )
    assert approval.scope_version == 2
    activated = service.activate_scope_revision("scope", revision_id)
    assert activated["revision"]["status"] == "APPROVED"
    assert service.preflight("scope")["scope_version"] == 2


def test_signed_constitutional_approval_is_verified(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    key = tmp_path / "human-key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], root)
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text(
        f"alice {key.with_suffix('.pub').read_text(encoding='utf-8')}", encoding="utf-8"
    )
    service = service_for(root, tmp_path, allowed_signers=allowed_signers)
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


def test_candidate_constitution_cannot_authorize_itself(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    create_request(service, request_id="self-amend", primary="operator")
    workspace = Path(service.start("self-amend")["workspace"]["path"])
    (workspace / "src/autobugfix/operator/constitution.yaml").write_text(
        "version: 999\nprotected_paths: []\n", encoding="utf-8"
    )
    report = service.verify("self-amend", mode="fast")
    assert report["check_run"]["status"] == "FAILED"
    assert report["policy"]["computed_risk"] == "constitutional"
    assert report["policy"]["trusted_policy_source"] == str(service.trusted_file)
    assert service.projection("self-amend").state == "ACTIVE"


def test_writer_failure_feedback_and_retry_are_distinct_runs(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    backend = OperatorBackend([RuntimeError("sdk transport failed"), "edit"])
    service = service_for(root, tmp_path, backend=backend)
    create_request(service, request_id="retry")
    service.start("retry")
    with pytest.raises(OperatorGovernanceError, match="writer failed"):
        service.start_writer("retry")
    assert service.writer_view("retry")["feedback"][-1]["category"] == "writer_failure"
    second = service.retry_writer("retry")
    runs = service.store.read_writer_runs("retry")
    assert [item.status for item in runs] == ["FAILED", "COMPLETED"]
    assert second["attempt"] == 2


def test_writer_cancel_can_transition_while_backend_is_running(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    backend = BlockingOperatorBackend()
    service = service_for(root, tmp_path, backend=backend)
    create_request(service, request_id="cancel")
    service.start("cancel")
    result: dict[str, object] = {}

    def run_writer() -> None:
        result.update(service.start_writer("cancel"))

    thread = threading.Thread(target=run_writer)
    thread.start()
    assert backend.started.wait(timeout=5)
    cancelled = service.cancel_writer("cancel", reason="Operator stopped the attempt")
    backend.release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert cancelled["status"] == "CANCELLED"
    assert result["status"] == "CANCELLED"
    assert service.projection("cancel").active_writer_run_id is None


def test_read_only_supervisor_records_advice_without_changing_phase(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    create_request(service, request_id="supervisor")
    before = service.projection("supervisor").state
    result = service.run_supervisor("supervisor")
    assert result["phase"] == "REQUESTED"
    assert service.projection("supervisor").state == before
    assert "recommended_action: writer_start" in result["recommendation"]
    assert Path(result["artifact"]["path"]).is_file()


def test_machine_constitution_projects_explicit_hook_role_assignments(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)

    assignments = service.governance_context()["hook_assignments"]

    assert assignments["operator_host"]["systems"] == ["operator"]
    assert "main_agent_operator" in assignments["operator_host"]["roles"]
    assert assignments["isolated_sdk_roles"]["hooks_enabled"] is False
    assert "writer" in assignments["isolated_sdk_roles"]["roles"]
    assert "operator_writer" in assignments["isolated_sdk_roles"]["roles"]


def test_project_config_cannot_weaken_operator_role_or_process_sandbox(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    config_path = root / ".autobugfix/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["operator"]["verification"]["require_process_sandbox"] = False
    config.setdefault("codex", {}).setdefault("roles", {})["operator_writer"] = {
        "sandbox": "danger-full-access"
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=write_test_policy(root, tmp_path),
        backend=OperatorBackend(),
    )
    create_request(service, request_id="weakened")
    assert not service.preflight("weakened")["allowed"]
    config["operator"]["verification"]["require_process_sandbox"] = True
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=service.trusted_file,
        backend=OperatorBackend(),
    )
    service.start("weakened")
    with pytest.raises(OperatorGovernanceError, match="violates machine constitution"):
        service.start_writer("weakened")


def test_static_policy_rejects_sdk_hook_enablement_and_runtime_disablement(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    policy = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))
    sdk_path = root / "src/autobugfix/codex_sdk.py"
    sdk_path.write_text(
        sdk_path.read_text(encoding="utf-8").replace('"hooks = false"', '"hooks = true"'),
        encoding="utf-8",
    )
    config_path = root / "src/autobugfix/config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('"enabled": True,', '"enabled": False,', 1),
        encoding="utf-8",
    )

    violations = static_constitution_violations(root, policy)

    assert "production SDK runtime missing required marker: hooks = false" in violations
    assert "codex.role_runtime.enabled expected True, got False" in violations


def test_real_state_machine_uses_sandboxed_checks_and_advisory_manifest(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request, candidate = complete_request(service, "bundle")
    assert service.audit(request.request_id)["allowed"]
    writer_view = json.dumps(service.writer_view(request.request_id), sort_keys=True)
    assert str(service.store.root) not in writer_view
    full_check = service.store.read_check_runs(request.request_id)[-1]
    assert full_check.command_results[0]["sandbox"] == "bubblewrap"
    assert Path(full_check.command_results[0]["stdout_path"]).read_text(encoding="utf-8").strip() == "validated"
    candidate_diffs = [
        item for item in service.store.read_artifacts(request.request_id) if item["kind"] == "candidate-diff"
    ]
    assert ".autobugfix-governance" not in Path(candidate_diffs[-1]["path"]).read_text(encoding="utf-8")

    manifest = candidate / ".autobugfix-governance" / request.request_id / "bundle.yaml"
    trusted = load_trusted_policy(root, trusted_ref=None, trusted_file=service.trusted_file)
    report = validate_bundle(manifest, candidate, trusted, run_profiles=False)
    assert report["allowed"]
    assert report["manifest_authority"] == "advisory_only"
    assert report["local_claim"]["state"] == "ACTIVE"
    assert report["policy"]["metadata_files"] == [
        f".autobugfix-governance/{request.request_id}/bundle.yaml"
    ]
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["projection"]["state"] = "CLOSED"
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(OperatorBundleError, match="digest mismatch"):
        validate_bundle(manifest, candidate, trusted, run_profiles=False)


def test_shadow_experiment_isolated_from_candidate_and_authority(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="experiment")
    service.start(request.request_id)
    service.start_writer(request.request_id)
    service.verify(request.request_id, mode="fast")
    service.commit_candidate(request.request_id, message="candidate for experiment")
    before = service._snapshot(request.request_id)
    result = service.run_experiment(request.request_id, profile="smoke")
    after = service._snapshot(request.request_id)
    assert result["status"] == "COMPLETED"
    assert (Path(result["shadow_state_root"]) / "result.txt").read_text(encoding="utf-8") == "passed"
    assert before.patch_digest == after.patch_digest
    assert Path(result["shadow_state_root"]).is_relative_to(service.store.root)
    assert not Path(result["candidate_worktree"]).exists()


def test_patch_change_reopens_verified_request_before_promotion(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    _, candidate = complete_request(service, "stale")
    (candidate / "src/autobugfix/eval/runner.py").write_text("# changed after verification\n", encoding="utf-8")
    with pytest.raises(OperatorGovernanceError, match="returned to ACTIVE"):
        service.prepare_promotion("stale")
    assert service.projection("stale").state == "ACTIVE"


def test_canary_activation_and_rollback_restore_last_known_good(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    old_release = root / ".autobugfix/releases/old"
    old_release.mkdir(parents=True)
    active = root / ".autobugfix/active-release"
    active.symlink_to(old_release)
    _, candidate = complete_request(service, "promotion")
    prepared = service.prepare_promotion("promotion")["promotion"]
    prepared["status"] = "MERGED"
    prepared["merge_sha"] = run(["git", "rev-parse", "HEAD"], candidate).stdout.strip()
    service.store.update_promotion(prepared)
    activated = service.run_canary(prepared["promotion_id"])
    assert activated["promotion"]["status"] == "ACTIVE"
    assert active.resolve() == Path(activated["promotion"]["candidate_release"]).resolve()
    rolled_back = service.rollback(prepared["promotion_id"], reason="canary regression observed later")
    assert rolled_back["promotion"]["status"] == "ROLLED_BACK"
    assert active.resolve() == old_release.resolve()
    assert Path(rolled_back["rollback_intent"]["path"]).is_file()


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


def test_cli_has_no_arbitrary_set_state_or_fake_human_switch(tmp_path: Path, monkeypatch):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    monkeypatch.chdir(root)
    trusted = str(write_test_policy(root, tmp_path))
    assert main(["operator", "guide", "--trusted-file", trusted]) == 0
    with pytest.raises(SystemExit):
        main(["operator", "set-state", "--request-id", "forged", "--state", "VERIFIED"])
    with pytest.raises(SystemExit):
        main(
            [
                "operator",
                "review",
                "--trusted-file",
                trusted,
                "missing",
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
