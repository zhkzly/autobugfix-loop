from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autobugfix.models import VerifierResult, utc_now
from autobugfix.runner import RunnerError
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project, run


def test_runner_rework_when_evaluator_rejects(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend(evaluator_text="decision: needs_changes\nreason: too broad\n"))
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    result = service.run_task(task.task_id)
    assert result.state == "writer_rework_required"
    assert result.block_reason == "too broad"


def test_runner_uses_resolved_writer_and_evaluator_roles(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["codex"]["roles"] = {
        "writer": {"model": "writer-model", "timeout_seconds": 41},
        "evaluator": {"model": "evaluator-model", "timeout_seconds": 42},
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    backend = FakeCodexBackend(evaluator_text="decision: needs_changes\nreason: inspect request\n")
    service = AutobugfixService(project_root, backend=backend)
    task = service.create_task("toy_repo", "fix toy add", "Bug")

    service.run_task(task.task_id)

    writer_request = backend.calls[0]
    evaluator_request = backend.calls[1]
    assert writer_request.role == "writer"
    assert writer_request.model == "writer-model"
    assert writer_request.sandbox == "workspace-write"
    assert writer_request.approval_mode == "auto_review"
    assert writer_request.timeout_seconds == 41
    assert evaluator_request.role == "evaluator"
    assert evaluator_request.model == "evaluator-model"
    assert evaluator_request.sandbox == "read-only"
    assert evaluator_request.approval_mode == "deny_all"
    assert evaluator_request.timeout_seconds == 42


def test_writer_runtime_failure_blocks_task_and_preserves_main(tmp_path):
    class FailingBackend(FakeCodexBackend):
        def run(self, request):
            if request.role == "writer":
                raise RuntimeError("sdk unavailable")
            return super().run(request)

    project_root, main = make_service_project(tmp_path)
    before_head = run(["git", "-C", str(main), "rev-parse", "HEAD"]).stdout
    service = AutobugfixService(project_root, backend=FailingBackend())
    task = service.create_task("toy_repo", "writer runtime failure", "Bug")

    with pytest.raises(RunnerError, match="writer runtime failed"):
        service.run_task(task.task_id)

    current = service.store.load(task.task_id)
    assert current.state == "blocked"
    assert "sdk unavailable" in current.block_reason
    assert run(["git", "-C", str(main), "rev-parse", "HEAD"]).stdout == before_head
    assert run(["git", "-C", str(main), "status", "--porcelain"]).stdout == ""
    assert any(event.kind == "execution_node_failed" for event in service.store.events(task.task_id))


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        (
            "python3 -c 'import sys; print(\"setup failed\", file=sys.stderr); raise SystemExit(2)'",
            "harness_error",
        ),
        (
            "python3 -c 'import sys; print(\"AUTOBUGFIX_VERIFIER_POLICY: forbidden\", file=sys.stderr); raise SystemExit(3)'",
            "policy_violation",
        ),
    ],
)
def test_non_repair_verifier_outcomes_block_without_evaluator(tmp_path, command, outcome):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["repos"]["toy_repo"]["test_commands"]["full"] = command
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    backend = FakeCodexBackend()
    service = AutobugfixService(project_root, backend=backend)
    task = service.create_task("toy_repo", f"{outcome} verifier", "Bug")

    with pytest.raises(RunnerError, match=outcome):
        service.run_task(task.task_id)

    current = service.store.load(task.task_id)
    task_dir = service.store.find_task_dir(task.task_id)
    result = (task_dir / "artifacts/attempts/0001/test-result.md").read_text(
        encoding="utf-8"
    )
    assert current.state == "blocked"
    assert f"outcome: {outcome}" in result
    assert [request.role for request in backend.calls] == ["writer"]


def test_main_checkout_mutation_is_a_policy_violation(tmp_path):
    project_root, main = make_service_project(tmp_path)

    class MainMutatingBackend(FakeCodexBackend):
        def run(self, request):
            result = super().run(request)
            if request.role == "writer":
                (main / "forbidden.txt").write_text("mutation\n", encoding="utf-8")
            return result

    service = AutobugfixService(project_root, backend=MainMutatingBackend())
    task = service.create_task("toy_repo", "main mutation", "Bug")

    with pytest.raises(RunnerError, match="main checkout changed"):
        service.run_task(task.task_id)

    current = service.store.load(task.task_id)
    snapshots = (
        service.store.find_task_dir(task.task_id)
        / "artifacts/attempts/0001/main-checkout.jsonl"
    ).read_text(encoding="utf-8")
    assert current.state == "blocked"
    assert '"matches_baseline": false' in snapshots


