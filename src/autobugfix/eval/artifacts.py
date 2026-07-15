from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from autobugfix.codex_sdk import write_private_text


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    write_private_text(path, yaml.safe_dump(data, sort_keys=False))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    write_private_text(path, text)


def copy_role_skills(project_root: Path, control_root: Path) -> bool:
    source = project_root / ".agents/role-skills"
    dest = control_root / ".agents/role-skills"
    if source.exists() and not dest.exists():
        shutil.copytree(source, dest)
        return True
    return dest.exists()
