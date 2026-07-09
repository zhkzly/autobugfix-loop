from __future__ import annotations

from pathlib import Path


def render_memory_context(root: Path, audience: str) -> str:
    active = root / "active/user-preferences.md"
    text = active.read_text(encoding="utf-8") if active.exists() else ""
    return f"# Autobugfix Memory Context\n\naudience: {audience}\n\n{text}"
