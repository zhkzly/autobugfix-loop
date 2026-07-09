from __future__ import annotations

from pathlib import Path
from typing import Protocol

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.config import load_config
from autobugfix.models import RoleConfig
from autobugfix.role_config import resolve_role


class MemoryMaintainerBackend(Protocol):
    def maintain(
        self,
        project_root: Path,
        run_dir: Path,
        digest: str,
        model: str | None,
        timeout_seconds: int | None,
        role_override: RoleConfig | None = None,
    ) -> str:
        """Generate proposal text from a digest."""


class CodexMemoryMaintainerBackend:
    def __init__(self, backend: CodexBackend) -> None:
        self.backend = backend

    def maintain(
        self,
        project_root: Path,
        run_dir: Path,
        digest: str,
        model: str | None,
        timeout_seconds: int | None,
        role_override: RoleConfig | None = None,
    ) -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved_role = resolve_role(
            load_config(project_root),
            "memory_maintainer",
            overrides=(role_override, RoleConfig(model=model, timeout_seconds=timeout_seconds)),
        )
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
            None,
            None,
            None,
            _run_path(run_dir, resolved_role.raw_log_template),
            _run_path(run_dir, resolved_role.stderr_log_template),
            resolved_role=resolved_role,
        )
        return self.backend.run(request).text


def _run_path(run_dir: Path, template: str) -> Path:
    path = Path(template.format(role="memory_maintainer", iteration=1, task_id="", repo_id=""))
    if not path.is_absolute():
        path = run_dir / path
    return path
