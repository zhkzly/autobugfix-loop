from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from autobugfix.operator.service import OperatorGovernanceService


NORMALIZATION_CONTRACT = (
    "from autobugfix.eval.runner import normalize_case_id; "
    "assert normalize_case_id('  A Bug  ') == 'a-bug'; "
    "assert normalize_case_id('A_Bug') == 'a-bug'"
)


def run(
    argv: list[str],
    cwd: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {result.stderr.strip()}")
    return result


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def acceptance_commands() -> list[dict[str, object]]:
    return [
        {
            "name": "operator-acceptance-tests",
            "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        },
        {
            "name": "case-id-underscore-and-whitespace-contract",
            "argv": [sys.executable, "-c", NORMALIZATION_CONTRACT],
        },
    ]


def build_repo(source_root: Path, root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    run(["git", "init", "-b", "main"], root)
    run(["git", "config", "user.email", "operator@example.com"], root)
    run(["git", "config", "user.name", "Operator Acceptance"], root)
    write(
        root / ".gitignore",
        ".venv/\n__pycache__/\n*.pyc\n.autobugfix/\n.autobugfix-evals/\n.autobugfix-experiments/\n",
    )
    write(
        root / "src/autobugfix/eval/runner.py",
        "def normalize_case_id(value: str) -> str:\n"
        "    return value.strip().lower().replace(' ', '_')\n",
    )
    write(root / "src/autobugfix/eval/__init__.py", "")
    write(root / "src/autobugfix/__init__.py", "")
    write(
        root / "tests/test_eval.py",
        "import unittest\n\nfrom autobugfix.eval.runner import normalize_case_id\n\n"
        "class NormalizeCaseTest(unittest.TestCase):\n"
        "    def test_normalizes_for_dataset_paths(self):\n"
        "        self.assertEqual(normalize_case_id('  A Bug  '), 'a-bug')\n",
    )
    write(
        root / "evidence/operator.yaml",
        "failure: normalize_case_id emits underscores but dataset paths require hyphens\n",
    )
    write(
        root / "evidence/study-manifest.yaml",
        yaml.safe_dump(
            {
                "schema": "autobugfix-operator-acceptance-v1",
                "cases": [
                    "whitespace-normalization",
                    "underscore-normalization",
                    "mixed-case-normalization",
                ],
            },
            sort_keys=False,
        ),
    )
    for relative in (
        "src/autobugfix/config.py",
        "src/autobugfix/codex_sdk.py",
        "src/autobugfix/service.py",
        "src/autobugfix/operator/constitution.yaml",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    shutil.copytree(source_root / ".agents/role-skills", root / ".agents/role-skills")
    policy_path = root / "src/autobugfix/operator/constitution.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    command = {
        "timeout_seconds": 120,
        "commands": acceptance_commands(),
    }
    policy["validation_profiles"]["eval"] = command
    policy["validation_profiles"]["full"] = command
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-m", "operator acceptance base"], root)
    return policy_path


def write_config(source_root: Path, root: Path, model: str) -> None:
    write(
        root / ".autobugfix/config.yaml",
        yaml.safe_dump(
            {
                "scheduler": {"codex_timeout_seconds": 500},
                "codex": {
                    "default_model": model,
                    "default_timeout_seconds": 500,
                    "role_runtime": {
                        "enabled": True,
                        "runtime_root": ".autobugfix/runtime/codex-sdk",
                        "codex_bin": shutil.which("codex"),
                        "bridge_auth": True,
                        "skill_guard": True,
                        "strict_skill_guard": True,
                    },
                },
                "operator": {
                    "verification": {
                        "fast_profiles": ["eval"],
                        "full_profiles": ["eval"],
                        "require_semantic_verifier": True,
                        "process_sandbox": "auto",
                        "require_process_sandbox": True,
                        "network_access": False,
                        "runtime_venv": str(source_root / ".venv"),
                    },
                    "experiments": {
                        "enabled": True,
                        "trusted_ref": "main",
                        "default_profile": "operator-acceptance",
                        "profiles": {
                            "operator-acceptance": {
                                "timeout_seconds": 120,
                                "baseline_mode": "measure",
                                "commands": acceptance_commands(),
                            }
                        },
                    },
                },
            },
            sort_keys=False,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real production Operator Writer acceptance case.")
    parser.add_argument("--root", default="/tmp/autobugfix-real-operator-e2e")
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()
    if args.model != "gpt-5.4-mini":
        raise RuntimeError("governed Operator acceptance requires gpt-5.4-mini")
    source_root = Path.cwd().resolve()
    root = Path(args.root).resolve()
    policy_path = build_repo(source_root, root)
    write_config(source_root, root, args.model)
    failing = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    if failing.returncode == 0:
        raise RuntimeError("Operator acceptance repository unexpectedly passes before Writer")

    service = OperatorGovernanceService(root, trusted_ref=None, trusted_file=policy_path)
    baseline = service.capture_baseline(
        "operator-acceptance-base",
        profile="operator-acceptance",
        notes="Expected failing repository state before the Operator repair.",
    )
    if baseline["baseline"]["metrics"]["pass_rate"] != 0.0:
        raise RuntimeError("Operator baseline did not preserve the expected failing state")
    run(["git", "add", ".autobugfix-baselines/operator-acceptance-base.yaml"], root)
    run(["git", "commit", "-m", "Record trusted Operator acceptance baseline"], root)
    h0_sha = run(["git", "rev-parse", "main"], root).stdout.strip()
    study = service.create_study(
        study_id="operator-acceptance-study",
        cohort_id="operator-acceptance-h0",
        purpose="Exercise the governed Operator integration line with real Codex roles",
        manifest_path=root / "evidence/study-manifest.yaml",
        success_contract={
            "observed_guard": {
                "require_all_commands_pass": True,
                "metrics": {
                    "pass_rate": {"operator": "eq", "value": 1.0},
                    "artifact_completeness": {"operator": "eq", "value": 1.0},
                },
                "protected_ref": "main",
            },
        },
        base_ref=h0_sha,
        primary_model=args.model,
        target_checkpoint_name="H_bug",
    )
    h0_metric = service.record_observed_baseline_metric(
        study.study_id,
        baseline_name="operator-acceptance-base",
    )
    service.initialize_experiment_line(
        study.study_id,
        metric_receipt_id=h0_metric.metric_id,
    )
    budget_request = service.create_budget_request(
        study.study_id,
        wave=3,
        case_ids=(
            "whitespace-normalization",
            "underscore-normalization",
            "mixed-case-normalization",
        ),
        reason="Run the bounded real Operator acceptance wave",
        requester="acceptance-operator",
        model=args.model,
        max_calls=6,
        max_writer_attempts=2,
        max_operator_revisions=3,
        wall_time_seconds=900,
        case_concurrency=1,
    )
    grant = service.approve_budget_grant(
        budget_request.budget_request_id,
        approver="acceptance-human",
        confirm_request_digest=budget_request.budget_request_digest,
    )
    triage = service.create_triage(
        triage_id="operator-real-triage",
        summary="Eval case IDs violate the dataset path contract",
        suspected_layers=("eval",),
        evidence=("evidence/operator.yaml",),
        confidence="high",
        creator="acceptance-operator",
    )
    request = service.create_request(
        request_id="operator-real",
        triage_id=triage.triage_id,
        summary="Normalize Eval case IDs to lowercase hyphenated paths and pass the real test",
        primary_layer="eval",
        planned_paths=("src/autobugfix/eval/runner.py", "tests/test_eval.py"),
        validation_profiles=("eval",),
        performance_baseline="operator-acceptance-base",
        experiment_line_id=study.line_id,
        budget_grant_id=grant.grant_id,
        creator="acceptance-operator",
    )
    service.start(request.request_id)
    service.run_supervisor(request.request_id)
    writer = service.start_writer(request.request_id)
    if writer["status"] != "COMPLETED":
        raise RuntimeError(f"Operator Writer did not complete: {writer}")
    fast = service.verify(request.request_id, mode="fast")
    if fast["check_run"]["status"] != "PASSED":
        retry = service.retry_writer(request.request_id)
        if retry["status"] != "COMPLETED":
            raise RuntimeError(f"Operator retry Writer did not complete: {retry}")
        writer = retry
        fast = service.verify(request.request_id, mode="fast")
        if fast["check_run"]["status"] != "PASSED":
            raise RuntimeError(
                f"Operator fast check still failed after feedback retry: {fast['check_run']['failures']}"
            )
    service.commit_candidate(request.request_id, message="Fix Eval case-id normalization")
    experiment = service.run_experiment(request.request_id, profile="operator-acceptance")
    if experiment["status"] != "COMPLETED" or not experiment["passed"]:
        raise RuntimeError(f"Operator candidate experiment did not pass: {experiment}")
    full = service.verify(request.request_id, mode="full")
    if full["check_run"]["status"] != "PASSED":
        raise RuntimeError(f"Operator full check failed: {full['check_run']['failures']}")
    audit = service.audit(request.request_id)
    if not audit["allowed"]:
        raise RuntimeError(f"Operator audit failed: {audit['violations']}")
    workspace = Path(service.store.read_workspace(request.request_id)["path"])
    diff = run(["git", "diff", request.base_sha, "HEAD", "--", "src/autobugfix/eval/runner.py"], workspace).stdout
    if not diff.strip():
        raise RuntimeError("Operator Writer did not change the requested Eval implementation")
    run(
        [
            sys.executable,
            "-c",
            NORMALIZATION_CONTRACT,
        ],
        workspace,
        env={**os.environ, "PYTHONPATH": str(workspace / "src")},
    )
    raw_artifacts = [
        item for item in service.store.read_artifacts(request.request_id) if item["kind"] == "writer-raw"
    ]
    if not raw_artifacts:
        raise RuntimeError("Operator Writer raw SDK log was not retained")
    workspace_record = service.store.read_workspace(request.request_id)
    baseline = workspace_record.get("writer_admission_baseline")
    writer_runs = service.store.read_writer_runs(request.request_id)
    latest_writer = writer_runs[-1] if writer_runs else None
    artifacts = service.store.read_artifacts(request.request_id)
    if not isinstance(baseline, dict) or not baseline.get("artifact_id"):
        raise RuntimeError("Operator candidate has no trusted Writer admission baseline")
    if latest_writer is None or not latest_writer.application_artifact_id:
        raise RuntimeError("Operator Writer has no trusted application artifact")
    if not any(
        item["artifact_id"] == baseline["artifact_id"]
        and item["kind"] == "writer-admission-baseline"
        and item["trust_class"] == "authoritative"
        for item in artifacts
    ):
        raise RuntimeError("Operator Writer admission baseline artifact was not retained")
    if not any(
        item["artifact_id"] == latest_writer.application_artifact_id
        and item["kind"] == "writer-application"
        and item["trust_class"] == "authoritative"
        for item in artifacts
    ):
        raise RuntimeError("Operator Writer application artifact was not retained")
    if not any(
        event.kind == "writer_applied"
        and event.payload.get("run_id") == latest_writer.run_id
        and event.payload.get("application_artifact_id") == latest_writer.application_artifact_id
        and event.payload.get("candidate_before_content_digest")
        and event.payload.get("candidate_after_content_digest")
        for event in service.store.read_events(request.request_id)
    ):
        raise RuntimeError("Operator Writer admission event was not retained")
    integration = service.integrate_candidate(
        request.request_id,
        grant_id=grant.grant_id,
        actor="acceptance-guard",
    )
    metric = service.record_observed_candidate_metric(study.study_id)
    checkpoint = service.create_checkpoint(
        study.line_id,
        metric_receipt_id=metric.metric_id,
    )
    if run(["git", "rev-parse", "main"], root).stdout.strip() != h0_sha:
        raise RuntimeError("Operator acceptance changed the protected main branch")
    if run(["git", "status", "--porcelain"], root).stdout.strip():
        raise RuntimeError("Operator acceptance left the main checkout dirty")
    print(
        json.dumps(
            {
                "request_id": request.request_id,
                "phase": service.projection(request.request_id).state,
                "writer_run_id": writer["run_id"],
                "integration_id": integration["integration"]["integration_id"],
                "checkpoint_id": checkpoint["checkpoint"]["checkpoint_id"],
                "budget_grant_id": grant.grant_id,
                "model": args.model,
                "workspace": str(workspace),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
