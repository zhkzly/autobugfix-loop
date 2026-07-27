from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from autobugfix.cli import main
from autobugfix.config import ConfigError, load_config
from autobugfix.codex_sdk import (
    CODEX_BROKER_SOCKET_ENV,
    CODEX_BROKER_TOKEN_ENV,
    CodexSDKBackend,
    CodexSDKError,
)
from autobugfix.git_utils import rev_parse
from autobugfix.models import CodexRequest, CodexResult
from autobugfix.operator.bundle import OperatorBundleError, validate_bundle
from autobugfix.operator.guard import compute_scope_risk
from autobugfix.operator.metrics import compare_baseline, derive_metric_receipt, record_baseline
from autobugfix.operator.policy import layers_for_file, static_constitution_violations
from autobugfix.operator.models import OperatorModelError, digest_payload
from autobugfix.operator.service import OperatorGovernanceError, OperatorGovernanceService
from autobugfix.operator.store import OperatorStoreError
from autobugfix.operator.trusted import load_trusted_policy
from autobugfix.operator.validator import (
    OperatorValidationError,
    _OperatorCodexServer,
    _run_command,
    validate_operator_request,
)
from autobugfix.operator.workspace import create_operator_workspace


PACKAGE_POLICY = Path(__file__).parents[1] / "src/autobugfix/operator/constitution.yaml"
PROJECT_ROOT = Path(__file__).parents[1]


