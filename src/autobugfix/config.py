from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.models import (
    AutobugfixConfig,
    CodexConfig,
    PpeConfig,
    RepoProfile,
    RoleRuntimeConfig,
    SchedulerConfig,
    TestCommands,
)


class ConfigError(RuntimeError):
    pass


DEFAULT_CONFIG: dict[str, Any] = {
    "task_root": ".autobugfix/tasks",
    "scheduler": {
        "default_max_concurrent": 2,
        "lock_timeout_seconds": 7200,
        "max_auto_iterations": 3,
        "codex_timeout_seconds": 1800,
        "writer_timeout_seconds": None,
        "evaluator_timeout_seconds": None,
    },
    "codex": {
        "writer_model": None,
        "evaluator_model": None,
        "controller_model": None,
        "role_runtime": {
            "enabled": True,
            "runtime_root": ".autobugfix/runtime/codex-sdk",
            "bridge_auth": True,
            "skill_guard": True,
            "strict_skill_guard": True,
        },
    },
    "repos": {},
}


def _as_mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a mapping")
    return value


def _resolve(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def default_config_dict() -> dict[str, Any]:
    return yaml.safe_load(yaml.safe_dump(DEFAULT_CONFIG))


def load_config(project_root: Path | str = ".") -> AutobugfixConfig:
    root = Path(project_root).resolve()
    path = root / ".autobugfix/config.yaml"
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(".autobugfix/config.yaml must contain a mapping")

    merged = default_config_dict()
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value

    scheduler_raw = _as_mapping(merged.get("scheduler"), "scheduler")
    scheduler = SchedulerConfig(
        default_max_concurrent=int(scheduler_raw.get("default_max_concurrent", 2)),
        lock_timeout_seconds=int(scheduler_raw.get("lock_timeout_seconds", 7200)),
        max_auto_iterations=int(scheduler_raw.get("max_auto_iterations", 3)),
        codex_timeout_seconds=int(scheduler_raw.get("codex_timeout_seconds", 1800)),
        writer_timeout_seconds=scheduler_raw.get("writer_timeout_seconds"),
        evaluator_timeout_seconds=scheduler_raw.get("evaluator_timeout_seconds"),
    )

    codex_raw = _as_mapping(merged.get("codex"), "codex")
    runtime_raw = _as_mapping(codex_raw.get("role_runtime"), "codex.role_runtime")
    role_runtime = RoleRuntimeConfig(
        enabled=bool(runtime_raw.get("enabled", True)),
        runtime_root=Path(str(runtime_raw.get("runtime_root", ".autobugfix/runtime/codex-sdk"))),
        bridge_auth=bool(runtime_raw.get("bridge_auth", True)),
        skill_guard=bool(runtime_raw.get("skill_guard", True)),
        strict_skill_guard=bool(runtime_raw.get("strict_skill_guard", True)),
    )
    codex = CodexConfig(
        writer_model=codex_raw.get("writer_model"),
        evaluator_model=codex_raw.get("evaluator_model"),
        controller_model=codex_raw.get("controller_model"),
        role_runtime=role_runtime,
    )

    repos: dict[str, RepoProfile] = {}
    for repo_id, repo_raw_any in _as_mapping(merged.get("repos"), "repos").items():
        repo_raw = _as_mapping(repo_raw_any, f"repos.{repo_id}")
        if not repo_raw.get("main_checkout"):
            raise ConfigError(f"repos.{repo_id}.main_checkout is required")
        test_raw = _as_mapping(repo_raw.get("test_commands"), f"repos.{repo_id}.test_commands")
        ppe_raw = _as_mapping(repo_raw.get("ppe"), f"repos.{repo_id}.ppe")
        worktree_root = _resolve(root, repo_raw.get("worktree_root"))
        if worktree_root is None:
            worktree_root = (root / ".autobugfix/worktrees" / str(repo_id)).resolve()
        repos[str(repo_id)] = RepoProfile(
            repo_id=str(repo_id),
            main_checkout=_resolve(root, repo_raw["main_checkout"]) or root,
            remote=str(repo_raw.get("remote", "origin")),
            main_branch=str(repo_raw.get("main_branch", "main")),
            worktree_root=worktree_root,
            branch_template=str(repo_raw.get("branch_template", "fix/{date}_oncall_{slug}")),
            test_commands=TestCommands(
                targeted=str(test_raw.get("targeted", "uv run pytest --no-cov {target}")),
                full=str(test_raw.get("full", "uv run pytest")),
            ),
            ppe=PpeConfig(
                enabled=bool(ppe_raw.get("enabled", True)),
                command_template=ppe_raw.get("command_template"),
            ),
        )

    return AutobugfixConfig(
        project_root=root,
        task_root=Path(str(merged.get("task_root", ".autobugfix/tasks"))),
        scheduler=scheduler,
        codex=codex,
        repos=repos,
    )


def write_default_config(project_root: Path | str = ".") -> Path:
    root = Path(project_root).resolve()
    path = root / ".autobugfix/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(default_config_dict(), sort_keys=False), encoding="utf-8")
    return path
