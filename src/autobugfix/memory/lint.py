from __future__ import annotations

from pathlib import Path


def lint_memory_root(root: Path) -> list[str]:
    errors: list[str] = []
    required = [
        root / "config.yaml",
        root / "active/user-preferences.md",
        root / "raw/tasks",
        root / "digests/tasks",
        root / "proposals",
        root / "skills/approved",
    ]
    for path in required:
        if not path.exists():
            errors.append(f"missing {path}")
    for skill_dir in (root / "skills/approved").glob("*"):
        if skill_dir.is_dir() and not (skill_dir / "SKILL.md").exists():
            errors.append(f"approved skill missing SKILL.md: {skill_dir}")
    return errors
