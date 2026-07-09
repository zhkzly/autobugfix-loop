from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from autobugfix.eval.models import EvalCase
from autobugfix.git_utils import run_git


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_role_skills(project_root: Path, control_root: Path) -> bool:
    source = project_root / ".agents/role-skills"
    dest = control_root / ".agents/role-skills"
    if source.exists() and not dest.exists():
        shutil.copytree(source, dest)
        return True
    return dest.exists()


def prepare_isolated_repo(case: EvalCase, case_dir: Path) -> tuple[Path, Path]:
    remote = case_dir / "remote.git"
    main = case_dir / "main"
    if remote.exists():
        shutil.rmtree(remote)
    if main.exists():
        shutil.rmtree(main)
    run_git(case.worktree_path, ["rev-parse", "--git-dir"], check=True)
    subprocess.run(["git", "clone", "--bare", str(case.worktree_path), str(remote)], check=True, text=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(main)], check=True, text=True, capture_output=True)
    run_git(main, ["checkout", "-B", "main", case.base_commit], check=True)
    run_git(main, ["push", "origin", "main", "--force"], check=True)
    return remote, main
