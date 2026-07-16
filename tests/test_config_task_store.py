from __future__ import annotations

import pytest
import yaml

from autobugfix.config import ConfigError, load_config
from autobugfix.models import TaskRecord
from autobugfix.role_config import resolve_role
from autobugfix.task_store import TaskStore
from tests.helpers import make_service_project


def test_config_defaults_and_task_store_round_trip(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    cfg = load_config(project_root)
    assert cfg.repo("toy_repo").worktree_root == project_root / ".autobugfix/worktrees/toy_repo"
    assert cfg.codex.default_model is None
    assert cfg.operator.experiments.default_profile == "real-e2e"
    real_profile = cfg.operator.experiments.profiles["real-e2e"]
    assert real_profile["network_access"] is True
    assert "scripts/real_repository_acceptance.py" in real_profile["commands"][0]["argv"]
    assert real_profile["codex_broker"] == {
        "enabled": True,
        "model": "gpt-5.4-mini",
        "required_role_sequence": [
            "writer",
            "evaluator",
            "memory_maintainer",
            "writer",
            "evaluator",
        ],
        "role_timeout_seconds": {
            "writer": 600,
            "evaluator": 300,
            "memory_maintainer": 1800,
        },
    }
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


def test_config_rejects_disabling_isolated_codex_role_runtime(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["codex"]["role_runtime"]["enabled"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="isolated CODEX_HOME"):
        load_config(project_root)


def test_config_rejects_invalid_operator_codex_broker_contract(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("operator", {}).setdefault("experiments", {}).setdefault(
        "profiles", {}
    ).setdefault("real-e2e", {})["codex_broker"] = {
        "enabled": True,
        "model": "gpt-5.4-mini",
        "required_role_sequence": ["operator_writer"],
        "role_timeout_seconds": {"operator_writer": 600},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported roles"):
        load_config(project_root)
