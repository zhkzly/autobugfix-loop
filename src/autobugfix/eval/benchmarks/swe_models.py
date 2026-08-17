from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, cast

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.swe_constants import (
    SWE_BENCH_COMMIT,
    SWE_BENCH_TREE,
    SWE_BENCH_VERSION,
    SWE_H0_SUBJECT,
    SWE_LIVE_COMMIT,
    SWE_LIVE_DATASET,
    SWE_LIVE_DATASET_REVISION,
    SWE_LIVE_LAUNCH_COMMIT,
    SWE_LIVE_LAUNCH_REPOSITORY,
    SWE_LIVE_LAUNCH_TREE,
    SWE_LIVE_REPOSITORY,
    SWE_LIVE_TREE,
    SWE_PLATFORM,
    SWE_PRIMARY_MODEL,
    SWE_VERIFIED_DATASET,
    SWE_VERIFIED_DATASET_REVISION,
)

SWEBenchmark = Literal["swebench_verified", "swebench_live"]
SWEExperimentRole = Literal["optimization", "sealed_holdout"]
SWETaskType = Literal["bugfix", "feature", "maintenance"]


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BenchmarkContractError(f"{field} must not be empty")
    return text


def _sha(value: object, field: str, *, length: int = 64) -> str:
    text = _required(value, field)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise BenchmarkContractError(f"{field} must be a lowercase hexadecimal digest")
    return text


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BenchmarkContractError(f"{field} must be a list")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            import json

            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BenchmarkContractError(f"{field} must contain a JSON list") from exc
    return tuple(str(item) for item in _sequence(value or (), field))


def _only_fields(data: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise BenchmarkContractError(
            f"{field} contains unsupported fields: {', '.join(unknown)}"
        )


@dataclass(slots=True, frozen=True)
class SWEOptimizationSelection:
    instance_id: str
    task_type: SWETaskType

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWEOptimizationSelection":
        _only_fields(data, {"instance_id", "task_type"}, "Optimization selection")
        instance_id = safe_component(data.get("instance_id"), "instance_id")
        task_type = _required(data.get("task_type"), "task_type")
        if task_type not in {"bugfix", "feature", "maintenance"}:
            raise BenchmarkContractError("unsupported Optimization task type")
        return cls(instance_id=instance_id, task_type=task_type)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, str]:
        return {"instance_id": self.instance_id, "task_type": self.task_type}


@dataclass(slots=True, frozen=True)
class SWESubjectTreatmentRuntime:
    """Immutable Codex treatment variables for one SWE experiment protocol."""

    model: str
    reasoning_effort: str
    service_tier: str | None
    sdk_package: str
    sdk_version: str
    cli_package: str
    cli_version: str
    max_attempts: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.model != SWE_PRIMARY_MODEL:
            raise BenchmarkContractError("SWE experiment model must be gpt-5.4-mini")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise BenchmarkContractError("unsupported SWE Codex reasoning effort")
        if self.sdk_package != "openai-codex" or self.sdk_version != "0.144.4":
            raise BenchmarkContractError("SWE experiment SDK must be openai-codex 0.144.4")
        if (
            self.cli_package != "openai-codex-cli-bin"
            or self.cli_version != "0.144.4"
        ):
            raise BenchmarkContractError(
                "SWE experiment Codex CLI must be openai-codex-cli-bin 0.144.4"
            )
        if self.max_attempts != 2:
            raise BenchmarkContractError("SWE experiment requires exactly two Writer attempts")
        if self.timeout_seconds != 900:
            raise BenchmarkContractError("SWE experiment timeout must be 900 seconds")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWESubjectTreatmentRuntime":
        _only_fields(
            data,
            {
                "model",
                "reasoning_effort",
                "service_tier",
                "sdk_package",
                "sdk_version",
                "cli_package",
                "cli_version",
                "max_attempts",
                "timeout_seconds",
            },
            "SWE Codex runtime",
        )
        return cls(
            model=_required(data.get("model"), "codex_runtime.model"),
            reasoning_effort=_required(
                data.get("reasoning_effort"), "codex_runtime.reasoning_effort"
            ),
            service_tier=(
                str(data["service_tier"])
                if data.get("service_tier") is not None
                else None
            ),
            sdk_package=_required(data.get("sdk_package"), "codex_runtime.sdk_package"),
            sdk_version=_required(data.get("sdk_version"), "codex_runtime.sdk_version"),
            cli_package=_required(data.get("cli_package"), "codex_runtime.cli_package"),
            cli_version=_required(data.get("cli_version"), "codex_runtime.cli_version"),
            max_attempts=int(data.get("max_attempts") or 0),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "sdk_package": self.sdk_package,
            "sdk_version": self.sdk_version,
            "cli_package": self.cli_package,
            "cli_version": self.cli_version,
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
        }

    @property
    def contract_digest(self) -> str:
        return digest_payload(self.to_dict())


