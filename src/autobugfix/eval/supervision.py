from __future__ import annotations

from pathlib import Path


def supervision_note(project_root: Path) -> Path:
    path = project_root / ".autobugfix-experiments/supervision.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Eval Supervision\n\nInspect eval artifacts before changing Autobugfix code.\n", encoding="utf-8")
    return path
