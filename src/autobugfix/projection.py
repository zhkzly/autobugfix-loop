from __future__ import annotations

from pathlib import Path
from typing import Any

from autobugfix.events import events_to_dicts
from autobugfix.models import TaskRecord
from autobugfix.task_store import TaskStore


def task_summary(record: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "repo_id": record.repo_id,
        "title": record.title,
        "state": record.state,
        "branch": record.branch,
        "worktree_path": record.worktree_path,
        "block_reason": record.block_reason,
        "iterations": record.iterations,
        "updated_at": record.updated_at,
    }


def status_projection(store: TaskStore) -> dict[str, Any]:
    return {"tasks": [task_summary(record) for record in store.list_active()]}


def inspect_projection(store: TaskStore, task_id: str) -> dict[str, Any]:
    record = store.load(task_id)
    task_dir = store.find_task_dir(task_id)
    artifacts = sorted(str(path.relative_to(task_dir)) for path in (task_dir / "artifacts").glob("*") if path.is_file())
    logs = sorted(str(path.relative_to(task_dir)) for path in (task_dir / "logs").glob("*") if path.is_file())
    return {
        "task": task_summary(record),
        "task_dir": str(task_dir),
        "events": events_to_dicts(store.events(task_id)),
        "artifacts": artifacts,
        "logs": logs,
    }


def render_inspect(data: dict[str, Any]) -> str:
    lines = ["# Task", ""]
    task = data["task"]
    for key in ("task_id", "repo_id", "title", "state", "branch", "worktree_path", "block_reason", "iterations"):
        lines.append(f"{key}: {task.get(key) or ''}")
    lines.append("")
    lines.append("## Artifacts")
    lines.extend(f"- {item}" for item in data.get("artifacts", []))
    lines.append("")
    lines.append("## Logs")
    lines.extend(f"- {item}" for item in data.get("logs", []))
    return "\n".join(lines)
