from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import yaml


def run(cmd: list[str], cwd: Path | None = None, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, input=input_text, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"command failed with {result.returncode}: {' '.join(cmd)}")
    return result


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_toy_repo(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    remote = root / "toy-remote.git"
    seed = root / "toy-seed"
    main = root / "toy-main"
    run(["git", "init", "--bare", str(remote)])
    run(["git", "init", "-b", "main", str(seed)])
    run(["git", "-C", str(seed), "config", "user.email", "toy@example.com"])
    run(["git", "-C", str(seed), "config", "user.name", "Toy User"])
    write(seed / "calc.py", "def add(a, b):\n    return a + b + 1\n")
    write(
        seed / "test_calc.py",
        "import unittest\n\nfrom calc import add\n\n\n"
        "class CalcTest(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n\n\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n",
    )
    run(["git", "-C", str(seed), "add", "."])
    run(["git", "-C", str(seed), "commit", "-m", "base bug"])
    run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)])
    run(["git", "-C", str(seed), "push", "-u", "origin", "main"])
    run(["git", "clone", str(remote), str(main)])
    run(["git", "-C", str(main), "config", "user.email", "toy@example.com"])
    run(["git", "-C", str(main), "config", "user.name", "Toy User"])
    failing = subprocess.run(["python3", "-m", "unittest", "discover"], cwd=main, text=True, capture_output=True, check=False)
    if failing.returncode == 0:
        raise RuntimeError("toy repo unexpectedly passes before Autobugfix run")
    return main


def write_control_config(project_root: Path, main_checkout: Path) -> None:
    data = {
        "task_root": ".autobugfix/tasks",
        "scheduler": {
            "default_max_concurrent": 1,
            "lock_timeout_seconds": 7200,
            "max_auto_iterations": 2,
            "codex_timeout_seconds": 500,
            "writer_timeout_seconds": 500,
            "evaluator_timeout_seconds": 300,
        },
        "codex": {
            "writer_model": None,
            "evaluator_model": None,
            "controller_model": None,
            "role_runtime": {
                "enabled": True,
                "runtime_root": ".autobugfix/runtime/codex-sdk",
                "bridge_auth": True,
                "skill_guard": True,
                "strict_skill_guard": True,
            },
        },
        "repos": {
            "toy_repo": {
                "main_checkout": str(main_checkout),
                "remote": "origin",
                "main_branch": "main",
                "branch_template": "fix/{date}_oncall_{slug}",
                "test_commands": {
                    "targeted": "python3 -m unittest discover",
                    "full": "python3 -m unittest discover",
                },
                "ppe": {"enabled": False, "command_template": None},
            }
        },
    }
    write(project_root / ".autobugfix/config.yaml", yaml.safe_dump(data, sort_keys=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/tmp/autobugfix-real-e2e")
    args = parser.parse_args()
    project_root = Path.cwd()
    root = Path(args.root)
    for rel in (".autobugfix", ".autobugfix-memory", ".autobugfix-evals"):
        path = project_root / rel
        if path.exists():
            shutil.rmtree(path)
    main_checkout = create_toy_repo(root)
    write_control_config(project_root, main_checkout)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "doctor"], cwd=project_root)
    create = run(
        [
            "uv",
            "run",
            "--cache-dir",
            "/tmp/uv-cache",
            "autobugfix",
            "create",
            "--repo",
            "toy_repo",
            "--title",
            "fix toy add off by one",
            "--from-stdin",
        ],
        cwd=project_root,
        input_text="Bug: calc.add(1, 2) returns 4 instead of 3. Fix the smallest possible code path and verify with python3 -m unittest discover.\n",
    )
    task_id = create.stdout.strip().splitlines()[-1]
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "inspect", task_id], cwd=project_root)
    run(["git", "-C", str(main_checkout), "worktree", "list"])
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "run", task_id], cwd=project_root)
    worktree = project_root / ".autobugfix/worktrees/toy_repo" / task_id
    diff = run(["git", "-C", str(worktree), "diff", "origin/main", "--", "calc.py"]).stdout
    if "return a + b" not in diff or "return a + b + 1" not in diff:
        raise RuntimeError("generated diff did not fix calc.add")
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "gate", task_id, "accepted"], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "archive", task_id, "--result", "accepted"], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "init"], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "collect", task_id], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "digest", task_id], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "lint"], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "maintain", task_id], cwd=project_root)
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "memory", "status"], cwd=project_root)
    run(["git", "-C", str(worktree), "config", "user.email", "toy@example.com"])
    run(["git", "-C", str(worktree), "config", "user.name", "Toy User"])
    run(["git", "-C", str(worktree), "add", "calc.py"])
    run(["git", "-C", str(worktree), "commit", "-m", "fix toy add off by one"])
    raw = root / "raw_commit_pairs.jsonl"
    run(["uv", "run", "--cache-dir", "/tmp/uv-cache", "autobugfix", "dataset", "build-raw", "--repo", "toy_repo", "--base-ref", "origin/main", "--out", str(raw)], cwd=project_root)
    raw_row = json.loads(raw.read_text(encoding="utf-8").splitlines()[0])
    problem = root / "problem_prompts.jsonl"
    raw_row.update(
        {
            "task_kind": "bugfix",
            "problem_statement": "Fix calc.add so add(1, 2) returns 3.",
            "agent_prompt": "Bug: calc.add has an off-by-one error. Fix the smallest code path and run python3 -m unittest discover.",
            "expected_behavior": "python3 -m unittest discover passes",
            "change_summary": "remove + 1 from add",
            "evidence": "unit test failure",
            "confidence": 1.0,
        }
    )
    write(problem, json.dumps(raw_row, sort_keys=True) + "\n")
    run(
        [
            "uv",
            "run",
            "--cache-dir",
            "/tmp/uv-cache",
            "autobugfix",
            "eval",
            "run",
            "--dataset",
            str(problem),
            "--case",
            raw_row["raw_id"],
            "--out",
            str(root / "eval-runs"),
            "--run-id",
            "toy-e2e",
            "--model-mode",
            "fake",
            "--test-command",
            "python3 -m unittest discover",
            "--codex-timeout-seconds",
            "500",
            "--writer-timeout-seconds",
            "500",
            "--evaluator-timeout-seconds",
            "300",
        ],
        cwd=project_root,
    )
    print(f"ACCEPTANCE_TASK_ID={task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
