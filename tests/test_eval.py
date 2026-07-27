from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autobugfix.dataset import build_raw_dataset
from autobugfix.eval.adapters import LocalGitAdapter
from autobugfix.eval.models import EvalCase, EvalCaseError
from autobugfix.eval.runner import EvalRunnerError, run_eval
from autobugfix.git_utils import run_git
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project, run


def prepare_historical_case(tmp_path: Path) -> tuple[Path, dict[str, object], Path]:
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
    return project_root, row, problem


def test_dataset_and_eval_call_execution_loop_in_isolated_repo(tmp_path):
    project_root, row, problem = prepare_historical_case(tmp_path)
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="toy",
        backend=FakeCodexBackend(),
        test_command="python3 -m unittest discover",
    )
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


def test_local_git_adapter_hides_reference_commit_from_execution_remote(tmp_path):
    _, row, _ = prepare_historical_case(tmp_path)
    case = EvalCase.from_row(row)
    adapter = LocalGitAdapter()

    materialized = adapter.materialize(case, tmp_path / "materialized")

    refs = run_git(
        materialized.remote,
        ["for-each-ref", "--format=%(refname)"],
        check=True,
    ).stdout.splitlines()
    assert refs == ["refs/heads/main"]
    assert case.final_commit
    leaked = run_git(
        materialized.remote,
        ["cat-file", "-e", f"{case.final_commit}^{{commit}}"],
        check=False,
    )
    assert leaked.returncode != 0
    assert adapter.oracle_diff(case)


def test_eval_accepts_behaviorally_correct_patch_that_differs_from_oracle(tmp_path):
    project_root, row, problem = prepare_historical_case(tmp_path)
    backend = FakeCodexBackend(
        writer_source=(
            "def add(a, b):\n"
            "    total = a + b\n"
            "    return total\n"
        )
    )
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="alternative",
        backend=backend,
        test_command="python3 -m unittest discover",
    )
    report = yaml.safe_load(
        (run_dir / str(row["raw_id"]) / "report.yaml").read_text(encoding="utf-8")
    )
    assert report["decision"] == "pass"
    assert report["oracle_passed"] is True
    assert report["generated_equals_oracle"] is False


def test_eval_rejects_identical_patch_when_independent_oracle_fails(tmp_path):
    project_root, row, _ = prepare_historical_case(tmp_path)
    row["test_command"] = "python3 -m unittest discover"
    row["oracle_command"] = "python3 -c 'raise SystemExit(9)'"
    problem = tmp_path / "oracle-failure.jsonl"
    problem.write_text(json.dumps(row) + "\n", encoding="utf-8")
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="oracle-failure",
        backend=FakeCodexBackend(),
    )
    report = yaml.safe_load(
        (run_dir / str(row["raw_id"]) / "report.yaml").read_text(encoding="utf-8")
    )
    summary = yaml.safe_load((run_dir / "summary.yaml").read_text(encoding="utf-8"))
    assert report["generated_equals_oracle"] is True
    assert report["decision"] == "fail"
    assert report["failure_stage"] == "oracle"
    assert summary["failed_count"] == 1


def test_eval_reports_unsupported_adapter_as_harness_error(tmp_path):
    project_root, row, _ = prepare_historical_case(tmp_path)
    row["source"] = {
        "adapter": "not-installed",
        "benchmark": "invalid",
        "revision": "v1",
        "split": "test",
        "instance_id": row["raw_id"],
    }
    row["task"] = {
        "type": "bugfix",
        "problem_statement": row["problem_statement"],
        "agent_prompt": row["agent_prompt"],
    }
    row["repository"] = {
        "repo_id": row["repo"],
        "worktree_path": row["worktree_path"],
        "base_commit": row["base_commit"],
        "reference_commit": row["final_commit"],
    }
    row["execution"] = {"test_command": "python3 -m unittest discover"}
    row["oracle"] = {"type": "command", "command": "python3 -m unittest discover"}
    problem = tmp_path / "unsupported-adapter.jsonl"
    problem.write_text(json.dumps(row) + "\n", encoding="utf-8")
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="unsupported",
        backend=FakeCodexBackend(),
    )
    report = yaml.safe_load(
        (run_dir / str(row["raw_id"]) / "report.yaml").read_text(encoding="utf-8")
    )
    assert report["decision"] == "error"
    assert report["failure_stage"] == "harness"
    assert "unknown Eval case adapter" in report["harness_error"]