@dataclass(slots=True, frozen=True)
class SWEExperimentProtocol:
    protocol_id: str
    h0_subject: str
    codex_runtime: SWESubjectTreatmentRuntime
    case_concurrency: int
    optimization_count: int
    holdout_count: int
    optimization_dataset: str
    holdout_dataset: str
    qualification_repeats: int
    optimization_cases: tuple[SWEOptimizationSelection, ...]
    holdout_excluded_instances: tuple[str, ...]
    verified_image_mode: Literal["local-build", "pinned-official-import"]
    schema_version: int = 3

    def __post_init__(self) -> None:
        if self.schema_version != 3:
            raise BenchmarkContractError("unsupported SWE experiment protocol schema")
        safe_component(self.protocol_id, "protocol_id")
        if self.h0_subject != SWE_H0_SUBJECT:
            raise BenchmarkContractError("SWE experiment must start from the frozen H0")
        if self.case_concurrency != 1:
            raise BenchmarkContractError("SWE experiment case concurrency must be one")
        if self.qualification_repeats != 2:
            raise BenchmarkContractError(
                "SWE experiment qualification requires two official gold runs"
            )
        if self.verified_image_mode not in {
            "local-build",
            "pinned-official-import",
        }:
            raise BenchmarkContractError(
                "unsupported SWE Verified image mode"
            )
        if (self.optimization_count, self.holdout_count) != (10, 6):
            raise BenchmarkContractError("SWE experiment requires 10 Optimization and six Holdout cases")
        if self.optimization_dataset != SWE_VERIFIED_DATASET:
            raise BenchmarkContractError("Optimization dataset must be SWE-bench Verified")
        if self.holdout_dataset != SWE_LIVE_DATASET:
            raise BenchmarkContractError("Holdout dataset must be SWE-bench-Live MultiLang")
        if not self.holdout_excluded_instances:
            raise BenchmarkContractError(
                "SWE Holdout protocol requires an explicit exposure exclusion ledger"
            )
        for instance_id in self.holdout_excluded_instances:
            safe_component(instance_id, "holdout.excluded_instances")
        if len(set(self.holdout_excluded_instances)) != len(
            self.holdout_excluded_instances
        ):
            raise BenchmarkContractError(
                "SWE Holdout exclusion identities must be unique"
            )
        if len(self.optimization_cases) != self.optimization_count:
            raise BenchmarkContractError(
                "SWE protocol must name exactly ten Optimization cases"
            )
        if len({case.instance_id for case in self.optimization_cases}) != len(
            self.optimization_cases
        ):
            raise BenchmarkContractError("SWE Optimization case IDs must be unique")
        if {case.task_type for case in self.optimization_cases} != {
            "bugfix",
            "feature",
            "maintenance",
        }:
            raise BenchmarkContractError(
                "SWE Optimization selections must cover all task types"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWEExperimentProtocol":
        _only_fields(
            data,
            {
                "schema_version",
                "protocol_id",
                "h0_subject",
                "codex_runtime",
                "case_concurrency",
                "qualification_repeats",
                "optimization",
                "holdout",
                "upstreams",
                "waves",
            },
            "SWE experiment protocol",
        )
        optimization = data.get("optimization")
        holdout = data.get("holdout")
        upstreams = data.get("upstreams")
        waves = data.get("waves")
        if not isinstance(optimization, Mapping) or not isinstance(holdout, Mapping):
            raise BenchmarkContractError("SWE protocol optimization and holdout must be mappings")
        codex_runtime = data.get("codex_runtime")
        if not isinstance(upstreams, Mapping) or not isinstance(waves, Mapping):
            raise BenchmarkContractError("SWE protocol upstreams and waves must be mappings")
        if not isinstance(codex_runtime, Mapping):
            raise BenchmarkContractError("SWE protocol codex_runtime must be a mapping")
        verified_image_mode = cls._validate_upstreams(upstreams)
        _only_fields(
            optimization,
            {"dataset", "count", "cases"},
            "SWE optimization protocol",
        )
        _only_fields(
            holdout,
            {"dataset", "count", "excluded_instances"},
            "SWE holdout protocol",
        )
        optimization_cases = tuple(
            SWEOptimizationSelection.from_dict(item)
            for item in _sequence(optimization.get("cases") or (), "optimization.cases")
            if isinstance(item, Mapping)
        )
        if len(optimization_cases) != len(
            _sequence(optimization.get("cases") or (), "optimization.cases")
        ):
            raise BenchmarkContractError("Optimization case entries must be mappings")
        if {str(key): int(value) for key, value in waves.items()} != {
            "3": 3,
            "8": 8,
            "16": 16,
        }:
            raise BenchmarkContractError("SWE protocol waves must be cumulative 3, 8, and 16")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            protocol_id=safe_component(data.get("protocol_id"), "protocol_id"),
            h0_subject=_required(data.get("h0_subject"), "h0_subject"),
            codex_runtime=SWESubjectTreatmentRuntime.from_dict(codex_runtime),
            case_concurrency=int(data.get("case_concurrency") or 0),
            qualification_repeats=int(data.get("qualification_repeats") or 0),
            optimization_count=int(optimization.get("count") or 0),
            holdout_count=int(holdout.get("count") or 0),
            optimization_dataset=_required(optimization.get("dataset"), "optimization.dataset"),
            holdout_dataset=_required(holdout.get("dataset"), "holdout.dataset"),
            optimization_cases=optimization_cases,
            holdout_excluded_instances=_string_tuple(
                holdout.get("excluded_instances") or (),
                "holdout.excluded_instances",
            ),
            verified_image_mode=verified_image_mode,
        )

    @staticmethod
    def _validate_upstreams(
        data: Mapping[str, Any],
    ) -> Literal["local-build", "pinned-official-import"]:
        expected = {
            "swebench_version": SWE_BENCH_VERSION,
            "swebench_commit": SWE_BENCH_COMMIT,
            "swebench_tree": SWE_BENCH_TREE,
            "verified_dataset_revision": SWE_VERIFIED_DATASET_REVISION,
            "live_repository": SWE_LIVE_REPOSITORY,
            "live_commit": SWE_LIVE_COMMIT,
            "live_tree": SWE_LIVE_TREE,
            "live_launch_repository": SWE_LIVE_LAUNCH_REPOSITORY,
            "live_launch_commit": SWE_LIVE_LAUNCH_COMMIT,
            "live_launch_tree": SWE_LIVE_LAUNCH_TREE,
            "live_dataset_revision": SWE_LIVE_DATASET_REVISION,
            "platform": SWE_PLATFORM,
        }
        observed = {key: str(data.get(key) or "") for key in expected}
        if observed != expected:
            drift = sorted(key for key in expected if observed[key] != expected[key])
            raise BenchmarkContractError(
                "SWE upstream identity drift: " + ", ".join(drift)
            )
        mode = str(data.get("verified_image_mode") or "")
        if mode not in {"local-build", "pinned-official-import"}:
            raise BenchmarkContractError(
                "SWE upstream identity drift: verified_image_mode"
            )
        return cast(Literal["local-build", "pinned-official-import"], mode)

    @classmethod
    def from_yaml(cls, path: Path) -> "SWEExperimentProtocol":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("SWE experiment protocol must be a mapping")
        return cls.from_dict(data)

    @property
    def protocol_digest(self) -> str:
        return digest_payload(self.to_dict())

    @property
    def qualification_contract_digest(self) -> str:
        """Identity for gold scoring/materialization, excluding subject treatment."""
        return digest_payload(
            {
                "schema": "autobugfix-swe-qualification-contract-v2",
                "qualification_repeats": self.qualification_repeats,
                "null_base_repeats": 1,
                "optimization_dataset": self.optimization_dataset,
                "holdout_dataset": self.holdout_dataset,
                "upstreams": self.to_dict()["upstreams"],
            }
        )

    @property
    def subject_runtime_contract_digest(self) -> str:
        return self.codex_runtime.contract_digest

    @property
    def model(self) -> str:
        return self.codex_runtime.model

    @property
    def max_attempts(self) -> int:
        return self.codex_runtime.max_attempts

    @property
    def timeout_seconds(self) -> int:
        return self.codex_runtime.timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_id": self.protocol_id,
            "h0_subject": self.h0_subject,
            "codex_runtime": self.codex_runtime.to_dict(),
            "case_concurrency": self.case_concurrency,
            "qualification_repeats": self.qualification_repeats,
            "optimization": {
                "dataset": self.optimization_dataset,
                "count": self.optimization_count,
                "cases": [case.to_dict() for case in self.optimization_cases],
            },
            "holdout": {
                "dataset": self.holdout_dataset,
                "count": self.holdout_count,
                "excluded_instances": list(self.holdout_excluded_instances),
            },
            "waves": {"3": 3, "8": 8, "16": 16},
            "upstreams": {
                "swebench_version": SWE_BENCH_VERSION,
                "swebench_commit": SWE_BENCH_COMMIT,
                "swebench_tree": SWE_BENCH_TREE,
                "verified_dataset_revision": SWE_VERIFIED_DATASET_REVISION,
                "live_repository": SWE_LIVE_REPOSITORY,
                "live_commit": SWE_LIVE_COMMIT,
                "live_tree": SWE_LIVE_TREE,
                "live_launch_repository": SWE_LIVE_LAUNCH_REPOSITORY,
                "live_launch_commit": SWE_LIVE_LAUNCH_COMMIT,
                "live_launch_tree": SWE_LIVE_LAUNCH_TREE,
                "live_dataset_revision": SWE_LIVE_DATASET_REVISION,
                "platform": SWE_PLATFORM,
                "verified_image_mode": self.verified_image_mode,
            },
        }


