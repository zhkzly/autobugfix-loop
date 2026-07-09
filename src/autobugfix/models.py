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
class RepoProfile:
    repo_id: str
    main_checkout: Path
    remote: str = "origin"
    main_branch: str = "main"
    worktree_root: Path | None = None
    branch_template: str = "fix/{date}_oncall_{slug}"
    test_commands: TestCommands = field(default_factory=TestCommands)
    ppe: PpeConfig = field(default_factory=PpeConfig)


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
    bridge_auth: bool = True
    skill_guard: bool = True
    strict_skill_guard: bool = True


@dataclass(slots=True)
class CodexConfig:
    writer_model: str | None = None
    evaluator_model: str | None = None
    controller_model: str | None = None
    role_runtime: RoleRuntimeConfig = field(default_factory=RoleRuntimeConfig)


@dataclass(slots=True)
class AutobugfixConfig:
    project_root: Path
    task_root: Path = Path(".autobugfix/tasks")
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
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
