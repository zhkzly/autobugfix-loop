from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml

from autobugfix.models import DEFECTS4J_FRAMEWORK_REVISION, utc_now


class BenchmarkContractError(ValueError):
    pass


EligibilityStatus = Literal["eligible", "ineligible", "harness_error"]
ExperimentRole = Literal["evaluation", "optimization", "sealed_holdout"]


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_payload(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_with_digest(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    return {**body, "record_digest": digest_payload(body)}


def verify_record(data: Mapping[str, Any]) -> None:
    stored = str(data.get("record_digest") or "")
    payload = {key: value for key, value in data.items() if key != "record_digest"}
    if not stored or stored != digest_payload(payload):
        raise BenchmarkContractError("benchmark record digest mismatch")


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BenchmarkContractError(f"{field} must not be empty")
    return text


def safe_component(value: object, field: str) -> str:
    text = _required(value, field)
    if text in {".", ".."} or Path(text).name != text or "/" in text or "\\" in text:
        raise BenchmarkContractError(f"{field} must be a safe path component")
    return text


@dataclass(slots=True, frozen=True)
class BenchmarkCaseSeed:
    case_id: str
    project: str
    bug_id: int
    first_wave: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkCaseSeed":
        wave = int(data.get("first_wave") or 0)
        if wave not in {3, 8, 16}:
            raise BenchmarkContractError("case first_wave must be 3, 8, or 16")
        bug_id = int(data.get("bug_id") or 0)
        if bug_id < 1:
            raise BenchmarkContractError("case bug_id must be positive")
        return cls(
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=_required(data.get("project"), "project"),
            bug_id=bug_id,
            first_wave=wave,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project": self.project,
            "bug_id": self.bug_id,
            "first_wave": self.first_wave,
        }


@dataclass(slots=True, frozen=True)
class EvaluationCaseSeed:
    case_id: str
    project: str
    bug_id: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationCaseSeed":
        bug_id = int(data.get("bug_id") or 0)
        if bug_id < 1:
            raise BenchmarkContractError("evaluation case bug_id must be positive")
        return cls(
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=_required(data.get("project"), "project"),
            bug_id=bug_id,
        )

    @property
    def first_wave(self) -> int:
        # Compatibility with the eligibility receipt. Pure evaluations have no waves.
        return 16

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project": self.project,
            "bug_id": self.bug_id,
        }


@dataclass(slots=True, frozen=True)
class EvaluationSeedManifest:
    manifest_id: str
    benchmark: str
    framework_revision: str
    dataset_revision: str
    cases: tuple[EvaluationCaseSeed, ...]
    expected_case_count: int
    model: str
    max_attempts: int
    schema_version: int = 3

    def __post_init__(self) -> None:
        if self.schema_version != 3:
            raise BenchmarkContractError(
                "unsupported benchmark evaluation seed schema version"
            )
        if self.benchmark != "defects4j":
            raise BenchmarkContractError("evaluation benchmark must be defects4j")
        if self.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
            raise BenchmarkContractError(
                "Defects4J evaluation framework revision is not pinned"
            )
        if self.expected_case_count < 1 or len(self.cases) != self.expected_case_count:
            raise BenchmarkContractError(
                "evaluation cases must match expected_case_count"
            )
        if len({item.case_id for item in self.cases}) != len(self.cases):
            raise BenchmarkContractError("evaluation case IDs must be unique")
        identities = {(item.project, item.bug_id) for item in self.cases}
        if len(identities) != len(self.cases):
            raise BenchmarkContractError(
                "evaluation benchmark identities must be unique"
            )
        if self.model != "gpt-5.4-mini":
            raise BenchmarkContractError(
                "primary Defects4J evaluation model must be gpt-5.4-mini"
            )
        if self.max_attempts < 1:
            raise BenchmarkContractError("evaluation max_attempts must be positive")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationSeedManifest":
        raw_cases = data.get("cases")
        if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
            raise BenchmarkContractError("evaluation cases must be a list")
        if not all(isinstance(item, Mapping) for item in raw_cases):
            raise BenchmarkContractError("evaluation case must be a mapping")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            benchmark=_required(data.get("benchmark"), "benchmark"),
            framework_revision=_required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=_required(
                data.get("dataset_revision"), "dataset_revision"
            ),
            cases=tuple(EvaluationCaseSeed.from_dict(item) for item in raw_cases),
            expected_case_count=int(data.get("expected_case_count") or 0),
            model=_required(data.get("model"), "model"),
            max_attempts=int(data.get("max_attempts") or 0),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "EvaluationSeedManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError(
                "benchmark evaluation seed manifest must be a mapping"
            )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "benchmark": self.benchmark,
            "framework_revision": self.framework_revision,
            "dataset_revision": self.dataset_revision,
            "expected_case_count": self.expected_case_count,
            "model": self.model,
            "max_attempts": self.max_attempts,
            "cases": [item.to_dict() for item in self.cases],
        }

    @property
    def manifest_digest(self) -> str:
        return digest_payload(self.to_dict())


