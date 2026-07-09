from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from autobugfix.models import CodexRequest, CodexResult, RoleConfig


class FakeCodexBackend:
    def __init__(self, edit: bool = True, evaluator_text: str = "decision: pass\nreason: ok\n") -> None:
        self.edit = edit
        self.evaluator_text = evaluator_text
        self.calls: list[CodexRequest] = []

    def run(self, request: CodexRequest) -> CodexResult:
        self.calls.append(request)
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.raw_log_path.write_text(
            json.dumps({"role": request.role, "cwd": str(request.cwd), "sandbox": request.sandbox}) + "\n",
            encoding="utf-8",
        )
        request.stderr_log_path.write_text("", encoding="utf-8")
        if request.role == "writer" and self.edit:
            calc = request.cwd / "calc.py"
            if calc.exists():
                calc.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            return CodexResult(text="writer fixed calc.py")
        if request.role == "evaluator":
            return CodexResult(text=self.evaluator_text)
        return CodexResult(text="NO_CHANGE\n")


class FakeMaintainerBackend:
    def maintain(
        self,
        project_root: Path,
        run_dir: Path,
        digest: str,
        model: str | None,
        timeout_seconds: int | None,
        role_override: RoleConfig | None = None,
    ) -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "fake.raw.jsonl").write_text("{}\n", encoding="utf-8")
        return "Remember to preserve verifier evidence when accepting fixes."


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=True)


def make_toy_repo(root: Path) -> Path:
    remote = root / "toy-remote.git"
    seed = root / "toy-seed"
    main = root / "toy-main"
    run(["git", "init", "--bare", str(remote)])
    run(["git", "init", "-b", "main", str(seed)])
    run(["git", "-C", str(seed), "config", "user.email", "toy@example.com"])
    run(["git", "-C", str(seed), "config", "user.name", "Toy User"])
    (seed / "calc.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    (seed / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\nclass CalcTest(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n",
        encoding="utf-8",
    )
    run(["git", "-C", str(seed), "add", "."])
    run(["git", "-C", str(seed), "commit", "-m", "base bug"])
    run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)])
    run(["git", "-C", str(seed), "push", "-u", "origin", "main"])
    run(["git", "clone", str(remote), str(main)])
    run(["git", "-C", str(main), "config", "user.email", "toy@example.com"])
    run(["git", "-C", str(main), "config", "user.name", "Toy User"])
    return main


def write_config(project_root: Path, main_checkout: Path, repo_id: str = "toy_repo") -> None:
    path = project_root / ".autobugfix/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "task_root": ".autobugfix/tasks",
        "scheduler": {
            "default_max_concurrent": 1,
            "lock_timeout_seconds": 7200,
            "max_auto_iterations": 2,
            "codex_timeout_seconds": 120,
            "writer_timeout_seconds": 120,
            "evaluator_timeout_seconds": 120,
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
                "strict_skill_guard": False,
            },
        },
        "repos": {
            repo_id: {
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
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def make_service_project(tmp_path: Path) -> tuple[Path, Path]:
    project_root = tmp_path / "control"
    project_root.mkdir()
    main = make_toy_repo(tmp_path / "repo")
    write_config(project_root, main)
    return project_root, main
