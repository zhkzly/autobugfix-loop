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
        "commands": [
            {
                "name": "operator-acceptance-tests",
                "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            }
        ],
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
                    }
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
        creator="acceptance-operator",
    )
    service.start(request.request_id)
    service.run_supervisor(request.request_id)
    writer = service.start_writer(request.request_id)
    if writer["status"] != "COMPLETED":
        raise RuntimeError(f"Operator Writer did not complete: {writer}")
    fast = service.verify(request.request_id, mode="fast")
    if fast["check_run"]["status"] != "PASSED":
        raise RuntimeError(f"Operator fast check failed: {fast['check_run']['failures']}")
    service.commit_candidate(request.request_id, message="Fix Eval case-id normalization")
    full = service.verify(request.request_id, mode="full")
    if full["check_run"]["status"] != "PASSED":
        raise RuntimeError(f"Operator full check failed: {full['check_run']['failures']}")
    audit = service.audit(request.request_id)
    if not audit["allowed"]:
        raise RuntimeError(f"Operator audit failed: {audit['violations']}")
    promotion = service.prepare_promotion(request.request_id)
    workspace = Path(service.store.read_workspace(request.request_id)["path"])
    diff = run(["git", "diff", request.base_sha, "HEAD", "--", "src/autobugfix/eval/runner.py"], workspace).stdout
    if not diff.strip():
        raise RuntimeError("Operator Writer did not change the requested Eval implementation")
    run(
        [
            sys.executable,
            "-c",
            "from autobugfix.eval.runner import normalize_case_id; "
            "assert normalize_case_id('  A Bug  ') == 'a-bug'; "
            "assert normalize_case_id('A_Bug') == 'a-bug'",
        ],
        workspace,
        env={**os.environ, "PYTHONPATH": str(workspace / "src")},
    )
    raw_artifacts = [
        item for item in service.store.read_artifacts(request.request_id) if item["kind"] == "writer-raw"
    ]
    if not raw_artifacts:
        raise RuntimeError("Operator Writer raw SDK log was not retained")
    print(
        json.dumps(
            {
                "request_id": request.request_id,
                "phase": service.projection(request.request_id).state,
                "writer_run_id": writer["run_id"],
                "promotion_id": promotion["promotion"]["promotion_id"],
                "model": args.model,
                "workspace": str(workspace),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