@dataclass(slots=True, frozen=True)
class PreparedEvaluationCase:
    case_id: str
    project: str
    bug_id: int
    receipt_digest: str

    def __post_init__(self) -> None:
        safe_component(self.case_id, "case_id")
        _required(self.project, "project")
        if self.bug_id < 1:
            raise BenchmarkContractError("prepared evaluation bug_id must be positive")
        if len(self.receipt_digest) != 64 or any(
            value not in "0123456789abcdef" for value in self.receipt_digest
        ):
            raise BenchmarkContractError(
                "prepared evaluation receipt_digest must be sha256"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedEvaluationCase":
        return cls(
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=_required(data.get("project"), "project"),
            bug_id=int(data.get("bug_id") or 0),
            receipt_digest=_required(
                data.get("receipt_digest"), "receipt_digest"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project": self.project,
            "bug_id": self.bug_id,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(slots=True, frozen=True)
class PreparedEvaluationManifest:
    manifest_id: str
    seed_manifest_digest: str
    benchmark: str
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    verifier_runtime_id: str
    subject_sha: str
    subject_tree: str
    config_digest: str
    roles_digest: str
    skills_digest: str
    memory_digest: str
    model: str
    max_attempts: int
    expected_case_count: int
    cases: tuple[PreparedEvaluationCase, ...]
    prepared_at: str
    schema_version: int = 4

    def __post_init__(self) -> None:
        if self.schema_version != 4:
            raise BenchmarkContractError(
                "unsupported prepared evaluation manifest schema"
            )
        safe_component(self.manifest_id, "manifest_id")
        if self.benchmark != "defects4j":
            raise BenchmarkContractError(
                "prepared evaluation benchmark must be defects4j"
            )
        if self.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
            raise BenchmarkContractError(
                "prepared Defects4J framework revision is not pinned"
            )
        if self.model != "gpt-5.4-mini":
            raise BenchmarkContractError(
                "prepared evaluation model must be gpt-5.4-mini"
            )
        if self.max_attempts < 1:
            raise BenchmarkContractError(
                "prepared evaluation max_attempts must be positive"
            )
        if self.expected_case_count < 1 or len(self.cases) != self.expected_case_count:
            raise BenchmarkContractError(
                "prepared evaluation cases must match expected_case_count"
            )
        if len({item.case_id for item in self.cases}) != len(self.cases):
            raise BenchmarkContractError(
                "prepared evaluation case IDs must be unique"
            )
        identities = {(item.project, item.bug_id) for item in self.cases}
        if len(identities) != len(self.cases):
            raise BenchmarkContractError(
                "prepared evaluation benchmark identities must be unique"
            )
        for name, value in (
            ("seed_manifest_digest", self.seed_manifest_digest),
            ("config_digest", self.config_digest),
            ("roles_digest", self.roles_digest),
            ("skills_digest", self.skills_digest),
            ("memory_digest", self.memory_digest),
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise BenchmarkContractError(f"{name} must be sha256")
        _required(self.dataset_revision, "dataset_revision")
        for name, value in (
            ("runtime_id", self.runtime_id),
            ("verifier_runtime_id", self.verifier_runtime_id),
        ):
            if not value.startswith("sha256:") or len(value) != 71:
                raise BenchmarkContractError(f"{name} must be an immutable image ID")
        _required(self.subject_sha, "subject_sha")
        _required(self.subject_tree, "subject_tree")
        _required(self.prepared_at, "prepared_at")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreparedEvaluationManifest":
        verify_record(data)
        raw_cases = data.get("cases")
        if not isinstance(raw_cases, Sequence) or isinstance(
            raw_cases, (str, bytes)
        ):
            raise BenchmarkContractError(
                "prepared evaluation cases must be a list"
            )
        if not all(isinstance(item, Mapping) for item in raw_cases):
            raise BenchmarkContractError(
                "prepared evaluation case must be a mapping"
            )
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            seed_manifest_digest=_required(
                data.get("seed_manifest_digest"), "seed_manifest_digest"
            ),
            benchmark=_required(data.get("benchmark"), "benchmark"),
            framework_revision=_required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=_required(
                data.get("dataset_revision"), "dataset_revision"
            ),
            runtime_id=_required(data.get("runtime_id"), "runtime_id"),
            verifier_runtime_id=_required(
                data.get("verifier_runtime_id"), "verifier_runtime_id"
            ),
            subject_sha=_required(data.get("subject_sha"), "subject_sha"),
            subject_tree=_required(data.get("subject_tree"), "subject_tree"),
            config_digest=_required(data.get("config_digest"), "config_digest"),
            roles_digest=_required(data.get("roles_digest"), "roles_digest"),
            skills_digest=_required(data.get("skills_digest"), "skills_digest"),
            memory_digest=_required(data.get("memory_digest"), "memory_digest"),
            model=_required(data.get("model"), "model"),
            max_attempts=int(data.get("max_attempts") or 0),
            expected_case_count=int(data.get("expected_case_count") or 0),
            cases=tuple(PreparedEvaluationCase.from_dict(item) for item in raw_cases),
            prepared_at=_required(data.get("prepared_at"), "prepared_at"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "PreparedEvaluationManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError(
                "prepared evaluation manifest must be a mapping"
            )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "manifest_id": self.manifest_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "benchmark": self.benchmark,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "subject_sha": self.subject_sha,
                "subject_tree": self.subject_tree,
                "config_digest": self.config_digest,
                "roles_digest": self.roles_digest,
                "skills_digest": self.skills_digest,
                "memory_digest": self.memory_digest,
                "model": self.model,
                "max_attempts": self.max_attempts,
                "expected_case_count": self.expected_case_count,
                "cases": [item.to_dict() for item in self.cases],
                "prepared_at": self.prepared_at,
            }
        )


@dataclass(slots=True, frozen=True)
class BenchmarkSeedManifest:
    manifest_id: str
    benchmark: str
    framework_revision: str
    dataset_revision: str
    optimization_cases: tuple[BenchmarkCaseSeed, ...]
    holdout_count: int = 6
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise BenchmarkContractError("unsupported benchmark seed schema version")
        if self.benchmark != "defects4j":
            raise BenchmarkContractError("Defects4J seed benchmark must be defects4j")
        if self.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
            raise BenchmarkContractError("Defects4J seed framework revision is not pinned")
        if len(self.optimization_cases) != 10:
            raise BenchmarkContractError("seed requires exactly 10 Optimization cases")
        if len({item.case_id for item in self.optimization_cases}) != 10:
            raise BenchmarkContractError("Optimization case IDs must be unique")
        identities = {(item.project, item.bug_id) for item in self.optimization_cases}
        if len(identities) != 10:
            raise BenchmarkContractError("Optimization benchmark identities must be unique")
        wave_counts = {
            wave: sum(1 for item in self.optimization_cases if item.first_wave <= wave)
            for wave in (3, 8, 16)
        }
        if wave_counts != {3: 2, 8: 5, 16: 10}:
            raise BenchmarkContractError(
                "Optimization waves must contain cumulative counts 2, 5, and 10"
            )
        if self.holdout_count != 6:
            raise BenchmarkContractError("seed requires exactly six Holdout cases")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkSeedManifest":
        raw_cases = data.get("optimization_cases")
        if "holdout_project_pool" in data:
            raise BenchmarkContractError(
                "schema v2 forbids public holdout_project_pool; provide it to the trusted Guard"
            )
        if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
            raise BenchmarkContractError("optimization_cases must be a list")
        cases: list[BenchmarkCaseSeed] = []
        for item in raw_cases:
            if not isinstance(item, Mapping):
                raise BenchmarkContractError("optimization case must be a mapping")
            cases.append(BenchmarkCaseSeed.from_dict(item))
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            benchmark=_required(data.get("benchmark"), "benchmark"),
            framework_revision=_required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=_required(data.get("dataset_revision"), "dataset_revision"),
            optimization_cases=tuple(cases),
            holdout_count=int(data.get("holdout_count") or 6),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "BenchmarkSeedManifest":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("benchmark seed manifest must be a mapping")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "benchmark": self.benchmark,
            "framework_revision": self.framework_revision,
            "dataset_revision": self.dataset_revision,
            "optimization_cases": [item.to_dict() for item in self.optimization_cases],
            "holdout_count": self.holdout_count,
        }

    @property
    def manifest_digest(self) -> str:
        return digest_payload(self.to_dict())


@dataclass(slots=True, frozen=True)
class DoctorCheck:
    name: str
    passed: bool
    expected: str
    observed: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "observed": self.observed,
            "error": self.error,
        }


