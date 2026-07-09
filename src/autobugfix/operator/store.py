from __future__ import annotations

import fcntl
import json
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.models import utc_now
from autobugfix.operator.models import OperatorApproval, OperatorEvent, OperatorRequest, OperatorTriage


class OperatorStoreError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def safe_id(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise OperatorStoreError(f"operator id contains unsupported characters: {value!r}")
    return value


class OperatorStore:
    """Own immutable governance records and hash-chained request events."""

    def __init__(self, project_root: Path | str = ".") -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".autobugfix/operator"

    def init(self) -> None:
        for name in (
            "triage",
            "requests",
            "approvals",
            "events",
            "validations",
            "baselines",
            "workspaces",
            "logs",
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def next_id(self, prefix: str = "op") -> str:
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
        return f"{safe_id(prefix)}-{stamp}-{uuid.uuid4().hex[:8]}"

    def _write_new_yaml(self, path: Path, data: Mapping[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                yaml.safe_dump(dict(data), handle, sort_keys=False)
        except FileExistsError as exc:
            raise OperatorStoreError(f"immutable operator record already exists: {path}") from exc
        return path

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise OperatorStoreError(f"missing operator record: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise OperatorStoreError(f"operator record must be a mapping: {path}")
        return data

    def write_triage(self, triage: OperatorTriage) -> Path:
        self.init()
        return self._write_new_yaml(self.root / "triage" / f"{safe_id(triage.triage_id)}.yaml", triage.to_dict())

    def read_triage(self, triage_id: str) -> OperatorTriage:
        return OperatorTriage.from_dict(self._read_yaml(self.root / "triage" / f"{safe_id(triage_id)}.yaml"))

    def write_request(self, request: OperatorRequest) -> Path:
        self.init()
        return self._write_new_yaml(self.root / "requests" / f"{safe_id(request.request_id)}.yaml", request.to_dict())

    def read_request(self, request_id: str) -> OperatorRequest:
        return OperatorRequest.from_dict(self._read_yaml(self.root / "requests" / f"{safe_id(request_id)}.yaml"))

    def write_approval(self, approval: OperatorApproval) -> Path:
        self.init()
        name = f"{safe_id(approval.request_id)}-{safe_id(approval.approval_id)}.yaml"
        return self._write_new_yaml(self.root / "approvals" / name, approval.to_dict())

    def read_approvals(self, request_id: str) -> list[OperatorApproval]:
        self.init()
        prefix = f"{safe_id(request_id)}-"
        return [
            OperatorApproval.from_dict(self._read_yaml(path))
            for path in sorted((self.root / "approvals").glob(f"{prefix}*.yaml"))
        ]

    def event_path(self, request_id: str) -> Path:
        return self.root / "events" / f"{safe_id(request_id)}.jsonl"

    def append_event(self, request_id: str, kind: str, actor: str, payload: Mapping[str, Any] | None = None) -> OperatorEvent:
        self.init()
        path = self.event_path(request_id)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            lines = [line for line in handle.read().splitlines() if line.strip()]
            previous_hash: str | None = None
            if lines:
                previous = OperatorEvent.from_dict(json.loads(lines[-1]))
                previous_hash = previous.computed_hash
            event = OperatorEvent(
                event_id=uuid.uuid4().hex,
                request_id=request_id,
                kind=kind,
                actor=actor,
                payload=dict(payload or {}),
                previous_hash=previous_hash,
            )
            handle.seek(0, 2)
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return event

    def read_events(self, request_id: str) -> list[OperatorEvent]:
        path = self.event_path(request_id)
        if not path.exists():
            return []
        events: list[OperatorEvent] = []
        expected_previous: str | None = None
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = OperatorEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise OperatorStoreError(f"invalid operator event at {path}:{line_number}: {exc}") from exc
            if event.request_id != request_id:
                raise OperatorStoreError(f"event request mismatch at {path}:{line_number}")
            if event.previous_hash != expected_previous:
                raise OperatorStoreError(f"event hash chain mismatch at {path}:{line_number}")
            expected_previous = event.computed_hash
            events.append(event)
        return events

    def write_validation(self, request_id: str, validation_id: str, data: Mapping[str, Any]) -> Path:
        self.init()
        name = f"{safe_id(request_id)}-{safe_id(validation_id)}.yaml"
        return self._write_new_yaml(self.root / "validations" / name, data)

    def read_validation(self, request_id: str, validation_id: str) -> dict[str, Any]:
        name = f"{safe_id(request_id)}-{safe_id(validation_id)}.yaml"
        return self._read_yaml(self.root / "validations" / name)

    def write_workspace(self, request_id: str, data: Mapping[str, Any]) -> Path:
        self.init()
        return self._write_new_yaml(self.root / "workspaces" / f"{safe_id(request_id)}.yaml", data)

    def read_workspace(self, request_id: str) -> dict[str, Any]:
        return self._read_yaml(self.root / "workspaces" / f"{safe_id(request_id)}.yaml")
