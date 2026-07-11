from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
import yaml

from autobugfix.models import CodexRequest, CodexResult
from autobugfix.cli import main
from autobugfix.operator.models import digest_payload
from autobugfix.operator.service import OperatorGovernanceError
from autobugfix.operator.store import OperatorStoreError
from tests.test_operator_policy import make_operator_repo, run, service_for


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
