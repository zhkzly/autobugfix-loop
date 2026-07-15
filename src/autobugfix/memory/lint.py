from __future__ import annotations

from pathlib import Path

from autobugfix.memory.fs import MemoryFileError, read_regular_file, require_directory


def lint_memory_root(root: Path) -> list[str]:
    errors: list[str] = []
    required_files = ("config.yaml", "active/user-preferences.md")
    required_directories = ("raw/tasks", "digests/tasks", "proposals", "skills/approved")
    for relative in required_files:
        try:
            read_regular_file(root, relative, label=f"memory file {relative}")
        except MemoryFileError as exc:
            errors.append(str(exc))
    for relative in required_directories:
        try:
            require_directory(root, relative)
        except MemoryFileError as exc:
            errors.append(str(exc))
    approved = root / "skills/approved"
    try:
        require_directory(root, "skills/approved")
    except MemoryFileError:
        return errors
    for skill_dir in approved.iterdir():
        if skill_dir.is_symlink():
            errors.append(f"approved skill must not be a symlink: {skill_dir}")
        elif skill_dir.is_dir():
            try:
                read_regular_file(
                    root,
                    Path("skills/approved") / skill_dir.name / "SKILL.md",
                    label=f"approved skill {skill_dir.name}/SKILL.md",
                )
            except MemoryFileError as exc:
                errors.append(str(exc))
    return errors
