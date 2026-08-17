from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import pytest
import yaml

from autobugfix.models import CodexRequest, CodexResult
from autobugfix.cli import main
from autobugfix.eval.benchmarks.authority import GuardCodeIdentity
from autobugfix.eval.benchmarks.guard import metric_payload, signed_metric
from autobugfix.operator.models import digest_payload
from autobugfix.operator.service import (
    OperatorGovernanceError,
    OperatorGovernanceService,
)
from autobugfix.operator.store import OperatorStoreError
from tests.test_operator_policy import make_operator_repo, run, service_for


def private_empty_memory_root(path: Path) -> Path:
    for directory in (
        path,
        path / "active",
        path / "skills",
        path / "skills/approved",
    ):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
    return path


class StudyBackend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[CodexRequest] = []

    def run(self, request: CodexRequest) -> CodexResult:
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text('{"kind":"real-sdk-shape"}\n', encoding="utf-8")
        request.stderr_log_path.write_text("", encoding="utf-8")
        if self.error is not None:
            request.stderr_log_path.write_text(str(self.error), encoding="utf-8")
            raise self.error
        return CodexResult(
            text="completed",
            raw={
                "response": {
                    "thread_id": "thread-1",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 30,
                    },
                }
            },
        )


def register_h0_metric(service, root: Path, study_id: str = "budget-study"):
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


def initialized_study(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    service.create_study(
        study_id="budget-study",
        purpose="Test deterministic budget authority",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref="main",
    )
    metric = register_h0_metric(service, root)
    service.initialize_experiment_line(
        "budget-study",
        metric_receipt_id=metric.metric_id,
    )
    return root, service


def test_empty_study_memory_matches_exp2_fixture_digest(tmp_path: Path) -> None:
    _, service = initialized_study(tmp_path)

    assert (
        service.store.read_study("budget-study").memory_digest
        == service.exp2_empty_memory_digest()
    )


def test_study_rejects_arbitrary_memory_snapshot_root(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1]\n", encoding="utf-8")
    forged_memory = tmp_path / "oracle-as-memory"
    forged_memory.mkdir()

    with pytest.raises(OperatorGovernanceError, match="canonical approved active"):
        service.create_study(
            study_id="forged-memory-study",
            purpose="Reject non-Memory evidence",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref="main",
            memory_root=forged_memory,
        )


def test_exp2_study_uses_dedicated_empty_memory_without_touching_canonical(
    tmp_path: Path,
) -> None:
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1]\n", encoding="utf-8")
    canonical_entry = root / ".autobugfix-memory/active/user-preferences.md"
    canonical_entry.parent.mkdir(parents=True)
    canonical_entry.write_text("preserve me\n", encoding="utf-8")
    dedicated = private_empty_memory_root(tmp_path / "exp2-empty-memory")
    guard_root = tmp_path / "exp2-guard"
    guard_root.mkdir(mode=0o700)

    study = service.create_study(
        study_id="dedicated-empty-memory-study",
        purpose="Freeze an isolated empty Memory input",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref="main",
        memory_root=dedicated,
        empty_memory_fixture=True,
        guard_root=guard_root,
    )

    snapshot = Path(study.memory_snapshot_path)
    assert study.memory_digest == service.exp2_empty_memory_digest()
    assert not any((snapshot / "active").iterdir())
    assert not any((snapshot / "skills/approved").iterdir())
    assert canonical_entry.read_text(encoding="utf-8") == "preserve me\n"


