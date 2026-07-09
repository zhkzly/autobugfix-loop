from __future__ import annotations

from pathlib import Path


def read_proposal_patch(proposal_dir: Path) -> str:
    path = proposal_dir / "patch.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""
