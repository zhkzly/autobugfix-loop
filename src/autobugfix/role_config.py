from __future__ import annotations

from pathlib import Path
from typing import Iterable

from autobugfix.models import AutobugfixConfig, ResolvedRoleConfig, RoleConfig


class RoleConfigError(RuntimeError):
    pass


ROLE_LOG_DEFAULTS: dict[str, tuple[str, str]] = {
    "writer": ("logs/writer-{iteration}.raw.jsonl", "logs/writer-{iteration}.stderr.log"),
    "evaluator": ("logs/evaluator-{iteration}.raw.jsonl", "logs/evaluator-{iteration}.stderr.log"),
    "controller": (".autobugfix/controller/{role}.raw.jsonl", ".autobugfix/controller/{role}.stderr.log"),
    "memory_maintainer": ("maintainer.raw.jsonl", "maintainer.stderr.log"),
    "eval_judge": ("judge.raw.jsonl", "judge.stderr.log"),
}


def merge_role_config(base: RoleConfig, override: RoleConfig | None) -> RoleConfig:
    if override is None:
        return base
    return RoleConfig(
        backend=override.backend if override.backend is not None else base.backend,
        model=override.model if override.model is not None else base.model,
        sandbox=override.sandbox if override.sandbox is not None else base.sandbox,
        approval_mode=override.approval_mode if override.approval_mode is not None else base.approval_mode,
        timeout_seconds=override.timeout_seconds if override.timeout_seconds is not None else base.timeout_seconds,
        skill_paths=override.skill_paths if override.skill_paths is not None else base.skill_paths,
        raw_log_template=override.raw_log_template if override.raw_log_template is not None else base.raw_log_template,
        stderr_log_template=override.stderr_log_template if override.stderr_log_template is not None else base.stderr_log_template,
        allow_repo_overrides=override.allow_repo_overrides if override.allow_repo_overrides is not None else base.allow_repo_overrides,
    )


def _timeout_fallback(config: AutobugfixConfig, role: str) -> int:
    if role == "writer" and config.scheduler.writer_timeout_seconds is not None:
        return int(config.scheduler.writer_timeout_seconds)
    if role == "evaluator" and config.scheduler.evaluator_timeout_seconds is not None:
        return int(config.scheduler.evaluator_timeout_seconds)
    if config.codex.default_timeout_seconds is not None:
        return int(config.codex.default_timeout_seconds)
    return int(config.scheduler.codex_timeout_seconds)


def _resolve_skill_paths(project_root: Path, role: str, values: Iterable[str] | None) -> tuple[Path, ...]:
    if not values:
        raise RoleConfigError(f"role {role} has no skill_paths configured")
    paths: list[Path] = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = project_root / path
        paths.append(path.resolve())
    return tuple(paths)


def resolve_role(
    config: AutobugfixConfig,
    role: str,
    repo_id: str | None = None,
    overrides: Iterable[RoleConfig | None] = (),
) -> ResolvedRoleConfig:
    base = config.codex.roles.get(role)
    if base is None:
        raise RoleConfigError(f"unknown role: {role}")
    source = {
        "base": "codex.roles",
        "model": "codex.default_model",
        "timeout": "scheduler.codex_timeout_seconds",
        "repo_override": "none",
    }
    merged = base
    for override in overrides:
        if override is not None:
            merged = merge_role_config(merged, override)
            source["base"] = "role override"
    allow_repo_overrides = True if merged.allow_repo_overrides is None else bool(merged.allow_repo_overrides)
    if repo_id and allow_repo_overrides:
        repo = config.repo(repo_id)
        repo_override = repo.codex_roles.get(role)
        if repo_override is not None:
            merged = merge_role_config(merged, repo_override)
            source["repo_override"] = f"repos.{repo_id}.codex.roles.{role}"

    model = merged.model if merged.model is not None else config.codex.default_model
    if merged.model is not None:
        source["model"] = "role"
    timeout_seconds = merged.timeout_seconds if merged.timeout_seconds is not None else _timeout_fallback(config, role)
    if merged.timeout_seconds is not None:
        source["timeout"] = "role"
    elif role == "writer" and config.scheduler.writer_timeout_seconds is not None:
        source["timeout"] = "scheduler.writer_timeout_seconds"
    elif role == "evaluator" and config.scheduler.evaluator_timeout_seconds is not None:
        source["timeout"] = "scheduler.evaluator_timeout_seconds"
    elif config.codex.default_timeout_seconds is not None:
        source["timeout"] = "codex.default_timeout_seconds"

    sandbox = merged.sandbox or "read-only"
    approval_mode = merged.approval_mode or ("auto_review" if sandbox == "workspace-write" else "deny_all")
    raw_default, stderr_default = ROLE_LOG_DEFAULTS.get(role, (f"{role}.raw.jsonl", f"{role}.stderr.log"))
    return ResolvedRoleConfig(
        role=role,
        backend=merged.backend or "codex",
        model=model,
        sandbox=sandbox,
        approval_mode=approval_mode,
        timeout_seconds=int(timeout_seconds),
        skill_paths=_resolve_skill_paths(config.project_root, role, merged.skill_paths),
        raw_log_template=merged.raw_log_template or raw_default,
        stderr_log_template=merged.stderr_log_template or stderr_default,
        allow_repo_overrides=allow_repo_overrides,
        source=source,
    )


def role_skill_catalog(config: AutobugfixConfig) -> dict[str, tuple[Path, ...]]:
    catalog: dict[str, tuple[Path, ...]] = {}
    for role in config.codex.roles:
        catalog[role] = resolve_role(config, role).skill_paths
    return catalog