@dataclass(slots=True, frozen=True)
class SWEAttachment:
    kind: str
    uri: str
    sha256: str
    media_type: str = "application/octet-stream"
    description: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWEAttachment":
        _only_fields(data, {"kind", "uri", "sha256", "media_type", "description"}, "attachment")
        return cls(
            kind=_required(data.get("kind"), "attachment.kind"),
            uri=_required(data.get("uri"), "attachment.uri"),
            sha256=_sha(data.get("sha256"), "attachment.sha256"),
            media_type=_required(data.get("media_type") or "application/octet-stream", "attachment.media_type"),
            description=str(data.get("description") or ""),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "uri": self.uri,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "description": self.description,
        }


@dataclass(slots=True, frozen=True)
class SWEVisibleCase:
    case_token: str
    benchmark: SWEBenchmark
    dataset_revision: str
    harness_commit: str
    repository: str
    base_commit: str
    language: str
    task_type: SWETaskType
    problem_statement: str
    public_hints: tuple[str, ...]
    attachments: tuple[SWEAttachment, ...]
    first_wave: int
    source_snapshot_digest: str
    verifier_profile: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise BenchmarkContractError("unsupported visible SWE case schema")
        safe_component(self.case_token, "case_token")
        if self.benchmark not in {"swebench_verified", "swebench_live"}:
            raise BenchmarkContractError("unsupported SWE benchmark")
        if self.task_type not in {"bugfix", "feature", "maintenance"}:
            raise BenchmarkContractError("unsupported SWE task type")
        if self.first_wave not in {3, 8, 16}:
            raise BenchmarkContractError("SWE case first_wave must be 3, 8, or 16")
        for field, value in (
            ("repository", self.repository),
            ("base_commit", self.base_commit),
            ("language", self.language),
            ("problem_statement", self.problem_statement),
            ("verifier_profile", self.verifier_profile),
        ):
            _required(value, field)
        _sha(self.source_snapshot_digest, "source_snapshot_digest")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SWEVisibleCase":
        allowed = {
            "schema_version",
            "case_token",
            "benchmark",
            "dataset_revision",
            "harness_commit",
            "repository",
            "base_commit",
            "language",
            "task_type",
            "problem_statement",
            "public_hints",
            "attachments",
            "first_wave",
            "source_snapshot_digest",
            "verifier_profile",
            "record_digest",
        }
        _only_fields(data, allowed, "visible SWE case")
        if "record_digest" in data:
            verify_record(data)
        raw_hints = _sequence(data.get("public_hints") or (), "public_hints")
        raw_attachments = _sequence(data.get("attachments") or (), "attachments")
        if not all(isinstance(item, Mapping) for item in raw_attachments):
            raise BenchmarkContractError("attachments must contain mappings")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            case_token=safe_component(data.get("case_token"), "case_token"),
            benchmark=str(data.get("benchmark") or ""),  # type: ignore[arg-type]
            dataset_revision=_required(data.get("dataset_revision"), "dataset_revision"),
            harness_commit=_required(data.get("harness_commit"), "harness_commit"),
            repository=_required(data.get("repository"), "repository"),
            base_commit=_required(data.get("base_commit"), "base_commit"),
            language=_required(data.get("language"), "language"),
            task_type=str(data.get("task_type") or ""),  # type: ignore[arg-type]
            problem_statement=_required(data.get("problem_statement"), "problem_statement"),
            public_hints=tuple(str(item) for item in raw_hints),
            attachments=tuple(SWEAttachment.from_dict(item) for item in raw_attachments),
            first_wave=int(data.get("first_wave") or 0),
            source_snapshot_digest=_sha(data.get("source_snapshot_digest"), "source_snapshot_digest"),
            verifier_profile=_required(data.get("verifier_profile"), "verifier_profile"),
        )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "case_token": self.case_token,
                "benchmark": self.benchmark,
                "dataset_revision": self.dataset_revision,
                "harness_commit": self.harness_commit,
                "repository": self.repository,
                "base_commit": self.base_commit,
                "language": self.language,
                "task_type": self.task_type,
                "problem_statement": self.problem_statement,
                "public_hints": list(self.public_hints),
                "attachments": [item.to_dict() for item in self.attachments],
                "first_wave": self.first_wave,
                "source_snapshot_digest": self.source_snapshot_digest,
                "verifier_profile": self.verifier_profile,
            }
        )