@dataclass(slots=True, frozen=True)
class DoctorReport:
    adapter: str
    framework_revision: str
    started_at: str
    finished_at: str
    checks: tuple[DoctorCheck, ...]
    runtime_id: str = "unavailable"
    verifier_runtime_id: str = "unavailable"

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(item.passed for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "adapter": self.adapter,
                "framework_revision": self.framework_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "passed": self.passed,
                "checks": [item.to_dict() for item in self.checks],
            }
        )


@dataclass(slots=True, frozen=True)
class CommandEvidence:
    name: str
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int | None
    timed_out: bool
    stdout_path: str
    stderr_path: str
    stdout_sha256: str
    stderr_sha256: str
    environment_digest: str

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "name": self.name,
                "argv": list(self.argv),
                "cwd": self.cwd,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_seconds": self.duration_seconds,
                "exit_code": self.exit_code,
                "timed_out": self.timed_out,
                "passed": self.passed,
                "stdout_path": self.stdout_path,
                "stderr_path": self.stderr_path,
                "stdout_sha256": self.stdout_sha256,
                "stderr_sha256": self.stderr_sha256,
                "environment_digest": self.environment_digest,
            }
        )


@dataclass(slots=True, frozen=True)
class EligibilityReceipt:
    receipt_id: str
    manifest_digest: str
    case_id: str
    project: str
    bug_id: int
    role: ExperimentRole
    first_wave: int
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    verifier_runtime_id: str
    issue_evidence_digest: str
    issue_evidence_path: str
    buggy_revision: str
    fixed_revision: str
    triggering_tests: tuple[str, ...]
    baseline_failing_tests: tuple[str, ...]
    source_roots: tuple[str, ...]
    sanitized_repo_path: str
    sanitized_base_sha: str
    gold_patch_path: str
    gold_patch_sha256: str
    commands: tuple[Mapping[str, Any], ...]
    status: EligibilityStatus
    reason: str
    created_at: str
    failure_evidence_path: str = "unavailable"
    failure_evidence_sha256: str = "unavailable"
    reproduction_command: str = "unavailable"
    verifier_metadata_path: str = "unavailable"
    verifier_metadata_sha256: str = "unavailable"

    def __post_init__(self) -> None:
        safe_component(self.receipt_id, "receipt_id")
        safe_component(self.case_id, "case_id")
        if self.role not in {"evaluation", "optimization", "sealed_holdout"}:
            raise BenchmarkContractError("invalid eligibility role")
        if self.first_wave not in {3, 8, 16}:
            raise BenchmarkContractError("eligibility first_wave must be 3, 8, or 16")
        if self.status not in {"eligible", "ineligible", "harness_error"}:
            raise BenchmarkContractError("invalid eligibility status")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "receipt_id": self.receipt_id,
                "manifest_digest": self.manifest_digest,
                "case_id": self.case_id,
                "project": self.project,
                "bug_id": self.bug_id,
                "role": self.role,
                "first_wave": self.first_wave,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "issue_evidence_digest": self.issue_evidence_digest,
                "issue_evidence_path": self.issue_evidence_path,
                "buggy_revision": self.buggy_revision,
                "fixed_revision": self.fixed_revision,
                "triggering_tests": list(self.triggering_tests),
                "baseline_failing_tests": list(self.baseline_failing_tests),
                "source_roots": list(self.source_roots),
                "sanitized_repo_path": self.sanitized_repo_path,
                "sanitized_base_sha": self.sanitized_base_sha,
                "gold_patch_path": self.gold_patch_path,
                "gold_patch_sha256": self.gold_patch_sha256,
                "commands": [dict(item) for item in self.commands],
                "status": self.status,
                "reason": self.reason,
                "created_at": self.created_at,
                "failure_evidence_path": self.failure_evidence_path,
                "failure_evidence_sha256": self.failure_evidence_sha256,
                "reproduction_command": self.reproduction_command,
                "verifier_metadata_path": self.verifier_metadata_path,
                "verifier_metadata_sha256": self.verifier_metadata_sha256,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EligibilityReceipt":
        verify_record(data)
        commands = data.get("commands") or []
        if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
            raise BenchmarkContractError("eligibility commands must be a list")
        normalized_commands: list[Mapping[str, Any]] = []
        for command in commands:
            if not isinstance(command, Mapping):
                raise BenchmarkContractError("eligibility command must be a mapping")
            verify_record(command)
            normalized_commands.append(command)
        return cls(
            receipt_id=safe_component(data.get("receipt_id"), "receipt_id"),
            manifest_digest=_required(data.get("manifest_digest"), "manifest_digest"),
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=_required(data.get("project"), "project"),
            bug_id=int(data.get("bug_id") or 0),
            role=str(data.get("role")),  # type: ignore[arg-type]
            first_wave=int(data.get("first_wave") or 0),
            framework_revision=_required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=_required(data.get("dataset_revision"), "dataset_revision"),
            runtime_id=_required(data.get("runtime_id"), "runtime_id"),
            verifier_runtime_id=_required(
                data.get("verifier_runtime_id"), "verifier_runtime_id"
            ),
            issue_evidence_digest=_required(
                data.get("issue_evidence_digest"), "issue_evidence_digest"
            ),
            issue_evidence_path=_required(
                data.get("issue_evidence_path"), "issue_evidence_path"
            ),
            buggy_revision=_required(data.get("buggy_revision"), "buggy_revision"),
            fixed_revision=_required(data.get("fixed_revision"), "fixed_revision"),
            triggering_tests=tuple(str(item) for item in data.get("triggering_tests") or []),
            baseline_failing_tests=tuple(
                str(item) for item in data.get("baseline_failing_tests") or []
            ),
            source_roots=tuple(str(item) for item in data.get("source_roots") or []),
            sanitized_repo_path=_required(
                data.get("sanitized_repo_path"), "sanitized_repo_path"
            ),
            sanitized_base_sha=_required(
                data.get("sanitized_base_sha"), "sanitized_base_sha"
            ),
            gold_patch_path=_required(data.get("gold_patch_path"), "gold_patch_path"),
            gold_patch_sha256=_required(
                data.get("gold_patch_sha256"), "gold_patch_sha256"
            ),
            commands=tuple(normalized_commands),
            status=str(data.get("status")),  # type: ignore[arg-type]
            reason=str(data.get("reason") or ""),
            created_at=_required(data.get("created_at"), "created_at"),
            failure_evidence_path=str(
                data.get("failure_evidence_path") or "unavailable"
            ),
            failure_evidence_sha256=str(
                data.get("failure_evidence_sha256") or "unavailable"
            ),
            reproduction_command=str(
                data.get("reproduction_command") or "unavailable"
            ),
            verifier_metadata_path=str(
                data.get("verifier_metadata_path") or "unavailable"
            ),
            verifier_metadata_sha256=str(
                data.get("verifier_metadata_sha256") or "unavailable"
            ),
        )

    @classmethod
    def pending(
        cls,
        *,
        receipt_id: str,
        manifest_digest: str,
        case_id: str,
        project: str,
        bug_id: int,
        role: ExperimentRole,
        first_wave: int,
        framework_revision: str,
        dataset_revision: str,
        status: EligibilityStatus,
        reason: str,
    ) -> "EligibilityReceipt":
        return cls(
            receipt_id=receipt_id,
            manifest_digest=manifest_digest,
            case_id=case_id,
            project=project,
            bug_id=bug_id,
            role=role,
            first_wave=first_wave,
            framework_revision=framework_revision,
            dataset_revision=dataset_revision,
            runtime_id="unavailable",
            verifier_runtime_id="unavailable",
            issue_evidence_digest="unavailable",
            issue_evidence_path="unavailable",
            buggy_revision="unavailable",
            fixed_revision="unavailable",
            triggering_tests=(),
            baseline_failing_tests=(),
            source_roots=(),
            sanitized_repo_path="unavailable",
            sanitized_base_sha="unavailable",
            gold_patch_path="unavailable",
            gold_patch_sha256="unavailable",
            commands=(),
            status=status,
            reason=reason,
            created_at=utc_now(),
            failure_evidence_path="unavailable",
            failure_evidence_sha256="unavailable",
            reproduction_command="unavailable",
            verifier_metadata_path="unavailable",
            verifier_metadata_sha256="unavailable",
        )


