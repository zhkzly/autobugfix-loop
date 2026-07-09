from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from autobugfix.models import Event, utc_now


def read_events(path: Path) -> list[Event]:
    if not path.exists():
        return []
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        events.append(
            Event(
                seq=int(raw["seq"]),
                timestamp=str(raw["timestamp"]),
                kind=str(raw["kind"]),
                payload=dict(raw.get("payload") or {}),
            )
        )
    return events


def append_event(path: Path, kind: str, payload: dict[str, Any]) -> Event:
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = len(read_events(path)) + 1
    event = Event(seq=seq, timestamp=utc_now(), kind=kind, payload=payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "seq": event.seq,
                    "timestamp": event.timestamp,
                    "kind": event.kind,
                    "payload": event.payload,
                },
                sort_keys=True,
            )
            + "\n"
        )
    return event


def events_to_dicts(events: Iterable[Event]) -> list[dict[str, Any]]:
    return [
        {
            "seq": event.seq,
            "timestamp": event.timestamp,
            "kind": event.kind,
            "payload": event.payload,
        }
        for event in events
    ]
