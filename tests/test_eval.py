from __future__ import annotations

import json
from pathlib import Path

import yaml

from autobugfix.dataset import build_raw_dataset
from autobugfix.eval.runner import run_eval
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project, run


def test_dataset_and_eval_call_execution_loop_in_isolated_repo(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["codex"]["role_runtime"]["codex_bin"] = "/usr/bin/true"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    service.run_task(task.task_id)
    worktree = service.store.load(task.task_id).worktree_path
    assert worktree
    run(["git", "-C", worktree, "config", "user.email", "toy@example.com"])
    run(["git", "-C", worktree, "config", "user.name", "Toy User"])
    run(["git", "-C", worktree, "add", "calc.py"])
    run(["git", "-C", worktree, "commit", "-m", "fix toy add"])

    raw = tmp_path / "raw.jsonl"
    build_raw_dataset(project_root, "toy_repo", raw, "origin/main")
    row = json.loads(raw.read_text(encoding="utf-8").splitlines()[0])
    row.update(
        {
            "problem_statement": "Fix add off by one",
            "agent_prompt": "Fix add off by one",
            "expected_behavior": "tests pass",
        }
    )
    problem = tmp_path / "problem.jsonl"
    problem.write_text(json.dumps(row) + "\n", encoding="utf-8")
    run_dir = run_eval(project_root, problem, tmp_path / "eval-runs", run_id="toy", backend=FakeCodexBackend(), test_command="python3 -m unittest discover")
    report = (run_dir / row["raw_id"] / "report.yaml").read_text(encoding="utf-8")
    resolved_roles = (run_dir / row["raw_id"] / "resolved-roles.yaml").read_text(encoding="utf-8")
    isolated_config = yaml.safe_load(
        (run_dir / row["raw_id"] / "control/.autobugfix/config.yaml").read_text(encoding="utf-8")
    )
    assert "decision: pass" in report
    assert "writer:" in resolved_roles
    assert "evaluator:" in resolved_roles
    assert "autobugfix-writer" in resolved_roles
    assert isolated_config["codex"]["role_runtime"]["codex_bin"] == str(Path("/usr/bin/true").resolve())
