from __future__ import annotations

from pathlib import Path


def improvements_root(project_root: Path) -> Path:
    return project_root / ".autobugfix-experiments/improvements"


def list_improvements(project_root: Path) -> list[str]:
    root = improvements_root(project_root)
    return sorted(path.name for path in root.glob("*.md")) if root.exists() else []


def show_improvement(project_root: Path, name: str) -> str:
    path = improvements_root(project_root) / name
    return path.read_text(encoding="utf-8")


def update_improvement(project_root: Path, name: str, text: str) -> Path:
    path = improvements_root(project_root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
