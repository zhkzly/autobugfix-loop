from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.models import (
    AutobugfixConfig,
    CodexConfig,
    EvalConfig,
    PpeConfig,
    RepoProfile,
    RoleConfig,
    RoleRuntimeConfig,
    SchedulerConfig,
    TestCommands,
    WorkerConfig,
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
        "default_model": None,
        "default_timeout_seconds": None,
        "writer_model": None,
        "evaluator_model": None,
        "controller_model": None,
        "role_runtime": {
            "enabled": True,
            "runtime_root": ".autobugfix/runtime/codex-sdk",
            "codex_bin": None,
            "bridge_auth": True,
            "skill_guard": True,
            "strict_skill_guard": True,
        },
        "roles": {
            "writer": {
                "backend": "codex",
                "model": None,
                "sandbox": "workspace-write",
                "approval_mode": "auto_review",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/execution/writer/autobugfix-writer/SKILL.md",
                ],
                "raw_log_template": "logs/writer-{iteration}.raw.jsonl",
                "stderr_log_template": "logs/writer-{iteration}.stderr.log",
                "allow_repo_overrides": True,
            },
            "evaluator": {
                "backend": "codex",
                "model": None,
                "sandbox": "read-only",
                "approval_mode": "deny_all",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md",
                ],
                "raw_log_template": "logs/evaluator-{iteration}.raw.jsonl",
                "stderr_log_template": "logs/evaluator-{iteration}.stderr.log",
                "allow_repo_overrides": True,
            },
            "controller": {
                "backend": "codex",
                "model": None,
                "sandbox": "read-only",
                "approval_mode": "deny_all",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                ],
                "raw_log_template": ".autobugfix/controller/{role}.raw.jsonl",
                "stderr_log_template": ".autobugfix/controller/{role}.stderr.log",
                "allow_repo_overrides": False,
            },
            "memory_maintainer": {
                "backend": "codex",
                "model": None,
                "sandbox": "workspace-write",
                "approval_mode": "auto_review",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/memory/maintainer/autobugfix-memory-maintainer/SKILL.md",
                ],
                "raw_log_template": "maintainer.raw.jsonl",
                "stderr_log_template": "maintainer.stderr.log",
                "allow_repo_overrides": False,
            },
            "eval_judge": {
                "backend": "codex",
                "model": None,
                "sandbox": "read-only",
                "approval_mode": "deny_all",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/eval/judge/autobugfix-eval-judge/SKILL.md",
                ],
                "raw_log_template": "judge.raw.jsonl",
                "stderr_log_template": "judge.stderr.log",
                "allow_repo_overrides": False,
            },
        },
    },
    "worker": {
        "tick_interval_seconds": 5,
        "heartbeat_interval_seconds": 5,
    },
    "memory_worker": {
        "tick_interval_seconds": 10,
        "heartbeat_interval_seconds": 10,
    },
    "eval": {
        "model_mode": "codex",
        "roles": {},
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


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _parse_role_config(raw_any: Any, field: str) -> RoleConfig:
    raw = _as_mapping(raw_any, field)
    skill_paths = raw.get("skill_paths")
    if skill_paths is None:
        parsed_skill_paths = None
    elif isinstance(skill_paths, list):
        parsed_skill_paths = tuple(str(item) for item in skill_paths)
    else:
        raise ConfigError(f"{field}.skill_paths must be a list")
    return RoleConfig(
        backend=raw.get("backend"),
        model=raw.get("model"),
        sandbox=raw.get("sandbox"),
        approval_mode=raw.get("approval_mode"),
        timeout_seconds=_optional_int(raw.get("timeout_seconds")),
        skill_paths=parsed_skill_paths,
        raw_log_template=raw.get("raw_log_template"),
        stderr_log_template=raw.get("stderr_log_template"),
        allow_repo_overrides=raw.get("allow_repo_overrides") if "allow_repo_overrides" in raw else None,
    )


def _parse_roles(raw_any: Any, field: str) -> dict[str, RoleConfig]:
    roles: dict[str, RoleConfig] = {}
    for role, role_raw in _as_mapping(raw_any, field).items():
        roles[str(role)] = _parse_role_config(role_raw, f"{field}.{role}")
    return roles


def parse_role_config(raw_any: Any, field: str = "role") -> RoleConfig:
    return _parse_role_config(raw_any, field)


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

    merged = _deep_merge(default_config_dict(), raw)

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
        codex_bin=_resolve(root, runtime_raw.get("codex_bin")),
        bridge_auth=bool(runtime_raw.get("bridge_auth", True)),
        skill_guard=bool(runtime_raw.get("skill_guard", True)),
        strict_skill_guard=bool(runtime_raw.get("strict_skill_guard", True)),
    )
    roles = _parse_roles(codex_raw.get("roles"), "codex.roles")
    legacy_role_models = {
        "writer": codex_raw.get("writer_model"),
        "evaluator": codex_raw.get("evaluator_model"),
        "controller": codex_raw.get("controller_model"),
    }
    for role, model in legacy_role_models.items():
        if model is not None and role in roles and roles[role].model is None:
            roles[role].model = str(model)
    codex = CodexConfig(
        default_model=codex_raw.get("default_model"),
        default_timeout_seconds=_optional_int(codex_raw.get("default_timeout_seconds")),
        writer_model=codex_raw.get("writer_model"),
        evaluator_model=codex_raw.get("evaluator_model"),
        controller_model=codex_raw.get("controller_model"),
        role_runtime=role_runtime,
        roles=roles,
    )

    worker_raw = _as_mapping(merged.get("worker"), "worker")
    worker = WorkerConfig(
        tick_interval_seconds=int(worker_raw.get("tick_interval_seconds", 5)),
        heartbeat_interval_seconds=int(worker_raw.get("heartbeat_interval_seconds", 5)),
    )
    memory_worker_raw = _as_mapping(merged.get("memory_worker"), "memory_worker")
    memory_worker = WorkerConfig(
        tick_interval_seconds=int(memory_worker_raw.get("tick_interval_seconds", 10)),
        heartbeat_interval_seconds=int(memory_worker_raw.get("heartbeat_interval_seconds", 10)),
    )
    eval_raw = _as_mapping(merged.get("eval"), "eval")
    eval_config = EvalConfig(
        model_mode=str(eval_raw.get("model_mode", "codex")),
        roles=_parse_roles(eval_raw.get("roles"), "eval.roles"),
    )

    repos: dict[str, RepoProfile] = {}
    for repo_id, repo_raw_any in _as_mapping(merged.get("repos"), "repos").items():
        repo_raw = _as_mapping(repo_raw_any, f"repos.{repo_id}")
        if not repo_raw.get("main_checkout"):
            raise ConfigError(f"repos.{repo_id}.main_checkout is required")
        test_raw = _as_mapping(repo_raw.get("test_commands"), f"repos.{repo_id}.test_commands")
        ppe_raw = _as_mapping(repo_raw.get("ppe"), f"repos.{repo_id}.ppe")
        repo_codex_raw = _as_mapping(repo_raw.get("codex"), f"repos.{repo_id}.codex")
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
            codex_roles=_parse_roles(repo_codex_raw.get("roles"), f"repos.{repo_id}.codex.roles"),
        )

    return AutobugfixConfig(
        project_root=root,
        task_root=Path(str(merged.get("task_root", ".autobugfix/tasks"))),
        scheduler=scheduler,
        codex=codex,
        worker=worker,
        memory_worker=memory_worker,
        eval=eval_config,
        repos=repos,
    )


def write_default_config(project_root: Path | str = ".") -> Path:
    root = Path(project_root).resolve()
    path = root / ".autobugfix/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(default_config_dict(), sort_keys=False), encoding="utf-8")
    return path
