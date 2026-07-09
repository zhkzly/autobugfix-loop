from __future__ import annotations

from typing import Any

from autobugfix.memory.store import MemoryStore


def memory_status(store: MemoryStore) -> dict[str, Any]:
    return {
        "root": str(store.root),
        "raw_tasks": len(list((store.root / "raw/tasks").glob("*"))),
        "digests": len(list((store.root / "digests/tasks").glob("*.md"))),
        "proposals": store.list_proposals() if (store.root / "proposals").exists() else [],
    }
