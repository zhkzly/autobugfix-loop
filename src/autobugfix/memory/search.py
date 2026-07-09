from __future__ import annotations

from pathlib import Path


def search_memory(root: Path, query: str) -> list[str]:
    needle = query.lower()
    matches: list[str] = []
    for path in sorted(root.glob("**/*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if needle in text.lower():
            matches.append(str(path.relative_to(root)))
    return matches
