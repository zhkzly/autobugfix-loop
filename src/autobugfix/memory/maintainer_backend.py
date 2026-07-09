from __future__ import annotations

from pathlib import Path
from typing import Protocol

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request


class MemoryMaintainerBackend(Protocol):
    def maintain(self, project_root: Path, run_dir: Path, digest: str, model: str | None, timeout_seconds: int) -> str:
        """Generate proposal text from a digest."""


class CodexMemoryMaintainerBackend:
    def __init__(self, backend: CodexBackend) -> None:
        self.backend = backend

    def maintain(self, project_root: Path, run_dir: Path, digest: str, model: str | None, timeout_seconds: int) -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        request = build_codex_request(
            project_root,
            "memory_maintainer",
            "\n\n".join(
                [
                    "Create a concise reviewed-memory proposal from this accepted task digest.",
                    "Return Markdown. If no stable memory should be changed, start with NO_CHANGE.",
                    digest,
                ]
            ),
            run_dir,
            "workspace-write",
            model,
            timeout_seconds,
            run_dir / "maintainer.raw.jsonl",
            run_dir / "maintainer.stderr.log",
        )
        return self.backend.run(request).text
