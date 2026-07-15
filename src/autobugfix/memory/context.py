from __future__ import annotations

from pathlib import Path

from autobugfix.memory.fs import MemoryFileError, read_regular_file


def render_memory_context(root: Path, audience: str) -> str:
    active = root / "active/user-preferences.md"
    if not active.exists() and not active.is_symlink():
        return ""
    text = read_regular_file(
        root,
        "active/user-preferences.md",
        label="active memory context",
    ).decode("utf-8")
    approved: list[str] = []
    skills_root = root / "skills/approved"
    if skills_root.exists() or skills_root.is_symlink():
        if skills_root.is_symlink() or not skills_root.is_dir():
            raise MemoryFileError("approved Memory skill root is redirected")
        for skill in sorted(skills_root.glob("*/SKILL.md")):
            relative = skill.relative_to(root)
            approved.append(
                read_regular_file(
                    root,
                    relative,
                    label=f"approved Memory skill {skill.parent.name}",
                ).decode("utf-8")
            )
    sections = [
        "# Autobugfix Memory Context",
        f"audience: {audience}",
        text,
    ]
    if approved:
        sections.extend(("# Approved Memory Skills", *approved))
    return "\n\n".join(sections)
