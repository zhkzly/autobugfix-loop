from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autobugfix.dataset import build_raw_dataset
from autobugfix.eval.models import EvalCase, EvalCaseError
from autobugfix.eval.benchmarks.models import record_with_digest, verify_record
from autobugfix.eval.reporting import write_evaluation_report
from autobugfix.eval.runner import EvalRunnerError, run_eval
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


def test_formal_evaluation_report_derives_loop_and_noninterference_metrics(tmp_path):
    project_root, row, problem = prepare_historical_case(tmp_path)
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="formal-report",
        backend=FakeCodexBackend(),
        test_command="python3 -m unittest discover",
    )
    subject = record_with_digest(
        {
            "schema": "autobugfix-evaluation-subject-noninterference-v1",
            "prepared_manifest_digest": "a" * 64,
            "unchanged": True,
            "expected": {"subject_sha": "test"},
            "observed": {"subject_sha": "test"},
            "checked_at": "2026-07-12T00:00:00Z",
        }
    )
    (run_dir / "subject-noninterference.yaml").write_text(
        yaml.safe_dump(subject, sort_keys=False),
        encoding="utf-8",
    )

    report_path = write_evaluation_report(run_dir)
    report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    verify_record(report)

    assert report["case_count"] == 1
    assert report["passed_count"] == 1
    assert report["first_attempt_passed_count"] == 1
    assert report["loop_rescued_count"] == 0
    assert report["verifier_oracle_agreement_count"] == 1
    assert report["noninterference_passed_count"] == 1
    assert report["cases"][0]["case_id"] == row["raw_id"]


@pytest.mark.parametrize("run_id", ("../escape", "nested/run", "nested\\run", ".", ".."))
def test_eval_rejects_unsafe_run_id_before_creating_output(tmp_path, run_id):
    project_root, _, problem = prepare_historical_case(tmp_path)
    out = tmp_path / "eval-runs"

    with pytest.raises(EvalRunnerError, match="safe path component"):
        run_eval(
            project_root,
            problem,
            out,
            run_id=run_id,
            backend=FakeCodexBackend(),
        )

    assert not out.exists()


def test_eval_retries_writer_with_real_verifier_feedback_and_model_override(tmp_path):
    class RetryOnceBackend(FakeCodexBackend):
        def __init__(self) -> None:
            super().__init__()
            self.writer_calls = 0

        def run(self, request):
            if request.role != "writer":
                return super().run(request)
            self.writer_calls += 1
            if self.writer_calls != 1:
                return super().run(request)
            edit = self.edit
            self.edit = False
            try:
                return super().run(request)
            finally:
                self.edit = edit

    project_root, row, problem = prepare_historical_case(tmp_path)
    backend = RetryOnceBackend()
    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="retry",
        backend=backend,
        model="gpt-5.4-mini",
        max_attempts=2,
        test_command="python3 -m unittest discover",
    )

    case_dir = run_dir / str(row["raw_id"])
    report = yaml.safe_load((case_dir / "report.yaml").read_text(encoding="utf-8"))
    feedback_files = list((case_dir / "control/.autobugfix/tasks").glob("*/feedback/*.md"))
    attempt_dirs = list(
        (case_dir / "control/.autobugfix/tasks").glob("*/artifacts/attempts/*")
    )
    writer_requests = [request for request in backend.calls if request.role == "writer"]

    assert report["decision"] == "pass"
    assert backend.writer_calls == 2
    assert len(writer_requests) == 2
    assert all(request.model == "gpt-5.4-mini" for request in writer_requests)
    assert len(feedback_files) == 1
    assert len(attempt_dirs) == 2
    assert all((path / "diff.patch").is_file() for path in attempt_dirs)
    assert all((path / "test-result.md").is_file() for path in attempt_dirs)
    assert "did not satisfy the repair contract" in feedback_files[0].read_text(
        encoding="utf-8"
    )


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


def test_official_scorer_runs_in_fresh_checkout_not_execution_worktree(tmp_path):
    project_root, row, _ = prepare_historical_case(tmp_path)
    row["test_command"] = "python3 -m unittest discover"
    row["oracle_command"] = (
        "python3 -c \"from pathlib import Path; "
        "Path('official-scorer-marker').write_text('scored')\""
    )
    problem = tmp_path / "isolated-scorer.jsonl"
    problem.write_text(json.dumps(row) + "\n", encoding="utf-8")

    run_dir = run_eval(
        project_root,
        problem,
        tmp_path / "eval-runs",
        run_id="isolated-scorer",
        backend=FakeCodexBackend(),
    )
    case_dir = run_dir / str(row["raw_id"])
    task_data = yaml.safe_load(
        next((case_dir / "control/.autobugfix/tasks").glob("*/task.yaml")).read_text(
            encoding="utf-8"
        )
    )
    execution_worktree = Path(task_data["worktree_path"])

    assert (case_dir / "oracle/candidate/official-scorer-marker").is_file()
    assert not (execution_worktree / "official-scorer-marker").exists()
    noninterference = yaml.safe_load(
        (case_dir / "oracle-noninterference.yaml").read_text(encoding="utf-8")
    )
    assert noninterference["unchanged"] is True


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
        "benchmark": {
            "framework_revision": "framework-sha",
            "dataset_revision": "dataset-sha",
            "runtime_id": "runtime-sha",
            "eligibility_receipt_digest": "receipt-sha",
            "visible_evidence_digest": "evidence-sha",
        },
        "experiment": {
            "role": "optimization",
            "first_wave": 3,
            "repository_group": "owner/repo",
            "case_token": "visible-upstream-123",
        },
    }

    case = EvalCase.from_row(row)
    encoded = case.to_dict()

    assert case.source.instance_id == "owner__repo-123"
    assert case.environment.image == row["environment"]["image"]
    assert case.task.attachments[0].media_type == "image/png"
    assert case.oracle.visibility == "hidden"
    assert case.oracle.patch_source == "dataset://gold_patch"
    assert case.benchmark is not None
    assert case.benchmark.framework_revision == "framework-sha"
    assert case.experiment is not None
    assert case.experiment.role == "optimization"
    assert EvalCase.from_row(encoded).to_dict() == encoded


@pytest.mark.parametrize(
    ("experiment", "message"),
    [
        (
            {
                "role": "training",
                "first_wave": 3,
                "repository_group": "repo",
                "case_token": "case",
            },
            "unsupported experiment role",
        ),
        (
            {
                "role": "optimization",
                "first_wave": 4,
                "repository_group": "repo",
                "case_token": "case",
            },
            "first_wave must be 3, 8, or 16",
        ),
    ],
)
def test_canonical_case_rejects_invalid_experiment_contract(experiment, message):
    row = {
        "schema_version": 1,
        "case_id": "case",
        "source": {
            "adapter": "future",
            "benchmark": "future",
            "revision": "v1",
            "split": "optimization",
            "instance_id": "case",
        },
        "task": {"type": "bugfix", "problem_statement": "Fix it"},
        "repository": {
            "repo_id": "repo",
            "url": "https://example.invalid/repo.git",
            "base_commit": "base",
        },
        "experiment": experiment,
    }
    with pytest.raises(EvalCaseError, match=message):
        EvalCase.from_row(row)


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
