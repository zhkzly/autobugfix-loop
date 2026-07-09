from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from autobugfix.config import parse_role_config
from autobugfix.models import RoleConfig


@dataclass(slots=True)
class MemoryMaintainerConfig:
    backend: str = "codex"
    model: str | None = None
    timeout_seconds: int | None = None
    role: RoleConfig | None = None


@dataclass(slots=True)
class MemoryConfig:
    project_root: Path
    root: Path
    maintainer: MemoryMaintainerConfig


def load_memory_config(project_root: Path | str = ".") -> MemoryConfig:
    root = Path(project_root).resolve()
    memory_root = root / ".autobugfix-memory"
    path = memory_root / "config.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    maintainer_raw = raw.get("maintainer") or {}
    if not isinstance(maintainer_raw, dict):
        maintainer_raw = {}
    return MemoryConfig(
        project_root=root,
        root=memory_root,
        maintainer=MemoryMaintainerConfig(
            backend=str(maintainer_raw.get("backend", "codex")),
            model=maintainer_raw.get("model"),
            timeout_seconds=int(maintainer_raw["timeout_seconds"]) if "timeout_seconds" in maintainer_raw else None,
            role=parse_role_config(maintainer_raw.get("role"), "maintainer.role") if isinstance(maintainer_raw.get("role"), dict) else None,
        ),
    )