@dataclass(slots=True, frozen=True)
class SealedCaseReference:
    case_token: str
    case_id: str
    project: str
    bug_id: int
    role: ExperimentRole
    first_wave: int
    receipt_path: str
    receipt_digest: str

    def __post_init__(self) -> None:
        safe_component(self.case_token, "case_token")
        safe_component(self.case_id, "case_id")
        if self.role not in {"optimization", "sealed_holdout"}:
            raise BenchmarkContractError("invalid sealed case role")
        if self.first_wave not in {3, 8, 16}:
            raise BenchmarkContractError(
                "sealed case first_wave must be 3, 8, or 16"
            )
        if self.bug_id < 1:
            raise BenchmarkContractError("sealed case bug_id must be positive")
        _required(self.project, "project")
        _required(self.receipt_path, "receipt_path")
        if len(self.receipt_digest) != 64 or any(
            value not in "0123456789abcdef" for value in self.receipt_digest
        ):
            raise BenchmarkContractError("sealed case receipt_digest must be sha256")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SealedCaseReference":
        return cls(
            case_token=safe_component(data.get("case_token"), "case_token"),
            case_id=safe_component(data.get("case_id"), "case_id"),
            project=_required(data.get("project"), "project"),
            bug_id=int(data.get("bug_id") or 0),
            role=str(data.get("role") or ""),  # type: ignore[arg-type]
            first_wave=int(data.get("first_wave") or 0),
            receipt_path=_required(data.get("receipt_path"), "receipt_path"),
            receipt_digest=_required(data.get("receipt_digest"), "receipt_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_token": self.case_token,
            "case_id": self.case_id,
            "project": self.project,
            "bug_id": self.bug_id,
            "role": self.role,
            "first_wave": self.first_wave,
            "receipt_path": self.receipt_path,
            "receipt_digest": self.receipt_digest,
        }


@dataclass(slots=True, frozen=True)
class SealedBenchmarkManifest:
    manifest_id: str
    seed_manifest_digest: str
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    verifier_runtime_id: str
    optimization_cases: tuple[SealedCaseReference, ...]
    holdout_cases: tuple[SealedCaseReference, ...]
    created_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        safe_component(self.manifest_id, "manifest_id")
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported sealed manifest schema")
        if self.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
            raise BenchmarkContractError(
                "sealed manifest framework revision is not pinned"
            )
        if not self.runtime_id.startswith("sha256:"):
            raise BenchmarkContractError(
                "sealed manifest runtime must be an immutable image ID"
            )
        if not self.verifier_runtime_id.startswith("sha256:"):
            raise BenchmarkContractError(
                "sealed manifest verifier runtime must be an immutable image ID"
            )
        if len(self.optimization_cases) != 10 or len(self.holdout_cases) != 6:
            raise BenchmarkContractError(
                "sealed manifest requires 10 Optimization and six Holdout cases"
            )
        if any(item.role != "optimization" for item in self.optimization_cases):
            raise BenchmarkContractError(
                "Optimization references must have optimization role"
            )
        if any(item.role != "sealed_holdout" for item in self.holdout_cases):
            raise BenchmarkContractError(
                "Holdout references must have sealed_holdout role"
            )
        all_cases = (*self.optimization_cases, *self.holdout_cases)
        if len({item.case_token for item in all_cases}) != 16:
            raise BenchmarkContractError("sealed case tokens must be unique")
        if len({(item.project, item.bug_id) for item in all_cases}) != 16:
            raise BenchmarkContractError("sealed benchmark identities must be unique")
        optimization_projects = {item.project for item in self.optimization_cases}
        holdout_projects = {item.project for item in self.holdout_cases}
        if optimization_projects & holdout_projects:
            raise BenchmarkContractError(
                "Optimization and Holdout repositories must be disjoint"
            )
        if len(holdout_projects) < 3:
            raise BenchmarkContractError(
                "sealed Holdout requires at least three repository groups"
            )
        wave_counts = {
            wave: sum(1 for item in all_cases if item.first_wave <= wave)
            for wave in (3, 8, 16)
        }
        if wave_counts != {3: 3, 8: 8, 16: 16}:
            raise BenchmarkContractError(
                "sealed waves must contain cumulative counts 3, 8, and 16"
            )
        _required(self.seed_manifest_digest, "seed_manifest_digest")
        _required(self.dataset_revision, "dataset_revision")
        _required(self.created_at, "created_at")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SealedBenchmarkManifest":
        verify_record(data)
        raw_optimization = data.get("optimization_cases")
        raw_holdout = data.get("holdout_cases")
        if not isinstance(raw_optimization, Sequence) or isinstance(
            raw_optimization, (str, bytes)
        ):
            raise BenchmarkContractError("optimization_cases must be a list")
        if not isinstance(raw_holdout, Sequence) or isinstance(
            raw_holdout, (str, bytes)
        ):
            raise BenchmarkContractError("holdout_cases must be a list")
        if not all(isinstance(item, Mapping) for item in raw_optimization):
            raise BenchmarkContractError("optimization case reference must be a mapping")
        if not all(isinstance(item, Mapping) for item in raw_holdout):
            raise BenchmarkContractError("holdout case reference must be a mapping")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            manifest_id=safe_component(data.get("manifest_id"), "manifest_id"),
            seed_manifest_digest=_required(
                data.get("seed_manifest_digest"), "seed_manifest_digest"
            ),
            framework_revision=_required(
                data.get("framework_revision"), "framework_revision"
            ),
            dataset_revision=_required(
                data.get("dataset_revision"), "dataset_revision"
            ),
            runtime_id=_required(data.get("runtime_id"), "runtime_id"),
            verifier_runtime_id=_required(
                data.get("verifier_runtime_id"), "verifier_runtime_id"
            ),
            optimization_cases=tuple(
                SealedCaseReference.from_dict(item) for item in raw_optimization
            ),
            holdout_cases=tuple(
                SealedCaseReference.from_dict(item) for item in raw_holdout
            ),
            created_at=_required(data.get("created_at"), "created_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "manifest_id": self.manifest_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "optimization_cases": [
                    item.to_dict() for item in self.optimization_cases
                ],
                "holdout_cases": [item.to_dict() for item in self.holdout_cases],
                "created_at": self.created_at,
            }
        )

    def visible_projection(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": 1,
                "manifest_id": self.manifest_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "waves": {"3": 3, "8": 8, "16": 16},
                "optimization_cases": [
                    {
                        "case_token": item.case_token,
                        "case_id": item.case_id,
                        "project": item.project,
                        "bug_id": item.bug_id,
                        "first_wave": item.first_wave,
                        "receipt_digest": item.receipt_digest,
                    }
                    for item in self.optimization_cases
                ],
                "sealed_holdout": {
                    "count": len(self.holdout_cases),
                    "cases": [
                        {
                            "case_token": item.case_token,
                            "first_wave": item.first_wave,
                        }
                        for item in self.holdout_cases
                    ],
                },
                "trusted_manifest_digest": self.to_dict()["record_digest"],
            }
        )