@dataclass(slots=True, frozen=True)
class SWEInstance:
    adapter: SWEBenchmark
    instance_id: str
    repository: str
    base_commit: str
    language: str
    problem_statement: str
    hints_text: str
    created_at: str
    docker_image: str
    gold_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    official: Mapping[str, Any]

    def __post_init__(self) -> None:
        safe_component(self.instance_id, "instance_id")
        if self.adapter not in {"swebench_verified", "swebench_live"}:
            raise BenchmarkContractError("unsupported SWE instance adapter")
        for field, value in (
            ("repository", self.repository),
            ("base_commit", self.base_commit),
            ("language", self.language),
            ("problem_statement", self.problem_statement),
            ("docker_image", self.docker_image),
            ("gold_patch", self.gold_patch),
            ("test_patch", self.test_patch),
        ):
            _required(value, field)

    @classmethod
    def from_verified(
        cls,
        row: Mapping[str, Any],
        image: Mapping[str, Any],
    ) -> "SWEInstance":
        return cls(
            adapter="swebench_verified",
            instance_id=safe_component(row.get("instance_id"), "instance_id"),
            repository=_required(row.get("repo"), "repo"),
            base_commit=_required(row.get("base_commit"), "base_commit"),
            language=_required(image.get("language"), "language"),
            problem_statement=_required(row.get("problem_statement"), "problem_statement"),
            hints_text=str(row.get("hints_text") or ""),
            created_at=str(row.get("created_at") or ""),
            docker_image=_required(image.get("docker_image"), "docker_image"),
            gold_patch=_required(row.get("patch"), "patch"),
            test_patch=_required(row.get("test_patch"), "test_patch"),
            fail_to_pass=_string_tuple(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS"),
            pass_to_pass=_string_tuple(row.get("PASS_TO_PASS"), "PASS_TO_PASS"),
            official={"version": str(row.get("version") or "")},
        )

    @classmethod
    def from_live(cls, row: Mapping[str, Any]) -> "SWEInstance":
        split = _required(
            row.get("autobugfix_dataset_split"),
            "autobugfix_dataset_split",
        )
        return cls(
            adapter="swebench_live",
            instance_id=safe_component(row.get("instance_id"), "instance_id"),
            repository=_required(row.get("repo"), "repo"),
            base_commit=_required(row.get("base_commit"), "base_commit"),
            language=split,
            problem_statement=_required(row.get("problem_statement"), "problem_statement"),
            hints_text=str(row.get("hints_text") or row.get("all_hints_text") or ""),
            created_at=str(row.get("created_at") or ""),
            docker_image=_required(row.get("docker_image"), "docker_image"),
            gold_patch=_required(row.get("patch"), "patch"),
            test_patch=_required(row.get("test_patch"), "test_patch"),
            fail_to_pass=_string_tuple(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS"),
            pass_to_pass=_string_tuple(row.get("PASS_TO_PASS"), "PASS_TO_PASS"),
            official={
                "rebuild_cmds": list(_sequence(row.get("rebuild_cmds") or (), "rebuild_cmds")),
                "test_cmds": list(_sequence(row.get("test_cmds") or (), "test_cmds")),
                "print_cmds": list(_sequence(row.get("print_cmds") or (), "print_cmds")),
                "log_parser": str(row.get("log_parser") or ""),
            },
        )

    def trusted_record(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-instance-v1",
                "adapter": self.adapter,
                "instance_id": self.instance_id,
                "repository": self.repository,
                "base_commit": self.base_commit,
                "language": self.language,
                "problem_statement": self.problem_statement,
                "hints_text": self.hints_text,
                "created_at": self.created_at,
                "docker_image": self.docker_image,
                "gold_patch": self.gold_patch,
                "test_patch": self.test_patch,
                "fail_to_pass": list(self.fail_to_pass),
                "pass_to_pass": list(self.pass_to_pass),
                "official": dict(self.official),
            }
        )

    @classmethod
    def from_trusted_record(cls, data: Mapping[str, Any]) -> "SWEInstance":
        _only_fields(
            data,
            {
                "schema",
                "adapter",
                "instance_id",
                "repository",
                "base_commit",
                "language",
                "problem_statement",
                "hints_text",
                "created_at",
                "docker_image",
                "gold_patch",
                "test_patch",
                "fail_to_pass",
                "pass_to_pass",
                "official",
                "record_digest",
            },
            "trusted SWE instance",
        )
        verify_record(data)
        if data.get("schema") != "autobugfix-swe-instance-v1":
            raise BenchmarkContractError("unsupported trusted SWE instance schema")
        official = data.get("official")
        if not isinstance(official, Mapping):
            raise BenchmarkContractError("trusted SWE official metadata must be a mapping")
        return cls(
            adapter=_required(data.get("adapter"), "adapter"),  # type: ignore[arg-type]
            instance_id=safe_component(data.get("instance_id"), "instance_id"),
            repository=_required(data.get("repository"), "repository"),
            base_commit=_required(data.get("base_commit"), "base_commit"),
            language=_required(data.get("language"), "language"),
            problem_statement=_required(
                data.get("problem_statement"), "problem_statement"
            ),
            hints_text=str(data.get("hints_text") or ""),
            created_at=str(data.get("created_at") or ""),
            docker_image=_required(data.get("docker_image"), "docker_image"),
            gold_patch=_required(data.get("gold_patch"), "gold_patch"),
            test_patch=_required(data.get("test_patch"), "test_patch"),
            fail_to_pass=_string_tuple(data.get("fail_to_pass"), "fail_to_pass"),
            pass_to_pass=_string_tuple(data.get("pass_to_pass"), "pass_to_pass"),
            official=dict(official),
        )


@dataclass(slots=True, frozen=True)
class SWEOfficialResult:
    adapter: SWEBenchmark
    instance_id: str
    run_id: str
    resolved: bool
    harness_error: str
    image: str
    image_id: str
    command: Mapping[str, Any]
    report_path: str
    report_sha256: str
    output_root: str
    started_at: str
    finished_at: str

    @property
    def passed(self) -> bool:
        return not self.harness_error and self.resolved

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-official-result-v1",
                "adapter": self.adapter,
                "instance_id": self.instance_id,
                "run_id": self.run_id,
                "resolved": self.resolved,
                "passed": self.passed,
                "harness_error": self.harness_error,
                "image": self.image,
                "image_id": self.image_id,
                "command": dict(self.command),
                "report_path": self.report_path,
                "report_sha256": self.report_sha256,
                "output_root": self.output_root,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
        )


