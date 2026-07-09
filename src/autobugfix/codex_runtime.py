from __future__ import annotations

from pathlib import Path

from autobugfix.config import load_config
from autobugfix.models import CodexRequest
from autobugfix.prompts import assert_skill_guard, load_role_instructions


def build_codex_request(
    project_root: Path,
    role: str,
    prompt: str,
    cwd: Path,
    sandbox: str,
    model: str | None,
    timeout_seconds: int | None,
    raw_log_path: Path,
    stderr_log_path: Path,
) -> CodexRequest:
    cfg = load_config(project_root)
    strict = cfg.codex.role_runtime.strict_skill_guard
    instructions = load_role_instructions(project_root, role, strict=strict)
    if cfg.codex.role_runtime.skill_guard:
        assert_skill_guard(project_root, role, instructions)
    return CodexRequest(
        role=role,
        prompt=prompt,
        cwd=cwd,
        sandbox=sandbox,
        model=model,
        timeout_seconds=timeout_seconds,
        developer_instructions=instructions,
        raw_log_path=raw_log_path,
        stderr_log_path=stderr_log_path,
    )
