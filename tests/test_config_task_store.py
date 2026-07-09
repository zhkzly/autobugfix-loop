from __future__ import annotations

from autobugfix.config import load_config
from autobugfix.models import TaskRecord
from autobugfix.task_store import TaskStore
from tests.helpers import make_service_project


def test_config_defaults_and_task_store_round_trip(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    cfg = load_config(project_root)
    assert cfg.repo("toy_repo").worktree_root == project_root / ".autobugfix/worktrees/toy_repo"
    store = TaskStore(project_root, cfg.task_root)
    record = TaskRecord(task_id="t1", repo_id="toy_repo", title="title", body="body", state="ready")
    store.create(record)
    store.add_context("t1", "log", "evidence")
    assert store.load("t1").state == "ready"
    assert store.events("t1")[0].kind == "task_created"