@dataclass(slots=True, frozen=True)
class SWESubmission:
    case_token: str
    subject_sha: str
    subject_tree: str
    base_commit: str
    patch: str
    patch_sha256: str
    events_sha256: str
    task_sha256: str
    subject_request_digest: str
    visible_case_digest: str
    source_snapshot_digest: str
    config_digest: str
    skills_digest: str
    execution_ledger_digest: str
    evidence_manifest_digest: str
    frozen_at: str

    def __post_init__(self) -> None:
        safe_component(self.case_token, "case_token")
        _sha(self.subject_sha, "subject_sha", length=40)
        _sha(self.subject_tree, "subject_tree", length=40)
        _sha(self.patch_sha256, "patch_sha256")
        _sha(self.events_sha256, "events_sha256")
        _sha(self.task_sha256, "task_sha256")
        for value, name in (
            (self.subject_request_digest, "subject_request_digest"),
            (self.visible_case_digest, "visible_case_digest"),
            (self.source_snapshot_digest, "source_snapshot_digest"),
            (self.config_digest, "config_digest"),
            (self.skills_digest, "skills_digest"),
            (self.execution_ledger_digest, "execution_ledger_digest"),
            (self.evidence_manifest_digest, "evidence_manifest_digest"),
        ):
            _sha(value, name)
        observed = hashlib.sha256(self.patch.encode("utf-8")).hexdigest()
        if observed != self.patch_sha256:
            raise BenchmarkContractError("SWE submission patch digest mismatch")
        _required(self.base_commit, "base_commit")
        _required(self.frozen_at, "frozen_at")

    @classmethod
    def from_record(
        cls,
        data: Mapping[str, Any],
        *,
        patch: str,
    ) -> "SWESubmission":
        _only_fields(
            data,
            {
                "schema",
                "case_token",
                "subject_sha",
                "subject_tree",
                "base_commit",
                "patch_sha256",
                "events_sha256",
                "task_sha256",
                "subject_request_digest",
                "visible_case_digest",
                "source_snapshot_digest",
                "config_digest",
                "skills_digest",
                "execution_ledger_digest",
                "evidence_manifest_digest",
                "frozen_at",
                "record_digest",
            },
            "SWE submission",
        )
        verify_record(data)
        if data.get("schema") != "autobugfix-swe-submission-v2":
            raise BenchmarkContractError("unsupported SWE submission schema")
        return cls(
            case_token=safe_component(data.get("case_token"), "case_token"),
            subject_sha=_sha(data.get("subject_sha"), "subject_sha", length=40),
            subject_tree=_sha(data.get("subject_tree"), "subject_tree", length=40),
            base_commit=_required(data.get("base_commit"), "base_commit"),
            patch=patch,
            patch_sha256=_sha(data.get("patch_sha256"), "patch_sha256"),
            events_sha256=_sha(data.get("events_sha256"), "events_sha256"),
            task_sha256=_sha(data.get("task_sha256"), "task_sha256"),
            subject_request_digest=_sha(
                data.get("subject_request_digest"), "subject_request_digest"
            ),
            visible_case_digest=_sha(
                data.get("visible_case_digest"), "visible_case_digest"
            ),
            source_snapshot_digest=_sha(
                data.get("source_snapshot_digest"), "source_snapshot_digest"
            ),
            config_digest=_sha(data.get("config_digest"), "config_digest"),
            skills_digest=_sha(data.get("skills_digest"), "skills_digest"),
            execution_ledger_digest=_sha(
                data.get("execution_ledger_digest"), "execution_ledger_digest"
            ),
            evidence_manifest_digest=_sha(
                data.get("evidence_manifest_digest"), "evidence_manifest_digest"
            ),
            frozen_at=_required(data.get("frozen_at"), "frozen_at"),
        )

    @property
    def record(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-submission-v2",
                "case_token": self.case_token,
                "subject_sha": self.subject_sha,
                "subject_tree": self.subject_tree,
                "base_commit": self.base_commit,
                "patch_sha256": self.patch_sha256,
                "events_sha256": self.events_sha256,
                "task_sha256": self.task_sha256,
                "subject_request_digest": self.subject_request_digest,
                "visible_case_digest": self.visible_case_digest,
                "source_snapshot_digest": self.source_snapshot_digest,
                "config_digest": self.config_digest,
                "skills_digest": self.skills_digest,
                "execution_ledger_digest": self.execution_ledger_digest,
                "evidence_manifest_digest": self.evidence_manifest_digest,
                "frozen_at": self.frozen_at,
            }
        )
