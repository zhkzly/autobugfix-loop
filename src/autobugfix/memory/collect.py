from __future__ import annotations

from pathlib import Path
from typing import Any

from autobugfix.events import events_to_dicts
from autobugfix.task_store import TaskStore


def collect_task_packet(store: TaskStore, task_id: str) -> dict[str, Any]:
    record = store.load(task_id)
    task_dir = store.find_task_dir(task_id)
    artifacts: dict[str, str] = {}
    for path in sorted((task_dir / "artifacts").glob("*")):
        if path.is_file():
            artifacts[path.name] = path.read_text(encoding="utf-8", errors="replace")
    logs = sorted(str(path.relative_to(task_dir)) for path in (task_dir / "logs").glob("*") if path.is_file())
    return {
        "task": record.to_dict(),
        "task_dir": str(task_dir),
        "events": events_to_dicts(store.events(task_id)),
        "context": store.read_text_tree(task_id, "context"),
        "feedback": store.read_text_tree(task_id, "feedback"),
        "artifacts": artifacts,
        "logs": logs,
    }