def test_exp2_empty_memory_rejects_nonempty_or_permissive_tree(
    tmp_path: Path,
) -> None:
    nonempty = private_empty_memory_root(tmp_path / "nonempty-memory")
    unexpected = nonempty / "active/preference.md"
    unexpected.write_text(
        "must not enter Exp2\n", encoding="utf-8"
    )
    unexpected.chmod(0o600)
    with pytest.raises(OperatorGovernanceError, match="frozen empty fixture"):
        OperatorGovernanceService.validate_exp2_empty_memory_root(nonempty)

    special = private_empty_memory_root(tmp_path / "special-memory")
    os.mkfifo(special / "active" / "unexpected-pipe", 0o600)
    with pytest.raises(OperatorGovernanceError, match="frozen empty fixture"):
        OperatorGovernanceService.validate_exp2_empty_memory_root(special)

    permissive = private_empty_memory_root(tmp_path / "permissive-memory")
    permissive.chmod(0o755)
    with pytest.raises(OperatorGovernanceError, match="current-user private"):
        OperatorGovernanceService.validate_exp2_empty_memory_root(permissive)


def test_exp2_empty_memory_rejects_redirected_or_protected_root(
    tmp_path: Path,
) -> None:
    real = private_empty_memory_root(tmp_path / "real-empty-memory")
    redirected = tmp_path / "redirected-empty-memory"
    redirected.symlink_to(real, target_is_directory=True)
    with pytest.raises(OperatorGovernanceError, match="absolute real directory"):
        OperatorGovernanceService.validate_exp2_empty_memory_root(redirected)

    protected_tmp = tmp_path / "protected"
    protected_tmp.mkdir()
    root = make_operator_repo(protected_tmp)
    service = service_for(root, protected_tmp)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1]\n", encoding="utf-8")
    canonical = private_empty_memory_root(root / ".autobugfix-memory")
    guard_root = protected_tmp / "exp2-guard"
    guard_root.mkdir(mode=0o700)
    with pytest.raises(OperatorGovernanceError, match="overlaps protected"):
        service.create_study(
            study_id="protected-empty-memory-study",
            purpose="Reject an in-project empty fixture",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref="main",
            memory_root=canonical,
            empty_memory_fixture=True,
            guard_root=guard_root,
        )


def test_exp2_empty_memory_requires_a_disjoint_guard_root(tmp_path: Path) -> None:
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1]\n", encoding="utf-8")
    dedicated = private_empty_memory_root(tmp_path / "exp2-empty-memory")

    with pytest.raises(OperatorGovernanceError, match="explicit Guard root"):
        service.create_study(
            study_id="missing-guard-root-study",
            purpose="Require the Exp2 Guard boundary",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref="main",
            memory_root=dedicated,
            empty_memory_fixture=True,
        )

    guard_root = tmp_path / "exp2-guard"
    guard_root.mkdir(mode=0o700)
    guarded_fixture = private_empty_memory_root(guard_root / "fixture")
    with pytest.raises(OperatorGovernanceError, match="overlaps protected"):
        service.create_study(
            study_id="guard-overlap-study",
            purpose="Reject a fixture inside Guard state",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref="main",
            memory_root=guarded_fixture,
            empty_memory_fixture=True,
            guard_root=guard_root,
        )


def test_study_rejects_symlinked_canonical_active_memory(tmp_path: Path):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "manifest.yaml"
    manifest.write_text("cases: [case-1]\n", encoding="utf-8")
    external = tmp_path / "external-memory"
    external.mkdir()
    active = root / ".autobugfix-memory/active"
    active.parent.mkdir(parents=True)
    active.symlink_to(external, target_is_directory=True)

    with pytest.raises(OperatorGovernanceError, match="must not be a symlink"):
        service.create_study(
            study_id="redirected-memory-study",
            purpose="Reject redirected Memory authority",
            manifest_path=manifest,
            success_contract={"visible_net_gain": ">0"},
            base_ref="main",
        )


