from __future__ import annotations

from pathlib import Path

import pytest

from autobugfix.service import AutobugfixService, ServiceError
from tests.helpers import FakeCodexBackend, make_service_project, run


def test_service_create_run_gate_archive_with_real_worktree_and_verifier(tmp_path):
    project_root, main = make_service_project(tmp_path)
    active = project_root / ".autobugfix-memory/active/user-preferences.md"
    active.parent.mkdir(parents=True)
    active.write_text("Prefer regression tests.\n", encoding="utf-8")
    skill = project_root / ".autobugfix-memory/skills/approved/repro/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Reproduce First\n\nRun the failing test.\n", encoding="utf-8")
    backend = FakeCodexBackend()
    service = AutobugfixService(project_root, backend=backend)
    task = service.create_task("toy_repo", "fix toy add", "Bug: add returns 4")
    assert task.state == "ready"
    assert task.worktree_path
    assert Path(task.worktree_path).is_dir()
    assert run(["git", "-C", str(main), "status", "--porcelain"]).stdout == ""

    result = service.run_task(task.task_id)
    assert result.state == "waiting_human_ppe_approval"
    task_dir = service.store.find_task_dir(task.task_id)
    assert (task_dir / "artifacts/diff.patch").read_text(encoding="utf-8")
    assert (task_dir / "artifacts/test-result.md").exists()
    assert (task_dir / "logs/writer-1.raw.jsonl").exists()
    assert backend.calls[0].cwd == Path(task.worktree_path)
    assert backend.calls[0].sandbox == "workspace-write"
    assert backend.calls[0].writable_paths == ()
    assert Path(task.worktree_path) / ".git" in backend.calls[0].readable_paths
    assert "Prefer regression tests." in backend.calls[0].prompt
    assert "# Reproduce First" in backend.calls[0].prompt
    assert backend.calls[1].sandbox == "read-only"

    accepted = service.apply_gate(task.task_id, "accepted")
    assert accepted.state == "accepted"
    archived = service.archive(task.task_id, "accepted")
    assert archived.exists()


def test_execution_terminal_state_cannot_be_reopened_by_feedback(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    service.run_task(task.task_id)
    service.apply_gate(task.task_id, "accepted")

    with pytest.raises(ServiceError, match="cannot add feedback from state accepted"):
        service.add_feedback(task.task_id, "retry", "reopen")
    assert service.store.load(task.task_id).state == "accepted"


def test_execution_gate_rejects_candidate_changed_after_verification(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    service.run_task(task.task_id)
    worktree = Path(service.store.load(task.task_id).worktree_path or "")
    (worktree / "calc.py").write_text("def add(a, b):\n    return -1\n", encoding="utf-8")

    with pytest.raises(ServiceError, match="changed after verification"):
        service.apply_gate(task.task_id, "accepted")
