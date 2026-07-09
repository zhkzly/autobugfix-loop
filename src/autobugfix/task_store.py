from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml

from autobugfix.events import append_event, read_events
from autobugfix.models import Event, TaskRecord, utc_now


class TaskStoreError(RuntimeError):
    pass


def slugify(value: str, max_len: int = 48) -> str:
    chars: list[str] = []
    last_dash = False
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    slug = "".join(chars).strip("-")
    return (slug or "task")[:max_len].strip("-") or "task"


class TaskStore:
    def __init__(self, project_root: Path, task_root: Path | str = ".autobugfix/tasks") -> None:
        self.project_root = project_root.resolve()
        self.task_root = self._resolve(task_root)
        self.archive_root = self.project_root / ".autobugfix/archive"

    def _resolve(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        return candidate.resolve()

    def new_task_id(self, title: str) -> str:
        return f"{utc_now()[:10].replace('-', '')}-{slugify(title, 36)}-{uuid.uuid4().hex[:8]}"

    def task_dir(self, task_id: str) -> Path:
        return self.task_root / task_id

    def events_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "events.jsonl"

    def create(self, record: TaskRecord) -> Path:
        path = self.task_dir(record.task_id)
        if path.exists():
            raise TaskStoreError(f"task already exists: {record.task_id}")
        for rel in ("context", "feedback", "runs", "artifacts", "logs", "controller"):
            (path / rel).mkdir(parents=True, exist_ok=True)
        self.save(record)
        self.append_event(record.task_id, "task_created", {"repo_id": record.repo_id, "title": record.title})
        if record.body:
            self.add_context(record.task_id, "initial", record.body)
        return path

    def find_task_dir(self, task_id: str) -> Path:
        active = self.task_root / task_id
        if active.exists():
            return active
        if self.archive_root.exists():
            for task_yaml in self.archive_root.glob(f"*/*/task.yaml"):
                if task_yaml.parent.name == task_id:
                    return task_yaml.parent
        raise TaskStoreError(f"task not found: {task_id}")

    def load(self, task_id: str) -> TaskRecord:
        path = self.find_task_dir(task_id) / "task.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise TaskStoreError(f"invalid task.yaml for {task_id}")
        return TaskRecord.from_dict(data)

    def save(self, record: TaskRecord) -> None:
        record.updated_at = utc_now()
        path = self.task_dir(record.task_id)
        if record.archived_path:
            path = Path(record.archived_path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "task.yaml").write_text(
            yaml.safe_dump(record.to_dict(), sort_keys=False),
            encoding="utf-8",
        )

    def append_event(self, task_id: str, kind: str, payload: dict[str, Any]) -> Event:
        path = self.find_task_dir(task_id) / "events.jsonl" if not (self.task_root / task_id).exists() else self.events_path(task_id)
        return append_event(path, kind, payload)

    def events(self, task_id: str) -> list[Event]:
        return read_events(self.find_task_dir(task_id) / "events.jsonl")

    def add_context(self, task_id: str, kind: str, content: str) -> Path:
        task_dir = self.find_task_dir(task_id)
        context_dir = task_dir / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(context_dir.glob("*.md"))) + 1
        path = context_dir / f"{index:03d}-{slugify(kind, 24)}.md"
        path.write_text(content, encoding="utf-8")
        self.append_event(task_id, "context_added", {"kind": kind, "path": str(path.relative_to(task_dir))})
        return path

    def add_feedback(self, task_id: str, decision: str, content: str, queue_only: bool = False) -> Path:
        task_dir = self.find_task_dir(task_id)
        feedback_dir = task_dir / "feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(feedback_dir.glob("*.md"))) + 1
        path = feedback_dir / f"{index:03d}-{slugify(decision, 24)}.md"
        path.write_text(content, encoding="utf-8")
        self.append_event(
            task_id,
            "feedback_added",
            {"decision": decision, "queue_only": queue_only, "path": str(path.relative_to(task_dir))},
        )
        return path

    def list_active(self) -> list[TaskRecord]:
        if not self.task_root.exists():
            return []
        records = []
        for path in sorted(self.task_root.glob("*/task.yaml")):
            records.append(TaskRecord.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))
        return records

    def read_text_tree(self, task_id: str, rel_dir: str) -> str:
        base = self.find_task_dir(task_id) / rel_dir
        if not base.exists():
            return ""
        parts = []
        for path in sorted(base.glob("*.md")):
            parts.append(f"# {path.name}\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(parts)

    def archive(self, task_id: str, result: str) -> Path:
        src = self.task_root / task_id
        if not src.exists():
            raise TaskStoreError(f"only active tasks can be archived: {task_id}")
        dest = self.archive_root / result / task_id
        if dest.exists():
            raise TaskStoreError(f"archive destination already exists: {dest}")
        record = self.load(task_id)
        record.archived_result = result
        record.archived_path = str(dest)
        record.state = "archived"
        record.updated_at = utc_now()
        (src / "task.yaml").write_text(
            yaml.safe_dump(record.to_dict(), sort_keys=False),
            encoding="utf-8",
        )
        self.append_event(task_id, "task_archived", {"result": result, "path": str(dest)})
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return dest