def test_study_freezes_canonical_manifest_memory_shape_and_separate_harness(
    tmp_path: Path,
):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    h0_sha = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    (root / "harness-marker.txt").write_text("trusted harness\n", encoding="utf-8")
    run(["git", "add", "harness-marker.txt"], cwd=root)
    run(["git", "commit", "-m", "advance trusted harness"], cwd=root)
    harness_sha = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()

    memory_root = root / ".autobugfix-memory"
    active = memory_root / "active/user-preferences.md"
    skill = memory_root / "skills/approved/retry/SKILL.md"
    pending = memory_root / "proposals/pending/patch.md"
    for path, content in (
        (active, "approved preference\n"),
        (skill, "# Approved retry skill\n"),
        (pending, "unreviewed\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    manifest_payload = {
        "schema": "autobugfix-swe-sealed-manifest-v1",
        "h0_subject": h0_sha,
        "h0_tree": run(["git", "rev-parse", f"{h0_sha}^{{tree}}"], cwd=root).stdout.strip(),
    }
    manifest = root / "sealed.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {**manifest_payload, "record_digest": digest_payload(manifest_payload)},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    study = service.create_study(
        study_id="frozen-shape-study",
        purpose="Freeze exact H0 treatment inputs",
        manifest_path=manifest,
        success_contract={"visible_net_gain": ">0"},
        base_ref=h0_sha,
        harness_ref=harness_sha,
    )
    snapshot = Path(study.memory_snapshot_path)

    assert study.base_subject_sha == h0_sha
    assert study.harness_sha == harness_sha
    assert study.manifest_digest == digest_payload(manifest_payload)
    assert (snapshot / "active/user-preferences.md").read_text() == "approved preference\n"
    assert (snapshot / "skills/approved/retry/SKILL.md").is_file()
    assert not (snapshot / "proposals").exists()


def test_signed_guard_metric_is_bound_to_study_harness_and_initializes_h0(
    tmp_path: Path,
):
    root = make_operator_repo(tmp_path)
    service = service_for(root, tmp_path)
    manifest = root / "guard-manifest.yaml"
    manifest.write_text("cases: [case-1, case-2, case-3]\n", encoding="utf-8")
    study = service.create_study(
        study_id="signed-guard-study",
        purpose="Prove signed Guard-to-Operator metric flow",
        manifest_path=manifest,
        success_contract={"pass_rate_delta": ">=0"},
        base_ref="main",
    )
    binding = service.guard_study_binding(study.study_id, kind="BASELINE")
    identity = GuardCodeIdentity(
        trusted_ref="main",
        trusted_commit=study.harness_sha,
        source_tree=run(
            ["git", "rev-parse", f"{study.harness_sha}^{{tree}}"], cwd=root
        ).stdout.strip(),
        machine_constitution_digest=study.policy_digest,
        harness_digest="a" * 64,
    )
    secret = "signed guard authority secret"
    signed = signed_metric(
        metric_payload(
            guard_id="guard-signed-study",
            run_id="h0-wave-3",
            wave=3,
            case_count=1,
            passed_count=0,
            failed_count=1,
            harness_error_count=0,
            encrypted_artifact_sha256="b" * 64,
            public_manifest_digest="c" * 64,
            code_identity=identity,
            study_binding=binding,
        ),
        secret,
    )
    metric_path = root / "signed-guard.metric.yaml"
    metric_path.write_text(yaml.safe_dump(signed, sort_keys=False), encoding="utf-8")

    with pytest.raises(OperatorGovernanceError, match="authentication failed"):
        service.register_signed_guard_metric(
            study.study_id,
            metric_path=metric_path,
            kind="BASELINE",
            guard_secret="wrong guard authority secret",
        )

    metric = service.register_signed_guard_metric(
        study.study_id,
        metric_path=metric_path,
        kind="BASELINE",
        guard_secret=secret,
    )
    assert metric.producer == "benchmark_guard"
    initialized = service.initialize_experiment_line(
        study.study_id,
        metric_receipt_id=metric.metric_id,
    )
    assert initialized["checkpoint"]["name"] == "H0"


