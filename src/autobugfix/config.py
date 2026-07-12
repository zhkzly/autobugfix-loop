from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.models import (
    AutobugfixConfig,
    CodexConfig,
    DEFECTS4J_DOCKER_IMAGE,
    DEFECTS4J_DOCKER_PLATFORM,
    DEFECTS4J_FRAMEWORK_REVISION,
    DEFECTS4J_VERIFIER_IMAGE,
    Defects4JBenchmarkConfig,
    EvalBenchmarkConfig,
    EvalConfig,
    EvalGuardConfig,
    OperatorArtifactConfig,
    OperatorBudgetConfig,
    OperatorConfig,
    OperatorExperimentConfig,
    OperatorExperimentLineConfig,
    OperatorPromotionConfig,
    OperatorRetryConfig,
    OperatorStateConfig,
    OperatorVerificationConfig,
    OperatorWorktreeConfig,
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


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


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
        "reasoning_effort": "medium",
        "service_tier": None,
        "disable_response_storage": True,
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
            "operator_supervisor": {
                "backend": "codex",
                "model": None,
                "sandbox": "read-only",
                "approval_mode": "deny_all",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md",
                ],
                "raw_log_template": ".autobugfix/operator-runs/{role}.raw.jsonl",
                "stderr_log_template": ".autobugfix/operator-runs/{role}.stderr.log",
                "allow_repo_overrides": False,
            },
            "operator_writer": {
                "backend": "codex",
                "model": None,
                "sandbox": "workspace-write",
                "approval_mode": "auto_review",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md",
                ],
                "raw_log_template": "writer.raw.jsonl",
                "stderr_log_template": "writer.stderr.log",
                "allow_repo_overrides": False,
            },
            "operator_verifier": {
                "backend": "codex",
                "model": None,
                "sandbox": "read-only",
                "approval_mode": "deny_all",
                "timeout_seconds": None,
                "skill_paths": [
                    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
                    ".agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md",
                ],
                "raw_log_template": "verifier.raw.jsonl",
                "stderr_log_template": "verifier.stderr.log",
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
        "benchmarks": {
            "cache_root": ".autobugfix/benchmark-cache",
            "trusted_case_root": ".autobugfix/trusted-eval-cases",
            "visible_manifest_root": ".autobugfix/eval-manifests",
            "command_timeout_seconds": 1800,
            "issue_timeout_seconds": 60,
            "min_free_disk_gb": 10,
            "guard": {"trusted_ref": "origin/main"},
            "defects4j": {
                "image": "autobugfix/defects4j:3.0.1",
                "verifier_image": "autobugfix/defects4j-verifier:3.0.1",
                "platform": "linux/amd64",
                "framework_revision": "6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09",
                "timezone": "America/Los_Angeles",
                "preflight_repetitions": 2,
                "memory_limit": "8g",
                "cpu_limit": 4.0,
                "pids_limit": 1024,
            },
        },
    },
    "operator": {
        "state": {
            "root": ".autobugfix/operator-v3",
            "database_name": "governance.sqlite3",
            "lease_timeout_seconds": 7200,
        },
        "artifacts": {"root": ".autobugfix/operator-artifacts"},
        "worktrees": {
            "root": ".autobugfix/operator-worktrees",
            "branch_template": "operator/experiment/{request_id}",
        },
        "retry": {
            "max_attempts": 5,
            "max_auto_retries": 2,
            "auto_retry_deterministic_failures": True,
        },
        "verification": {
            "fast_profiles": ["operator"],
            "full_profiles": ["full"],
            "require_semantic_verifier": True,
            "process_sandbox": "auto",
            "require_process_sandbox": True,
            "network_access": False,
            "runtime_venv": ".venv",
        },
        "experiments": {
            "enabled": True,
            "trusted_ref": "origin/main",
            "default_profile": "real-e2e",
            "profiles": {
                "real-e2e": {
                    "timeout_seconds": 3600,
                    "network_access": True,
                    "commands": [
                        {
                            "name": "real-repository-e2e",
                            "argv": [
                                "uv",
                                "run",
                                "--cache-dir",
                                "/tmp/uv-cache",
                                "python",
                                "scripts/real_repository_acceptance.py",
                                "--root",
                                "{shadow_state_root}/real-e2e",
                                "--model",
                                "gpt-5.4-mini",
                            ],
                        }
                    ],
                },
                "toy-fast": {
                    "timeout_seconds": 900,
                    "commands": [
                        {
                            "name": "real-toy-acceptance",
                            "argv": [
                                "uv",
                                "run",
                                "--cache-dir",
                                "/tmp/uv-cache",
                                "python",
                                "scripts/real_toy_acceptance.py",
                            ],
                        }
                    ],
                },
                "local-dataset-e2e": {
                    "timeout_seconds": 7200,
                    "required_values": ["dataset"],
                    "commands": [
                        {
                            "name": "local-dataset-e2e",
                            "argv": [
                                "uv",
                                "run",
                                "--cache-dir",
                                "/tmp/uv-cache",
                                "autobugfix",
                                "eval",
                                "run",
                                "--dataset",
                                "{dataset}",
                                "--out",
                                "{shadow_state_root}/dataset-e2e",
                            ],
                        }
                    ],
                },
            },
        },
        "experiment_lines": {
            "root": ".autobugfix/operator-line-worktrees",
            "checkpoint_root": ".autobugfix/operator-checkpoints",
            "active_release_root": ".autobugfix/operator-active-experiments",
            "branch_template": "experiment/{study_id}-main",
            "remote": "origin",
            "update_timeout_seconds": 300,
        },
        "budgets": {
            "allowed_waves": [3, 8, 16],
            "allowed_primary_models": ["gpt-5.4-mini"],
            "max_calls_by_wave": {3: 30, 8: 80, 16: 160},
            "default_case_concurrency": 1,
            "max_case_concurrency": 1,
            "default_max_writer_attempts": 2,
            "default_max_operator_revisions": 3,
            "default_wall_time_seconds": 7200,
            "allow_model_fallback": False,
        },
        "promotion": {
            "release_root": ".autobugfix/releases",
            "active_release_link": ".autobugfix/active-release",
            "require_pull_request": True,
            "require_canary": True,
            "auto_rollback_on_canary_failure": True,
            "canary_profiles": ["full"],
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
    if not role_runtime.enabled:
        raise ConfigError(
            "codex.role_runtime.enabled must remain true so production SDK roles use an isolated CODEX_HOME"
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
        reasoning_effort=str(codex_raw.get("reasoning_effort", "medium")),
        service_tier=(
            str(codex_raw["service_tier"])
            if codex_raw.get("service_tier") is not None
            else None
        ),
        disable_response_storage=bool(
            codex_raw.get("disable_response_storage", True)
        ),
        writer_model=codex_raw.get("writer_model"),
        evaluator_model=codex_raw.get("evaluator_model"),
        controller_model=codex_raw.get("controller_model"),
        role_runtime=role_runtime,
        roles=roles,
    )
    if codex.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ConfigError("codex.reasoning_effort must be low, medium, high, or xhigh")
    if not codex.disable_response_storage:
        raise ConfigError("codex.disable_response_storage must remain true")

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
    benchmark_raw = _as_mapping(eval_raw.get("benchmarks"), "eval.benchmarks")
    guard_raw = _as_mapping(benchmark_raw.get("guard"), "eval.benchmarks.guard")
    defects4j_raw = _as_mapping(
        benchmark_raw.get("defects4j"), "eval.benchmarks.defects4j"
    )
    unsupported_defects4j_fields = sorted(
        set(defects4j_raw)
        & {
            "framework_root",
            "java_home",
            "git_bin",
            "svn_bin",
            "perl_bin",
            "cpanm_bin",
            "perl5lib",
            "library_path",
        }
    )
    if unsupported_defects4j_fields:
        raise ConfigError(
            "eval.benchmarks.defects4j host runtime fields are unsupported in "
            "Docker-only mode: "
            + ", ".join(unsupported_defects4j_fields)
        )
    eval_config = EvalConfig(
        model_mode=str(eval_raw.get("model_mode", "codex")),
        roles=_parse_roles(eval_raw.get("roles"), "eval.roles"),
        benchmarks=EvalBenchmarkConfig(
            cache_root=_resolve(root, benchmark_raw.get("cache_root"))
            or (root / ".autobugfix/benchmark-cache"),
            trusted_case_root=_resolve(root, benchmark_raw.get("trusted_case_root"))
            or (root / ".autobugfix/trusted-eval-cases"),
            visible_manifest_root=_resolve(
                root, benchmark_raw.get("visible_manifest_root")
            )
            or (root / ".autobugfix/eval-manifests"),
            command_timeout_seconds=int(
                benchmark_raw.get("command_timeout_seconds", 1800)
            ),
            issue_timeout_seconds=int(benchmark_raw.get("issue_timeout_seconds", 60)),
            min_free_disk_gb=int(benchmark_raw.get("min_free_disk_gb", 10)),
            guard=EvalGuardConfig(
                trusted_ref=str(guard_raw.get("trusted_ref", "origin/main")),
            ),
            defects4j=Defects4JBenchmarkConfig(
                image=str(defects4j_raw.get("image", DEFECTS4J_DOCKER_IMAGE)),
                verifier_image=str(
                    defects4j_raw.get("verifier_image", DEFECTS4J_VERIFIER_IMAGE)
                ),
                platform=str(
                    defects4j_raw.get("platform", DEFECTS4J_DOCKER_PLATFORM)
                ),
                framework_revision=str(
                    defects4j_raw.get(
                        "framework_revision", DEFECTS4J_FRAMEWORK_REVISION
                    )
                ),
                timezone=str(
                    defects4j_raw.get("timezone", "America/Los_Angeles")
                ),
                preflight_repetitions=int(
                    defects4j_raw.get("preflight_repetitions", 2)
                ),
                memory_limit=str(defects4j_raw.get("memory_limit", "8g")),
                cpu_limit=float(defects4j_raw.get("cpu_limit", 4.0)),
                pids_limit=int(defects4j_raw.get("pids_limit", 1024)),
            ),
        ),
    )

    operator_raw = _as_mapping(merged.get("operator"), "operator")
    state_raw = _as_mapping(operator_raw.get("state"), "operator.state")
    artifact_raw = _as_mapping(operator_raw.get("artifacts"), "operator.artifacts")
    worktree_raw = _as_mapping(operator_raw.get("worktrees"), "operator.worktrees")
    retry_raw = _as_mapping(operator_raw.get("retry"), "operator.retry")
    verification_raw = _as_mapping(operator_raw.get("verification"), "operator.verification")
    experiment_raw = _as_mapping(operator_raw.get("experiments"), "operator.experiments")
    experiment_line_raw = _as_mapping(
        operator_raw.get("experiment_lines"), "operator.experiment_lines"
    )
    budget_raw = _as_mapping(operator_raw.get("budgets"), "operator.budgets")
    promotion_raw = _as_mapping(operator_raw.get("promotion"), "operator.promotion")

    def _string_tuple(raw_value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(raw_value, list):
            raise ConfigError(f"{field} must be a list")
        return tuple(str(item) for item in raw_value)

    def _integer_tuple(raw_value: Any, field: str) -> tuple[int, ...]:
        if not isinstance(raw_value, list):
            raise ConfigError(f"{field} must be a list")
        try:
            return tuple(int(item) for item in raw_value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{field} must contain integers") from exc

    max_calls_raw = _as_mapping(
        budget_raw.get("max_calls_by_wave"), "operator.budgets.max_calls_by_wave"
    )

    try:
        max_calls = {int(wave): int(limit) for wave, limit in max_calls_raw.items()}
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "operator.budgets.max_calls_by_wave must map integer waves to integer limits"
        ) from exc

    operator_config = OperatorConfig(
        state=OperatorStateConfig(
            root=_resolve(root, state_raw.get("root")) or (root / ".autobugfix/operator-v3"),
            database_name=str(state_raw.get("database_name", "governance.sqlite3")),
            lease_timeout_seconds=int(state_raw.get("lease_timeout_seconds", 7200)),
        ),
        artifacts=OperatorArtifactConfig(
            root=_resolve(root, artifact_raw.get("root")) or (root / ".autobugfix/operator-artifacts"),
        ),
        worktrees=OperatorWorktreeConfig(
            root=_resolve(root, worktree_raw.get("root")) or (root / ".autobugfix/operator-worktrees"),
            branch_template=str(worktree_raw.get("branch_template", "operator/experiment/{request_id}")),
        ),
        retry=OperatorRetryConfig(
            max_attempts=int(retry_raw.get("max_attempts", 5)),
            max_auto_retries=int(retry_raw.get("max_auto_retries", 2)),
            auto_retry_deterministic_failures=bool(
                retry_raw.get("auto_retry_deterministic_failures", True)
            ),
        ),
        verification=OperatorVerificationConfig(
            fast_profiles=_string_tuple(
                verification_raw.get("fast_profiles", ["operator"]),
                "operator.verification.fast_profiles",
            ),
            full_profiles=_string_tuple(
                verification_raw.get("full_profiles", ["full"]),
                "operator.verification.full_profiles",
            ),
            require_semantic_verifier=bool(verification_raw.get("require_semantic_verifier", True)),
            process_sandbox=str(verification_raw.get("process_sandbox", "auto")),
            require_process_sandbox=bool(verification_raw.get("require_process_sandbox", True)),
            network_access=bool(verification_raw.get("network_access", False)),
            runtime_venv=_resolve(root, verification_raw.get("runtime_venv")),
        ),
        experiments=OperatorExperimentConfig(
            enabled=bool(experiment_raw.get("enabled", True)),
            trusted_ref=str(experiment_raw.get("trusted_ref", "origin/main")),
            default_profile=str(experiment_raw.get("default_profile", "real-e2e")),
            profiles={
                str(name): _as_mapping(value, f"operator.experiments.profiles.{name}")
                for name, value in _as_mapping(
                    experiment_raw.get("profiles"), "operator.experiments.profiles"
                ).items()
            },
        ),
        experiment_lines=OperatorExperimentLineConfig(
            root=_resolve(root, experiment_line_raw.get("root"))
            or (root / ".autobugfix/operator-line-worktrees"),
            checkpoint_root=_resolve(root, experiment_line_raw.get("checkpoint_root"))
            or (root / ".autobugfix/operator-checkpoints"),
            active_release_root=_resolve(root, experiment_line_raw.get("active_release_root"))
            or (root / ".autobugfix/operator-active-experiments"),
            branch_template=str(
                experiment_line_raw.get("branch_template", "experiment/{study_id}-main")
            ),
            remote=(
                str(experiment_line_raw["remote"])
                if experiment_line_raw.get("remote") is not None
                else None
            ),
            update_timeout_seconds=int(
                experiment_line_raw.get("update_timeout_seconds", 300)
            ),
        ),
        budgets=OperatorBudgetConfig(
            allowed_waves=_integer_tuple(
                budget_raw.get("allowed_waves", [3, 8, 16]),
                "operator.budgets.allowed_waves",
            ),
            allowed_primary_models=_string_tuple(
                budget_raw.get("allowed_primary_models", ["gpt-5.4-mini"]),
                "operator.budgets.allowed_primary_models",
            ),
            max_calls_by_wave=max_calls,
            default_case_concurrency=int(budget_raw.get("default_case_concurrency", 1)),
            max_case_concurrency=int(budget_raw.get("max_case_concurrency", 1)),
            default_max_writer_attempts=int(
                budget_raw.get("default_max_writer_attempts", 2)
            ),
            default_max_operator_revisions=int(
                budget_raw.get("default_max_operator_revisions", 3)
            ),
            default_wall_time_seconds=int(budget_raw.get("default_wall_time_seconds", 7200)),
            allow_model_fallback=bool(budget_raw.get("allow_model_fallback", False)),
        ),
        promotion=OperatorPromotionConfig(
            release_root=_resolve(root, promotion_raw.get("release_root")) or (root / ".autobugfix/releases"),
            active_release_link=_resolve(root, promotion_raw.get("active_release_link"))
            or (root / ".autobugfix/active-release"),
            require_pull_request=bool(promotion_raw.get("require_pull_request", True)),
            require_canary=bool(promotion_raw.get("require_canary", True)),
            auto_rollback_on_canary_failure=bool(
                promotion_raw.get("auto_rollback_on_canary_failure", True)
            ),
            canary_profiles=_string_tuple(
                promotion_raw.get("canary_profiles", ["full"]),
                "operator.promotion.canary_profiles",
            ),
        ),
    )
    if operator_config.retry.max_attempts < 1:
        raise ConfigError("operator.retry.max_attempts must be positive")
    if operator_config.state.lease_timeout_seconds < 1:
        raise ConfigError("operator.state.lease_timeout_seconds must be positive")
    if operator_config.retry.max_auto_retries < 0:
        raise ConfigError("operator.retry.max_auto_retries must not be negative")
    if operator_config.retry.max_auto_retries >= operator_config.retry.max_attempts:
        raise ConfigError("operator.retry.max_auto_retries must be lower than max_attempts")
    if "{request_id}" not in operator_config.worktrees.branch_template:
        raise ConfigError("operator.worktrees.branch_template must contain {request_id}")
    if operator_config.verification.process_sandbox not in {"auto", "bubblewrap", "none"}:
        raise ConfigError("operator.verification.process_sandbox must be auto, bubblewrap, or none")
    if (
        operator_config.verification.require_process_sandbox
        and operator_config.verification.process_sandbox == "none"
    ):
        raise ConfigError("required Operator process sandbox cannot use process_sandbox: none")
    if not operator_config.verification.fast_profiles:
        raise ConfigError("operator.verification.fast_profiles must not be empty")
    if not operator_config.verification.full_profiles:
        raise ConfigError("operator.verification.full_profiles must not be empty")
    if operator_config.experiments.enabled:
        if operator_config.experiments.default_profile not in operator_config.experiments.profiles:
            raise ConfigError("operator.experiments.default_profile is not defined in profiles")
        for name, profile in operator_config.experiments.profiles.items():
            commands = profile.get("commands")
            if not isinstance(commands, list) or not commands:
                raise ConfigError(f"operator.experiments.profiles.{name}.commands must be a non-empty list")
            for index, command in enumerate(commands):
                if not isinstance(command, dict) or not isinstance(command.get("argv"), list):
                    raise ConfigError(
                        f"operator.experiments.profiles.{name}.commands[{index}].argv must be a list"
                    )
    if "{study_id}" not in operator_config.experiment_lines.branch_template:
        raise ConfigError("operator.experiment_lines.branch_template must contain {study_id}")
    try:
        rendered_line_branch = operator_config.experiment_lines.branch_template.format(
            study_id="study"
        )
    except (KeyError, ValueError) as exc:
        raise ConfigError("operator.experiment_lines.branch_template is invalid") from exc
    if rendered_line_branch in {"main", "master"}:
        raise ConfigError("operator experiment line cannot target a protected branch")
    if operator_config.experiment_lines.remote is not None and not operator_config.experiment_lines.remote.strip():
        raise ConfigError("operator.experiment_lines.remote must be null or non-empty")
    if operator_config.experiment_lines.update_timeout_seconds < 1:
        raise ConfigError("operator.experiment_lines.update_timeout_seconds must be positive")
    if operator_config.budgets.allowed_waves != (3, 8, 16):
        raise ConfigError("operator.budgets.allowed_waves must be exactly [3, 8, 16]")
    if operator_config.budgets.allowed_primary_models != ("gpt-5.4-mini",):
        raise ConfigError(
            "operator.budgets.allowed_primary_models must be exactly [gpt-5.4-mini]"
        )
    if set(operator_config.budgets.max_calls_by_wave) != {3, 8, 16} or any(
        limit < 1 for limit in operator_config.budgets.max_calls_by_wave.values()
    ):
        raise ConfigError(
            "operator.budgets.max_calls_by_wave must define positive limits for 3, 8, and 16"
        )
    if operator_config.budgets.default_case_concurrency != 1:
        raise ConfigError("operator.budgets.default_case_concurrency must remain 1")
    if operator_config.budgets.max_case_concurrency != 1:
        raise ConfigError("operator.budgets.max_case_concurrency must remain 1")
    for value, field in (
        (operator_config.budgets.default_max_writer_attempts, "default_max_writer_attempts"),
        (operator_config.budgets.default_max_operator_revisions, "default_max_operator_revisions"),
        (operator_config.budgets.default_wall_time_seconds, "default_wall_time_seconds"),
    ):
        if value < 1:
            raise ConfigError(f"operator.budgets.{field} must be positive")
    if operator_config.budgets.allow_model_fallback:
        raise ConfigError("operator.budgets.allow_model_fallback must remain false")
    if operator_config.promotion.require_canary and not operator_config.promotion.canary_profiles:
        raise ConfigError("operator.promotion.canary_profiles must not be empty when canary is required")
    benchmark_config = eval_config.benchmarks
    for value, field in (
        (benchmark_config.command_timeout_seconds, "command_timeout_seconds"),
        (benchmark_config.issue_timeout_seconds, "issue_timeout_seconds"),
        (benchmark_config.min_free_disk_gb, "min_free_disk_gb"),
        (
            benchmark_config.defects4j.preflight_repetitions,
            "defects4j.preflight_repetitions",
        ),
    ):
        if value < 1:
            raise ConfigError(f"eval.benchmarks.{field} must be positive")
    if benchmark_config.defects4j.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
        raise ConfigError(
            "eval.benchmarks.defects4j.framework_revision must remain pinned to "
            f"{DEFECTS4J_FRAMEWORK_REVISION}"
        )
    if benchmark_config.defects4j.timezone != "America/Los_Angeles":
        raise ConfigError(
            "eval.benchmarks.defects4j.timezone must remain America/Los_Angeles"
        )
    if not benchmark_config.defects4j.image.strip():
        raise ConfigError("eval.benchmarks.defects4j.image must not be empty")
    if not benchmark_config.guard.trusted_ref.strip():
        raise ConfigError("eval.benchmarks.guard.trusted_ref must not be empty")
    if not benchmark_config.defects4j.verifier_image.strip():
        raise ConfigError(
            "eval.benchmarks.defects4j.verifier_image must not be empty"
        )
    if not benchmark_config.defects4j.memory_limit.strip():
        raise ConfigError("eval.benchmarks.defects4j.memory_limit must not be empty")
    if benchmark_config.defects4j.cpu_limit <= 0:
        raise ConfigError("eval.benchmarks.defects4j.cpu_limit must be positive")
    if benchmark_config.defects4j.pids_limit < 64:
        raise ConfigError(
            "eval.benchmarks.defects4j.pids_limit must be at least 64"
        )
    if benchmark_config.defects4j.platform != DEFECTS4J_DOCKER_PLATFORM:
        raise ConfigError(
            "eval.benchmarks.defects4j.platform must remain "
            f"{DEFECTS4J_DOCKER_PLATFORM}"
        )
    runtime_roots = {
        "state": operator_config.state.root.resolve(),
        "artifacts": operator_config.artifacts.root.resolve(),
        "candidate_worktrees": operator_config.worktrees.root.resolve(),
        "integration_worktrees": operator_config.experiment_lines.root.resolve(),
        "checkpoints": operator_config.experiment_lines.checkpoint_root.resolve(),
        "active_releases": operator_config.experiment_lines.active_release_root.resolve(),
    }
    for left_name, left_root in runtime_roots.items():
        for right_name, right_root in runtime_roots.items():
            if left_name >= right_name:
                continue
            if _paths_overlap(left_root, right_root):
                raise ConfigError(
                    "operator runtime roots must not overlap: "
                    f"{left_name}={left_root}, {right_name}={right_root}"
                )
    benchmark_roots = {
        "benchmark_cache": benchmark_config.cache_root.resolve(),
        "trusted_cases": benchmark_config.trusted_case_root.resolve(),
        "visible_manifests": benchmark_config.visible_manifest_root.resolve(),
    }
    memory_root = (root / ".autobugfix-memory").resolve()
    for left_name, left_root in benchmark_roots.items():
        for right_name, right_root in benchmark_roots.items():
            if left_name >= right_name:
                continue
            if _paths_overlap(left_root, right_root):
                raise ConfigError(
                    "Eval benchmark roots must not overlap: "
                    f"{left_name}={left_root}, {right_name}={right_root}"
                )
        for operator_name, operator_root in runtime_roots.items():
            if _paths_overlap(left_root, operator_root):
                raise ConfigError(
                    "Eval benchmark roots must not overlap Operator runtime roots: "
                    f"{left_name}={left_root}, {operator_name}={operator_root}"
                )
        if _paths_overlap(left_root, memory_root):
            raise ConfigError(
                "Eval benchmark roots must not overlap Memory runtime root: "
                f"{left_name}={left_root}, memory={memory_root}"
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

    project_runtime_root = (root / ".autobugfix").resolve()
    configured_task_root = Path(str(merged.get("task_root", ".autobugfix/tasks")))
    protected_data_roots = {
        "project_git": (root / ".git").resolve(),
        "project_source": (root / "src").resolve(),
        "project_tests": (root / "tests").resolve(),
        "execution_tasks": (
            (root / configured_task_root)
            if not configured_task_root.is_absolute()
            else configured_task_root
        ).resolve(),
        "execution_archive": (root / ".autobugfix/archive").resolve(),
        "codex_runtime": (
            (root / role_runtime.runtime_root)
            if not role_runtime.runtime_root.is_absolute()
            else role_runtime.runtime_root
        ).resolve(),
    }
    for repo_id, repo in repos.items():
        protected_data_roots[f"repo.{repo_id}.main_checkout"] = repo.main_checkout.resolve()
        if repo.worktree_root is not None:
            protected_data_roots[f"repo.{repo_id}.worktrees"] = repo.worktree_root.resolve()
    for benchmark_name, benchmark_root in benchmark_roots.items():
        if benchmark_root.is_relative_to(root) and not benchmark_root.is_relative_to(
            project_runtime_root
        ):
            raise ConfigError(
                "Eval benchmark roots inside the project must live under the gitignored "
                f".autobugfix runtime root: {benchmark_name}={benchmark_root}"
            )
        for protected_name, protected_root in protected_data_roots.items():
            if _paths_overlap(benchmark_root, protected_root):
                raise ConfigError(
                    "Eval benchmark root overlaps protected data plane: "
                    f"{benchmark_name}={benchmark_root}, "
                    f"{protected_name}={protected_root}"
                )

    return AutobugfixConfig(
        project_root=root,
        task_root=Path(str(merged.get("task_root", ".autobugfix/tasks"))),
        scheduler=scheduler,
        codex=codex,
        worker=worker,
        memory_worker=memory_worker,
        eval=eval_config,
        operator=operator_config,
        repos=repos,
    )


def write_default_config(project_root: Path | str = ".") -> Path:
    root = Path(project_root).resolve()
    path = root / ".autobugfix/config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(yaml.safe_dump(default_config_dict(), sort_keys=False), encoding="utf-8")
    return path
