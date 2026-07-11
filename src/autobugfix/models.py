from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

TaskState = Literal[
    "new",
    "ready",
    "writing",
    "verifying",
    "evaluating",
    "writer_rework_required",
    "waiting_human_review",
    "waiting_human_ppe_approval",
    "ppe_approved",
    "ppe_deployed",
    "waiting_human_acceptance",
    "feedback_available",
    "accepted",
    "abandoned",
    "paused",
    "archived",
    "blocked",
]

RUNNABLE_STATES: set[str] = {"ready", "feedback_available", "writer_rework_required"}
TERMINAL_STATES: set[str] = {"accepted", "abandoned", "archived"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class TestCommands:
    targeted: str = "uv run pytest --no-cov {target}"
    full: str = "uv run pytest"


@dataclass(slots=True)
class PpeConfig:
    enabled: bool = True
    command_template: str | None = None


@dataclass(slots=True)
class RoleConfig:
    backend: str | None = None
    model: str | None = None
    sandbox: str | None = None
    approval_mode: str | None = None
    timeout_seconds: int | None = None
    skill_paths: tuple[str, ...] | None = None
    raw_log_template: str | None = None
    stderr_log_template: str | None = None
    allow_repo_overrides: bool | None = None


@dataclass(slots=True)
class ResolvedRoleConfig:
    role: str
    backend: str
    model: str | None
    sandbox: str
    approval_mode: str
    timeout_seconds: int
    skill_paths: tuple[Path, ...]
    raw_log_template: str
    stderr_log_template: str
    allow_repo_overrides: bool
    source: dict[str, str] = field(default_factory=dict)

    def to_dict(self, project_root: Path | None = None) -> dict[str, Any]:
        def display_path(path: Path) -> str:
            if project_root is not None:
                try:
                    return path.resolve().relative_to(project_root.resolve()).as_posix()
                except ValueError:
                    pass
            return str(path)

        return {
            "role": self.role,
            "backend": self.backend,
            "model": self.model,
            "sandbox": self.sandbox,
            "approval_mode": self.approval_mode,
            "timeout_seconds": self.timeout_seconds,
            "skill_paths": [display_path(path) for path in self.skill_paths],
            "raw_log_template": self.raw_log_template,
            "stderr_log_template": self.stderr_log_template,
            "allow_repo_overrides": self.allow_repo_overrides,
            "source": self.source,
        }


@dataclass(slots=True)
class RepoProfile:
    repo_id: str
    main_checkout: Path
    remote: str = "origin"
    main_branch: str = "main"
    worktree_root: Path | None = None
    branch_template: str = "fix/{date}_oncall_{slug}"
    test_commands: TestCommands = field(default_factory=TestCommands)
    ppe: PpeConfig = field(default_factory=PpeConfig)
    codex_roles: dict[str, RoleConfig] = field(default_factory=dict)


@dataclass(slots=True)
class SchedulerConfig:
    default_max_concurrent: int = 2
    lock_timeout_seconds: int = 7200
    max_auto_iterations: int = 3
    codex_timeout_seconds: int = 1800
    writer_timeout_seconds: int | None = None
    evaluator_timeout_seconds: int | None = None


@dataclass(slots=True)
class RoleRuntimeConfig:
    enabled: bool = True
    runtime_root: Path = Path(".autobugfix/runtime/codex-sdk")
    codex_bin: Path | None = None
    bridge_auth: bool = True
    skill_guard: bool = True
    strict_skill_guard: bool = True


@dataclass(slots=True)
class CodexConfig:
    default_model: str | None = None
    default_timeout_seconds: int | None = None
    writer_model: str | None = None
    evaluator_model: str | None = None
    controller_model: str | None = None
    role_runtime: RoleRuntimeConfig = field(default_factory=RoleRuntimeConfig)
    roles: dict[str, RoleConfig] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerConfig:
    tick_interval_seconds: int = 5
    heartbeat_interval_seconds: int = 5


@dataclass(slots=True)
class EvalConfig:
    model_mode: str = "codex"
    roles: dict[str, RoleConfig] = field(default_factory=dict)


@dataclass(slots=True)
class OperatorStateConfig:
    root: Path = Path(".autobugfix/operator-v3")
    database_name: str = "governance.sqlite3"
    lease_timeout_seconds: int = 7200


@dataclass(slots=True)
class OperatorArtifactConfig:
    root: Path = Path(".autobugfix/operator-artifacts")


@dataclass(slots=True)
class OperatorWorktreeConfig:
    root: Path = Path(".autobugfix/operator-worktrees")
    branch_template: str = "operator/experiment/{request_id}"


@dataclass(slots=True)
class OperatorRetryConfig:
    max_attempts: int = 5
    max_auto_retries: int = 2
    auto_retry_deterministic_failures: bool = True


@dataclass(slots=True)
class OperatorVerificationConfig:
    fast_profiles: tuple[str, ...] = ("operator",)
    full_profiles: tuple[str, ...] = ("full",)
    require_semantic_verifier: bool = True
    process_sandbox: str = "auto"
    require_process_sandbox: bool = True
    network_access: bool = False
    runtime_venv: Path | None = None


@dataclass(slots=True)
class OperatorExperimentConfig:
    enabled: bool = True
    trusted_ref: str = "origin/main"
    default_profile: str = "real-e2e"
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class OperatorPromotionConfig:
    release_root: Path = Path(".autobugfix/releases")
    active_release_link: Path = Path(".autobugfix/active-release")
    require_pull_request: bool = True
    require_canary: bool = True
    auto_rollback_on_canary_failure: bool = True
    canary_profiles: tuple[str, ...] = ("full",)


@dataclass(slots=True)
class OperatorConfig:
    state: OperatorStateConfig = field(default_factory=OperatorStateConfig)
    artifacts: OperatorArtifactConfig = field(default_factory=OperatorArtifactConfig)
    worktrees: OperatorWorktreeConfig = field(default_factory=OperatorWorktreeConfig)
    retry: OperatorRetryConfig = field(default_factory=OperatorRetryConfig)
    verification: OperatorVerificationConfig = field(default_factory=OperatorVerificationConfig)
    experiments: OperatorExperimentConfig = field(default_factory=OperatorExperimentConfig)
    promotion: OperatorPromotionConfig = field(default_factory=OperatorPromotionConfig)


@dataclass(slots=True)
class AutobugfixConfig:
    project_root: Path
    task_root: Path = Path(".autobugfix/tasks")
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    memory_worker: WorkerConfig = field(default_factory=lambda: WorkerConfig(tick_interval_seconds=10, heartbeat_interval_seconds=10))
    eval: EvalConfig = field(default_factory=EvalConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    repos: dict[str, RepoProfile] = field(default_factory=dict)

    @property
    def archive_root(self) -> Path:
        return self.project_root / ".autobugfix/archive"

    def repo(self, repo_id: str) -> RepoProfile:
        try:
            return self.repos[repo_id]
        except KeyError as exc:
            raise KeyError(f"unknown repo id: {repo_id}") from exc


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    repo_id: str
    title: str
    body: str
    state: TaskState = "new"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    branch: str | None = None
    worktree_path: str | None = None
    main_checkout: str | None = None
    block_reason: str = ""
    iterations: int = 0
    archived_result: str | None = None
    archived_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo_id": self.repo_id,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "main_checkout": self.main_checkout,
            "block_reason": self.block_reason,
            "iterations": self.iterations,
            "archived_result": self.archived_result,
            "archived_path": self.archived_path,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRecord":
        return cls(
            task_id=str(data["task_id"]),
            repo_id=str(data["repo_id"]),
            title=str(data["title"]),
            body=str(data.get("body", "")),
            state=str(data.get("state", "new")),  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or utc_now()),
            branch=data.get("branch"),
            worktree_path=data.get("worktree_path"),
            main_checkout=data.get("main_checkout"),
            block_reason=str(data.get("block_reason", "")),
            iterations=int(data.get("iterations", 0)),
            archived_result=data.get("archived_result"),
            archived_path=data.get("archived_path"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class Event:
    seq: int
    timestamp: str
    kind: str
    payload: dict[str, Any]


@dataclass(slots=True)
class CodexRequest:
    role: str
    prompt: str
    cwd: Path
    sandbox: str
    model: str | None
    timeout_seconds: int | None
    developer_instructions: str
    raw_log_path: Path
    stderr_log_path: Path
    approval_mode: str | None = None


@dataclass(slots=True)
class CodexResult:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0


@dataclass(slots=True)
class VerifierResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0