def grant_wave_three(service, *, max_calls: int = 30):
    request = service.create_budget_request(
        "budget-study",
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        reason="Human authorizes the first bounded model wave",
        requester="operator",
        max_calls=max_calls,
        wall_time_seconds=600,
    )
    grant = service.approve_budget_grant(
        request.budget_request_id,
        approver="human",
        confirm_request_digest=request.budget_request_digest,
    )
    return request, grant


def codex_request(root: Path, *, role: str, model: str = "gpt-5.4-mini") -> CodexRequest:
    return CodexRequest(
        role=role,
        prompt="Run one bounded study node",
        cwd=root,
        control_root=root,
        sandbox="workspace-write" if role.endswith("writer") or role == "writer" else "read-only",
        model=model,
        timeout_seconds=30,
        developer_instructions="Bounded unit-test role",
        raw_log_path=root / ".autobugfix-test-logs" / f"{role}.raw.jsonl",
        stderr_log_path=root / ".autobugfix-test-logs" / f"{role}.stderr.log",
        approval_mode="auto_review" if role.endswith("writer") or role == "writer" else "deny_all",
    )


def test_budget_requires_digest_bound_human_grant_and_ordered_expansion(tmp_path: Path):
    _, service = initialized_study(tmp_path)
    request = service.create_budget_request(
        "budget-study",
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        reason="Authorize bounded wave three",
        requester="operator",
    )
    assert service.budget_status("budget-study")["grants"] == []
    with pytest.raises(OperatorGovernanceError, match="does not confirm"):
        service.approve_budget_grant(
            request.budget_request_id,
            approver="human",
            confirm_request_digest="forged",
        )
    grant = service.approve_budget_grant(
        request.budget_request_id,
        approver="human",
        confirm_request_digest=request.budget_request_digest,
    )
    assert grant.wave == 3
    assert grant.model == "gpt-5.4-mini"

    with pytest.raises(OperatorGovernanceError, match="advance to 8"):
        service.create_budget_request(
            "budget-study",
            wave=16,
            case_ids=tuple(f"case-{index}" for index in range(1, 17)),
            reason="Invalid wave skip",
        )
    with pytest.raises(OperatorGovernanceError, match="retain every previously granted case"):
        service.create_budget_request(
            "budget-study",
            wave=8,
            case_ids=tuple(f"new-{index}" for index in range(8)),
            reason="Invalid case replacement",
        )
    expanded = service.create_budget_request(
        "budget-study",
        wave=8,
        case_ids=tuple(f"case-{index}" for index in range(1, 9)),
        reason="Human reviews five additional visible cases",
    )
    wave_eight = service.approve_budget_grant(
        expanded.budget_request_id,
        approver="human",
        confirm_request_digest=expanded.budget_request_digest,
    )
    assert wave_eight.previous_grant_id == grant.grant_id
    assert set(grant.case_ids).issubset(wave_eight.case_ids)


def test_optimization_binding_admits_only_current_grant_cases_and_wave(
    tmp_path: Path,
) -> None:
    _, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service)
    binding = service.guard_study_binding("budget-study", kind="OPTIMIZATION")

    assert service.validate_optimization_case_binding(
        binding,
        case_id="case-1",
        first_wave=3,
    ) == grant
    with pytest.raises(OperatorGovernanceError, match="trusted budget wave"):
        service.validate_optimization_case_binding(
            binding,
            case_id="case-not-granted",
            first_wave=3,
        )
    with pytest.raises(OperatorGovernanceError, match="trusted budget wave"):
        service.validate_optimization_case_binding(
            binding,
            case_id="case-1",
            first_wave=8,
        )


