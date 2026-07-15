from __future__ import annotations

from pathlib import Path

from autobugfix.config import load_config
from autobugfix.git_utils import GitError, git_common_dir, git_dir
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
    hidden_paths: tuple[Path, ...] = (),
    expected_git_common_dir: Path | None = None,
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
    task_root = cfg.task_root
    if not task_root.is_absolute():
        task_root = project_root / task_root
    authority_paths = (
        task_root,
        project_root / ".autobugfix/archive",
        project_root / ".autobugfix/controller",
        cfg.eval.benchmarks.cache_root,
        cfg.eval.benchmarks.trusted_case_root,
        cfg.operator.state.root,
        cfg.operator.artifacts.root,
        project_root / ".autobugfix-memory",
        *hidden_paths,
    )
    readable_paths: tuple[Path, ...] = ()
    writable_paths: tuple[Path, ...] = ()
    if expected_git_common_dir is not None:
        expected = expected_git_common_dir.resolve()
        try:
            observed_common = git_common_dir(cwd)
            observed_git_dir = git_dir(cwd)
        except GitError as exc:
            raise RuntimeError(
                f"cannot resolve trusted task worktree Git metadata: {exc}"
            ) from exc
        if observed_common != expected:
            raise RuntimeError(
                "task worktree Git common directory differs from service-owned repository"
            )
        if observed_git_dir != observed_common and not observed_git_dir.is_relative_to(
            observed_common
        ):
            raise RuntimeError("task worktree Git directory escapes its common directory")
        git_pointer = cwd.resolve() / ".git"
        readable_paths = tuple(
            dict.fromkeys(
                path
                for path in (observed_common, git_pointer)
                if path.exists()
            )
        )
    return CodexRequest(
        role=role,
        prompt=prompt,
        cwd=cwd,
        control_root=project_root.resolve(),
        sandbox=sandbox or resolved.sandbox,
        model=model if model is not None else resolved.model,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else resolved.timeout_seconds,
        developer_instructions=instructions,
        raw_log_path=raw_log_path,
        stderr_log_path=stderr_log_path,
        approval_mode=resolved.approval_mode,
        hidden_paths=tuple(dict.fromkeys(path.resolve() for path in authority_paths)),
        readable_paths=readable_paths,
        writable_paths=writable_paths,
        require_process_isolation=True,
    )