def test_verifier_runs_in_ephemeral_copy_and_writer_diff_includes_untracked_source(tmp_path):
    class NewSourceBackend(FakeCodexBackend):
        def run(self, request):
            result = super().run(request)
            if request.role == "writer":
                (request.cwd / "new_source.py").write_text("VALUE = 1\n", encoding="utf-8")
                ignored = request.cwd / "__pycache__"
                ignored.mkdir()
                (ignored / "writer.pyc").write_bytes(b"not-source")
            return result

    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["repos"]["toy_repo"]["test_commands"]["full"] = (
        "python3 -c 'from pathlib import Path; Path(\"verifier-generated.txt\").write_text(\"x\")'"
    )
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    service = AutobugfixService(project_root, backend=NewSourceBackend())
    task = service.create_task("toy_repo", "isolated verifier", "Bug")

    result = service.run_task(task.task_id)

    worktree = Path(result.worktree_path or "")
    task_dir = service.store.find_task_dir(task.task_id)
    patch = (task_dir / "artifacts/attempts/0001/diff.patch").read_text(encoding="utf-8")
    ignored_receipt = (task_dir / "artifacts/attempts/0001/ignored-writer-outputs.json").read_text(
        encoding="utf-8"
    )
    assert result.state == "waiting_human_ppe_approval"
    assert "new_source.py" in patch
    assert not (worktree / "verifier-generated.txt").exists()
    assert not (worktree / "__pycache__").exists()
    assert "__pycache__/writer.pyc" in ignored_receipt


def test_execution_role_permissions_cannot_be_weakened_by_repo_config(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["repos"]["toy_repo"]["codex"] = {
        "roles": {"evaluator": {"sandbox": "workspace-write"}}
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "weak evaluator", "Bug")

    with pytest.raises(RunnerError, match="evaluator role must use sandbox=read-only"):
        service.run_task(task.task_id)

    assert service.store.load(task.task_id).state == "blocked"


def test_read_only_evaluator_mutation_is_detected_even_with_fake_backend(tmp_path):
    class MutatingEvaluator(FakeCodexBackend):
        def run(self, request):
            result = super().run(request)
            if request.role == "evaluator":
                (request.cwd / "calc.py").write_text(
                    "def add(a, b):\n    return 999\n",
                    encoding="utf-8",
                )
            return result

    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=MutatingEvaluator())
    task = service.create_task("toy_repo", "evaluator mutation", "Bug")

    with pytest.raises(RunnerError, match="read-only evaluator"):
        service.run_task(task.task_id)

    assert service.store.load(task.task_id).state == "blocked"


def test_service_injected_managed_verifier_runs_in_isolated_worktree(tmp_path):
    class ManagedVerifier:
        command_id = "managed:test:receipt"

        def __init__(self):
            self.cwd: Path | None = None

        def run(self, worktree, artifact_dir, *, timeout_seconds):
            self.cwd = worktree
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "raw.log").write_text("real managed check\n", encoding="utf-8")
            now = utc_now()
            return VerifierResult(
                command=self.command_id,
                exit_code=0,
                stdout="managed pass",
                stderr="",
                started_at=now,
                finished_at=now,
                outcome="passed",
            )

    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["repos"]["toy_repo"]["test_commands"]["full"] = "managed:test:receipt"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    verifier = ManagedVerifier()
    service = AutobugfixService(
        project_root,
        backend=FakeCodexBackend(),
        verifier_backend=verifier,
    )
    task = service.create_task("toy_repo", "managed verifier", "Bug")

    result = service.run_task(task.task_id)

    assert result.state == "waiting_human_ppe_approval"
    assert verifier.cwd is not None
    assert verifier.cwd != Path(result.worktree_path or "")
    assert not verifier.cwd.exists()
    assert (
        service.store.find_task_dir(task.task_id)
        / "artifacts/attempts/0001/managed-verifier/raw.log"
    ).is_file()


def test_managed_verifier_command_binding_cannot_be_changed_in_config(tmp_path):
    class ManagedVerifier:
        command_id = "managed:test:trusted"

        def run(self, worktree, artifact_dir, *, timeout_seconds):
            raise AssertionError("mismatched managed verifier must not execute")

    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(
        project_root,
        backend=FakeCodexBackend(),
        verifier_backend=ManagedVerifier(),
    )
    task = service.create_task("toy_repo", "managed mismatch", "Bug")

    with pytest.raises(RunnerError, match="command ID does not match"):
        service.run_task(task.task_id)

    assert service.store.load(task.task_id).state == "blocked"
    events = service.store.events(task.task_id)
    assert any(
        event.kind == "execution_blocked"
        and event.payload.get("classification") == "policy_violation"
        for event in events
    )
    assert not any(
        event.kind == "execution_node_failed"
        and event.payload.get("node") == "verifier"
        for event in events
    )