def load_real_repository_acceptance():
    path = PROJECT_ROOT / "scripts/real_repository_acceptance.py"
    spec = importlib.util.spec_from_file_location("candidate_real_repository_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_operator_pr_validator():
    path = PROJECT_ROOT / "scripts/validate_operator_pr.py"
    spec = importlib.util.spec_from_file_location("trusted_operator_pr_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def register_baseline_metric(
    service: OperatorGovernanceService,
    root: Path,
    study_id: str,
):
    study = service.store.read_study(study_id)
    payload = {
        "schema": "autobugfix-study-baseline-v1",
        "study_id": study.study_id,
        "line_id": study.line_id,
        "subject_sha": study.base_subject_sha,
        "manifest_digest": study.manifest_digest,
        "success_contract_digest": digest_payload(study.success_contract),
        "metrics": {"pass_rate": 0.0},
    }
    path = root / f"{study_id}-h0-metric.yaml"
    path.write_text(
        yaml.safe_dump(
            {**payload, "receipt_digest": digest_payload(payload)},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return service.register_guard_metric_receipt(
        study_id,
        receipt_path=path,
        kind="BASELINE",
    )


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


class StagingCommitOperatorBackend(OperatorBackend):
    def run(self, request: CodexRequest) -> CodexResult:
        if request.role != "operator_writer":
            return super().run(request)
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text("{}\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        target = request.cwd / "src/autobugfix/eval/runner.py"
        target.write_text("# repaired from staging commit\n", encoding="utf-8")
        run(["git", "add", "src/autobugfix/eval/runner.py"], request.cwd)
        run(["git", "commit", "-m", "writer staging only"], request.cwd)
        return CodexResult(text="Writer committed only its service-owned staging tree.")


class DirectCandidateMutationBackend(OperatorBackend):
    def __init__(self, candidate: Path) -> None:
        super().__init__()
        self.candidate = candidate

    def run(self, request: CodexRequest) -> CodexResult:
        if request.role != "operator_writer":
            return super().run(request)
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text("{}\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        (self.candidate / "src/autobugfix/eval/runner.py").write_text(
            "# illegal direct candidate mutation\n", encoding="utf-8"
        )
        return CodexResult(text="Writer attempted a direct candidate mutation.")


class OutOfScopeStagingBackend(OperatorBackend):
    def run(self, request: CodexRequest) -> CodexResult:
        if request.role != "operator_writer":
            return super().run(request)
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text("{}\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        (request.cwd / "src/autobugfix/operator/constitution.yaml").write_text(
            "version: 999\n", encoding="utf-8"
        )
        return CodexResult(text="Writer attempted an out-of-scope staging mutation.")


class NoOpWriterBackend(OperatorBackend):
    def run(self, request: CodexRequest) -> CodexResult:
        if request.role != "operator_writer":
            return super().run(request)
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text("{}\n", encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        return CodexResult(text="Writer inspected its staging directory without applying a patch.")


class BrokerBackend:
    def __init__(self) -> None:
        self.calls: list[CodexRequest] = []

    def run(self, request: CodexRequest) -> CodexResult:
        self.calls.append(request)
        request.raw_log_path.write_text(
            json.dumps({"role": request.role, "model": request.model}) + "\n",
            encoding="utf-8",
        )
        request.stderr_log_path.write_text("", encoding="utf-8")
        return CodexResult(text=f"completed {request.role}")


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
    policy["baseline_required_layers"] = []
    state_db = root / ".autobugfix/operator-v3/governance.sqlite3"
    validation_source = (
        "print('validated in inherited sandbox')"
        if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap"
        else (
            "from pathlib import Path; "
            f"assert not Path({str(state_db)!r}).exists(); print('validated')"
        )
    )
    policy["validation_profiles"]["eval"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "sandboxed-real-process",
                "argv": [
                    sys.executable,
                    "-c",
                    validation_source,
                ],
            }
        ],
    }
    policy["validation_profiles"]["full"] = policy["validation_profiles"]["eval"]
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
    planned_paths: tuple[str, ...] | None = None,
):
    default_paths = {
        "execution": ("src/autobugfix/runner.py",),
        "memory": ("src/autobugfix/memory/**",),
        "eval": ("src/autobugfix/eval/runner.py",),
        "operator": ("src/autobugfix/operator/**",),
        "shared_runtime": ("src/autobugfix/config.py",),
        "docs_skills": ("docs/**",),
    }
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
        planned_paths=planned_paths or default_paths[primary],
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


def test_signed_constitutional_approval_uses_repo_signers_file_by_default(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    key = tmp_path / "human-key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], root)
    signers = root / ".github/autobugfix-allowed-signers"
    signers.parent.mkdir(parents=True, exist_ok=True)
    signers.write_text(
        f"alice {key.with_suffix('.pub').read_text(encoding='utf-8')}", encoding="utf-8"
    )
    service = service_for(root, tmp_path, backend=OperatorBackend())
    assert service.allowed_signers == signers.resolve()
    create_request(service, request_id="signed-default", primary="eval", risk="constitutional")
    payload = tmp_path / "approval.json"
    service.create_approval_payload(
        "signed-default",
        payload,
        approver="alice",
        stage="scope",
        reason="authorize governance repair",
    )
    run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "autobugfix-operator", str(payload)], root)
    service.import_signed_approval(
        "signed-default", payload_path=payload, signature_path=Path(f"{payload}.sig")
    )
    service.start("signed-default")
    service.start_writer("signed-default")
    trusted = load_trusted_policy(root, trusted_ref=None, trusted_file=service.trusted_file)
    workspace = Path(service.store.read_workspace("signed-default")["path"])
    report = validate_operator_request(
        root,
        "signed-default",
        candidate_root=workspace,
        trusted_policy=trusted,
        run_profiles=False,
        allowed_signers=service.allowed_signers,
    )
    assert report["policy"]["allowed"]


def test_merge_approval_payload_defaults_to_stable_patch_binding(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="merge-patch-binding")
    service.start(request.request_id)
    service.start_writer(request.request_id)
    snapshot = service._snapshot(request.request_id)
    payload = tmp_path / "merge-approval.json"

    service.create_approval_payload(
        request.request_id,
        payload,
        approver="human-owner",
        stage="merge",
        reason="approve the verified candidate patch",
    )

    data = json.loads(payload.read_text(encoding="utf-8"))
    assert data["patch_digest"] == snapshot.patch_digest
    assert data["head_sha"] is None


def test_merge_approval_payload_can_explicitly_bind_head(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="merge-head-binding")
    service.start(request.request_id)
    service.start_writer(request.request_id)
    snapshot = service._snapshot(request.request_id)
    payload = tmp_path / "merge-approval.json"

    service.create_approval_payload(
        request.request_id,
        payload,
        approver="human-owner",
        stage="merge",
        reason="approve the exact candidate commit",
        merge_binding="head",
    )

    data = json.loads(payload.read_text(encoding="utf-8"))
    assert data["patch_digest"] is None
    assert data["head_sha"] == snapshot.head_sha


def test_candidate_constitution_cannot_authorize_itself(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    create_request(service, request_id="self-amend", primary="eval")
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


def test_writer_staging_commit_is_isolated_and_applied_by_trusted_service(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    backend = StagingCommitOperatorBackend()
    service = service_for(root, tmp_path, backend=backend)
    request = create_request(service, request_id="staging-commit")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])

    result = service.start_writer(request.request_id)

    assert backend.calls[-1].cwd != candidate
    assert "writer-staging" in str(backend.calls[-1].cwd)
    assert str(candidate) not in json.dumps(service.writer_view(request.request_id), sort_keys=True)
    assert (candidate / "src/autobugfix/eval/runner.py").read_text(encoding="utf-8") == (
        "# repaired from staging commit\n"
    )
    assert rev_parse(candidate, "HEAD") == request.base_sha
    assert result["application_artifact_id"]
    baseline = service.store.read_workspace(request.request_id)["writer_admission_baseline"]
    assert baseline["artifact_id"]
    assert baseline["baseline_digest"]
    assert any(
        event.kind == "writer_applied" for event in service.store.read_events(request.request_id)
    )


def test_writer_direct_candidate_mutation_is_reverted_and_fails_closed(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="direct-candidate")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    service.backend = DirectCandidateMutationBackend(candidate)

    with pytest.raises(OperatorGovernanceError, match="outside service-owned staging"):
        service.start_writer(request.request_id)

    assert (candidate / "src/autobugfix/eval/runner.py").read_text(encoding="utf-8") == (
        "# broken eval runner\n"
    )
    assert rev_parse(candidate, "HEAD") == request.base_sha
    run_record = service.store.read_writer_runs(request.request_id)[-1]
    assert run_record.status == "FAILED"
    assert any(
        event.kind == "writer_candidate_mutation_reverted"
        and event.payload["restored"] is True
        for event in service.store.read_events(request.request_id)
    )


def test_out_of_scope_writer_staging_diff_never_reaches_candidate(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path, backend=OutOfScopeStagingBackend())
    request = create_request(service, request_id="staging-scope")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    before = (candidate / "src/autobugfix/operator/constitution.yaml").read_text(encoding="utf-8")

    with pytest.raises(OperatorGovernanceError, match="outside declared scope"):
        service.start_writer(request.request_id)

    assert (candidate / "src/autobugfix/operator/constitution.yaml").read_text(encoding="utf-8") == before
    assert not any(
        event.kind == "writer_applied" for event in service.store.read_events(request.request_id)
    )


def test_direct_candidate_commit_cannot_enter_trusted_commit_or_full_verify(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="direct-commit")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    service.start_writer(request.request_id)
    run(["git", "add", "--all"], candidate)
    run(["git", "commit", "-m", "illegal candidate commit"], candidate)

    with pytest.raises(OperatorGovernanceError, match="direct candidate commits are forbidden"):
        service.commit_candidate(request.request_id, message="trusted candidate commit")

    verified = service.verify(request.request_id, mode="full")
    assert verified["check_run"]["status"] == "FAILED"
    assert any(
        "matching trusted candidate_committed event" in failure
        for failure in verified["check_run"]["failures"]
    )


def test_writer_cannot_launder_a_preexisting_manual_candidate_patch(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    backend = NoOpWriterBackend()
    service = service_for(root, tmp_path, backend=backend)
    request = create_request(service, request_id="preexisting-manual-patch")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    target = candidate / "src/autobugfix/eval/runner.py"
    target.write_text("# manually changed before Writer start\n", encoding="utf-8")

    with pytest.raises(
        OperatorGovernanceError,
        match="not the latest service-applied Writer snapshot",
    ):
        service.start_writer(request.request_id)

    assert backend.calls == []
    assert service.store.read_writer_runs(request.request_id) == []
    assert target.read_text(encoding="utf-8") == "# manually changed before Writer start\n"


def test_writer_cannot_continue_after_candidate_tampering_between_applications(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="between-applications")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    service.start_writer(request.request_id)
    target = candidate / "src/autobugfix/eval/runner.py"
    target.write_text("# manually changed after trusted application\n", encoding="utf-8")

    with pytest.raises(
        OperatorGovernanceError,
        match="not the latest service-applied Writer snapshot",
    ):
        service.start_writer(request.request_id)

    assert len(service.store.read_writer_runs(request.request_id)) == 1
    assert target.read_text(encoding="utf-8") == "# manually changed after trusted application\n"


def test_candidate_commit_rejects_manual_patch_after_writer_application(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(service, request_id="manual-patch-before-commit")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    service.start_writer(request.request_id)
    (candidate / "src/autobugfix/eval/runner.py").write_text(
        "# manual mutation after service application\n",
        encoding="utf-8",
    )

    with pytest.raises(
        OperatorGovernanceError,
        match="not the latest service-applied Writer snapshot",
    ):
        service.commit_candidate(request.request_id, message="must not commit manual candidate patch")


def test_fast_verification_uses_detached_worktree_without_mutating_candidate(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    policy_path = write_test_policy(root, tmp_path)
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["validation_profiles"]["eval"] = {
        "timeout_seconds": 30,
        "commands": [
            {
                "name": "write-verifier-runtime-output",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "Path('__pycache__').mkdir(); "
                    "Path('__pycache__/verifier.pyc').write_bytes(b'generated'); "
                    "Path('verification-side-effect.txt').write_text('temporary'); "
                    "print(Path.cwd())",
                ],
            }
        ],
    }
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    service = service_for(root, tmp_path, policy=policy_path)
    request = create_request(service, request_id="detached-fast-verification")
    candidate = Path(service.start(request.request_id)["workspace"]["path"])
    service.start_writer(request.request_id)

    report = service.verify(request.request_id, mode="fast")

    assert report["check_run"]["status"] == "PASSED"
    command = report["check_run"]["command_results"][0]
    assert ".verification" in command["cwd"]
    assert command["cwd"] != str(candidate)
    assert not (candidate / "verification-side-effect.txt").exists()
    assert not (candidate / "__pycache__/verifier.pyc").exists()
    committed = service.commit_candidate(
        request.request_id,
        message="Commit verified Writer patch without verifier residue",
    )
    assert committed["head_sha"] != request.base_sha


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
    context = service.governance_context()
    assert context["schema"] == "autobugfix-machine-constitution-v3"
    assert context["experiment_governance_source"] == "trusted-v3-compatibility-contract"
    assert len(context["experiment_governance_digest"]) == 64
    experiment = context["experiment_governance"]
    assert experiment["studies"]["common_baseline"] == "H0_per_cohort"
    assert experiment["studies"]["independent_successors"] == ["H_bug", "H_general"]
    assert experiment["budgets"]["waves"] == [3, 8, 16]
    assert experiment["budgets"]["allowed_primary_models"] == ["gpt-5.4-mini"]
    assert experiment["budgets"]["model_fallback"] == "forbidden"
    assert experiment["metrics"]["registration_owner"] == "trusted_benchmark_guard"
    assert experiment["metrics"]["transition_input"] == "registered_metric_id_only"


def test_independent_experiment_lines_share_h0_and_bind_requests(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "experiment-manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    h0 = run(["git", "rev-parse", "HEAD"], root).stdout.strip()

    bug_study = service.create_study(
        study_id="bugfix",
        cohort_id="independent-h0-study",
        purpose="Improve the bugfix-specialized harness",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0", "holdout_regressions": 0},
        base_ref=h0,
    )
    general_study = service.create_study(
        study_id="general",
        cohort_id="independent-h0-study",
        purpose="Independently evolve the H0 bugfix harness toward general issue resolution",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0", "holdout_rescues": ">=1"},
        base_ref=h0,
        target_checkpoint_name="H_general",
    )
    bug_metric = register_baseline_metric(service, root, "bugfix")
    general_metric = register_baseline_metric(service, root, "general")
    bug = service.initialize_experiment_line(
        "bugfix",
        metric_receipt_id=bug_metric.metric_id,
    )
    general = service.initialize_experiment_line(
        "general",
        metric_receipt_id=general_metric.metric_id,
    )

    assert bug_study.base_subject_sha == general_study.base_subject_sha == h0
    assert bug_study.memory_digest == general_study.memory_digest
    assert bug_study.memory_snapshot_path != general_study.memory_snapshot_path
    assert not (Path(bug_study.memory_snapshot_path).stat().st_mode & 0o200)
    assert not (Path(general_study.memory_snapshot_path).stat().st_mode & 0o200)
    assert bug_study.manifest_digest == general_study.manifest_digest
    assert bug_study.manifest_snapshot_path != general_study.manifest_snapshot_path
    assert not (Path(bug_study.manifest_snapshot_path).stat().st_mode & 0o200)
    assert not (Path(general_study.manifest_snapshot_path).stat().st_mode & 0o200)
    assert bug["checkpoint"]["name"] == general["checkpoint"]["name"] == "H0"
    assert bug["line"]["branch"] == "experiment/bugfix-main"
    assert general["line"]["branch"] == "experiment/general-main"
    assert run(["git", "rev-parse", "experiment/bugfix-main"], root).stdout.strip() == h0
    assert run(["git", "rev-parse", "experiment/general-main"], root).stdout.strip() == h0
    assert run(["git", "rev-parse", "HEAD"], root).stdout.strip() == h0
    assert run(["git", "branch", "--show-current"], root).stdout.strip() == "main"
    budget_request = service.create_budget_request(
        "bugfix",
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        reason="Authorize the first bugfix optimization wave",
    )
    budget_grant = service.approve_budget_grant(
        budget_request.budget_request_id,
        approver="human",
        confirm_request_digest=budget_request.budget_request_digest,
    )

    triage = service.create_triage(
        triage_id="triage-line-bound",
        summary="Visible benchmark evidence points to Eval orchestration",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
        creator="operator",
        confidence="high",
    )
    request = service.create_request(
        request_id="line-bound",
        triage_id=triage.triage_id,
        summary="Repair Eval orchestration on the bugfix experiment line",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py",),
        validation_profiles=("eval",),
        experiment_line_id="bugfix",
        budget_grant_id=budget_grant.grant_id,
        creator="operator",
    )
    assert request.base_sha == h0
    assert request.experiment_line_id == "bugfix"
    assert request.experiment_line_generation == 0
    assert service.preflight(request.request_id)["allowed"]

    tree = run(["git", "rev-parse", f"{h0}^{{tree}}"], root).stdout.strip()
    advanced_sha = run(
        ["git", "commit-tree", tree, "-p", h0, "-m", "competing integration"],
        root,
    ).stdout.strip()
    run(["git", "update-ref", "refs/heads/experiment/bugfix-main", advanced_sha, h0], root)
    line = service.store.read_experiment_line("bugfix")
    service.store.compare_and_swap_experiment_line(
        replace(line, head_sha=advanced_sha, generation=1),
        expected_head_sha=h0,
        expected_generation=0,
    )
    stale = service.preflight(request.request_id)
    assert not stale["allowed"]
    assert "operator request experiment line advanced after request creation" in stale["violations"]
    assert run(["git", "rev-parse", "experiment/general-main"], root).stdout.strip() == h0


def test_experiment_study_and_line_cli_use_service_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    policy = write_test_policy(root, tmp_path)
    manifest = root / "manifest.yaml"
    contract = root / "success.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    contract.write_text("visible_net_gain: '>0'\nholdout_regressions: 0\n", encoding="utf-8")
    monkeypatch.chdir(root)

    assert main(
        [
            "operator",
            "study",
            "create",
            "--trusted-file",
            str(policy),
            "--study-id",
            "cli-study",
            "--purpose",
            "Exercise the governed CLI",
            "--manifest",
            str(manifest),
            "--success-contract",
            str(contract),
            "--base-ref",
            "main",
        ]
    ) == 0
    created = yaml.safe_load(capsys.readouterr().out)
    assert created["study_id"] == "cli-study"
    assert created["primary_model"] == "gpt-5.4-mini"
    assert "memory_snapshot_path" not in created
    assert "manifest_snapshot_path" not in created
    guard_service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=policy,
        backend=OperatorBackend(),
    )
    metric = register_baseline_metric(guard_service, root, "cli-study")

    assert main(
        [
            "operator",
            "line",
            "init",
            "--trusted-file",
            str(policy),
            "--study-id",
            "cli-study",
            "--metric-receipt-id",
            metric.metric_id,
        ]
    ) == 0
    initialized = yaml.safe_load(capsys.readouterr().out)
    assert initialized["line"]["branch"] == "experiment/cli-study-main"

    assert main(
        [
            "operator",
            "line",
            "show",
            "--trusted-file",
            str(policy),
            "--line-id",
            "cli-study",
        ]
    ) == 0
    projection = yaml.safe_load(capsys.readouterr().out)
    assert projection["line"]["generation"] == 0
    assert projection["checkpoints"][0]["name"] == "H0"
    assert "memory_snapshot_path" not in projection["study"]
    assert "manifest_snapshot_path" not in projection["study"]
    assert "artifact_path" not in projection["metrics"][0]

    assert main(
        [
            "operator",
            "budget",
            "request",
            "--trusted-file",
            str(policy),
            "--study-id",
            "cli-study",
            "--wave",
            "3",
            "--case",
            "case-1",
            "--case",
            "case-2",
            "--case",
            "case-3",
            "--reason",
            "Approve the bounded CLI smoke wave",
        ]
    ) == 0
    budget_request = yaml.safe_load(capsys.readouterr().out)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _: f"APPROVE {budget_request['record_digest']}",
    )
    assert main(
        [
            "operator",
            "budget",
            "approve",
            "--trusted-file",
            str(policy),
            "--budget-request-id",
            budget_request["budget_request_id"],
            "--approver",
            "human",
            "--confirm-request-digest",
            budget_request["record_digest"],
        ]
    ) == 0
    grant = yaml.safe_load(capsys.readouterr().out)
    assert grant["wave"] == 3
    assert grant["model"] == "gpt-5.4-mini"


def test_experiment_cohort_rejects_a_different_h0_commit(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    service.create_study(
        study_id="cohort-bugfix",
        cohort_id="shared-h0",
        purpose="Freeze the bugfix treatment baseline",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref="main",
        target_checkpoint_name="H_bug",
    )
    h0 = run(["git", "rev-parse", "main"], root).stdout.strip()
    tree = run(["git", "rev-parse", f"{h0}^{{tree}}"], root).stdout.strip()
    different_h0 = run(
        ["git", "commit-tree", tree, "-p", h0, "-m", "different H0 identity"],
        root,
    ).stdout.strip()

    with pytest.raises(OperatorGovernanceError, match="base_subject_sha"):
        service.create_study(
            study_id="cohort-general",
            cohort_id="shared-h0",
            purpose="Must not silently use another H0",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref=different_h0,
            target_checkpoint_name="H_general",
        )

    assert [item.study_id for item in service.store.read_studies()] == [
        "cohort-bugfix"
    ]


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


def test_remote_operator_gate_fetches_history_and_preserves_authoritative_logs():
    workflow = (PROJECT_ROOT / ".github/workflows/operator-policy.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request_review" not in workflow
    assert "branches: [main]" in workflow
    assert "github.event_name == 'pull_request_target'" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert workflow.count("fetch-depth: 0") == 3
    assert "ref: ${{ github.workflow_sha }}" in workflow
    assert "path: guard" in workflow
    assert "Resolve signed frozen authority base" in workflow
    assert "--resolve-frozen-base-only" in workflow
    assert "candidate.glob" not in workflow
    assert "ref: ${{ steps.frozen-base.outputs.sha }}" in workflow
    assert "merge-base --is-ancestor" in workflow
    assert "kernel.unprivileged_userns_clone=1" in workflow
    assert 'profile autobugfix-bwrap /usr/bin/bwrap flags=(unconfined)' in workflow
    assert 'sudo apparmor_parser -r "$profile"' in workflow
    assert "--unshare-net" in workflow
    assert "bwrap --die-with-parent --unshare-user --ro-bind / /" in workflow
    assert "guard/.venv/bin/python guard/scripts/validate_operator_pr.py" in workflow
    assert "--runtime-venv guard/.venv" in workflow
    assert "--expected-guard-sha ${{ github.workflow_sha }}" in workflow
    assert "--expected-base-sha ${{ steps.frozen-base.outputs.sha }}" in workflow
    assert workflow.count("--pull-request-base-sha") == 2
    assert "--skip-live-experiment" in workflow
    assert "GH_TOKEN" not in workflow
    assert "pull-requests: read" not in workflow
    assert "kernel.apparmor_restrict_unprivileged_userns=0" not in workflow
    assert workflow.index("--unshare-net") < workflow.index(
        "Validate candidate with trusted base policy"
    )
    assert "actions/upload-artifact@v4" in workflow
    assert "trusted/.autobugfix/operator-pr" in workflow


def test_remote_gate_selects_only_bundle_changed_by_current_pull_request(tmp_path: Path):
    validator = load_operator_pr_validator()
    root = tmp_path / "candidate"
    root.mkdir()
    run(["git", "init", "-b", "main"], root)
    run(["git", "config", "user.email", "operator@example.com"], root)
    run(["git", "config", "user.name", "Operator User"], root)
    inherited = root / ".autobugfix-governance/old-request/bundle.yaml"
    inherited.parent.mkdir(parents=True)
    inherited.write_text("request: {base_sha: inherited}\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "historical bundle"], root)
    pull_request_base = run(["git", "rev-parse", "HEAD"], root).stdout.strip()

    current = root / ".autobugfix-governance/current-request/bundle.yaml"
    current.parent.mkdir(parents=True)
    current.write_text("request: {base_sha: current}\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "current bundle"], root)

    assert validator.changed_operator_bundle(root, pull_request_base) == current

    duplicate = root / ".autobugfix-governance/duplicate-request/bundle.yaml"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text("request: {base_sha: duplicate}\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "duplicate current bundle"], root)

    with pytest.raises(RuntimeError, match="changed by this pull request, found 2"):
        validator.changed_operator_bundle(root, pull_request_base)


def test_sandbox_remaps_read_only_runtime_venv_environment(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("runtime overlay is owned by the inherited admission sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    destination = candidate / ".venv"
    destination.mkdir()
    (destination / "candidate-controlled").write_text("untrusted\n", encoding="utf-8")
    package = candidate / "src/autobugfix"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('ORIGIN = "candidate"\n', encoding="utf-8")
    runtime_venv = Path(sys.prefix).resolve()

    result = _run_command(
        candidate,
        tmp_path / "logs",
        "runtime-venv",
        [
            "uv",
            "run",
            "--cache-dir",
            "/tmp/uv-cache",
            "python",
            "-c",
            (
                "import os, subprocess; from pathlib import Path; import autobugfix; "
                f"assert os.environ['VIRTUAL_ENV'] == {str(destination)!r}; "
                f"assert os.environ['UV_PROJECT_ENVIRONMENT'] == {str(destination)!r}; "
                "assert autobugfix.ORIGIN == 'candidate'; "
                "assert subprocess.run(['uv', '--version'], capture_output=True).returncode == 0; "
                "assert not Path('.venv/candidate-controlled').exists(); "
                "p = Path('.venv/guard-write-probe'); "
                "\ntry: p.write_text('forbidden')\nexcept OSError: pass\n"
                "else: raise AssertionError('trusted runtime venv is writable')"
            ),
        ],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=((runtime_venv, destination),),
    )

    assert result["passed"], Path(result["stderr_path"]).read_text(encoding="utf-8")
    argv = result["executed_argv"]
    candidate_bind = argv.index(str(candidate))
    runtime_bind = argv.index(str(runtime_venv))
    assert candidate_bind < runtime_bind

    console_script = _run_command(
        candidate,
        tmp_path / "console-logs",
        "runtime-console-script",
        ["uv", "run", "--cache-dir", "/tmp/uv-cache", "pytest", "--version"],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=((runtime_venv, destination),),
    )
    assert console_script["passed"], Path(console_script["stderr_path"]).read_text(
        encoding="utf-8"
    )


def test_sandbox_reopens_linked_worktree_git_metadata_read_only(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("linked metadata is owned by the inherited admission sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    repository = tmp_path / "repository"
    repository.mkdir()
    run(["git", "init", "-b", "main"], repository)
    run(["git", "config", "user.email", "operator@example.com"], repository)
    run(["git", "config", "user.name", "Operator User"], repository)
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    run(["git", "add", "tracked.txt"], repository)
    run(["git", "commit", "-m", "base"], repository)
    base_sha = run(["git", "rev-parse", "HEAD"], repository).stdout.strip()
    candidate = tmp_path / "candidate"
    run(["git", "worktree", "add", "-b", "candidate", str(candidate)], repository)
    (candidate / "tracked.txt").write_text("candidate\n", encoding="utf-8")

    result = _run_command(
        candidate,
        tmp_path / "logs",
        "linked-worktree-git",
        [
            "/bin/sh",
            "-c",
            (
                f"git diff --check {base_sha} -- && "
                "git ls-files --error-unmatch tracked.txt >/dev/null && "
                "common=$(git rev-parse --git-common-dir) && "
                "if touch \"$common/write-probe\" 2>/dev/null; then exit 73; fi"
            ),
        ],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=(),
    )

    assert result["passed"], Path(result["stderr_path"]).read_text(encoding="utf-8")
    assert not (repository / ".git/write-probe").exists()
    common_dir = str((repository / ".git").resolve())
    argv = result["executed_argv"]
    assert any(
        argv[index : index + 3] == ["--ro-bind", common_dir, common_dir]
        for index in range(len(argv) - 2)
    )


def test_sandbox_rejects_mismatched_linked_worktree_back_pointer(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("linked metadata is owned by the inherited admission sandbox")
    repository = tmp_path / "repository"
    repository.mkdir()
    run(["git", "init", "-b", "main"], repository)
    run(["git", "config", "user.email", "operator@example.com"], repository)
    run(["git", "config", "user.name", "Operator User"], repository)
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    run(["git", "add", "tracked.txt"], repository)
    run(["git", "commit", "-m", "base"], repository)
    candidate = tmp_path / "candidate"
    run(["git", "worktree", "add", "-b", "candidate", str(candidate)], repository)
    git_dir = Path(
        (candidate / ".git")
        .read_text(encoding="utf-8")
        .strip()
        .removeprefix("gitdir: ")
    )
    (git_dir / "gitdir").write_text(
        str(tmp_path / "other/.git") + "\n", encoding="utf-8"
    )

    with pytest.raises(OperatorValidationError, match="back pointer does not match"):
        _run_command(
            candidate,
            tmp_path / "logs",
            "mismatched-worktree-git",
            ["/bin/true"],
            30,
            process_sandbox="bubblewrap",
            require_process_sandbox=True,
            network_access=False,
            hidden_roots=(),
            writable_roots=(),
            read_only_binds=(),
        )


def test_sandbox_reopens_exact_runtime_source_below_masked_tmp(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("runtime overlay is owned by the inherited admission sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    destination = candidate / ".venv"
    runtime = tmp_path / "trusted-runtime"
    subprocess.run(
        [sys.executable, "-m", "venv", str(runtime)],
        check=True,
        text=True,
        capture_output=True,
    )
    probe = runtime / "bin/runtime-probe"
    probe.write_text(
        f"#!{runtime / 'bin/python'}\n"
        "import os\n"
        f"assert os.environ['VIRTUAL_ENV'] == {str(destination)!r}\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    result = _run_command(
        candidate,
        tmp_path / "logs",
        "tmp-runtime-source",
        [str(probe)],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=((runtime, destination),),
    )

    assert result["passed"], Path(result["stderr_path"]).read_text(encoding="utf-8")


def test_sandbox_rejects_runtime_source_equal_to_masked_tmp(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("runtime overlay is owned by the inherited admission sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    with pytest.raises(OperatorValidationError, match="masked authority root"):
        _run_command(
            candidate,
            tmp_path / "logs",
            "broad-tmp-runtime",
            ["/bin/true"],
            30,
            process_sandbox="bubblewrap",
            require_process_sandbox=True,
            network_access=False,
            hidden_roots=(),
            writable_roots=(),
            read_only_binds=((Path("/tmp"), candidate / ".venv"),),
        )


def test_sandbox_blocks_secrets_and_host_docker_daemon_across_nested_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    monkeypatch.setenv("AUTOBUGFIX_REVIEW_API_KEY", "must-not-enter-sandbox")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    outer = _run_command(
        candidate,
        tmp_path / "outer-logs",
        "outer-secret-boundary",
        ["/bin/sh", "-c", "test -z \"${AUTOBUGFIX_REVIEW_API_KEY:-}\""],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=(),
    )
    assert outer["passed"], Path(outer["stderr_path"]).read_text(encoding="utf-8")

    nested = _run_command(
        candidate,
        tmp_path / "nested-logs",
        "nested-userns",
        [
            "/bin/sh",
            "-c",
            "test -z \"${AUTOBUGFIX_REVIEW_API_KEY:-}\" && "
            "bwrap --die-with-parent --unshare-user --ro-bind / / -- "
            "/bin/sh -c 'test -z \"${AUTOBUGFIX_REVIEW_API_KEY:-}\"'",
        ],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=(),
    )
    if not nested["passed"]:
        nested_error = Path(nested["stderr_path"]).read_text(encoding="utf-8")
        assert (
            "No permissions to create a new namespace" in nested_error
            or "Operation not permitted" in nested_error
        ), nested_error

    if shutil.which("docker") is None:
        pytest.skip("Docker client is unavailable")
    host = subprocess.run(
        ["docker", "version"], text=True, capture_output=True, check=False, timeout=30
    )
    if host.returncode != 0:
        pytest.skip("host Docker daemon is unavailable")
    docker = _run_command(
        candidate,
        tmp_path / "docker-logs",
        "docker-daemon",
        ["docker", "version"],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(),
        read_only_binds=(),
    )
    assert not docker["passed"], "sandbox connected to the host Docker daemon"


def test_sandbox_rejects_candidate_runtime_symlink(tmp_path: Path):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("runtime destination is prepared by the inherited admission sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (candidate / ".venv").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OperatorValidationError, match="traverses a symlink"):
        _run_command(
            candidate,
            tmp_path / "logs",
            "runtime-symlink",
            ["/bin/true"],
            30,
            process_sandbox="bubblewrap",
            require_process_sandbox=True,
            network_access=False,
            hidden_roots=(),
            writable_roots=(),
            read_only_binds=((Path(sys.prefix), candidate / ".venv"),),
        )


def test_operator_codex_server_enforces_role_sequence_and_authority_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_home = tmp_path / "host-codex"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"host-only"}\n', encoding="utf-8")
    allowed_root = tmp_path / "shadow"
    control_root = allowed_root / "control"
    worktree = allowed_root / "target-worktree"
    worktree.mkdir(parents=True)
    (control_root / ".autobugfix").mkdir(parents=True)
    (control_root / ".autobugfix/config.yaml").write_text("{}\n", encoding="utf-8")
    outside_control = tmp_path / "outside-control"
    (outside_control / ".autobugfix").mkdir(parents=True)
    (outside_control / ".autobugfix/config.yaml").write_text("{}\n", encoding="utf-8")
    backend = BrokerBackend()
    socket_path = tmp_path / "capability/codex.sock"
    artifact_root = tmp_path / "authority/calls"
    runtime_root = tmp_path / "authority/runtime"
    server = _OperatorCodexServer(
        socket_path,
        "one-command-token",
        allowed_roots=(allowed_root,),
        artifact_root=artifact_root,
        runtime_root=runtime_root,
        source_home=source_home,
        model="gpt-5.4-mini",
        required_role_sequence=("writer", "evaluator"),
        role_timeout_seconds={"writer": 60, "evaluator": 60},
        backend=backend,
    )

    def request(
        role: str,
        cwd: Path,
        *,
        sandbox: str,
        approval_mode: str,
        index: int,
        timeout_seconds: int = 30,
    ) -> CodexRequest:
        return CodexRequest(
            role=role,
            prompt=f"prompt {index}",
            cwd=cwd,
            sandbox=sandbox,
            model="gpt-5.4-mini",
            timeout_seconds=timeout_seconds,
            developer_instructions="bounded role contract",
            raw_log_path=control_root / f"client-{index}.raw.jsonl",
            stderr_log_path=control_root / f"client-{index}.stderr.log",
            approval_mode=approval_mode,
        )

    with server:
        monkeypatch.setenv(CODEX_BROKER_SOCKET_ENV, str(socket_path))
        monkeypatch.setenv(CODEX_BROKER_TOKEN_ENV, "one-command-token")
        client = CodexSDKBackend("candidate_sdk_must_not_load")
        with pytest.raises(CodexSDKError, match="role sequence mismatch"):
            client.run(
                request(
                    "evaluator",
                    worktree,
                    sandbox="read-only",
                    approval_mode="deny_all",
                    index=1,
                )
            )
        with pytest.raises(CodexSDKError, match="outside sandboxed profile state"):
            client.run(
                request(
                    "writer",
                    outside_control,
                    sandbox="workspace-write",
                    approval_mode="auto_review",
                    index=2,
                )
            )
        with pytest.raises(CodexSDKError, match="timeout exceeds"):
            client.run(
                request(
                    "writer",
                    worktree,
                    sandbox="workspace-write",
                    approval_mode="auto_review",
                    index=3,
                    timeout_seconds=61,
                )
            )
        writer_result = client.run(
            request(
                "writer",
                worktree,
                sandbox="workspace-write",
                approval_mode="auto_review",
                index=4,
            )
        )
        assert writer_result.text == "completed writer"
        with pytest.raises(CodexSDKError, match="role sequence mismatch"):
            client.run(
                request(
                    "writer",
                    worktree,
                    sandbox="workspace-write",
                    approval_mode="auto_review",
                    index=5,
                )
            )
        evaluator_result = client.run(
            request(
                "evaluator",
                worktree,
                sandbox="read-only",
                approval_mode="deny_all",
                index=6,
            )
        )
        assert evaluator_result.text == "completed evaluator"
        assert server.completion_error() is None

    assert [call.role for call in backend.calls] == ["writer", "evaluator"]
    assert not socket_path.exists()
    assert not runtime_root.exists()
    artifacts = server.artifact_paths()
    assert len(artifacts) == 7
    events = (artifact_root / "broker-events.jsonl").read_text(encoding="utf-8")
    assert events.count('"status": "rejected"') == 4
    assert events.count('"status": "completed"') == 2
    assert all("one-command-token" not in Path(path).read_text(encoding="utf-8") for path in artifacts)
    assert all("host-only" not in Path(path).read_text(encoding="utf-8") for path in artifacts)


def test_brokered_command_fails_if_required_sdk_sequence_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap":
        pytest.skip("credential broker intentionally rejects an inherited sandbox")
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-test-key")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    result = _run_command(
        candidate,
        tmp_path / "logs",
        "broker-skip",
        ["/bin/true"],
        30,
        process_sandbox="bubblewrap",
        require_process_sandbox=True,
        network_access=False,
        hidden_roots=(),
        writable_roots=(shadow,),
        read_only_binds=(),
        codex_broker={
            "enabled": True,
            "model": "gpt-5.4-mini",
            "required_role_sequence": ["writer"],
            "role_timeout_seconds": {"writer": 60},
        },
    )

    assert result["exit_code"] == 125
    assert not result["passed"]
    assert "role sequence incomplete" in Path(result["stderr_path"]).read_text(
        encoding="utf-8"
    )
    capability_paths = {
        Path(value)
        for value in result["executed_argv"]
        if value.startswith("/tmp/autobugfix-operator-codex-")
    }
    assert capability_paths
    assert all(not path.exists() for path in capability_paths)
    assert result["codex_call_artifacts"] == []


def test_real_state_machine_uses_sandboxed_checks_and_advisory_manifest(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request, candidate = complete_request(service, "bundle")
    assert service.audit(request.request_id)["allowed"]
    writer_view = json.dumps(service.writer_view(request.request_id), sort_keys=True)
    assert str(service.store.root) not in writer_view
    full_check = service.store.read_check_runs(request.request_id)[-1]
    assert full_check.command_results[0]["sandbox"] == "bubblewrap"
    expected = (
        "validated in inherited sandbox"
        if os.environ.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap"
        else "validated"
    )
    assert (
        Path(full_check.command_results[0]["stdout_path"])
        .read_text(encoding="utf-8")
        .strip()
        == expected
    )
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
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    stdout.write_text("passed\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    profile_contract = {"commands": [{"name": "smoke", "argv": ["true"]}]}
    baseline_receipt = derive_metric_receipt(
        source="test",
        profile="smoke",
        values={},
        base_sha="abc123",
        head_sha="abc123",
        patch_digest="base-patch",
        command_results=(
            {
                "name": "smoke",
                "exit_code": 0,
                "passed": True,
                "timed_out": False,
                "duration_seconds": 10.0,
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
            },
        ),
        profile_contract=profile_contract,
    )
    record_baseline(
        root,
        "eval-smoke",
        baseline_receipt,
        profile_values={},
    )
    contract = yaml.safe_load(PACKAGE_POLICY.read_text(encoding="utf-8"))["metrics"]
    missing_receipt = dict(baseline_receipt)
    missing_receipt["metrics"] = {"pass_rate": 1.0}
    missing_payload = {key: value for key, value in missing_receipt.items() if key != "receipt_digest"}
    missing_receipt["receipt_digest"] = digest_payload(missing_payload)
    missing = compare_baseline(root, "eval-smoke", missing_receipt, contract)
    regressed_receipt = derive_metric_receipt(
        source="test",
        profile="smoke",
        values={},
        base_sha="abc123",
        head_sha="def456",
        patch_digest="candidate-patch",
        command_results=(
            {
                "name": "smoke",
                "exit_code": 1,
                "passed": False,
                "timed_out": False,
                "duration_seconds": 20.0,
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
            },
        ),
        profile_contract=profile_contract,
    )
    regressed = compare_baseline(
        root,
        "eval-smoke",
        regressed_receipt,
        contract,
    )
    assert not missing["ok"]
    assert any("artifact_completeness" in item for item in missing["failures"])
    assert not regressed["ok"]
    assert any("pass_rate" in item for item in regressed["failures"])


def test_behavior_scope_requires_baseline_and_patch_bound_experiment(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    policy_path = write_test_policy(root, tmp_path)
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["baseline_required_layers"] = ["eval"]
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    service = service_for(root, tmp_path, policy=policy_path)

    missing = create_request(service, request_id="baseline-missing")
    report = service.preflight(missing.request_id)
    assert not report["allowed"]
    assert "behavior-affecting scope requires a trusted performance baseline" in report["violations"]

    captured = service.capture_baseline("eval-smoke", profile="smoke")
    assert captured["baseline"]["metrics"]["pass_rate"] == 1.0
    unpublished = create_request(
        service,
        request_id="baseline-unpublished",
        baseline="eval-smoke",
    )
    unpublished_report = service.preflight(unpublished.request_id)
    assert not unpublished_report["allowed"]
    assert any("not committed" in item for item in unpublished_report["violations"])
    run(["git", "add", ".autobugfix-baselines/eval-smoke.yaml"], root)
    run(["git", "commit", "-m", "Record trusted Eval baseline"], root)
    request = create_request(service, request_id="baseline-bound", baseline="eval-smoke")
    service.start(request.request_id)
    service.start_writer(request.request_id)
    assert service.verify(request.request_id, mode="fast")["check_run"]["status"] == "PASSED"
    service.commit_candidate(request.request_id, message="candidate with trusted baseline")

    without_experiment = service.verify(request.request_id, mode="full")
    assert without_experiment["check_run"]["status"] == "FAILED"
    assert any(
        "missing completed trusted experiment" in item
        for item in without_experiment["check_run"]["failures"]
    )

    experiment = service.run_experiment(request.request_id, profile="smoke")
    assert experiment["status"] == "COMPLETED"
    assert experiment["metric_receipt"]["patch_digest"] == service._snapshot(
        request.request_id
    ).patch_digest
    verified = service.verify(request.request_id, mode="full")
    assert verified["check_run"]["status"] == "PASSED"
    assert verified["regression"]["ok"]
    manifest = (
        Path(service.store.read_workspace(request.request_id)["path"])
        / ".autobugfix-governance"
        / request.request_id
        / "bundle.yaml"
    )
    trusted = load_trusted_policy(root, trusted_ref=None, trusted_file=policy_path)
    remote = validate_bundle(
        manifest,
        manifest.parents[2],
        trusted,
        run_profiles=True,
        trusted_baseline_root=root,
    )
    assert remote["allowed"]
    assert remote["regression"]["ok"]
    assert remote["metric_receipt"]["source"] == "trusted_pr_admission_experiment"

    deterministic_only = validate_bundle(
        manifest,
        manifest.parents[2],
        trusted,
        run_profiles=True,
        run_experiments=False,
        trusted_baseline_root=root,
    )
    assert deterministic_only["allowed"]
    assert deterministic_only["experiments_deferred"]
    assert deterministic_only["experiment_results"] == []
    assert deterministic_only["metric_receipt"] is None
    assert deterministic_only["regression"] is None


def test_failed_profile_cannot_be_published_as_trusted_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    monkeypatch.setattr(
        "autobugfix.operator.service.run_command_specs",
        lambda *args, **kwargs: [
            {
                "name": "failed-real-e2e",
                "passed": False,
                "timed_out": False,
                "exit_code": 1,
            }
        ],
    )

    with pytest.raises(
        OperatorGovernanceError,
        match="trusted baseline profile did not pass: failed-real-e2e",
    ):
        service.capture_baseline("failed-baseline", profile="smoke")

    assert not (root / ".autobugfix-baselines/failed-baseline.yaml").exists()


def test_measurement_baseline_records_real_expected_target_failure(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    config_path = root / ".autobugfix/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["operator"]["experiments"]["profiles"]["failing-measurement"] = {
        "baseline_mode": "measure",
        "commands": [
            {
                "name": "known-bug-test",
                "argv": [sys.executable, "-c", "raise SystemExit(1)"],
            }
        ],
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    service = OperatorGovernanceService(
        root,
        trusted_ref=None,
        trusted_file=write_test_policy(root, tmp_path),
        backend=OperatorBackend(),
    )

    captured = service.capture_baseline("known-bug-h0", profile="failing-measurement")

    command = captured["baseline"]["commands"][0]
    assert captured["baseline"]["metrics"]["pass_rate"] == 0.0
    assert captured["baseline"]["profile_contract"]["baseline_mode"] == "measure"
    assert command["exit_code"] == 1
    assert command["passed"] is False
    assert len(command["stdout_sha256"]) == 64
    assert len(command["stderr_sha256"]) == 64
    baseline_logs = service.store.artifact_root / "baselines"
    assert any(path.name.endswith("stdout.log") for path in baseline_logs.rglob("*"))
    assert any(path.name.endswith("stderr.log") for path in baseline_logs.rglob("*"))


def test_operator_experiment_profile_rejects_unknown_baseline_mode(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    write_control_config(root)
    config_path = root / ".autobugfix/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["operator"]["experiments"]["profiles"]["smoke"]["baseline_mode"] = "ignore-all"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="baseline_mode must be strict or measure"):
        load_config(root)


def test_committed_baseline_rejects_later_behavior_commit(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    policy_path = write_test_policy(root, tmp_path)
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["baseline_required_layers"] = ["eval"]
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    service = service_for(root, tmp_path, policy=policy_path)
    service.capture_baseline("stale-eval", profile="smoke")
    run(["git", "add", ".autobugfix-baselines/stale-eval.yaml"], root)
    run(["git", "commit", "-m", "Record Eval baseline"], root)
    runner = root / "src/autobugfix/eval/runner.py"
    runner.write_text("# behavior changed after baseline measurement\n", encoding="utf-8")
    run(["git", "add", str(runner.relative_to(root))], root)
    run(["git", "commit", "-m", "Change Eval behavior"], root)
    request = create_request(service, request_id="stale-baseline", baseline="stale-eval")

    report = service.preflight(request.request_id)

    assert not report["allowed"]
    assert any("baseline is stale" in item for item in report["violations"])


def test_baseline_profile_values_reject_secrets_and_local_absolute_paths(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)

    with pytest.raises(OperatorGovernanceError, match="sensitive authority data"):
        service.capture_baseline(
            "secret-input",
            profile="smoke",
            values={"api_token": "do-not-commit"},
        )
    with pytest.raises(OperatorGovernanceError, match="CI-portable"):
        service.capture_baseline(
            "local-path-input",
            profile="smoke",
            values={"dataset": str(tmp_path / "cases.jsonl")},
        )


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
                "verify",
                "--trusted-file",
                trusted,
                "--request-id",
                "forged",
                "--metric",
                "pass_rate=1",
            ]
        )
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


def test_constitution_classifies_every_governed_source_path_once():
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
    invalid = {
        path: layers_for_file(constitution, path)
        for path in tracked
        if path.startswith(prefixes) and len(layers_for_file(constitution, path)) != 1
    }
    assert invalid == {}


def test_layer_resolution_prefers_specific_rule_and_rejects_tied_owners():
    constitution = {
        "layer_resolution": {"strategy": "most_specific", "ambiguity": "reject"},
        "layers": {
            "docs_skills": {"paths": [".agents/role-skills/**"]},
            "execution": {"paths": [".agents/role-skills/execution/**"]},
        },
    }
    assert layers_for_file(constitution, ".agents/role-skills/execution/writer/SKILL.md") == [
        "execution"
    ]
    constitution["layers"]["memory"] = {"paths": [".agents/role-skills/execution/**"]}
    assert layers_for_file(constitution, ".agents/role-skills/execution/writer/SKILL.md") == [
        "execution",
        "memory",
    ]


def test_planned_glob_that_can_touch_protected_path_requires_constitutional_scope(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    request = create_request(
        service,
        request_id="protected-glob",
        planned_paths=("src/autobugfix/eval/**",),
    )

    risk, violations = compute_scope_risk(request, service.policy().data)

    assert risk == "constitutional"
    assert not violations


def test_request_requires_planned_paths_and_scope_layer_expansion_requires_path(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    triage = service.create_triage(
        triage_id="triage-scope-required",
        summary="scope",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
        creator="operator-agent",
    )
    with pytest.raises(OperatorModelError, match="planned_paths must not be empty"):
        service.create_request(
            request_id="scope-required",
            triage_id=triage.triage_id,
            summary="scope",
            primary_layer="eval",
            validation_profiles=("eval",),
            creator="operator-agent",
        )

    create_request(service, request_id="scope-expand")
    with pytest.raises(OperatorGovernanceError, match="requires at least one planned path"):
        service.request_scope_change(
            "scope-expand",
            add_layers=("shared_runtime",),
            reason="config is implicated",
        )
    with pytest.raises(OperatorGovernanceError, match="no matching planned path"):
        service.request_scope_change(
            "scope-expand",
            add_layers=("shared_runtime",),
            add_paths=("src/autobugfix/eval/runner.py",),
            reason="declared layer does not match the proposed path",
        )


def test_real_repository_clone_uses_http1_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_repository_acceptance = load_real_repository_acceptance()
    seed = tmp_path / "upstream-seed"
    calls: list[list[str]] = []
    delays: list[int] = []
    timeouts: list[int | None] = []

    def fake_run(argv: list[str], *args, **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        timeouts.append(kwargs.get("timeout_seconds"))
        if len(calls) < 3:
            seed.mkdir()
            raise RuntimeError("transient clone failure")
        seed.mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(real_repository_acceptance, "run", fake_run)
    monkeypatch.setattr(real_repository_acceptance.time, "sleep", delays.append)

    real_repository_acceptance.clone_upstream(seed)

    assert len(calls) == 3
    assert all(call[1:3] == ["-c", "http.version=HTTP/1.1"] for call in calls)
    assert all(call[-2:] == [real_repository_acceptance.UPSTREAM_URL, str(seed)] for call in calls)
    assert delays == [1, 2]
    assert timeouts == [real_repository_acceptance.UPSTREAM_CLONE_TIMEOUT_SECONDS] * 3


def test_real_repository_oracle_uses_independent_git_object_database(
    tmp_path: Path,
) -> None:
    acceptance = load_real_repository_acceptance()
    target = tmp_path / "target"
    target.mkdir()
    run(["git", "init", "-b", "main"], target)
    run(["git", "config", "user.email", "target@example.invalid"], target)
    run(["git", "config", "user.name", "Target Fixture"], target)
    package = target / "src/itsdangerous"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "encoding.py").write_text(
        "import base64\n\n"
        "def base64_encode(string: bytes) -> bytes:\n"
        f"    {acceptance.BUGGY_LINE}\n",
        encoding="utf-8",
    )
    test_path = target / acceptance.TEST_PATH
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "from itsdangerous.encoding import base64_encode\n\n"
        "def test_base64_encode_omits_url_padding():\n"
        "    assert base64_encode(b'a') == b'YQ'\n",
        encoding="utf-8",
    )
    run(["git", "add", "."], target)
    run(["git", "commit", "-m", "buggy base"], target)
    base = run(["git", "rev-parse", "HEAD"], target).stdout.strip()
    guard_root = tmp_path / "guard"
    guard_root.mkdir()

    oracle_main, _, oracle_worktree, oracle_commit = (
        acceptance.prepare_independent_oracle(
            guard_root,
            target,
            base,
            Path(sys.executable),
        )
    )

    assert oracle_main.is_dir()
    assert oracle_worktree.is_dir()
    assert run(
        ["git", "show-ref", "--verify", f"refs/heads/{acceptance.ORACLE_BRANCH}"],
        oracle_main,
    ).stdout.strip()
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{acceptance.ORACLE_BRANCH}"],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{oracle_commit}^{{commit}}"],
            cwd=target,
            text=True,
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )


def test_real_repository_clone_fails_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_repository_acceptance = load_real_repository_acceptance()
    seed = tmp_path / "upstream-seed"
    calls = 0

    def fake_run(argv: list[str], *args, **kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        seed.mkdir()
        raise RuntimeError("persistent clone failure")

    monkeypatch.setattr(real_repository_acceptance, "run", fake_run)
    monkeypatch.setattr(real_repository_acceptance.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="persistent clone failure"):
        real_repository_acceptance.clone_upstream(seed)

    assert calls == real_repository_acceptance.UPSTREAM_CLONE_ATTEMPTS


def test_real_repository_command_timeout_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_repository_acceptance = load_real_repository_acceptance()

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            ["git", "clone"],
            1,
            output=b"partial stdout",
            stderr=b"stalled transport",
        )

    monkeypatch.setattr(real_repository_acceptance.subprocess, "run", time_out)

    with pytest.raises(RuntimeError, match=r"command timed out after 1s: git clone"):
        real_repository_acceptance.run(["git", "clone"], timeout_seconds=1)