def test_budget_approval_cli_rejects_noninteractive_operator(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    root, service = initialized_study(tmp_path)
    request = service.create_budget_request(
        "budget-study",
        wave=3,
        case_ids=("case-1", "case-2", "case-3"),
        reason="Require a real terminal human",
    )
    monkeypatch.chdir(root)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main(
        [
            "operator",
            "budget",
            "approve",
            "--trusted-file",
            str(service.trusted_file),
            "--budget-request-id",
            request.budget_request_id,
            "--approver",
            "claimed-human",
            "--confirm-request-digest",
            request.budget_request_digest,
        ]
    ) == 1
    assert "interactive human terminal" in capsys.readouterr().err
    assert service.store.read_budget_grants("budget-study") == []


def test_line_request_rejects_tampered_frozen_memory_snapshot(tmp_path: Path):
    _, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service)
    study = service.store.read_study("budget-study")
    snapshot = Path(study.memory_snapshot_path)
    snapshot.chmod(0o700)
    (snapshot / "injected.md").write_text("case-specific treatment\n", encoding="utf-8")
    triage = service.create_triage(
        triage_id="memory-tamper",
        summary="Attempt a request after changing frozen Memory",
        suspected_layers=("memory",),
        evidence=("evidence/report.yaml",),
        creator="operator",
    )

    with pytest.raises(OperatorGovernanceError, match="Memory snapshot digest mismatch"):
        service.create_request(
            request_id="memory-tamper-request",
            triage_id=triage.triage_id,
            summary="Must fail before candidate work begins",
            primary_layer="memory",
            planned_paths=("src/autobugfix/memory/service.py",),
            validation_profiles=("memory",),
            experiment_line_id="budget-study",
            budget_grant_id=grant.grant_id,
            creator="operator",
        )


def test_line_request_rejects_tampered_frozen_manifest_snapshot(tmp_path: Path):
    _, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service)
    study = service.store.read_study("budget-study")
    snapshot = Path(study.manifest_snapshot_path)
    snapshot.chmod(0o600)
    snapshot.write_text("cases: [replacement-case]\n", encoding="utf-8")
    triage = service.create_triage(
        triage_id="manifest-tamper",
        summary="Attempt a request after replacing the frozen case manifest",
        suspected_layers=("eval",),
        evidence=("evidence/report.yaml",),
        creator="operator",
    )

    with pytest.raises(OperatorGovernanceError, match="manifest snapshot digest mismatch"):
        service.create_request(
            request_id="manifest-tamper-request",
            triage_id=triage.triage_id,
            summary="Must fail before candidate work begins",
            primary_layer="eval",
            planned_paths=("src/autobugfix/eval/runner.py",),
            validation_profiles=("eval",),
            experiment_line_id="budget-study",
            budget_grant_id=grant.grant_id,
            creator="operator",
        )


def test_metered_backend_reserves_before_call_and_retains_host_logs(tmp_path: Path):
    root, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service, max_calls=3)
    backend = StudyBackend()
    metered = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="h0-case-1-writer-1",
        execution_id="h0-case-1",
        case_id="case-1",
        attempt=1,
        backend=backend,
    )

    result = metered.run(codex_request(root, role="writer"))

    assert result.text == "completed"
    assert len(backend.calls) == 1
    status = service.budget_status("budget-study")
    assert status["consumed_calls"] == 1
    usage = status["usage"][0]
    assert usage["status"] == "COMPLETED"
    assert usage["result_id"] == "thread-1"
    assert usage["input_tokens"] == 100
    assert usage["cached_input_tokens"] == 20
    assert usage["output_tokens"] == 30
    assert Path(usage["raw_log_path"]).is_relative_to(service.store.artifact_root)
    assert json.loads(Path(usage["raw_log_path"]).read_text(encoding="utf-8"))["kind"] == (
        "real-sdk-shape"
    )


