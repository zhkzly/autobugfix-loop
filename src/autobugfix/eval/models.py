from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


class EvalCaseError(ValueError):
    pass


TaskType = Literal["bugfix", "feature", "maintenance", "unknown"]
OracleStatus = Literal["passed", "failed", "error"]
OracleVisibility = Literal["hidden", "diagnostic", "public"]
ExperimentRole = Literal["optimization", "sealed_holdout"]


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EvalCaseError(f"{name} must not be empty")
    return text


def _case_id(value: object) -> str:
    text = _required(value, "case_id")
    if text in {".", ".."} or Path(text).name != text or "/" in text or "\\" in text:
        raise EvalCaseError(f"case_id must be a safe directory name: {text!r}")
    return text


@dataclass(slots=True, frozen=True)
class EvalCaseSource:
    adapter: str
    benchmark: str
    revision: str
    split: str
    instance_id: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, case_id: str) -> "EvalCaseSource":
        return cls(
            adapter=_required(data.get("adapter") or "local-git", "source.adapter"),
            benchmark=_required(data.get("benchmark") or "autobugfix-local", "source.benchmark"),
            revision=_required(data.get("revision") or "local", "source.revision"),
            split=_required(data.get("split") or "local", "source.split"),
            instance_id=_required(data.get("instance_id") or case_id, "source.instance_id"),
        )


@dataclass(slots=True, frozen=True)
class EvalBenchmarkSpec:
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    eligibility_receipt_digest: str
    visible_evidence_digest: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalBenchmarkSpec":
        return cls(
            framework_revision=_required(
                data.get("framework_revision"), "benchmark.framework_revision"
            ),
            dataset_revision=_required(
                data.get("dataset_revision"), "benchmark.dataset_revision"
            ),
            runtime_id=_required(data.get("runtime_id"), "benchmark.runtime_id"),
            eligibility_receipt_digest=_required(
                data.get("eligibility_receipt_digest"),
                "benchmark.eligibility_receipt_digest",
            ),
            visible_evidence_digest=_required(
                data.get("visible_evidence_digest"),
                "benchmark.visible_evidence_digest",
            ),
        )


@dataclass(slots=True, frozen=True)
class EvalExperimentSpec:
    role: ExperimentRole
    first_wave: int
    repository_group: str
    case_token: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalExperimentSpec":
        role = str(data.get("role") or "")
        if role not in {"optimization", "sealed_holdout"}:
            raise EvalCaseError(f"unsupported experiment role: {role!r}")
        first_wave = int(data.get("first_wave") or 0)
        if first_wave not in {3, 8, 16}:
            raise EvalCaseError("experiment.first_wave must be 3, 8, or 16")
        return cls(
            role=role,  # type: ignore[arg-type]
            first_wave=first_wave,
            repository_group=_required(
                data.get("repository_group"), "experiment.repository_group"
            ),
            case_token=_required(data.get("case_token"), "experiment.case_token"),
        )


@dataclass(slots=True, frozen=True)
class EvalAttachment:
    kind: str
    uri: str
    description: str = ""
    media_type: str | None = None
    sha256: str | None = None

    @classmethod
    def from_value(cls, value: object) -> "EvalAttachment":
        if isinstance(value, str):
            return cls(kind="file", uri=_required(value, "attachment.uri"))
        if not isinstance(value, Mapping):
            raise EvalCaseError("attachment must be a string or mapping")
        return cls(
            kind=_required(value.get("kind") or "file", "attachment.kind"),
            uri=_required(value.get("uri") or value.get("path") or value.get("url"), "attachment.uri"),
            description=str(value.get("description") or ""),
            media_type=str(value["media_type"]) if value.get("media_type") else None,
            sha256=str(value["sha256"]) if value.get("sha256") else None,
        )


