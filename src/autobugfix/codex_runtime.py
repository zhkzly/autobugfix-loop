from __future__ import annotations

from pathlib import Path

from autobugfix.config import load_config
from autobugfix.models import CodexRequest, ResolvedRoleConfig, RoleConfig
from autobugfix.prompts import assert_skill_guard, load_role_instructions
from autobugfix.role_config import resolve_role, role_skill_catalog


def build_codex_request(
    project_root: Path,
    role: str,
    prompt: str,
    cwd: Path,
    sandbox: str | None,
    model: str | None,
    timeout_seconds: int | None,
    raw_log_path: Path,
    stderr_log_path: Path,
    repo_id: str | None = None,
    role_override: RoleConfig | None = None,
    resolved_role: ResolvedRoleConfig | None = None,
) -> CodexRequest:
    cfg = load_config(project_root)
    resolved = resolved_role or resolve_role(cfg, role, repo_id=repo_id, overrides=(role_override,))
    strict = cfg.codex.role_runtime.strict_skill_guard
    instructions = load_role_instructions(project_root, role, strict=strict, skill_paths=resolved.skill_paths)
    if cfg.codex.role_runtime.skill_guard:
        assert_skill_guard(
            project_root,
            role,
            instructions,
            expected_paths=resolved.skill_paths,
            role_catalog=role_skill_catalog(cfg),
        )
    return CodexRequest(
        role=role,
        prompt=prompt,
        cwd=cwd,
        sandbox=sandbox or resolved.sandbox,
        model=model if model is not None else resolved.model,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else resolved.timeout_seconds,
        developer_instructions=instructions,
        raw_log_path=raw_log_path,
        stderr_log_path=stderr_log_path,
        approval_mode=resolved.approval_mode,
    )
