from __future__ import annotations

import yaml

from autobugfix.config import load_config
from autobugfix.models import TaskRecord
from autobugfix.role_config import resolve_role
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


def test_role_config_defaults_legacy_models_and_repo_override(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["codex"]["writer_model"] = "legacy-writer"
    data["codex"]["roles"] = {
        "evaluator": {
            "model": "global-evaluator",
            "timeout_seconds": 77,
        }
    }
    data["repos"]["toy_repo"]["codex"] = {
        "roles": {
            "writer": {
                "model": "repo-writer",
                "timeout_seconds": 33,
            }
        }
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    cfg = load_config(project_root)
    global_writer = resolve_role(cfg, "writer")
    writer = resolve_role(cfg, "writer", repo_id="toy_repo")
    evaluator = resolve_role(cfg, "evaluator", repo_id="toy_repo")
    assert global_writer.model == "legacy-writer"
    assert writer.model == "repo-writer"
    assert writer.sandbox == "workspace-write"
    assert writer.approval_mode == "auto_review"
    assert writer.timeout_seconds == 33
    assert any("autobugfix-writer" in str(path) for path in writer.skill_paths)
    assert evaluator.model == "global-evaluator"
    assert evaluator.sandbox == "read-only"
    assert evaluator.timeout_seconds == 77