def test_canonical_case_preserves_benchmark_environment_attachment_and_hidden_oracle():
    row = {
        "schema_version": 1,
        "case_id": "upstream-123",
        "source": {
            "adapter": "future-official-adapter",
            "benchmark": "upstream-benchmark",
            "revision": "2026-07-01",
            "split": "verified",
            "instance_id": "owner__repo-123",
        },
        "task": {
            "type": "bugfix",
            "problem_statement": "Repair the regression shown in the screenshot.",
            "agent_prompt": "Repair the regression shown in the screenshot.",
            "attachments": [
                {
                    "kind": "screenshot",
                    "uri": "artifacts/failure.png",
                    "media_type": "image/png",
                    "sha256": "abc123",
                }
            ],
        },
        "repository": {
            "repo_id": "owner/repo",
            "url": "https://example.invalid/owner/repo.git",
            "base_commit": "base-sha",
        },
        "environment": {
            "image": "registry.invalid/benchmark/case:sha256-deadbeef",
            "platform": "linux/amd64",
            "setup_commands": ["python -m pip install -e ."],
        },
        "execution": {"test_command": "pytest -q tests/test_regression.py"},
        "oracle": {
            "type": "official-harness",
            "visibility": "hidden",
            "patch_source": "dataset://gold_patch",
            "require_patch": True,
            "timeout_seconds": 900,
        },
    }

    case = EvalCase.from_row(row)
    encoded = case.to_dict()

    assert case.source.instance_id == "owner__repo-123"
    assert case.environment.image == row["environment"]["image"]
    assert case.task.attachments[0].media_type == "image/png"
    assert case.oracle.visibility == "hidden"
    assert case.oracle.patch_source == "dataset://gold_patch"
    assert EvalCase.from_row(encoded).to_dict() == encoded


def test_case_schema_rejects_unknown_version_and_local_adapter_environment(tmp_path):
    with pytest.raises(EvalCaseError, match="unsupported Eval case schema version"):
        EvalCase.from_row({"schema_version": 2, "case_id": "future"})

    project_root, row, _ = prepare_historical_case(tmp_path)
    row["source"] = {
        "adapter": "local-git",
        "benchmark": "local",
        "revision": "v1",
        "split": "test",
        "instance_id": row["raw_id"],
    }
    row["task"] = {
        "type": "bugfix",
        "problem_statement": row["problem_statement"],
    }
    row["repository"] = {
        "repo_id": row["repo"],
        "worktree_path": row["worktree_path"],
        "base_commit": row["base_commit"],
        "reference_commit": row["final_commit"],
    }
    row["environment"] = {"image": "benchmark.invalid/case:latest"}
    row["execution"] = {"test_command": "python3 -m unittest discover"}
    row["oracle"] = {"type": "command", "command": "python3 -m unittest discover"}
    problem = tmp_path / "unsupported-environment.jsonl"
    problem.write_text(json.dumps(row) + "\n", encoding="utf-8")

    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="unsupported-environment",
        backend=FakeCodexBackend(),
    )
    report = yaml.safe_load(
        (run_dir / str(row["raw_id"]) / "report.yaml").read_text(encoding="utf-8")
    )
    assert report["decision"] == "error"
    assert "cannot satisfy a declared container environment" in report["harness_error"]


def test_eval_records_schema_errors_and_refuses_to_mix_reused_run_artifacts(tmp_path):
    project_root, _, _ = prepare_historical_case(tmp_path)
    dataset = tmp_path / "invalid-schema.jsonl"
    dataset.write_text(
        json.dumps({"schema_version": 2, "case_id": "future-case"}) + "\n",
        encoding="utf-8",
    )

    run_dir = run_eval(
        project_root,
        dataset,
        tmp_path / "eval-runs",
        run_id="schema-errors",
        backend=FakeCodexBackend(),
    )
    summary = yaml.safe_load((run_dir / "summary.yaml").read_text(encoding="utf-8"))
    report = yaml.safe_load(
        (run_dir / "schema-error-0001/report.yaml").read_text(encoding="utf-8")
    )

    assert summary["harness_error_count"] == 1
    assert report["failure_stage"] == "case_schema"
    assert "case_schema" in (run_dir / "diagnosis.md").read_text(encoding="utf-8")
    with pytest.raises(EvalRunnerError, match="run directory is not empty"):
        run_eval(
            project_root,
            dataset,
            tmp_path / "eval-runs",
            run_id="schema-errors",
            backend=FakeCodexBackend(),
        )
