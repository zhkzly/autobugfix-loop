from __future__ import annotations

from pathlib import Path

from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project, run


def test_service_create_run_gate_archive_with_real_worktree_and_verifier(tmp_path):
    project_root, main = make_service_project(tmp_path)
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
    assert backend.calls[1].sandbox == "read-only"

    accepted = service.apply_gate(task.task_id, "accepted")
    assert accepted.state == "accepted"
    archived = service.archive(task.task_id, "accepted")
    assert archived.exists()