def test_wrong_model_and_quota_failure_never_fall_back(tmp_path: Path):
    root, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service, max_calls=3)
    wrong_model_backend = StudyBackend()
    wrong_model = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="wrong-model",
        execution_id="h0-case-1",
        case_id="case-1",
        attempt=1,
        backend=wrong_model_backend,
    )
    with pytest.raises(OperatorStoreError, match="model does not match"):
        wrong_model.run(codex_request(root, role="writer", model="gpt-5.3-codex-spark"))
    assert wrong_model_backend.calls == []

    quota_backend = StudyBackend(RuntimeError("provider quota exhausted"))
    quota = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="quota-failure",
        execution_id="h0-case-1",
        case_id="case-1",
        attempt=1,
        backend=quota_backend,
    )
    with pytest.raises(RuntimeError, match="provider quota exhausted"):
        quota.run(codex_request(root, role="writer"))
    assert len(quota_backend.calls) == 1
    status = service.budget_status("budget-study")
    assert status["usage"][0]["status"] == "INDETERMINATE"
    assert status["usage"][0]["model"] == "gpt-5.4-mini"


def test_operator_role_revisions_are_sequential_and_not_replayable(tmp_path: Path):
    root, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service, max_calls=3)
    backend = StudyBackend()
    first = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="operator-supervisor-1",
        execution_id="operator-request",
        revision=1,
        backend=backend,
    )
    first.run(codex_request(root, role="operator_supervisor"))

    replayed_revision = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="operator-supervisor-another-key",
        execution_id="operator-request",
        revision=1,
        backend=backend,
    )
    with pytest.raises(OperatorStoreError, match="next reserved revision"):
        replayed_revision.run(codex_request(root, role="operator_supervisor"))

    second = service.metered_codex_backend(
        grant_id=grant.grant_id,
        call_key="operator-supervisor-2",
        execution_id="operator-request",
        revision=2,
        backend=backend,
    )
    second.run(codex_request(root, role="operator_supervisor"))
    assert len(backend.calls) == 2


def test_atomic_reservation_enforces_single_call_concurrency(tmp_path: Path):
    _, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service)
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []

    def reserve(index: int) -> None:
        barrier.wait(timeout=5)
        try:
            entry = service.reserve_usage(
                grant.grant_id,
                call_key=f"concurrent-{index}",
                execution_id=f"h0-case-{index}",
                case_id=f"case-{index}",
                role="evaluator",
                model="gpt-5.4-mini",
                attempt=1,
            )
            results.append(("reserved", entry))
        except OperatorStoreError as exc:
            results.append(("denied", exc))

    threads = [threading.Thread(target=reserve, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item[0] for item in results) == ["denied", "reserved"]
    reserved = next(item for status, item in results if status == "reserved")
    service.finalize_usage(reserved.usage_id, status="COMPLETED")


def test_usage_budget_counts_indeterminate_calls_and_rejects_replay(tmp_path: Path):
    _, service = initialized_study(tmp_path)
    _, grant = grant_wave_three(service, max_calls=2)
    first = service.reserve_usage(
        grant.grant_id,
        call_key="call-1",
        execution_id="h0-case-1",
        case_id="case-1",
        role="writer",
        model="gpt-5.4-mini",
        attempt=1,
    )
    service.finalize_usage(first.usage_id, status="INDETERMINATE", error="process lost")
    with pytest.raises(OperatorStoreError, match="call key or id already exists"):
        service.reserve_usage(
            grant.grant_id,
            call_key="call-1",
            execution_id="h0-case-1",
            case_id="case-1",
            role="writer",
            model="gpt-5.4-mini",
            attempt=2,
        )
    second = service.reserve_usage(
        grant.grant_id,
        call_key="call-2",
        execution_id="h0-case-2",
        case_id="case-2",
        role="evaluator",
        model="gpt-5.4-mini",
        attempt=1,
    )
    service.finalize_usage(second.usage_id, status="COMPLETED")
    with pytest.raises(OperatorStoreError, match="budget is exhausted"):
        service.reserve_usage(
            grant.grant_id,
            call_key="call-3",
            execution_id="h0-case-3",
            case_id="case-3",
            role="evaluator",
            model="gpt-5.4-mini",
            attempt=1,
        )
