from __future__ import annotations

from autobugfix.dataset import build_raw_dataset
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project, run


def test_dataset_build_raw_writes_commit_pair(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix", "Bug")
    service.run_task(task.task_id)
    worktree = service.store.load(task.task_id).worktree_path
    run(["git", "-C", worktree, "config", "user.email", "toy@example.com"])
    run(["git", "-C", worktree, "config", "user.name", "Toy User"])
    run(["git", "-C", worktree, "add", "calc.py"])
    run(["git", "-C", worktree, "commit", "-m", "fix"])
    out = build_raw_dataset(project_root, "toy_repo", tmp_path / "raw.jsonl", "origin/main")
    assert out.read_text(encoding="utf-8").strip()