@dataclass(slots=True, frozen=True)
class EvalTaskSpec:
    task_type: TaskType
    problem_statement: str
    agent_prompt: str
    expected_behavior: str = ""
    attachments: tuple[EvalAttachment, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalTaskSpec":
        problem = _required(
            data.get("problem_statement") or data.get("agent_prompt"),
            "task.problem_statement",
        )
        task_type = str(data.get("type") or data.get("task_type") or "unknown")
        if task_type not in {"bugfix", "feature", "maintenance", "unknown"}:
            raise EvalCaseError(f"unsupported task type: {task_type!r}")
        attachments = data.get("attachments") or []
        if not isinstance(attachments, (list, tuple)):
            raise EvalCaseError("task.attachments must be a list")
        return cls(
            task_type=task_type,  # type: ignore[arg-type]
            problem_statement=problem,
            agent_prompt=str(data.get("agent_prompt") or problem),
            expected_behavior=str(data.get("expected_behavior") or ""),
            attachments=tuple(
                EvalAttachment.from_value(item) for item in attachments
            ),
        )


@dataclass(slots=True, frozen=True)
class EvalRepositorySpec:
    repo_id: str
    base_commit: str
    worktree_path: Path | None = None
    url: str | None = None
    reference_commit: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalRepositorySpec":
        reference = data.get("reference_commit") or data.get("final_commit")
        worktree = data.get("worktree_path")
        url = data.get("url") or data.get("repo_url")
        if not worktree and not url:
            raise EvalCaseError("repository requires worktree_path or url")
        return cls(
            repo_id=_required(data.get("repo_id") or data.get("repo"), "repository.repo_id"),
            base_commit=_required(data.get("base_commit"), "repository.base_commit"),
            worktree_path=Path(str(worktree)).expanduser().resolve() if worktree else None,
            url=str(url) if url else None,
            reference_commit=str(reference) if reference else None,
        )


@dataclass(slots=True, frozen=True)
class EvalExecutionSpec:
    test_command: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalExecutionSpec":
        command = data.get("test_command")
        return cls(test_command=str(command) if command else None)


@dataclass(slots=True, frozen=True)
class EvalEnvironmentSpec:
    image: str | None = None
    platform: str | None = None
    setup_commands: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalEnvironmentSpec":
        commands = data.get("setup_commands") or []
        if not isinstance(commands, (list, tuple)):
            raise EvalCaseError("environment.setup_commands must be a list")
        return cls(
            image=str(data["image"]) if data.get("image") else None,
            platform=str(data["platform"]) if data.get("platform") else None,
            setup_commands=tuple(_required(item, "environment.setup_commands") for item in commands),
        )


@dataclass(slots=True, frozen=True)
class EvalOracleSpec:
    oracle_type: str
    command: str | None = None
    require_patch: bool = True
    timeout_seconds: int | None = None
    visibility: OracleVisibility = "hidden"
    patch_source: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalOracleSpec":
        timeout = data.get("timeout_seconds")
        visibility = str(data.get("visibility") or "hidden")
        if visibility not in {"hidden", "diagnostic", "public"}:
            raise EvalCaseError(f"unsupported oracle visibility: {visibility!r}")
        if timeout is not None and int(timeout) <= 0:
            raise EvalCaseError("oracle.timeout_seconds must be positive")
        return cls(
            oracle_type=_required(data.get("type") or "command", "oracle.type"),
            command=str(data["command"]) if data.get("command") else None,
            require_patch=bool(data.get("require_patch", True)),
            timeout_seconds=int(timeout) if timeout is not None else None,
            visibility=visibility,  # type: ignore[arg-type]
            patch_source=str(data["patch_source"]) if data.get("patch_source") else None,
        )


@dataclass(slots=True, frozen=True)
class EvalCase:
    schema_version: int
    case_id: str
    source: EvalCaseSource
    task: EvalTaskSpec
    repository: EvalRepositorySpec
    environment: EvalEnvironmentSpec
    execution: EvalExecutionSpec
    oracle: EvalOracleSpec
    benchmark: EvalBenchmarkSpec | None = None
    experiment: EvalExperimentSpec | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "EvalCase":
        case_id = _case_id(row.get("case_id") or row.get("raw_id") or row.get("id"))
        schema_version = int(row.get("schema_version") or 1)
        if schema_version != 1:
            raise EvalCaseError(f"unsupported Eval case schema version: {schema_version}")

        if isinstance(row.get("source"), Mapping):
            source_data = row["source"]
            task_data = row.get("task")
            repository_data = row.get("repository")
            environment_data = row.get("environment") or {}
            execution_data = row.get("execution") or {}
            oracle_data = row.get("oracle") or {}
            benchmark_data = row.get("benchmark")
            experiment_data = row.get("experiment")
            if not isinstance(task_data, Mapping) or not isinstance(repository_data, Mapping):
                raise EvalCaseError("canonical case requires task and repository mappings")
            if (
                not isinstance(environment_data, Mapping)
                or not isinstance(execution_data, Mapping)
                or not isinstance(oracle_data, Mapping)
            ):
                raise EvalCaseError("environment, execution, and oracle must be mappings")
            if benchmark_data is not None and not isinstance(benchmark_data, Mapping):
                raise EvalCaseError("benchmark must be a mapping")
            if experiment_data is not None and not isinstance(experiment_data, Mapping):
                raise EvalCaseError("experiment must be a mapping")
        else:
            source_data = {
                "adapter": "local-git",
                "benchmark": row.get("benchmark") or "autobugfix-local",
                "revision": row.get("dataset_revision") or "legacy-local-v1",
                "split": row.get("split") or "local",
                "instance_id": case_id,
            }
            task_data = {
                "type": row.get("task_type") or "unknown",
                "problem_statement": row.get("problem_statement") or row.get("agent_prompt"),
                "agent_prompt": row.get("agent_prompt") or row.get("problem_statement"),
                "expected_behavior": row.get("expected_behavior") or "",
                "attachments": row.get("attachments") or [],
            }
            repository_data = {
                "repo_id": row.get("repo"),
                "worktree_path": row.get("worktree_path"),
                "url": row.get("repo_url"),
                "base_commit": row.get("base_commit"),
                "reference_commit": row.get("final_commit"),
            }
            execution_data = {"test_command": row.get("test_command")}
            environment_data = {
                "image": row.get("image"),
                "platform": row.get("platform"),
                "setup_commands": row.get("setup_commands") or [],
            }
            oracle_data = {
                "type": row.get("oracle_type") or "command",
                "command": row.get("oracle_command") or row.get("test_command"),
                "require_patch": row.get("require_patch", True),
                "timeout_seconds": row.get("oracle_timeout_seconds"),
                "visibility": row.get("oracle_visibility") or "hidden",
                "patch_source": row.get("oracle_patch_source"),
            }
            benchmark_data = None
            experiment_data = None

        return cls(
            schema_version=schema_version,
            case_id=case_id,
            source=EvalCaseSource.from_dict(source_data, case_id=case_id),
            task=EvalTaskSpec.from_dict(task_data),
            repository=EvalRepositorySpec.from_dict(repository_data),
            environment=EvalEnvironmentSpec.from_dict(environment_data),
            execution=EvalExecutionSpec.from_dict(execution_data),
            oracle=EvalOracleSpec.from_dict(oracle_data),
            benchmark=(
                EvalBenchmarkSpec.from_dict(benchmark_data)
                if benchmark_data is not None
                else None
            ),
            experiment=(
                EvalExperimentSpec.from_dict(experiment_data)
                if experiment_data is not None
                else None
            ),
            raw=row,
        )

    @property
    def repo(self) -> str:
        return self.repository.repo_id

    @property
    def worktree_path(self) -> Path:
        if self.repository.worktree_path is None:
            raise EvalCaseError(
                f"adapter {self.source.adapter!r} requested a local worktree for remote case {self.case_id!r}"
            )
        return self.repository.worktree_path

    @property
    def base_commit(self) -> str:
        return self.repository.base_commit

    @property
    def final_commit(self) -> str | None:
        return self.repository.reference_commit

    @property
    def problem_statement(self) -> str:
        return self.task.problem_statement

    @property
    def agent_prompt(self) -> str:
        return self.task.agent_prompt

    @property
    def expected_behavior(self) -> str:
        return self.task.expected_behavior

    def resolved_test_command(self, override: str | None = None) -> str | None:
        return override or self.execution.test_command

    def resolved_oracle_command(self, override: str | None = None) -> str | None:
        return self.oracle.command or override or self.execution.test_command

    def to_dict(self) -> dict[str, Any]:
        encoded: dict[str, Any] = {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "source": {
                "adapter": self.source.adapter,
                "benchmark": self.source.benchmark,
                "revision": self.source.revision,
                "split": self.source.split,
                "instance_id": self.source.instance_id,
            },
            "task": {
                "type": self.task.task_type,
                "problem_statement": self.task.problem_statement,
                "agent_prompt": self.task.agent_prompt,
                "expected_behavior": self.task.expected_behavior,
                "attachments": [
                    {
                        "kind": item.kind,
                        "uri": item.uri,
                        "description": item.description,
                        "media_type": item.media_type,
                        "sha256": item.sha256,
                    }
                    for item in self.task.attachments
                ],
            },
            "repository": {
                "repo_id": self.repository.repo_id,
                "worktree_path": (
                    str(self.repository.worktree_path)
                    if self.repository.worktree_path is not None
                    else None
                ),
                "url": self.repository.url,
                "base_commit": self.repository.base_commit,
                "reference_commit": self.repository.reference_commit,
            },
            "environment": {
                "image": self.environment.image,
                "platform": self.environment.platform,
                "setup_commands": list(self.environment.setup_commands),
            },
            "execution": {"test_command": self.execution.test_command},
            "oracle": {
                "type": self.oracle.oracle_type,
                "command": self.oracle.command,
                "require_patch": self.oracle.require_patch,
                "timeout_seconds": self.oracle.timeout_seconds,
                "visibility": self.oracle.visibility,
                "patch_source": self.oracle.patch_source,
            },
        }
        if self.benchmark is not None:
            encoded["benchmark"] = {
                "framework_revision": self.benchmark.framework_revision,
                "dataset_revision": self.benchmark.dataset_revision,
                "runtime_id": self.benchmark.runtime_id,
                "eligibility_receipt_digest": (
                    self.benchmark.eligibility_receipt_digest
                ),
                "visible_evidence_digest": self.benchmark.visible_evidence_digest,
            }
        if self.experiment is not None:
            encoded["experiment"] = {
                "role": self.experiment.role,
                "first_wave": self.experiment.first_wave,
                "repository_group": self.experiment.repository_group,
                "case_token": self.experiment.case_token,
            }
        return encoded


@dataclass(slots=True, frozen=True)
class OracleResult:
    status: OracleStatus
    oracle_type: str
    command: str | None
    exit_code: int | None
    stdout_path: str | None
    stderr_path: str | None
    started_at: str
    finished_at: str
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "oracle_type": self.oracle_type,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass(slots=True, frozen=True)
class EvalObservation:
    case_id: str
    patch_required: bool
    generated_non_empty: bool
    execution_verifier_passed: bool | None
    execution_state: str
    execution_reached_human_gate: bool
    oracle_status: OracleStatus
    oracle_exit_code: int | None
    generated_equals_oracle: bool | None
    harness_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "patch_required": self.patch_required,
            "generated_non_empty": self.generated_non_empty,
            "execution_verifier_passed": self.execution_verifier_passed,
            "execution_state": self.execution_state,
            "execution_reached_human_gate": self.execution_reached_human_gate,
            "oracle_status": self.oracle_status,
            "oracle_exit_code": self.oracle_exit_code,
            "generated_equals_oracle": self.generated_equals_oracle,
            "harness_error": self.harness_error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalObservation":
        oracle_status = str(data.get("oracle_status") or "error")
        if oracle_status not in {"passed", "failed", "error"}:
            raise EvalCaseError(f"invalid oracle status: {oracle_status!r}")
        return cls(
            case_id=_required(data.get("case_id"), "observation.case_id"),
            patch_required=bool(data.get("patch_required", True)),
            generated_non_empty=bool(data.get("generated_non_empty")),
            execution_verifier_passed=data.get("execution_verifier_passed"),
            execution_state=str(data.get("execution_state") or "unknown"),
            execution_reached_human_gate=bool(data.get("execution_reached_human_gate")),
            oracle_status=oracle_status,  # type: ignore[arg-type]
            oracle_exit_code=(
                int(data["oracle_exit_code"]) if data.get("oracle_exit_code") is not None else None
            ),
            generated_equals_oracle=data.get("generated_equals_oracle"),
            harness_error=str(data.get("harness_error") or ""),
        )


@dataclass(slots=True, frozen=True)
class EvalScore:
    decision: Literal["pass", "fail", "error"]
    failure_stage: str | None
    generated_equals_oracle: bool | None
    generated_non_empty: bool
    execution_verifier_passed: bool | None
    execution_reached_human_gate: bool
    oracle_passed: bool
