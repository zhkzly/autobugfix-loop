"""Resume-first Experiment 2 v2 records and coordinator.

The original Exp2 coordinator is retained for reading its v1 audit journals.
This module is the writable v2 path.  Its event log is deliberately small: it
coordinates immutable receipts produced by Execution, Eval, and Operator but
does not recreate any of those state machines.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux is required for real Exp2 runs.
    fcntl = None  # type: ignore[assignment]

from autobugfix.eval.benchmarks.exp2_records import (
    Exp2ContractError,
    Exp2EmptyMemoryFixture,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.models import utc_now


class Exp2ResumeError(RuntimeError):
    """A v2 coordinator transition, integrity, or recovery failure."""


StudyKind = Literal["calibration", "resume_pilot"]
ResumeStage = Literal["CALIBRATION", "H0", "H1_SOURCE", "H1_TRANSFER"]
ResumeArm = Literal["H0", "H1"]
AttemptKind = Literal["execution", "scorer_only_retry"]
CaseTerminalStatus = Literal[
    "official_terminal",
    "preflight_rejected",
    "execution_infrastructure_invalid",
    "scorer_infrastructure_invalid",
]

_SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")
_SHA1 = __import__("re").compile(r"^[0-9a-f]{40}$")
_TERMINAL_STATUSES = {
    "official_terminal",
    "preflight_rejected",
    "execution_infrastructure_invalid",
    "scorer_infrastructure_invalid",
}
_UNSET = object()
_FORBIDDEN_PROJECTION_KEYS = {
    "gold_patch",
    "gold",
    "hidden_test",
    "hidden_tests",
    "scorer_diagnosis",
    "official_diagnosis",
    "transfer",
    "reserve",
    "holdout",
    "guard",
}


def _required(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Exp2ContractError(f"{field_name} must not be empty")
    return text


def _sha256(value: object, field_name: str) -> str:
    text = _required(value, field_name)
    if not _SHA256.fullmatch(text):
        raise Exp2ContractError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _sha1(value: object, field_name: str) -> str:
    text = _required(value, field_name)
    if not _SHA1.fullmatch(text):
        raise Exp2ContractError(f"{field_name} must be a lowercase Git SHA")
    return text


def _safe(value: object, field_name: str) -> str:
    try:
        return safe_component(value, field_name)
    except BenchmarkContractError as exc:
        raise Exp2ContractError(str(exc)) from exc


def _absolute_path(value: object, field_name: str) -> str:
    text = _required(value, field_name)
    path = Path(text)
    if not path.is_absolute():
        raise Exp2ContractError(f"{field_name} must be an absolute path")
    return str(path.resolve())


def _tuple_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Exp2ContractError(f"{field_name} must be a list")
    result = tuple(_required(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise Exp2ContractError(f"{field_name} must contain unique values")
    return result


def _tuple_digests(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise Exp2ContractError(f"{field_name} must be a list")
    return tuple(_sha256(item, field_name) for item in value)


def _verify(data: Mapping[str, Any], schema: str, fields: set[str], label: str) -> None:
    try:
        verify_record(data)
    except BenchmarkContractError as exc:
        raise Exp2ContractError(str(exc)) from exc
    if data.get("schema") != schema:
        raise Exp2ContractError(f"unsupported {label} schema")
    observed = set(data)
    unknown = sorted(observed - fields)
    missing = sorted(fields - observed)
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unsupported fields: {', '.join(unknown)}")
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        raise Exp2ContractError(f"{label} has " + "; ".join(details))


def _digest_record(data: Mapping[str, Any], field_name: str) -> str:
    try:
        verify_record(data)
    except BenchmarkContractError as exc:
        raise Exp2ContractError(f"{field_name} is not an immutable record") from exc
    return _sha256(data.get("record_digest"), field_name)


def _read_immutable_record(path: Path, label: str) -> dict[str, Any]:
    source = path.resolve()
    if path.is_symlink() or not source.is_file():
        raise Exp2ResumeError(f"{label} is missing or redirected")
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise Exp2ResumeError(f"{label} is not a mapping")
    try:
        verify_record(raw)
    except BenchmarkContractError as exc:
        raise Exp2ResumeError(f"{label} digest is invalid") from exc
    return dict(raw)


@dataclass(frozen=True, slots=True)
class Exp2ResumeCase:
    """One frozen resume-MVP case.  The order is part of the protocol."""

    order: int
    case_id: str
    repository: str
    difficulty: str
    slice: Literal["calibration", "source", "transfer", "reserve"]

    def __post_init__(self) -> None:
        if self.order < 1:
            raise Exp2ContractError("Exp2 case order must be positive")
        _safe(self.case_id, "case_id")
        if not self.repository or "/" not in self.repository:
            raise Exp2ContractError("Exp2 case repository must be an owner/name value")
        if not self.difficulty.strip():
            raise Exp2ContractError("Exp2 case difficulty must not be empty")
        if self.slice not in {"calibration", "source", "transfer", "reserve"}:
            raise Exp2ContractError("unsupported Exp2 case slice")

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "case_id": self.case_id,
            "repository": self.repository,
            "difficulty": self.difficulty,
            "slice": self.slice,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2ResumeCase":
        expected = {"order", "case_id", "repository", "difficulty", "slice"}
        unknown = sorted(set(data) - expected)
        if unknown:
            raise Exp2ContractError(
                "Exp2 resume case contains unsupported fields: " + ", ".join(unknown)
            )
        return cls(
            order=int(data.get("order") or 0),
            case_id=str(data.get("case_id") or ""),
            repository=str(data.get("repository") or ""),
            difficulty=str(data.get("difficulty") or ""),
            slice=str(data.get("slice") or ""),  # type: ignore[arg-type]
        )


_CALIBRATION_CASES = (
    Exp2ResumeCase(1, "pallets__flask-5014", "pallets/flask", "<15 min", "calibration"),
    Exp2ResumeCase(2, "pylint-dev__pylint-4970", "pylint-dev/pylint", "<15 min", "calibration"),
)
_H0_CASES = (
    Exp2ResumeCase(1, "astropy__astropy-13398", "astropy/astropy", "1–4 hours", "source"),
    Exp2ResumeCase(2, "django__django-10097", "django/django", "<15 min", "source"),
    Exp2ResumeCase(3, "matplotlib__matplotlib-24627", "matplotlib/matplotlib", "15 min–1 hour", "transfer"),
    Exp2ResumeCase(4, "pydata__xarray-2905", "pydata/xarray", "15 min–1 hour", "transfer"),
    Exp2ResumeCase(5, "sympy__sympy-13091", "sympy/sympy", "15 min–1 hour", "transfer"),
    Exp2ResumeCase(6, "mwaskom__seaborn-3187", "mwaskom/seaborn", "15 min–1 hour", "reserve"),
    Exp2ResumeCase(7, "psf__requests-6028", "psf/requests", "15 min–1 hour", "reserve"),
    Exp2ResumeCase(8, "pytest-dev__pytest-10051", "pytest-dev/pytest", "15 min–1 hour", "reserve"),
    Exp2ResumeCase(9, "scikit-learn__scikit-learn-13439", "scikit-learn/scikit-learn", "<15 min", "reserve"),
    Exp2ResumeCase(10, "sphinx-doc__sphinx-9229", "sphinx-doc/sphinx", "1–4 hours", "reserve"),
)


@dataclass(frozen=True, slots=True)
class Exp2OciImageIdentity:
    """Resolved OCI identity; a tag alone is never a frozen execution input."""

    case_id: str
    image: str
    qualification_digest: str
    manifest_digest: str
    config_digest: str
    layer_digests: tuple[str, ...]
    platform: str = "linux/amd64"

    def __post_init__(self) -> None:
        _safe(self.case_id, "image case_id")
        _required(self.image, "OCI image")
        _sha256(self.qualification_digest, "OCI qualification_digest")
        _sha256(self.manifest_digest, "OCI manifest_digest")
        _sha256(self.config_digest, "OCI config_digest")
        if not self.layer_digests:
            raise Exp2ContractError("OCI image must bind at least one layer digest")
        for digest in self.layer_digests:
            _sha256(digest, "OCI layer_digests")
        if self.platform != "linux/amd64":
            raise Exp2ContractError("Exp2 OCI platform must be linux/amd64")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "image": self.image,
            "qualification_digest": self.qualification_digest,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "layer_digests": list(self.layer_digests),
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2OciImageIdentity":
        expected = {
            "case_id",
            "image",
            "qualification_digest",
            "manifest_digest",
            "config_digest",
            "layer_digests",
            "platform",
        }
        unknown = sorted(set(data) - expected)
        if unknown:
            raise Exp2ContractError(
                "OCI image identity contains unsupported fields: " + ", ".join(unknown)
            )
        return cls(
            case_id=str(data.get("case_id") or ""),
            image=str(data.get("image") or ""),
            qualification_digest=str(data.get("qualification_digest") or ""),
            manifest_digest=str(data.get("manifest_digest") or ""),
            config_digest=str(data.get("config_digest") or ""),
            layer_digests=_tuple_digests(data.get("layer_digests") or (), "layer_digests"),
            platform=str(data.get("platform") or ""),
        )


@dataclass(frozen=True, slots=True)
class Exp2ResumeProtocol:
    """The v2 frozen MVP protocol and exact 2 + 10 cohort.

    A protocol can be stored as ``pending_qualification`` to make the source
    contract reviewable without inventing OCI digests.  It cannot be executed
    until all twelve image identities have been resolved.
    """

    protocol_id: str
    dataset_revision: str
    scorer_digest: str
    runtime_digest: str
    memory_fixture_spec_digest: str
    memory_fixture_digest: str
    operator_policy_digest: str
    operator_role_skill_digest: str
    execution_role_skill_digest: str
    model: str
    reasoning_effort: str
    execution_mode: Literal["protected"]
    max_attempts: int
    timeout_seconds: int
    case_concurrency: int
    execution_allowlist: tuple[str, ...]
    calibration_cases: tuple[Exp2ResumeCase, ...] = _CALIBRATION_CASES
    h0_cases: tuple[Exp2ResumeCase, ...] = _H0_CASES
    oci_images: tuple[Exp2OciImageIdentity, ...] = ()
    qualification_status: Literal["pending_qualification", "qualified"] = "pending_qualification"
    one_revision_cap: int = 1
    schema_version: int = 2

    def __post_init__(self) -> None:
        _safe(self.protocol_id, "protocol_id")
        _sha1(self.dataset_revision, "dataset_revision")
        for field_name in (
            "scorer_digest",
            "runtime_digest",
            "memory_fixture_spec_digest",
            "memory_fixture_digest",
            "operator_policy_digest",
            "operator_role_skill_digest",
            "execution_role_skill_digest",
        ):
            _sha256(getattr(self, field_name), field_name)
        if self.schema_version != 2:
            raise Exp2ContractError("unsupported Exp2 resume protocol schema")
        if self.model != "gpt-5.4-mini" or self.reasoning_effort != "low":
            raise Exp2ContractError("Exp2 resume protocol model or reasoning drift")
        if self.execution_mode != "protected":
            raise Exp2ContractError(
                "formal Exp2 execution requires OS-enforced protected mode"
            )
        if self.max_attempts != 2 or self.timeout_seconds != 900 or self.case_concurrency != 1:
            raise Exp2ContractError("Exp2 resume protocol budget/timeout/concurrency drift")
        if self.one_revision_cap != 1:
            raise Exp2ContractError("Exp2 resume protocol permits exactly one revision")
        if not self.execution_allowlist:
            raise Exp2ContractError("Exp2 resume execution allowlist must not be empty")
        if any(Path(item).is_absolute() or ".." in Path(item).parts for item in self.execution_allowlist):
            raise Exp2ContractError("Exp2 execution allowlist contains an unsafe path")
        if tuple(self.calibration_cases) != _CALIBRATION_CASES:
            raise Exp2ContractError("Exp2 resume calibration IDs/order/slices are frozen")
        if tuple(self.h0_cases) != _H0_CASES:
            raise Exp2ContractError("Exp2 resume H0 IDs/order/slices are frozen")
        image_case_ids = tuple(item.case_id for item in self.oci_images)
        all_case_ids = tuple(item.case_id for item in (*self.calibration_cases, *self.h0_cases))
        if self.qualification_status == "qualified":
            if image_case_ids != all_case_ids:
                raise Exp2ContractError("qualified Exp2 protocol must bind every selected OCI image in case order")
        elif self.qualification_status == "pending_qualification":
            if self.oci_images:
                raise Exp2ContractError("pending Exp2 protocol cannot claim partially resolved OCI identities")
        else:
            raise Exp2ContractError("unsupported Exp2 qualification status")

    @property
    def execution_ready(self) -> bool:
        return self.qualification_status == "qualified"

    @property
    def source_cases(self) -> tuple[Exp2ResumeCase, ...]:
        return tuple(item for item in self.h0_cases if item.slice == "source")

    @property
    def transfer_cases(self) -> tuple[Exp2ResumeCase, ...]:
        return tuple(item for item in self.h0_cases if item.slice == "transfer")

    @property
    def reserve_cases(self) -> tuple[Exp2ResumeCase, ...]:
        return tuple(item for item in self.h0_cases if item.slice == "reserve")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-resume-protocol-v2",
                "schema_version": self.schema_version,
                "protocol_id": self.protocol_id,
                "dataset_revision": self.dataset_revision,
                "scorer_digest": self.scorer_digest,
                "runtime_digest": self.runtime_digest,
                "memory_fixture_spec_digest": self.memory_fixture_spec_digest,
                "memory_fixture_digest": self.memory_fixture_digest,
                "operator_policy_digest": self.operator_policy_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_role_skill_digest": self.execution_role_skill_digest,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "execution_mode": self.execution_mode,
                "max_attempts": self.max_attempts,
                "timeout_seconds": self.timeout_seconds,
                "case_concurrency": self.case_concurrency,
                "execution_allowlist": list(self.execution_allowlist),
                "calibration_cases": [item.to_dict() for item in self.calibration_cases],
                "h0_cases": [item.to_dict() for item in self.h0_cases],
                "oci_images": [item.to_dict() for item in self.oci_images],
                "qualification_status": self.qualification_status,
                "one_revision_cap": self.one_revision_cap,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2ResumeProtocol":
        fields = {
            "schema", "schema_version", "protocol_id", "dataset_revision", "scorer_digest",
            "runtime_digest", "memory_fixture_digest", "operator_policy_digest",
            "memory_fixture_spec_digest",
            "operator_role_skill_digest", "execution_role_skill_digest", "model",
            "reasoning_effort", "execution_mode", "max_attempts", "timeout_seconds", "case_concurrency",
            "execution_allowlist", "calibration_cases", "h0_cases", "oci_images",
            "qualification_status", "one_revision_cap", "record_digest",
        }
        _verify(data, "autobugfix-exp2-resume-protocol-v2", fields, "Exp2 resume protocol")
        raw_calibration = data.get("calibration_cases")
        raw_h0 = data.get("h0_cases")
        raw_images = data.get("oci_images")
        if not all(isinstance(value, Sequence) and not isinstance(value, (str, bytes)) for value in (raw_calibration, raw_h0, raw_images)):
            raise Exp2ContractError("Exp2 resume protocol case/image collections must be lists")
        if not all(isinstance(item, Mapping) for item in (*raw_calibration, *raw_h0, *raw_images)):
            raise Exp2ContractError("Exp2 resume protocol case/image entries must be mappings")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            protocol_id=str(data.get("protocol_id") or ""),
            dataset_revision=str(data.get("dataset_revision") or ""),
            scorer_digest=str(data.get("scorer_digest") or ""),
            runtime_digest=str(data.get("runtime_digest") or ""),
            memory_fixture_spec_digest=str(
                data.get("memory_fixture_spec_digest") or ""
            ),
            memory_fixture_digest=str(data.get("memory_fixture_digest") or ""),
            operator_policy_digest=str(data.get("operator_policy_digest") or ""),
            operator_role_skill_digest=str(data.get("operator_role_skill_digest") or ""),
            execution_role_skill_digest=str(data.get("execution_role_skill_digest") or ""),
            model=str(data.get("model") or ""),
            reasoning_effort=str(data.get("reasoning_effort") or ""),
            execution_mode=str(data.get("execution_mode") or ""),  # type: ignore[arg-type]
            max_attempts=int(data.get("max_attempts") or 0),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
            case_concurrency=int(data.get("case_concurrency") or 0),
            execution_allowlist=_tuple_strings(data.get("execution_allowlist") or (), "execution_allowlist"),
            calibration_cases=tuple(Exp2ResumeCase.from_dict(item) for item in raw_calibration),
            h0_cases=tuple(Exp2ResumeCase.from_dict(item) for item in raw_h0),
            oci_images=tuple(Exp2OciImageIdentity.from_dict(item) for item in raw_images),
            qualification_status=str(data.get("qualification_status") or ""),  # type: ignore[arg-type]
            one_revision_cap=int(data.get("one_revision_cap") or 0),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "Exp2ResumeProtocol":
        source = path.resolve()
        if path.is_symlink() or not source.is_file():
            raise Exp2ContractError("Exp2 resume protocol is missing or redirected")
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2ContractError("Exp2 resume protocol must be a mapping")
        return cls.from_dict(raw)


@dataclass(frozen=True, slots=True)
class Exp2ResumeStudyPlan:
    """Content-addressed v2 plan.  It intentionally has no mutable candidate path."""

    study_id: str
    study_kind: StudyKind
    protocol_path: str
    protocol_digest: str
    swe_protocol_path: str
    swe_protocol_sha256: str
    apparatus_sha: str
    apparatus_tree: str
    apparatus_receipt_path: str
    apparatus_receipt_digest: str
    h0_subject_sha: str
    h0_subject_tree: str
    h0_binding_digest: str
    scorer_digest: str
    runtime_digest: str
    memory_fixture_spec_path: str
    memory_fixture_digest: str
    operator_policy_digest: str
    operator_role_skill_digest: str
    execution_role_skill_digest: str
    selected_images_digest: str
    disposable_root: str
    artifact_root: str
    eval_root: str
    operator_root: str
    memory_root: str
    guard_root: str
    public_manifest_path: str | None = None
    public_manifest_digest: str | None = None
    h0_binding_path: str | None = None
    calibration_terminal_receipt_path: str | None = None
    calibration_terminal_receipt_digest: str | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        if self.study_kind not in {"calibration", "resume_pilot"}:
            raise Exp2ContractError("Exp2 v2 plan study_kind is invalid")
        if self.schema_version != 2:
            raise Exp2ContractError("unsupported Exp2 v2 plan schema")
        for field_name in (
            "protocol_digest", "swe_protocol_sha256", "apparatus_receipt_digest",
            "h0_binding_digest", "scorer_digest",
            "runtime_digest", "memory_fixture_digest", "operator_policy_digest",
            "operator_role_skill_digest", "execution_role_skill_digest", "selected_images_digest",
        ):
            _sha256(getattr(self, field_name), field_name)
        _sha1(self.apparatus_sha, "apparatus_sha")
        _sha1(self.apparatus_tree, "apparatus_tree")
        _sha1(self.h0_subject_sha, "h0_subject_sha")
        _sha1(self.h0_subject_tree, "h0_subject_tree")
        for field_name in (
            "protocol_path", "swe_protocol_path", "apparatus_receipt_path",
            "memory_fixture_spec_path", "disposable_root", "artifact_root",
            "eval_root", "operator_root", "memory_root", "guard_root",
        ):
            _absolute_path(getattr(self, field_name), field_name)
        roots = tuple(
            Path(getattr(self, field_name)).resolve()
            for field_name in (
                "disposable_root", "artifact_root", "eval_root",
                "operator_root", "memory_root", "guard_root",
            )
        )
        if len(set(roots)) != len(roots):
            raise Exp2ContractError("Exp2 v2 roots must be pairwise distinct")
        if self.study_kind == "calibration":
            if (
                self.public_manifest_path is not None
                or self.public_manifest_digest is not None
                or self.h0_binding_path is not None
                or
                self.calibration_terminal_receipt_path is not None
                or self.calibration_terminal_receipt_digest is not None
            ):
                raise Exp2ContractError(
                    "calibration plan cannot consume formal-pilot inputs"
                )
        elif (
            self.public_manifest_path is None
            or self.public_manifest_digest is None
            or self.h0_binding_path is None
            or
            self.calibration_terminal_receipt_path is None
            or self.calibration_terminal_receipt_digest is None
        ):
            raise Exp2ContractError(
                "resume-pilot plan requires formal Eval inputs and a calibration terminal receipt"
            )
        else:
            _absolute_path(self.public_manifest_path, "public_manifest_path")
            _absolute_path(self.h0_binding_path, "h0_binding_path")
            _sha256(self.public_manifest_digest, "public_manifest_digest")
            _absolute_path(
                self.calibration_terminal_receipt_path,
                "calibration_terminal_receipt_path",
            )
            _sha256(self.calibration_terminal_receipt_digest, "calibration_terminal_receipt_digest")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-resume-study-plan-v2",
                "schema_version": self.schema_version,
                "study_id": self.study_id,
                "study_kind": self.study_kind,
                "protocol_path": self.protocol_path,
                "protocol_digest": self.protocol_digest,
                "swe_protocol_path": self.swe_protocol_path,
                "swe_protocol_sha256": self.swe_protocol_sha256,
                "apparatus_sha": self.apparatus_sha,
                "apparatus_tree": self.apparatus_tree,
                "apparatus_receipt_path": self.apparatus_receipt_path,
                "apparatus_receipt_digest": self.apparatus_receipt_digest,
                "h0_subject_sha": self.h0_subject_sha,
                "h0_subject_tree": self.h0_subject_tree,
                "h0_binding_digest": self.h0_binding_digest,
                "scorer_digest": self.scorer_digest,
                "runtime_digest": self.runtime_digest,
                "memory_fixture_spec_path": self.memory_fixture_spec_path,
                "memory_fixture_digest": self.memory_fixture_digest,
                "operator_policy_digest": self.operator_policy_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_role_skill_digest": self.execution_role_skill_digest,
                "selected_images_digest": self.selected_images_digest,
                "disposable_root": self.disposable_root,
                "artifact_root": self.artifact_root,
                "eval_root": self.eval_root,
                "operator_root": self.operator_root,
                "memory_root": self.memory_root,
                "guard_root": self.guard_root,
                "public_manifest_path": self.public_manifest_path,
                "public_manifest_digest": self.public_manifest_digest,
                "h0_binding_path": self.h0_binding_path,
                "calibration_terminal_receipt_path": self.calibration_terminal_receipt_path,
                "calibration_terminal_receipt_digest": self.calibration_terminal_receipt_digest,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2ResumeStudyPlan":
        fields = {
            "schema", "schema_version", "study_id", "study_kind", "protocol_path", "protocol_digest",
            "swe_protocol_path", "swe_protocol_sha256",
            "apparatus_sha", "apparatus_tree", "apparatus_receipt_path",
            "apparatus_receipt_digest", "h0_subject_sha",
            "h0_subject_tree", "h0_binding_digest", "scorer_digest", "runtime_digest",
            "memory_fixture_spec_path",
            "memory_fixture_digest", "operator_policy_digest", "operator_role_skill_digest",
            "execution_role_skill_digest", "selected_images_digest", "disposable_root", "artifact_root",
            "eval_root", "operator_root", "memory_root", "guard_root",
            "public_manifest_path", "public_manifest_digest", "h0_binding_path",
            "calibration_terminal_receipt_path",
            "calibration_terminal_receipt_digest", "record_digest",
        }
        _verify(data, "autobugfix-exp2-resume-study-plan-v2", fields, "Exp2 v2 study plan")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            study_id=str(data.get("study_id") or ""),
            study_kind=str(data.get("study_kind") or ""),  # type: ignore[arg-type]
            protocol_path=str(data.get("protocol_path") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            swe_protocol_path=str(data.get("swe_protocol_path") or ""),
            swe_protocol_sha256=str(data.get("swe_protocol_sha256") or ""),
            apparatus_sha=str(data.get("apparatus_sha") or ""),
            apparatus_tree=str(data.get("apparatus_tree") or ""),
            apparatus_receipt_path=str(
                data.get("apparatus_receipt_path") or ""
            ),
            apparatus_receipt_digest=str(data.get("apparatus_receipt_digest") or ""),
            h0_subject_sha=str(data.get("h0_subject_sha") or ""),
            h0_subject_tree=str(data.get("h0_subject_tree") or ""),
            h0_binding_digest=str(data.get("h0_binding_digest") or ""),
            scorer_digest=str(data.get("scorer_digest") or ""),
            runtime_digest=str(data.get("runtime_digest") or ""),
            memory_fixture_spec_path=str(
                data.get("memory_fixture_spec_path") or ""
            ),
            memory_fixture_digest=str(data.get("memory_fixture_digest") or ""),
            operator_policy_digest=str(data.get("operator_policy_digest") or ""),
            operator_role_skill_digest=str(data.get("operator_role_skill_digest") or ""),
            execution_role_skill_digest=str(data.get("execution_role_skill_digest") or ""),
            selected_images_digest=str(data.get("selected_images_digest") or ""),
            disposable_root=str(data.get("disposable_root") or ""),
            artifact_root=str(data.get("artifact_root") or ""),
            eval_root=str(data.get("eval_root") or ""),
            operator_root=str(data.get("operator_root") or ""),
            memory_root=str(data.get("memory_root") or ""),
            guard_root=str(data.get("guard_root") or ""),
            public_manifest_path=(
                str(data["public_manifest_path"])
                if data.get("public_manifest_path") is not None
                else None
            ),
            public_manifest_digest=(
                str(data["public_manifest_digest"])
                if data.get("public_manifest_digest") is not None
                else None
            ),
            h0_binding_path=(
                str(data["h0_binding_path"])
                if data.get("h0_binding_path") is not None
                else None
            ),
            calibration_terminal_receipt_path=(
                str(data["calibration_terminal_receipt_path"])
                if data.get("calibration_terminal_receipt_path") is not None
                else None
            ),
            calibration_terminal_receipt_digest=(
                str(data["calibration_terminal_receipt_digest"])
                if data.get("calibration_terminal_receipt_digest") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Exp2CaseAttemptIntent:
    study_id: str
    study_kind: StudyKind
    stage: ResumeStage
    arm: ResumeArm
    case_id: str
    slice: str
    run_id: str
    output_root: str
    subject_sha: str
    subject_tree: str
    frozen_input_digest: str
    binding_digest: str
    attempt_kind: AttemptKind
    retry_of_receipt_digest: str | None = None
    frozen_submission_digest: str | None = None
    retry_source_output_root: str | None = None
    predecessor_event_digest: str | None = None
    started_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        _safe(self.case_id, "case_id")
        _safe(self.run_id, "run_id")
        if self.study_kind not in {"calibration", "resume_pilot"}:
            raise Exp2ContractError("case intent study_kind is invalid")
        if self.stage not in {"CALIBRATION", "H0", "H1_SOURCE", "H1_TRANSFER"}:
            raise Exp2ContractError("case intent stage is invalid")
        if self.arm not in {"H0", "H1"}:
            raise Exp2ContractError("case intent arm is invalid")
        if self.attempt_kind not in {"execution", "scorer_only_retry"}:
            raise Exp2ContractError("case intent kind is invalid")
        if self.attempt_kind == "execution":
            if (
                self.retry_of_receipt_digest is not None
                or self.frozen_submission_digest is not None
                or self.retry_source_output_root is not None
            ):
                raise Exp2ContractError("normal execution intent cannot bind a scorer retry")
        elif (
            self.retry_of_receipt_digest is None
            or self.frozen_submission_digest is None
            or self.retry_source_output_root is None
        ):
            raise Exp2ContractError(
                "scorer-only retry must bind its prior receipt and frozen submission"
            )
        else:
            _sha256(self.retry_of_receipt_digest, "retry_of_receipt_digest")
            _sha256(self.frozen_submission_digest, "frozen_submission_digest")
            _absolute_path(
                self.retry_source_output_root,
                "retry_source_output_root",
            )
        if (self.stage in {"CALIBRATION", "H0"}) != (self.arm == "H0"):
            raise Exp2ContractError("case intent arm disagrees with stage")
        if self.stage in {"H1_SOURCE", "H1_TRANSFER"} and self.arm != "H1":
            raise Exp2ContractError("case intent arm disagrees with H1 stage")
        _absolute_path(self.output_root, "output_root")
        _sha1(self.subject_sha, "subject_sha")
        _sha1(self.subject_tree, "subject_tree")
        _sha256(self.frozen_input_digest, "frozen_input_digest")
        _sha256(self.binding_digest, "binding_digest")
        if self.predecessor_event_digest is not None:
            _sha256(self.predecessor_event_digest, "predecessor_event_digest")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-case-attempt-started-v2",
                "study_id": self.study_id,
                "study_kind": self.study_kind,
                "stage": self.stage,
                "arm": self.arm,
                "case_id": self.case_id,
                "slice": self.slice,
                "run_id": self.run_id,
                "output_root": self.output_root,
                "subject_sha": self.subject_sha,
                "subject_tree": self.subject_tree,
                "frozen_input_digest": self.frozen_input_digest,
                "binding_digest": self.binding_digest,
                "attempt_kind": self.attempt_kind,
                "retry_of_receipt_digest": self.retry_of_receipt_digest,
                "frozen_submission_digest": self.frozen_submission_digest,
                "retry_source_output_root": self.retry_source_output_root,
                "predecessor_event_digest": self.predecessor_event_digest,
                "started_at": self.started_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2CaseAttemptIntent":
        fields = {
            "schema", "study_id", "study_kind", "stage", "arm", "case_id", "slice", "run_id",
            "output_root", "subject_sha", "subject_tree", "frozen_input_digest", "binding_digest",
            "attempt_kind", "retry_of_receipt_digest", "frozen_submission_digest",
            "retry_source_output_root",
            "predecessor_event_digest", "started_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-case-attempt-started-v2", fields, "Exp2 case attempt intent")
        return cls(
            study_id=str(data.get("study_id") or ""),
            study_kind=str(data.get("study_kind") or ""),  # type: ignore[arg-type]
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            case_id=str(data.get("case_id") or ""),
            slice=str(data.get("slice") or ""),
            run_id=str(data.get("run_id") or ""),
            output_root=str(data.get("output_root") or ""),
            subject_sha=str(data.get("subject_sha") or ""),
            subject_tree=str(data.get("subject_tree") or ""),
            frozen_input_digest=str(data.get("frozen_input_digest") or ""),
            binding_digest=str(data.get("binding_digest") or ""),
            attempt_kind=str(data.get("attempt_kind") or ""),  # type: ignore[arg-type]
            retry_of_receipt_digest=(
                str(data["retry_of_receipt_digest"])
                if data.get("retry_of_receipt_digest") is not None
                else None
            ),
            frozen_submission_digest=(
                str(data["frozen_submission_digest"])
                if data.get("frozen_submission_digest") is not None
                else None
            ),
            retry_source_output_root=(
                str(data["retry_source_output_root"])
                if data.get("retry_source_output_root") is not None
                else None
            ),
            predecessor_event_digest=(
                str(data["predecessor_event_digest"])
                if data.get("predecessor_event_digest") is not None
                else None
            ),
            started_at=str(data.get("started_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2CaseAttemptReceipt:
    """A terminal receipt for one attempt; scorer retries reuse its submission."""

    study_id: str
    issuer: Literal["eval-benchmark-service-v2"]
    stage: ResumeStage
    arm: ResumeArm
    case_id: str
    slice: str
    started_event_digest: str
    run_id: str
    attempt_kind: AttemptKind
    terminal_status: CaseTerminalStatus
    subject_sha: str
    subject_tree: str
    frozen_input_digest: str
    binding_digest: str
    execution_mode: Literal["protected", "workspace_only"]
    sdk_call_occurred: bool
    report_digest: str | None = None
    failure_artifact_digest: str | None = None
    submission_digest: str | None = None
    official_result_digest: str | None = None
    noninterference_digest: str | None = None
    execution_receipt_digest: str | None = None
    workspace_only_preflight_digest: str | None = None
    image_digest: str | None = None
    runtime_digest: str | None = None
    memory_digest: str | None = None
    usage_digest: str | None = None
    model_calls: int = 0
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    model_time_seconds: float | None = None
    first_verifier_outcome: str | None = None
    loop_rescue: bool | None = None
    changed_files: int | None = None
    changed_lines: int | None = None
    empty_patch: bool | None = None
    resolved: bool | None = None
    failure_stage: Literal["execution", "visible_verifier", "official_eval", "infrastructure", "unknown"] = "unknown"
    writer_attempts: int = 0
    frozen_submission: bool = False
    scorer_retry_legal: bool = False
    terminal_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        if self.issuer != "eval-benchmark-service-v2":
            raise Exp2ContractError("case terminal receipt issuer is invalid")
        _safe(self.case_id, "case_id")
        _safe(self.run_id, "run_id")
        _sha256(self.started_event_digest, "started_event_digest")
        _sha1(self.subject_sha, "subject_sha")
        _sha1(self.subject_tree, "subject_tree")
        _sha256(self.frozen_input_digest, "frozen_input_digest")
        _sha256(self.binding_digest, "binding_digest")
        if self.execution_mode not in {"protected", "workspace_only"}:
            raise Exp2ContractError("case receipt execution mode is invalid")
        if self.terminal_status not in _TERMINAL_STATUSES:
            raise Exp2ContractError("unsupported Exp2 terminal case status")
        if self.attempt_kind not in {"execution", "scorer_only_retry"}:
            raise Exp2ContractError("case terminal attempt kind is invalid")
        if type(self.sdk_call_occurred) is not bool:
            raise Exp2ContractError("sdk_call_occurred must be boolean")
        if self.writer_attempts < 0 or self.writer_attempts > 2:
            raise Exp2ContractError("writer_attempts must be in the frozen two-attempt budget")
        if self.model_calls < 0:
            raise Exp2ContractError("model_calls must not be negative")
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise Exp2ContractError(f"{field_name} must not be negative")
        if self.model_time_seconds is not None and self.model_time_seconds < 0:
            raise Exp2ContractError("model_time_seconds must not be negative")
        for field_name in ("changed_files", "changed_lines"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise Exp2ContractError(f"{field_name} must not be negative")
        for field_name in ("loop_rescue", "empty_patch"):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise Exp2ContractError(f"{field_name} must be boolean or null")
        if self.failure_stage not in {"execution", "visible_verifier", "official_eval", "infrastructure", "unknown"}:
            raise Exp2ContractError("unsupported case failure stage")
        for field_name in (
            "report_digest", "failure_artifact_digest", "submission_digest", "official_result_digest",
            "noninterference_digest", "execution_receipt_digest", "workspace_only_preflight_digest",
            "image_digest", "runtime_digest", "usage_digest",
            "memory_digest",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _sha256(value, field_name)
        if self.terminal_status == "official_terminal":
            required = (
                self.report_digest, self.submission_digest, self.official_result_digest,
                self.noninterference_digest, self.execution_receipt_digest,
                self.image_digest, self.runtime_digest, self.memory_digest,
                self.usage_digest,
            )
            if any(value is None for value in required) or type(self.resolved) is not bool:
                raise Exp2ContractError("official terminal receipt is incomplete")
            if (
                self.execution_mode == "workspace_only"
                and self.workspace_only_preflight_digest is None
            ):
                raise Exp2ContractError(
                    "workspace-only official receipt lacks preflight evidence"
                )
            if (
                self.execution_mode == "protected"
                and self.workspace_only_preflight_digest is not None
            ):
                raise Exp2ContractError(
                    "protected official receipt cannot claim workspace-only preflight"
                )
            expected_sdk_call = self.attempt_kind == "execution"
            if (
                self.sdk_call_occurred is not expected_sdk_call
                or not self.frozen_submission
                or self.scorer_retry_legal
            ):
                raise Exp2ContractError("official terminal receipt execution facts are invalid")
            if self.model_calls < (1 if expected_sdk_call else 0):
                raise Exp2ContractError(
                    "official terminal receipt model-call count is invalid"
                )
        elif self.terminal_status == "preflight_rejected":
            if self.failure_artifact_digest is None:
                raise Exp2ContractError(
                    "rejected preflight requires a trusted failure artifact"
                )
            if self.sdk_call_occurred or self.resolved is not None or self.frozen_submission or self.scorer_retry_legal:
                raise Exp2ContractError("rejected preflight must terminate before SDK execution")
        elif self.terminal_status == "execution_infrastructure_invalid":
            if self.failure_artifact_digest is None:
                raise Exp2ContractError(
                    "execution infrastructure receipt requires failure evidence"
                )
            if self.resolved is not None or self.scorer_retry_legal:
                raise Exp2ContractError("execution infrastructure receipt cannot claim result/retry")
        elif self.terminal_status == "scorer_infrastructure_invalid":
            if self.failure_artifact_digest is None:
                raise Exp2ContractError(
                    "scorer infrastructure receipt requires failure evidence"
                )
            expected_sdk_call = self.attempt_kind == "execution"
            if (
                self.sdk_call_occurred is not expected_sdk_call
                or not self.frozen_submission
                or not self.submission_digest
            ):
                raise Exp2ContractError("scorer infrastructure receipt requires a frozen submission")
            expected_retry_legal = self.attempt_kind == "execution"
            if (
                self.resolved is not None
                or self.scorer_retry_legal is not expected_retry_legal
            ):
                raise Exp2ContractError(
                    "only the first scorer failure may permit one scorer-only retry"
                )
        if self.attempt_kind == "scorer_only_retry" and self.writer_attempts != 0:
            raise Exp2ContractError("scorer-only retry must not start another Writer attempt")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-case-attempt-receipt-v2",
                "study_id": self.study_id,
                "issuer": self.issuer,
                "stage": self.stage,
                "arm": self.arm,
                "case_id": self.case_id,
                "slice": self.slice,
                "started_event_digest": self.started_event_digest,
                "run_id": self.run_id,
                "attempt_kind": self.attempt_kind,
                "terminal_status": self.terminal_status,
                "subject_sha": self.subject_sha,
                "subject_tree": self.subject_tree,
                "frozen_input_digest": self.frozen_input_digest,
                "binding_digest": self.binding_digest,
                "execution_mode": self.execution_mode,
                "sdk_call_occurred": self.sdk_call_occurred,
                "report_digest": self.report_digest,
                "failure_artifact_digest": self.failure_artifact_digest,
                "submission_digest": self.submission_digest,
                "official_result_digest": self.official_result_digest,
                "noninterference_digest": self.noninterference_digest,
                "execution_receipt_digest": self.execution_receipt_digest,
                "workspace_only_preflight_digest": self.workspace_only_preflight_digest,
                "image_digest": self.image_digest,
                "runtime_digest": self.runtime_digest,
                "memory_digest": self.memory_digest,
                "usage_digest": self.usage_digest,
                "model_calls": self.model_calls,
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "model_time_seconds": self.model_time_seconds,
                "first_verifier_outcome": self.first_verifier_outcome,
                "loop_rescue": self.loop_rescue,
                "changed_files": self.changed_files,
                "changed_lines": self.changed_lines,
                "empty_patch": self.empty_patch,
                "resolved": self.resolved,
                "failure_stage": self.failure_stage,
                "writer_attempts": self.writer_attempts,
                "frozen_submission": self.frozen_submission,
                "scorer_retry_legal": self.scorer_retry_legal,
                "terminal_at": self.terminal_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2CaseAttemptReceipt":
        fields = {
            "schema", "study_id", "issuer", "stage", "arm", "case_id", "slice", "started_event_digest",
            "run_id", "attempt_kind", "terminal_status", "subject_sha", "subject_tree",
            "frozen_input_digest", "binding_digest", "execution_mode", "sdk_call_occurred", "report_digest",
            "failure_artifact_digest", "submission_digest", "official_result_digest",
            "noninterference_digest", "execution_receipt_digest", "workspace_only_preflight_digest",
            "image_digest", "runtime_digest", "memory_digest", "usage_digest", "model_calls",
            "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_tokens", "model_time_seconds", "resolved", "failure_stage",
            "first_verifier_outcome", "loop_rescue", "changed_files",
            "changed_lines", "empty_patch",
            "writer_attempts", "frozen_submission", "scorer_retry_legal", "terminal_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-case-attempt-receipt-v2", fields, "Exp2 case attempt receipt")
        for field_name in ("sdk_call_occurred", "frozen_submission", "scorer_retry_legal"):
            if type(data.get(field_name)) is not bool:
                raise Exp2ContractError(f"{field_name} must be boolean")
        if data.get("resolved") is not None and type(data.get("resolved")) is not bool:
            raise Exp2ContractError("resolved must be boolean or null")
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            if data.get(field_name) is not None and type(data.get(field_name)) is not int:
                raise Exp2ContractError(f"{field_name} must be an integer or null")
        return cls(
            study_id=str(data.get("study_id") or ""),
            issuer=str(data.get("issuer") or ""),  # type: ignore[arg-type]
            stage=str(data.get("stage") or ""),  # type: ignore[arg-type]
            arm=str(data.get("arm") or ""),  # type: ignore[arg-type]
            case_id=str(data.get("case_id") or ""),
            slice=str(data.get("slice") or ""),
            started_event_digest=str(data.get("started_event_digest") or ""),
            run_id=str(data.get("run_id") or ""),
            attempt_kind=str(data.get("attempt_kind") or ""),  # type: ignore[arg-type]
            terminal_status=str(data.get("terminal_status") or ""),  # type: ignore[arg-type]
            subject_sha=str(data.get("subject_sha") or ""),
            subject_tree=str(data.get("subject_tree") or ""),
            frozen_input_digest=str(data.get("frozen_input_digest") or ""),
            binding_digest=str(data.get("binding_digest") or ""),
            execution_mode=str(data.get("execution_mode") or ""),  # type: ignore[arg-type]
            sdk_call_occurred=data["sdk_call_occurred"],
            report_digest=data.get("report_digest"),
            failure_artifact_digest=data.get("failure_artifact_digest"),
            submission_digest=data.get("submission_digest"),
            official_result_digest=data.get("official_result_digest"),
            noninterference_digest=data.get("noninterference_digest"),
            execution_receipt_digest=data.get("execution_receipt_digest"),
            workspace_only_preflight_digest=data.get("workspace_only_preflight_digest"),
            image_digest=data.get("image_digest"),
            runtime_digest=data.get("runtime_digest"),
            memory_digest=data.get("memory_digest"),
            usage_digest=data.get("usage_digest"),
            model_calls=int(data.get("model_calls") or 0),
            input_tokens=data.get("input_tokens"),
            cached_input_tokens=data.get("cached_input_tokens"),
            output_tokens=data.get("output_tokens"),
            reasoning_tokens=data.get("reasoning_tokens"),
            model_time_seconds=(
                float(data["model_time_seconds"])
                if data.get("model_time_seconds") is not None
                else None
            ),
            first_verifier_outcome=data.get("first_verifier_outcome"),
            loop_rescue=data.get("loop_rescue"),
            changed_files=data.get("changed_files"),
            changed_lines=data.get("changed_lines"),
            empty_patch=data.get("empty_patch"),
            resolved=data.get("resolved"),
            failure_stage=str(data.get("failure_stage") or ""),  # type: ignore[arg-type]
            writer_attempts=int(data.get("writer_attempts") or 0),
            frozen_submission=data["frozen_submission"],
            scorer_retry_legal=data["scorer_retry_legal"],
            terminal_at=str(data.get("terminal_at") or utc_now()),
        )

    @classmethod
    def from_official_report(
        cls,
        intent: Exp2CaseAttemptIntent,
        report: Mapping[str, Any],
        *,
        writer_attempts: int = 1,
        failure_stage: Literal["execution", "visible_verifier", "official_eval", "infrastructure", "unknown"] = "unknown",
        image_digest: str | None = None,
        runtime_digest: str | None = None,
        memory_digest: str | None = None,
        usage_digest: str | None = None,
        usage_summary: Mapping[str, Any] | None = None,
        execution_summary: Mapping[str, Any] | None = None,
        patch_summary: Mapping[str, Any] | None = None,
    ) -> "Exp2CaseAttemptReceipt":
        """Normalize a frozen Eval report without exposing its private contents."""

        _digest_record(report, "official report")
        official = report.get("official_result")
        noninterference = report.get("noninterference")
        execution = report.get("execution_receipt")
        if not isinstance(official, Mapping) or not isinstance(noninterference, Mapping) or not isinstance(execution, Mapping):
            raise Exp2ContractError("official report lacks trusted result/noninterference/execution receipts")
        official_digest = _digest_record(official, "official_result")
        noninterference_digest = _digest_record(noninterference, "noninterference")
        execution_digest = _digest_record(execution, "execution_receipt")
        if official.get("schema") != "autobugfix-swe-official-result-v1":
            raise Exp2ContractError("official report has an unsupported scorer result")
        if noninterference.get("unchanged") is not True:
            raise Exp2ContractError("official report noninterference receipt is invalid")
        execution_mode = str(execution.get("execution_mode") or "")
        if execution_mode == "protected":
            if (
                execution.get("direct_sdk_in_process") is not False
                or execution.get("outer_bubblewrap") is not True
            ):
                raise Exp2ContractError(
                    "protected v2 report lacks OS-enforced process isolation"
                )
            preflight_digest = None
        elif execution_mode == "workspace_only":
            if (
                execution.get("direct_sdk_in_process") is not True
                or execution.get("outer_bubblewrap") is not False
            ):
                raise Exp2ContractError(
                    "workspace-only v2 execution facts are invalid"
                )
            preflight_digest = _sha256(
                execution.get("workspace_only_preflight_digest"),
                "workspace_only_preflight_digest",
            )
        else:
            raise Exp2ContractError("v2 execution report mode is invalid")
        observed_image_digest = image_digest or report.get("image_digest")
        observed_runtime_digest = runtime_digest or report.get(
            "subject_runtime_digest"
        )
        observed_memory_digest = memory_digest or report.get("memory_digest")
        observed_usage_digest = usage_digest or execution.get(
            "execution_ledger_digest"
        )
        summary = dict(usage_summary or {})
        execution_facts = dict(execution_summary or {})
        patch_facts = dict(patch_summary or {})
        submission_digest = _sha256(report.get("submission_digest"), "submission_digest")
        if noninterference.get("submission_digest") != submission_digest or noninterference.get("official_result_digest") != official_digest:
            raise Exp2ContractError("official report cross-receipt bindings drift")
        if _safe(official.get("instance_id"), "official instance_id") != intent.case_id:
            raise Exp2ContractError("official report case differs from case attempt intent")
        if _sha1(report.get("executed_subject_sha"), "executed_subject_sha") != intent.subject_sha:
            raise Exp2ContractError("official report subject differs from case attempt intent")
        resolved = official.get("resolved")
        if type(resolved) is not bool:
            raise Exp2ContractError("official report resolved must be boolean")
        return cls(
            study_id=intent.study_id,
            issuer="eval-benchmark-service-v2",
            stage=intent.stage,
            arm=intent.arm,
            case_id=intent.case_id,
            slice=intent.slice,
            started_event_digest=intent.record_digest,
            run_id=intent.run_id,
            attempt_kind=intent.attempt_kind,
            terminal_status="official_terminal",
            subject_sha=intent.subject_sha,
            subject_tree=intent.subject_tree,
            frozen_input_digest=intent.frozen_input_digest,
            binding_digest=intent.binding_digest,
            execution_mode=execution_mode,  # type: ignore[arg-type]
            sdk_call_occurred=intent.attempt_kind == "execution",
            report_digest=_sha256(report.get("record_digest"), "report_digest"),
            submission_digest=submission_digest,
            official_result_digest=official_digest,
            noninterference_digest=noninterference_digest,
            execution_receipt_digest=execution_digest,
            workspace_only_preflight_digest=preflight_digest,
            image_digest=_sha256(observed_image_digest, "image_digest"),
            runtime_digest=_sha256(observed_runtime_digest, "runtime_digest"),
            memory_digest=_sha256(observed_memory_digest, "memory_digest"),
            usage_digest=_sha256(observed_usage_digest, "usage_digest"),
            model_calls=int(
                summary["model_calls"]
                if summary.get("model_calls") is not None
                else (0 if intent.attempt_kind == "scorer_only_retry" else max(1, writer_attempts))
            ),
            input_tokens=summary.get("input_tokens"),
            cached_input_tokens=summary.get("cached_input_tokens"),
            output_tokens=summary.get("output_tokens"),
            reasoning_tokens=summary.get("reasoning_tokens"),
            model_time_seconds=summary.get("model_time_seconds"),
            first_verifier_outcome=execution_facts.get(
                "first_verifier_outcome"
            ),
            loop_rescue=execution_facts.get("loop_rescue"),
            changed_files=patch_facts.get("changed_files"),
            changed_lines=patch_facts.get("changed_lines"),
            empty_patch=patch_facts.get("empty_patch"),
            resolved=resolved,
            failure_stage=failure_stage,
            writer_attempts=writer_attempts,
            frozen_submission=True,
        )


@dataclass(frozen=True, slots=True)
class Exp2CalibrationTerminalReceipt:
    study_id: str
    plan_digest: str
    protocol_digest: str
    apparatus_receipt_digest: str
    scorer_digest: str
    runtime_digest: str
    memory_fixture_digest: str
    case_receipt_digests: tuple[str, ...]
    status: Literal["CALIBRATION_COMPLETE", "CALIBRATION_BLOCKED"]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        for field_name in (
            "plan_digest", "protocol_digest", "apparatus_receipt_digest", "scorer_digest",
            "runtime_digest", "memory_fixture_digest",
        ):
            _sha256(getattr(self, field_name), field_name)
        if len(self.case_receipt_digests) != 2:
            raise Exp2ContractError("calibration terminal receipt requires two case receipts")
        for digest in self.case_receipt_digests:
            _sha256(digest, "calibration case receipt")
        if self.status not in {"CALIBRATION_COMPLETE", "CALIBRATION_BLOCKED"}:
            raise Exp2ContractError("invalid calibration terminal status")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-calibration-terminal-receipt-v2",
                "study_id": self.study_id,
                "plan_digest": self.plan_digest,
                "protocol_digest": self.protocol_digest,
                "apparatus_receipt_digest": self.apparatus_receipt_digest,
                "scorer_digest": self.scorer_digest,
                "runtime_digest": self.runtime_digest,
                "memory_fixture_digest": self.memory_fixture_digest,
                "case_receipt_digests": list(self.case_receipt_digests),
                "status": self.status,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2CalibrationTerminalReceipt":
        fields = {
            "schema", "study_id", "plan_digest", "protocol_digest", "apparatus_receipt_digest",
            "scorer_digest", "runtime_digest", "memory_fixture_digest", "case_receipt_digests",
            "status", "created_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-calibration-terminal-receipt-v2", fields, "calibration terminal receipt")
        return cls(
            study_id=str(data.get("study_id") or ""),
            plan_digest=str(data.get("plan_digest") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            apparatus_receipt_digest=str(data.get("apparatus_receipt_digest") or ""),
            scorer_digest=str(data.get("scorer_digest") or ""),
            runtime_digest=str(data.get("runtime_digest") or ""),
            memory_fixture_digest=str(data.get("memory_fixture_digest") or ""),
            case_receipt_digests=_tuple_digests(data.get("case_receipt_digests") or (), "case_receipt_digests"),
            status=str(data.get("status") or ""),  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2PilotTerminalReceipt:
    study_id: str
    plan_digest: str
    protocol_digest: str
    reason: Literal[
        "h0_invalid",
        "h0_floor",
        "h0_saturation",
        "no_legal_adaptation_signal",
    ]
    decision: Literal["blocked_invalid", "no_signal"]
    h0_case_receipt_digests: tuple[str, ...]
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        _sha256(self.plan_digest, "plan_digest")
        _sha256(self.protocol_digest, "protocol_digest")
        if self.reason not in {
            "h0_invalid",
            "h0_floor",
            "h0_saturation",
            "no_legal_adaptation_signal",
        }:
            raise Exp2ContractError("invalid Exp2 pilot terminal reason")
        expected_decision = (
            "blocked_invalid" if self.reason == "h0_invalid" else "no_signal"
        )
        if self.decision != expected_decision:
            raise Exp2ContractError(
                "Exp2 pilot terminal decision differs from its reason"
            )
        if len(self.h0_case_receipt_digests) != 10:
            raise Exp2ContractError(
                "Exp2 pilot terminal receipt requires ten H0 case receipts"
            )
        for digest in self.h0_case_receipt_digests:
            _sha256(digest, "h0_case_receipt_digest")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-pilot-terminal-receipt-v2",
                "study_id": self.study_id,
                "plan_digest": self.plan_digest,
                "protocol_digest": self.protocol_digest,
                "reason": self.reason,
                "decision": self.decision,
                "h0_case_receipt_digests": list(
                    self.h0_case_receipt_digests
                ),
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2PilotTerminalReceipt":
        fields = {
            "schema",
            "study_id",
            "plan_digest",
            "protocol_digest",
            "reason",
            "decision",
            "h0_case_receipt_digests",
            "created_at",
            "record_digest",
        }
        _verify(
            data,
            "autobugfix-exp2-pilot-terminal-receipt-v2",
            fields,
            "Exp2 pilot terminal receipt",
        )
        return cls(
            study_id=str(data.get("study_id") or ""),
            plan_digest=str(data.get("plan_digest") or ""),
            protocol_digest=str(data.get("protocol_digest") or ""),
            reason=str(data.get("reason") or ""),  # type: ignore[arg-type]
            decision=str(data.get("decision") or ""),  # type: ignore[arg-type]
            h0_case_receipt_digests=_tuple_digests(
                data.get("h0_case_receipt_digests") or (),
                "h0_case_receipt_digests",
            ),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2SourceProjection:
    case_id: str
    receipt_digest: str
    terminal_label: Literal["resolved", "unresolved"]
    failure_stage: str

    def __post_init__(self) -> None:
        _safe(self.case_id, "source projection case_id")
        _sha256(self.receipt_digest, "source projection receipt_digest")
        if self.terminal_label not in {"resolved", "unresolved"}:
            raise Exp2ContractError("source projection terminal label is invalid")
        if self.failure_stage not in {"execution", "visible_verifier", "official_eval", "infrastructure", "unknown"}:
            raise Exp2ContractError("source projection failure stage is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "receipt_digest": self.receipt_digest,
            "terminal_label": self.terminal_label,
            "failure_stage": self.failure_stage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2SourceProjection":
        fields = {"case_id", "receipt_digest", "terminal_label", "failure_stage"}
        if set(data) != fields:
            raise Exp2ContractError("source projection fields are invalid")
        if any(key in _FORBIDDEN_PROJECTION_KEYS for key in data):
            raise Exp2ContractError("source projection contains a private field")
        return cls(
            case_id=str(data.get("case_id") or ""),
            receipt_digest=str(data.get("receipt_digest") or ""),
            terminal_label=str(data.get("terminal_label") or ""),  # type: ignore[arg-type]
            failure_stage=str(data.get("failure_stage") or ""),
        )


@dataclass(frozen=True, slots=True)
class Exp2SourceProjectionBundle:
    study_id: str
    h0_receipt_digest: str
    feasibility: Literal["passed", "saturation", "floor", "no_legal_adaptation_signal"]
    projections: tuple[Exp2SourceProjection, ...]
    audience: Literal["operator"] = "operator"
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        _sha256(self.h0_receipt_digest, "h0_receipt_digest")
        if self.feasibility not in {"passed", "saturation", "floor", "no_legal_adaptation_signal"}:
            raise Exp2ContractError("source projection feasibility is invalid")
        if self.audience != "operator":
            raise Exp2ContractError("source projection audience must be operator")
        expected = tuple(item.case_id for item in _H0_CASES if item.slice == "source")
        if tuple(item.case_id for item in self.projections) != expected:
            raise Exp2ContractError("source projection bundle may contain only the frozen source pair")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-source-projection-bundle-v2",
                "study_id": self.study_id,
                "h0_receipt_digest": self.h0_receipt_digest,
                "feasibility": self.feasibility,
                "projections": [item.to_dict() for item in self.projections],
                "audience": self.audience,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2SourceProjectionBundle":
        fields = {"schema", "study_id", "h0_receipt_digest", "feasibility", "projections", "audience", "created_at", "record_digest"}
        _verify(data, "autobugfix-exp2-source-projection-bundle-v2", fields, "source projection bundle")
        raw = data.get("projections")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not all(isinstance(item, Mapping) for item in raw):
            raise Exp2ContractError("source projection bundle projections must be a list")
        return cls(
            study_id=str(data.get("study_id") or ""),
            h0_receipt_digest=str(data.get("h0_receipt_digest") or ""),
            feasibility=str(data.get("feasibility") or ""),  # type: ignore[arg-type]
            projections=tuple(Exp2SourceProjection.from_dict(item) for item in raw),
            audience=str(data.get("audience") or ""),  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2AttributionHypothesis:
    """Untrusted explanatory hypothesis, bound to source evidence but not authority."""

    study_id: str
    issuer: Literal["operator-governance-service-v4"]
    operator_study_id: str
    evidence_id: str
    evidence_digest: str
    author_role: Literal["operator_supervisor"]
    source_projection_bundle_digest: str
    supporting_receipt_digests: tuple[str, ...]
    expected_mechanism: str
    execution_scope: tuple[str, ...]
    validation_plan: tuple[str, ...]
    hypothesis: str
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        _safe(self.operator_study_id, "operator_study_id")
        _safe(self.evidence_id, "evidence_id")
        if self.issuer != "operator-governance-service-v4":
            raise Exp2ContractError("attribution issuer is invalid")
        if self.author_role != "operator_supervisor":
            raise Exp2ContractError(
                "attribution must be authored by the Operator supervisor"
            )
        _sha256(self.evidence_digest, "evidence_digest")
        _sha256(self.source_projection_bundle_digest, "source_projection_bundle_digest")
        if not self.supporting_receipt_digests:
            raise Exp2ContractError("attribution requires supporting receipt digests")
        for digest in self.supporting_receipt_digests:
            _sha256(digest, "supporting_receipt_digests")
        if not self.expected_mechanism.strip() or not self.hypothesis.strip():
            raise Exp2ContractError("attribution mechanism and hypothesis are required")
        if not self.execution_scope or not self.validation_plan:
            raise Exp2ContractError("attribution requires an execution scope and validation plan")
        for path in self.execution_scope:
            if Path(path).is_absolute() or ".." in Path(path).parts or not path.strip():
                raise Exp2ContractError("attribution scope contains unsafe path")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-attribution-hypothesis-v2",
                "study_id": self.study_id,
                "issuer": self.issuer,
                "operator_study_id": self.operator_study_id,
                "evidence_id": self.evidence_id,
                "evidence_digest": self.evidence_digest,
                "author_role": self.author_role,
                "source_projection_bundle_digest": self.source_projection_bundle_digest,
                "supporting_receipt_digests": list(self.supporting_receipt_digests),
                "expected_mechanism": self.expected_mechanism,
                "execution_scope": list(self.execution_scope),
                "validation_plan": list(self.validation_plan),
                "hypothesis": self.hypothesis,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2AttributionHypothesis":
        fields = {
            "schema", "study_id", "issuer", "operator_study_id", "evidence_id",
            "evidence_digest", "author_role", "source_projection_bundle_digest", "supporting_receipt_digests",
            "expected_mechanism", "execution_scope", "validation_plan", "hypothesis", "created_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-attribution-hypothesis-v2", fields, "attribution hypothesis")
        return cls(
            study_id=str(data.get("study_id") or ""),
            issuer=str(data.get("issuer") or ""),  # type: ignore[arg-type]
            operator_study_id=str(data.get("operator_study_id") or ""),
            evidence_id=str(data.get("evidence_id") or ""),
            evidence_digest=str(data.get("evidence_digest") or ""),
            author_role=str(data.get("author_role") or ""),  # type: ignore[arg-type]
            source_projection_bundle_digest=str(data.get("source_projection_bundle_digest") or ""),
            supporting_receipt_digests=_tuple_digests(data.get("supporting_receipt_digests") or (), "supporting_receipt_digests"),
            expected_mechanism=str(data.get("expected_mechanism") or ""),
            execution_scope=_tuple_strings(data.get("execution_scope") or (), "execution_scope"),
            validation_plan=_tuple_strings(data.get("validation_plan") or (), "validation_plan"),
            hypothesis=str(data.get("hypothesis") or ""),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2CandidateBinding:
    """Content-addressed immutable treatment binding consumed by v2 H1."""

    study_id: str
    parent_sha: str
    parent_tree: str
    candidate_sha: str
    candidate_tree: str
    candidate_diff_digest: str
    allowlist_digest: str
    scope_digest: str
    operator_policy_digest: str
    memory_fixture_digest: str
    operator_role_skill_digest: str
    execution_role_skill_digest: str
    runtime_digest: str
    request_digest: str
    integration_digest: str

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        for field_name in ("parent_sha", "parent_tree", "candidate_sha", "candidate_tree"):
            _sha1(getattr(self, field_name), field_name)
        if self.parent_sha == self.candidate_sha or self.parent_tree == self.candidate_tree:
            raise Exp2ContractError("candidate binding must differ from H0")
        for field_name in (
            "candidate_diff_digest", "allowlist_digest", "scope_digest", "operator_policy_digest",
            "memory_fixture_digest", "operator_role_skill_digest", "execution_role_skill_digest",
            "runtime_digest", "request_digest", "integration_digest",
        ):
            _sha256(getattr(self, field_name), field_name)

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-candidate-binding-v2",
                "study_id": self.study_id,
                "parent_sha": self.parent_sha,
                "parent_tree": self.parent_tree,
                "candidate_sha": self.candidate_sha,
                "candidate_tree": self.candidate_tree,
                "candidate_diff_digest": self.candidate_diff_digest,
                "allowlist_digest": self.allowlist_digest,
                "scope_digest": self.scope_digest,
                "operator_policy_digest": self.operator_policy_digest,
                "memory_fixture_digest": self.memory_fixture_digest,
                "operator_role_skill_digest": self.operator_role_skill_digest,
                "execution_role_skill_digest": self.execution_role_skill_digest,
                "runtime_digest": self.runtime_digest,
                "request_digest": self.request_digest,
                "integration_digest": self.integration_digest,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2CandidateBinding":
        fields = {
            "schema", "study_id", "parent_sha", "parent_tree", "candidate_sha", "candidate_tree",
            "candidate_diff_digest", "allowlist_digest", "scope_digest", "operator_policy_digest",
            "memory_fixture_digest", "operator_role_skill_digest", "execution_role_skill_digest",
            "runtime_digest", "request_digest", "integration_digest", "record_digest",
        }
        _verify(data, "autobugfix-exp2-candidate-binding-v2", fields, "candidate binding")
        return cls(**{key: str(data.get(key) or "") for key in fields - {"schema", "record_digest"}})


@dataclass(frozen=True, slots=True)
class Exp2CandidateTransitionReceipt:
    """Receipt emitted and independently revalidated by OperatorGovernanceService."""

    study_id: str
    issuer: Literal["operator-governance-service-v4"]
    attribution_digest: str
    source_projection_bundle_digest: str
    operator_study_id: str
    line_id: str
    request_id: str
    request_digest: str
    grant_id: str
    grant_digest: str
    writer_run_id: str
    writer_run_digest: str
    fast_check_digest: str
    full_check_digest: str
    integration_id: str
    integration_digest: str
    usage_digest: str
    eval_study_binding_path: str
    eval_study_binding_digest: str
    requested_paths: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    actual_paths: tuple[str, ...]
    binding: Exp2CandidateBinding
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        for field_name in ("operator_study_id", "line_id", "request_id", "grant_id", "writer_run_id", "integration_id"):
            _safe(getattr(self, field_name), field_name)
        if self.issuer != "operator-governance-service-v4":
            raise Exp2ContractError("candidate transition issuer is invalid")
        for field_name in (
            "attribution_digest", "source_projection_bundle_digest", "request_digest", "grant_digest",
            "writer_run_digest", "fast_check_digest", "full_check_digest", "integration_digest", "usage_digest",
            "eval_study_binding_digest",
        ):
            _sha256(getattr(self, field_name), field_name)
        _absolute_path(self.eval_study_binding_path, "eval_study_binding_path")
        if (
            not self.requested_paths
            or not self.allowed_paths
            or not self.actual_paths
            or len(set(self.requested_paths)) != len(self.requested_paths)
            or len(set(self.allowed_paths)) != len(self.allowed_paths)
            or len(set(self.actual_paths)) != len(self.actual_paths)
            or not set(self.requested_paths).issubset(self.allowed_paths)
            or not set(self.actual_paths).issubset(self.allowed_paths)
        ):
            raise Exp2ContractError(
                "candidate transition requested/allowed/actual paths are invalid"
            )
        for field_name in ("requested_paths", "allowed_paths", "actual_paths"):
            for value in getattr(self, field_name):
                if Path(value).is_absolute() or ".." in Path(value).parts:
                    raise Exp2ContractError(
                        f"candidate transition {field_name} contains an unsafe path"
                    )
        if self.binding.study_id != self.study_id or self.binding.request_digest != self.request_digest or self.binding.integration_digest != self.integration_digest:
            raise Exp2ContractError("candidate transition binding is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-candidate-transition-receipt-v2",
                "study_id": self.study_id,
                "issuer": self.issuer,
                "attribution_digest": self.attribution_digest,
                "source_projection_bundle_digest": self.source_projection_bundle_digest,
                "operator_study_id": self.operator_study_id,
                "line_id": self.line_id,
                "request_id": self.request_id,
                "request_digest": self.request_digest,
                "grant_id": self.grant_id,
                "grant_digest": self.grant_digest,
                "writer_run_id": self.writer_run_id,
                "writer_run_digest": self.writer_run_digest,
                "fast_check_digest": self.fast_check_digest,
                "full_check_digest": self.full_check_digest,
                "integration_id": self.integration_id,
                "integration_digest": self.integration_digest,
                "usage_digest": self.usage_digest,
                "eval_study_binding_path": self.eval_study_binding_path,
                "eval_study_binding_digest": self.eval_study_binding_digest,
                "requested_paths": list(self.requested_paths),
                "allowed_paths": list(self.allowed_paths),
                "actual_paths": list(self.actual_paths),
                "binding": self.binding.to_dict(),
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2CandidateTransitionReceipt":
        fields = {
            "schema", "study_id", "issuer", "attribution_digest", "source_projection_bundle_digest",
            "operator_study_id", "line_id", "request_id", "request_digest", "grant_id", "grant_digest",
            "writer_run_id", "writer_run_digest", "fast_check_digest", "full_check_digest", "integration_id",
            "integration_digest", "usage_digest", "eval_study_binding_path",
            "eval_study_binding_digest", "requested_paths", "allowed_paths",
            "actual_paths", "binding", "created_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-candidate-transition-receipt-v2", fields, "candidate transition receipt")
        binding = data.get("binding")
        if not isinstance(binding, Mapping):
            raise Exp2ContractError("candidate transition binding is missing")
        return cls(
            study_id=str(data.get("study_id") or ""),
            issuer=str(data.get("issuer") or ""),  # type: ignore[arg-type]
            attribution_digest=str(data.get("attribution_digest") or ""),
            source_projection_bundle_digest=str(data.get("source_projection_bundle_digest") or ""),
            operator_study_id=str(data.get("operator_study_id") or ""),
            line_id=str(data.get("line_id") or ""),
            request_id=str(data.get("request_id") or ""),
            request_digest=str(data.get("request_digest") or ""),
            grant_id=str(data.get("grant_id") or ""),
            grant_digest=str(data.get("grant_digest") or ""),
            writer_run_id=str(data.get("writer_run_id") or ""),
            writer_run_digest=str(data.get("writer_run_digest") or ""),
            fast_check_digest=str(data.get("fast_check_digest") or ""),
            full_check_digest=str(data.get("full_check_digest") or ""),
            integration_id=str(data.get("integration_id") or ""),
            integration_digest=str(data.get("integration_digest") or ""),
            usage_digest=str(data.get("usage_digest") or ""),
            eval_study_binding_path=str(data.get("eval_study_binding_path") or ""),
            eval_study_binding_digest=str(data.get("eval_study_binding_digest") or ""),
            requested_paths=_tuple_strings(
                data.get("requested_paths") or (), "requested_paths"
            ),
            allowed_paths=_tuple_strings(
                data.get("allowed_paths") or (), "allowed_paths"
            ),
            actual_paths=_tuple_strings(
                data.get("actual_paths") or (), "actual_paths"
            ),
            binding=Exp2CandidateBinding.from_dict(binding),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2RollbackAuthorization:
    study_id: str
    issuer: Literal["exp2-eval-coordinator-v2"]
    candidate_transition_digest: str
    transfer_metrics_digest: str
    decision: Literal["rollback"] = "rollback"
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        if self.issuer != "exp2-eval-coordinator-v2":
            raise Exp2ContractError("rollback authorization issuer is invalid")
        _sha256(
            self.candidate_transition_digest,
            "candidate_transition_digest",
        )
        _sha256(self.transfer_metrics_digest, "transfer_metrics_digest")
        if self.decision != "rollback":
            raise Exp2ContractError("rollback authorization decision is invalid")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-rollback-authorization-v2",
                "study_id": self.study_id,
                "issuer": self.issuer,
                "candidate_transition_digest": self.candidate_transition_digest,
                "transfer_metrics_digest": self.transfer_metrics_digest,
                "decision": self.decision,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "Exp2RollbackAuthorization":
        fields = {
            "schema",
            "study_id",
            "issuer",
            "candidate_transition_digest",
            "transfer_metrics_digest",
            "decision",
            "created_at",
            "record_digest",
        }
        _verify(
            data,
            "autobugfix-exp2-rollback-authorization-v2",
            fields,
            "Exp2 rollback authorization",
        )
        return cls(
            study_id=str(data.get("study_id") or ""),
            issuer=str(data.get("issuer") or ""),  # type: ignore[arg-type]
            candidate_transition_digest=str(
                data.get("candidate_transition_digest") or ""
            ),
            transfer_metrics_digest=str(
                data.get("transfer_metrics_digest") or ""
            ),
            decision=str(data.get("decision") or ""),  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2RollbackReceipt:
    study_id: str
    issuer: Literal["operator-governance-service-v4"]
    candidate_transition_digest: str
    rollback_authorization_digest: str
    line_id: str
    rollback_integration_id: str
    rollback_integration_digest: str
    post_rollback_head_sha: str
    post_rollback_tree_sha: str
    rollback_artifact_digest: str
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _safe(self.study_id, "study_id")
        _safe(self.line_id, "line_id")
        _safe(self.rollback_integration_id, "rollback_integration_id")
        if self.issuer != "operator-governance-service-v4":
            raise Exp2ContractError("rollback issuer is invalid")
        for field_name in (
            "candidate_transition_digest",
            "rollback_authorization_digest",
            "rollback_integration_digest",
            "rollback_artifact_digest",
        ):
            _sha256(getattr(self, field_name), field_name)
        _sha1(self.post_rollback_head_sha, "post_rollback_head_sha")
        _sha1(self.post_rollback_tree_sha, "post_rollback_tree_sha")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-rollback-receipt-v2",
                "study_id": self.study_id,
                "issuer": self.issuer,
                "candidate_transition_digest": self.candidate_transition_digest,
                "rollback_authorization_digest": self.rollback_authorization_digest,
                "line_id": self.line_id,
                "rollback_integration_id": self.rollback_integration_id,
                "rollback_integration_digest": self.rollback_integration_digest,
                "post_rollback_head_sha": self.post_rollback_head_sha,
                "post_rollback_tree_sha": self.post_rollback_tree_sha,
                "rollback_artifact_digest": self.rollback_artifact_digest,
                "created_at": self.created_at,
            }
        )

    @property
    def record_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Exp2RollbackReceipt":
        fields = {
            "schema", "study_id", "issuer", "candidate_transition_digest",
            "rollback_authorization_digest", "line_id",
            "rollback_integration_id", "rollback_integration_digest", "post_rollback_head_sha",
            "post_rollback_tree_sha", "rollback_artifact_digest", "created_at", "record_digest",
        }
        _verify(data, "autobugfix-exp2-rollback-receipt-v2", fields, "rollback receipt")
        return cls(
            study_id=str(data.get("study_id") or ""),
            issuer=str(data.get("issuer") or ""),  # type: ignore[arg-type]
            candidate_transition_digest=str(data.get("candidate_transition_digest") or ""),
            rollback_authorization_digest=str(
                data.get("rollback_authorization_digest") or ""
            ),
            line_id=str(data.get("line_id") or ""),
            rollback_integration_id=str(data.get("rollback_integration_id") or ""),
            rollback_integration_digest=str(data.get("rollback_integration_digest") or ""),
            post_rollback_head_sha=str(data.get("post_rollback_head_sha") or ""),
            post_rollback_tree_sha=str(data.get("post_rollback_tree_sha") or ""),
            rollback_artifact_digest=str(data.get("rollback_artifact_digest") or ""),
            created_at=str(data.get("created_at") or utc_now()),
        )


@dataclass(frozen=True, slots=True)
class Exp2PairedMetrics:
    population: Literal["source", "transfer"]
    case_ids: tuple[str, ...]
    cells: Mapping[str, int]
    h0_resolved: int
    h1_resolved: int
    net_paired_gain: float | None

    def __post_init__(self) -> None:
        expected = 2 if self.population == "source" else 3
        if len(self.case_ids) != expected or len(set(self.case_ids)) != expected:
            raise Exp2ContractError("paired metrics have the wrong fixed denominator")
        required = {
            "both-pass", "both-fail", "rescue", "observed-regression",
            "invalid-H0-only", "invalid-H1-only", "both-invalid",
        }
        if set(self.cells) != required or any(value < 0 for value in self.cells.values()):
            raise Exp2ContractError("paired metrics cells are invalid")
        if sum(self.cells.values()) != expected:
            raise Exp2ContractError("paired metrics cells do not partition the population")
        invalid = self.invalid_any
        if invalid and self.net_paired_gain is not None:
            raise Exp2ContractError("net paired gain must be null when an arm is invalid")
        if not invalid and self.net_paired_gain != (self.cells["rescue"] - self.cells["observed-regression"]) / expected:
            raise Exp2ContractError("net paired gain is inconsistent with paired cells")

    @property
    def invalid_any(self) -> bool:
        return any(self.cells[name] for name in ("invalid-H0-only", "invalid-H1-only", "both-invalid"))

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-paired-metrics-v2",
                "population": self.population,
                "case_ids": list(self.case_ids),
                "fixed_denominator": len(self.case_ids),
                "cells": dict(self.cells),
                "h0_resolved": self.h0_resolved,
                "h1_resolved": self.h1_resolved,
                "invalid_any": self.invalid_any,
                "net_paired_gain": self.net_paired_gain,
            }
        )


def reduce_exp2_pairs(
    h0: Sequence[Exp2CaseAttemptReceipt],
    h1: Sequence[Exp2CaseAttemptReceipt],
    *,
    population: Literal["source", "transfer"],
) -> Exp2PairedMetrics:
    expected_cases = tuple(
        item.case_id for item in _H0_CASES if item.slice == population
    )
    h0_by_case = {item.case_id: item for item in h0}
    h1_by_case = {item.case_id: item for item in h1}
    if tuple(h0_by_case) != expected_cases or tuple(h1_by_case) != expected_cases:
        raise Exp2ContractError("paired reduction cases differ from frozen population order")
    cells = {
        "both-pass": 0,
        "both-fail": 0,
        "rescue": 0,
        "observed-regression": 0,
        "invalid-H0-only": 0,
        "invalid-H1-only": 0,
        "both-invalid": 0,
    }
    h0_resolved = 0
    h1_resolved = 0
    for case_id in expected_cases:
        left, right = h0_by_case[case_id], h1_by_case[case_id]
        left_valid = left.terminal_status == "official_terminal"
        right_valid = right.terminal_status == "official_terminal"
        if not left_valid and not right_valid:
            cells["both-invalid"] += 1
            continue
        if not left_valid:
            cells["invalid-H0-only"] += 1
            continue
        if not right_valid:
            cells["invalid-H1-only"] += 1
            continue
        assert left.resolved is not None and right.resolved is not None
        h0_resolved += int(left.resolved)
        h1_resolved += int(right.resolved)
        if left.resolved and right.resolved:
            cells["both-pass"] += 1
        elif not left.resolved and not right.resolved:
            cells["both-fail"] += 1
        elif not left.resolved and right.resolved:
            cells["rescue"] += 1
        else:
            cells["observed-regression"] += 1
    invalid = any(cells[name] for name in ("invalid-H0-only", "invalid-H1-only", "both-invalid"))
    return Exp2PairedMetrics(
        population=population,
        case_ids=expected_cases,
        cells=cells,
        h0_resolved=h0_resolved,
        h1_resolved=h1_resolved,
        net_paired_gain=None if invalid else (cells["rescue"] - cells["observed-regression"]) / len(expected_cases),
    )


def lint_exp2_claims(text: str) -> tuple[str, ...]:
    """Reject claims which the small descriptive pilot cannot support."""

    lowered = text.lower()
    prohibited = (
        "statistical significance", "statistically significant", "population confidence",
        "benchmark superiority", "leaderboard", "production safe", "production-ready",
        "zero regression risk", "broad generalization", "all repositories",
        "combined five-case", "five-case effectiveness", "paper claim",
    )
    return tuple(item for item in prohibited if item in lowered)


def _event(kind: str, payload: Mapping[str, Any], predecessor: str | None) -> dict[str, Any]:
    return record_with_digest(
        {
            "schema": "autobugfix-exp2-resume-event-v2",
            "kind": kind,
            "payload": dict(payload),
            "predecessor_event_digest": predecessor,
            "created_at": utc_now(),
        }
    )


def _decode_event(data: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    fields = {"schema", "kind", "payload", "predecessor_event_digest", "created_at", "record_digest"}
    _verify(data, "autobugfix-exp2-resume-event-v2", fields, "Exp2 resume event")
    kind = _required(data.get("kind"), "event kind")
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        raise Exp2ResumeError("Exp2 resume event payload must be a mapping")
    previous = data.get("predecessor_event_digest")
    if previous is not None:
        _sha256(previous, "event predecessor_event_digest")
    return kind, payload


@dataclass(slots=True)
class _ReplayState:
    plan: Exp2ResumeStudyPlan
    protocol: Exp2ResumeProtocol
    state: str
    intents: dict[tuple[str, str, str], list[Exp2CaseAttemptIntent]] = field(default_factory=dict)
    receipts: dict[tuple[str, str, str], list[Exp2CaseAttemptReceipt]] = field(default_factory=dict)
    calibration_terminal: Exp2CalibrationTerminalReceipt | None = None
    pilot_terminal: Exp2PilotTerminalReceipt | None = None
    source_bundle: Exp2SourceProjectionBundle | None = None
    attribution: Exp2AttributionHypothesis | None = None
    candidate_transition: Exp2CandidateTransitionReceipt | None = None
    source_metrics: Exp2PairedMetrics | None = None
    transfer_metrics: Exp2PairedMetrics | None = None
    decision: str | None = None
    rollback_authorization: Exp2RollbackAuthorization | None = None
    rollback: Exp2RollbackReceipt | None = None
    report_digest: str | None = None
    last_event_digest: str | None = None


class Exp2ResumeCoordinator:
    """Append-only v2 scheduler.  It never synthesizes Eval or Operator truth."""

    def __init__(self, state_root: Path, study_id: str):
        if state_root.is_symlink():
            raise Exp2ResumeError("Exp2 state root cannot be redirected")
        self.state_root = state_root.resolve()
        self.study_id = _safe(study_id, "study_id")
        self.plan_path = self.state_root / "plan.yaml"
        self.protocol_path = self.state_root / "protocol.yaml"
        self.events_path = self.state_root / "events.jsonl"
        self.events_lock_path = self.state_root / ".events.lock"

    @contextmanager
    def _journal_lock(self):
        if fcntl is None:
            raise Exp2ResumeError(
                "Exp2 durable journal requires POSIX file locking"
            )
        self.state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(
            self.events_lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def initialize(self, plan: Exp2ResumeStudyPlan, protocol: Exp2ResumeProtocol) -> dict[str, Any]:
        if plan.study_id != self.study_id:
            raise Exp2ResumeError("Exp2 v2 plan study ID differs from coordinator")
        if plan.protocol_digest != protocol.record_digest:
            raise Exp2ResumeError("Exp2 v2 plan protocol digest differs from protocol")
        protocol_source = Path(plan.protocol_path)
        if protocol_source.is_symlink() or not protocol_source.is_file():
            raise Exp2ResumeError(
                "Exp2 v2 source protocol is missing or redirected"
            )
        raw_protocol = yaml.safe_load(
            protocol_source.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(raw_protocol, Mapping):
            raise Exp2ResumeError("Exp2 v2 source protocol is invalid")
        persisted_protocol = Exp2ResumeProtocol.from_dict(raw_protocol)
        if persisted_protocol.to_dict() != protocol.to_dict():
            raise Exp2ResumeError(
                "Exp2 v2 source protocol differs from the initialized protocol"
            )
        apparatus = _read_immutable_record(
            Path(plan.apparatus_receipt_path),
            "Exp2 apparatus receipt",
        )
        if (
            apparatus.get("schema")
            != "autobugfix-exp2-apparatus-receipt-v2"
            or apparatus.get("record_digest")
            != plan.apparatus_receipt_digest
            or apparatus.get("apparatus_sha") != plan.apparatus_sha
            or apparatus.get("apparatus_tree") != plan.apparatus_tree
            or apparatus.get("protocol_digest") != protocol.record_digest
            or apparatus.get("scorer_digest") != plan.scorer_digest
            or apparatus.get("runtime_digest") != plan.runtime_digest
            or apparatus.get("memory_fixture_digest")
            != plan.memory_fixture_digest
            or apparatus.get("operator_policy_digest")
            != plan.operator_policy_digest
        ):
            raise Exp2ResumeError(
                "Exp2 apparatus receipt differs from the frozen plan"
            )
        memory_fixture = Exp2EmptyMemoryFixture.from_yaml(
            Path(plan.memory_fixture_spec_path)
        )
        if (
            memory_fixture.fixture_file_digest
            != protocol.memory_fixture_spec_digest
        ):
            raise Exp2ResumeError(
                "Exp2 empty Memory fixture spec differs from protocol"
            )
        swe_protocol_source = Path(plan.swe_protocol_path)
        if swe_protocol_source.is_symlink() or not swe_protocol_source.is_file():
            raise Exp2ResumeError(
                "Exp2 v2 SWE protocol is missing or redirected"
            )
        if digest_file(swe_protocol_source) != plan.swe_protocol_sha256:
            raise Exp2ResumeError("Exp2 v2 SWE protocol digest drift")
        for field_name in (
            "scorer_digest", "runtime_digest", "memory_fixture_digest", "operator_policy_digest",
            "operator_role_skill_digest", "execution_role_skill_digest",
        ):
            if getattr(plan, field_name) != getattr(protocol, field_name):
                raise Exp2ResumeError(f"Exp2 v2 plan {field_name} differs from protocol")
        image_digest = digest_payload({"oci_images": [item.to_dict() for item in protocol.oci_images]})
        if plan.selected_images_digest != image_digest:
            raise Exp2ResumeError("Exp2 v2 selected OCI image digest differs from protocol")
        if plan.study_kind == "resume_pilot":
            assert plan.public_manifest_path is not None
            assert plan.public_manifest_digest is not None
            assert plan.h0_binding_path is not None
            assert plan.calibration_terminal_receipt_path is not None
            assert plan.calibration_terminal_receipt_digest is not None
            public_manifest = _read_immutable_record(
                Path(plan.public_manifest_path),
                "resume pilot public manifest",
            )
            h0_binding = _read_immutable_record(
                Path(plan.h0_binding_path),
                "resume pilot H0 binding",
            )
            if (
                public_manifest.get("record_digest")
                != plan.public_manifest_digest
                or h0_binding.get("record_digest") != plan.h0_binding_digest
                or h0_binding.get("kind") != "BASELINE"
                or h0_binding.get("subject_sha") != plan.h0_subject_sha
                or h0_binding.get("subject_tree") != plan.h0_subject_tree
                or h0_binding.get("memory_digest")
                != plan.memory_fixture_digest
                or h0_binding.get("primary_model") != protocol.model
            ):
                raise Exp2ResumeError(
                    "resume pilot formal Eval inputs differ from the plan"
                )
            source = Path(plan.calibration_terminal_receipt_path)
            if source.is_symlink() or not source.is_file():
                raise Exp2ResumeError(
                    "resume pilot calibration terminal receipt is missing or redirected"
                )
            raw_terminal = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
            if not isinstance(raw_terminal, Mapping):
                raise Exp2ResumeError(
                    "resume pilot calibration terminal receipt is invalid"
                )
            terminal = Exp2CalibrationTerminalReceipt.from_dict(raw_terminal)
            if (
                terminal.record_digest
                != plan.calibration_terminal_receipt_digest
                or terminal.status != "CALIBRATION_COMPLETE"
                or terminal.protocol_digest != protocol.record_digest
                or terminal.apparatus_receipt_digest
                != plan.apparatus_receipt_digest
                or terminal.scorer_digest != plan.scorer_digest
                or terminal.runtime_digest != plan.runtime_digest
                or terminal.memory_fixture_digest
                != plan.memory_fixture_digest
            ):
                raise Exp2ResumeError(
                    "resume pilot calibration terminal identities are incompatible"
                )
        if self.plan_path.exists() or self.protocol_path.exists() or self.events_path.exists():
            raise Exp2ResumeError("Exp2 v2 state root already exists")
        self.state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._write_once(self.plan_path, plan.to_dict())
        self._write_once(self.protocol_path, protocol.to_dict())
        initial = {
            "plan_digest": plan.record_digest,
            "protocol_digest": protocol.record_digest,
            "state": "CALIBRATION_PREPARED" if plan.study_kind == "calibration" else "PREPARED",
        }
        self._append("initialized", initial, expected_predecessor=None)
        return self.status()

    @staticmethod
    def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
        serialized = yaml.safe_dump(dict(payload), sort_keys=False)
        if path.exists():
            if path.is_symlink() or path.read_text(encoding="utf-8") != serialized:
                raise Exp2ResumeError(f"immutable Exp2 v2 record already exists: {path}")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _append(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        expected_predecessor: str | None | object = _UNSET,
    ) -> str:
        with self._journal_lock():
            events = self._load_events(allow_missing=True, repair_torn=True)
            predecessor = str(events[-1]["record_digest"]) if events else None
            if (
                expected_predecessor is not _UNSET
                and predecessor != expected_predecessor
            ):
                raise Exp2ResumeError(
                    "Exp2 journal advanced concurrently; retry from current state"
                )
            event = _event(kind, payload, predecessor)
            encoded = (
                json.dumps(event, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            descriptor = os.open(
                self.events_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return str(event["record_digest"])

    def _load_events(
        self,
        *,
        allow_missing: bool = False,
        repair_torn: bool = False,
    ) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            if allow_missing:
                return []
            raise Exp2ResumeError("Exp2 v2 event journal is missing")
        if self.events_path.is_symlink():
            raise Exp2ResumeError("Exp2 v2 event journal is redirected")
        raw_bytes = self.events_path.read_bytes()
        if raw_bytes and not raw_bytes.endswith(b"\n"):
            boundary = raw_bytes.rfind(b"\n")
            complete = raw_bytes[: boundary + 1] if boundary >= 0 else b""
            if repair_torn:
                descriptor = os.open(
                    self.events_path,
                    os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    os.ftruncate(descriptor, len(complete))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            raw_bytes = complete
        events: list[dict[str, Any]] = []
        predecessor: str | None = None
        try:
            lines = raw_bytes.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise Exp2ResumeError("Exp2 v2 event journal is not UTF-8") from exc
        for index, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise Exp2ResumeError(f"Exp2 v2 event journal line {index} is not JSON") from exc
            if not isinstance(event, Mapping):
                raise Exp2ResumeError(f"Exp2 v2 event journal line {index} is not a mapping")
            try:
                _decode_event(event)
            except (Exp2ContractError, Exp2ResumeError) as exc:
                raise Exp2ResumeError(f"Exp2 v2 event journal line {index} is invalid: {exc}") from exc
            if event.get("predecessor_event_digest") != predecessor:
                raise Exp2ResumeError(f"Exp2 v2 event journal line {index} chain is invalid")
            predecessor = str(event["record_digest"])
            events.append(dict(event))
        return events

    def load_plan(self) -> Exp2ResumeStudyPlan:
        if self.plan_path.is_symlink() or not self.plan_path.is_file():
            raise Exp2ResumeError("Exp2 v2 plan is missing or redirected")
        raw = yaml.safe_load(self.plan_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2ResumeError("Exp2 v2 plan is invalid")
        return Exp2ResumeStudyPlan.from_dict(raw)

    def load_protocol(self) -> Exp2ResumeProtocol:
        if self.protocol_path.is_symlink() or not self.protocol_path.is_file():
            raise Exp2ResumeError("Exp2 v2 protocol is missing or redirected")
        raw = yaml.safe_load(self.protocol_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2ResumeError("Exp2 v2 protocol is invalid")
        return Exp2ResumeProtocol.from_dict(raw)

    def _replay(self) -> _ReplayState:
        plan = self.load_plan()
        protocol = self.load_protocol()
        if plan.study_id != self.study_id or plan.protocol_digest != protocol.record_digest:
            raise Exp2ResumeError("Exp2 v2 persisted plan/protocol binding drift")
        state = _ReplayState(plan=plan, protocol=protocol, state="UNINITIALIZED")
        with self._journal_lock():
            events = self._load_events()
        for event in events:
            kind, payload = _decode_event(event)
            digest = str(event["record_digest"])
            if kind == "initialized":
                if state.state != "UNINITIALIZED" or payload.get("plan_digest") != plan.record_digest or payload.get("protocol_digest") != protocol.record_digest:
                    raise Exp2ResumeError("Exp2 v2 initialized event is invalid")
                state.state = str(payload.get("state") or "")
            elif kind == "case_attempt_started":
                intent = Exp2CaseAttemptIntent.from_dict(payload)
                self._validate_intent(state, intent, replay=True)
                key = (intent.stage, intent.arm, intent.case_id)
                intents = state.intents.get(key) or []
                receipts = state.receipts.get(key) or []
                if len(intents) > len(receipts):
                    raise Exp2ResumeError("Exp2 v2 case has multiple open intents")
                state.intents.setdefault(key, []).append(intent)
                if state.state in {"CALIBRATION_PREPARED", "PREPARED"}:
                    state.state = "CALIBRATION_RUNNING" if plan.study_kind == "calibration" else "H0_RUNNING"
            elif kind == "case_attempt_terminal":
                receipt = Exp2CaseAttemptReceipt.from_dict(payload)
                key = (receipt.stage, receipt.arm, receipt.case_id)
                intents = state.intents.get(key) or []
                receipts = state.receipts.get(key) or []
                if (
                    len(intents) != len(receipts) + 1
                    or intents[len(receipts)].record_digest
                    != receipt.started_event_digest
                ):
                    raise Exp2ResumeError("Exp2 v2 terminal receipt lacks its current intent")
                self._validate_receipt(state, intents[len(receipts)], receipt)
                state.receipts.setdefault(key, []).append(receipt)
            elif kind == "calibration_terminal":
                receipt = Exp2CalibrationTerminalReceipt.from_dict(payload)
                self._validate_calibration_terminal(state, receipt)
                if state.calibration_terminal is not None:
                    raise Exp2ResumeError("Exp2 v2 calibration terminal receipt is duplicated")
                state.calibration_terminal = receipt
                state.state = receipt.status
            elif kind == "pilot_terminal":
                receipt = Exp2PilotTerminalReceipt.from_dict(payload)
                self._validate_pilot_terminal(state, receipt)
                if state.pilot_terminal is not None:
                    raise Exp2ResumeError(
                        "Exp2 v2 pilot terminal receipt is duplicated"
                    )
                state.pilot_terminal = receipt
                state.decision = receipt.decision
                state.state = "BLOCKED"
            elif kind == "source_bundle_released":
                bundle = Exp2SourceProjectionBundle.from_dict(payload)
                self._validate_source_bundle(state, bundle)
                if state.source_bundle is not None:
                    raise Exp2ResumeError("Exp2 v2 source projection bundle is duplicated")
                state.source_bundle = bundle
                state.state = "SOURCE_RELEASED"
            elif kind == "attribution_recorded":
                attribution = Exp2AttributionHypothesis.from_dict(payload)
                if state.state != "SOURCE_RELEASED" or state.source_bundle is None:
                    raise Exp2ResumeError("Exp2 v2 attribution is not awaiting source evidence")
                if attribution.study_id != self.study_id or attribution.source_projection_bundle_digest != state.source_bundle.record_digest:
                    raise Exp2ResumeError("Exp2 v2 attribution source binding drift")
                state.attribution = attribution
                state.state = "CANDIDATE_TRANSITION_AWAITING"
            elif kind == "candidate_transition_locked":
                receipt = Exp2CandidateTransitionReceipt.from_dict(payload)
                self._validate_candidate_transition(state, receipt)
                if state.candidate_transition is not None:
                    raise Exp2ResumeError("Exp2 v2 candidate transition is duplicated")
                state.candidate_transition = receipt
                state.state = "CANDIDATE_LOCKED"
            elif kind == "source_replay_complete":
                metrics = self._metrics_from_payload(payload, "source")
                self._validate_complete_h1_stage(state, "source", metrics)
                state.source_metrics = metrics
                state.state = "SOURCE_REPLAY_COMPLETE"
            elif kind == "transfer_decided":
                metrics = self._metrics_from_payload(payload, "transfer")
                self._validate_complete_h1_stage(state, "transfer", metrics)
                decision = _required(payload.get("decision"), "transfer decision")
                expected = self._transfer_decision(metrics)
                if decision != expected:
                    raise Exp2ResumeError("Exp2 v2 transfer decision differs from paired metrics")
                raw_authorization = payload.get("rollback_authorization")
                if decision == "rollback":
                    if (
                        not isinstance(raw_authorization, Mapping)
                        or state.candidate_transition is None
                    ):
                        raise Exp2ResumeError(
                            "rollback decision lacks Eval authorization"
                        )
                    authorization = Exp2RollbackAuthorization.from_dict(
                        raw_authorization
                    )
                    if (
                        authorization.study_id != self.study_id
                        or authorization.candidate_transition_digest
                        != state.candidate_transition.record_digest
                        or authorization.transfer_metrics_digest
                        != metrics.record_digest
                    ):
                        raise Exp2ResumeError(
                            "rollback authorization differs from transfer evidence"
                        )
                    state.rollback_authorization = authorization
                elif raw_authorization is not None:
                    raise Exp2ResumeError(
                        "non-rollback decision cannot carry rollback authorization"
                    )
                state.transfer_metrics = metrics
                state.decision = decision
                state.state = "ROLLBACK_AWAITING" if decision == "rollback" else ("BLOCKED" if decision == "blocked_invalid" else "PILOT_COMPLETE")
            elif kind == "rollback_recorded":
                rollback = Exp2RollbackReceipt.from_dict(payload)
                if (
                    state.state != "ROLLBACK_AWAITING"
                    or state.candidate_transition is None
                    or state.rollback_authorization is None
                    or rollback.candidate_transition_digest
                    != state.candidate_transition.record_digest
                    or rollback.rollback_authorization_digest
                    != state.rollback_authorization.record_digest
                ):
                    raise Exp2ResumeError("Exp2 v2 rollback receipt is not awaiting a matching rollback")
                state.rollback = rollback
                state.state = "ROLLED_BACK"
            elif kind == "report_published":
                report_digest = _sha256(payload.get("report_digest"), "report_digest")
                if state.state not in {"PILOT_COMPLETE", "ROLLED_BACK", "BLOCKED", "CALIBRATION_COMPLETE", "CALIBRATION_BLOCKED"}:
                    raise Exp2ResumeError("Exp2 v2 report was published before terminal state")
                state.report_digest = report_digest
                state.state = "REPORTED"
            else:
                raise Exp2ResumeError(f"unsupported Exp2 v2 event kind: {kind}")
            state.last_event_digest = digest
        if state.state == "UNINITIALIZED":
            raise Exp2ResumeError("Exp2 v2 journal has no initialized event")
        return state

    def _stage_cases(self, state: _ReplayState, stage: ResumeStage) -> tuple[Exp2ResumeCase, ...]:
        if stage == "CALIBRATION":
            return state.protocol.calibration_cases
        if stage == "H0":
            return state.protocol.h0_cases
        if stage == "H1_SOURCE":
            return state.protocol.source_cases
        if stage == "H1_TRANSFER":
            return state.protocol.transfer_cases
        raise AssertionError(stage)

    @staticmethod
    def _stage_arm(stage: ResumeStage) -> ResumeArm:
        return "H0" if stage in {"CALIBRATION", "H0"} else "H1"

    def _expected_stage(self, state: _ReplayState) -> ResumeStage | None:
        if state.plan.study_kind == "calibration":
            return "CALIBRATION" if state.calibration_terminal is None else None
        if state.state in {"PREPARED", "H0_RUNNING"}:
            return "H0"
        if state.state in {"CANDIDATE_LOCKED", "SOURCE_REPLAY_RUNNING"}:
            return "H1_SOURCE"
        if state.state in {"SOURCE_REPLAY_COMPLETE", "TRANSFER_RUNNING"}:
            return "H1_TRANSFER"
        return None

    def _validate_intent(
        self,
        state: _ReplayState,
        intent: Exp2CaseAttemptIntent,
        *,
        replay: bool = False,
    ) -> None:
        if intent.study_id != self.study_id or intent.study_kind != state.plan.study_kind:
            raise Exp2ResumeError("case intent study identity drift")
        expected = self._expected_stage(state)
        if expected != intent.stage:
            raise Exp2ResumeError("case intent does not match next study stage")
        expected_case = self._next_case(state, intent.stage)
        if (
            expected_case is None
            or expected_case[0].case_id != intent.case_id
            or expected_case[1] != intent.attempt_kind
        ):
            raise Exp2ResumeError(
                "case intent differs from the next frozen case or retry"
            )
        if intent.predecessor_event_digest != state.last_event_digest:
            raise Exp2ResumeError("case intent predecessor differs from the event chain")
        cases = self._stage_cases(state, intent.stage)
        matching = [item for item in cases if item.case_id == intent.case_id]
        if len(matching) != 1 or matching[0].slice != intent.slice or intent.arm != self._stage_arm(intent.stage):
            raise Exp2ResumeError("case intent differs from frozen stage schedule")
        if intent.subject_sha != (state.plan.h0_subject_sha if intent.arm == "H0" else (state.candidate_transition.binding.candidate_sha if state.candidate_transition else "")):
            raise Exp2ResumeError("case intent subject differs from frozen binding")
        if intent.subject_tree != (state.plan.h0_subject_tree if intent.arm == "H0" else (state.candidate_transition.binding.candidate_tree if state.candidate_transition else "")):
            raise Exp2ResumeError("case intent subject tree differs from frozen binding")
        expected_binding = state.plan.h0_binding_digest if intent.arm == "H0" else (state.candidate_transition.binding.record_digest if state.candidate_transition else "")
        if intent.binding_digest != expected_binding:
            raise Exp2ResumeError("case intent binding digest differs from frozen binding")
        expected_frozen = self._frozen_input_digest(state, intent.arm, intent.binding_digest)
        if intent.frozen_input_digest != expected_frozen:
            raise Exp2ResumeError("case intent frozen input digest drift")
        expected_output = (
            Path(state.plan.artifact_root) / "runs" / intent.run_id
        ).resolve()
        if Path(intent.output_root).resolve() != expected_output:
            raise Exp2ResumeError("case intent output root differs from its run ID")
        if intent.attempt_kind == "scorer_only_retry":
            previous = self._last_receipt(
                state,
                intent.stage,
                intent.arm,
                intent.case_id,
            )
            prior_intents = state.intents.get(
                (intent.stage, intent.arm, intent.case_id)
            ) or []
            if (
                previous is None
                or not prior_intents
                or previous.terminal_status != "scorer_infrastructure_invalid"
                or not previous.scorer_retry_legal
                or previous.submission_digest is None
                or intent.retry_of_receipt_digest != previous.record_digest
                or intent.frozen_submission_digest != previous.submission_digest
                or intent.retry_source_output_root
                != prior_intents[-1].output_root
            ):
                raise Exp2ResumeError(
                    "scorer-only retry is not bound to the prior frozen submission"
                )

    @staticmethod
    def _frozen_input_digest(state: _ReplayState, arm: ResumeArm, binding_digest: str) -> str:
        return digest_payload(
            {
                "protocol_digest": state.protocol.record_digest,
                "scorer_digest": state.plan.scorer_digest,
                "runtime_digest": state.plan.runtime_digest,
                "memory_fixture_spec_digest": (
                    state.protocol.memory_fixture_spec_digest
                ),
                "memory_fixture_digest": state.plan.memory_fixture_digest,
                "execution_role_skill_digest": state.plan.execution_role_skill_digest if arm == "H0" else state.candidate_transition.binding.execution_role_skill_digest if state.candidate_transition else "",
                "model": state.protocol.model,
                "reasoning_effort": state.protocol.reasoning_effort,
                "execution_mode": state.protocol.execution_mode,
                "max_attempts": state.protocol.max_attempts,
                "timeout_seconds": state.protocol.timeout_seconds,
                "case_concurrency": state.protocol.case_concurrency,
                "binding_digest": binding_digest,
            }
        )

    def _validate_receipt(self, state: _ReplayState, intent: Exp2CaseAttemptIntent, receipt: Exp2CaseAttemptReceipt) -> None:
        for field_name in ("study_id", "stage", "arm", "case_id", "slice", "run_id", "attempt_kind", "subject_sha", "subject_tree", "frozen_input_digest", "binding_digest"):
            if getattr(receipt, field_name) != getattr(intent, field_name):
                raise Exp2ResumeError(f"case terminal receipt {field_name} differs from intent")
        if receipt.started_event_digest != intent.record_digest:
            raise Exp2ResumeError("case terminal receipt started-event digest differs from intent")
        if receipt.execution_mode != state.protocol.execution_mode:
            raise Exp2ResumeError(
                "case terminal receipt execution mode differs from protocol"
            )
        if receipt.terminal_status == "official_terminal":
            images = [
                item
                for item in state.protocol.oci_images
                if item.case_id == intent.case_id
            ]
            if (
                len(images) != 1
                or receipt.image_digest != images[0].config_digest
            ):
                raise Exp2ResumeError(
                    "official receipt image differs from the frozen case image"
                )
            if receipt.runtime_digest != state.plan.runtime_digest:
                raise Exp2ResumeError(
                    "official receipt runtime differs from the frozen plan"
                )
            if receipt.memory_digest != state.plan.memory_fixture_digest:
                raise Exp2ResumeError(
                    "official receipt Memory differs from the frozen empty fixture"
                )
        if (
            intent.attempt_kind == "scorer_only_retry"
            and receipt.submission_digest != intent.frozen_submission_digest
        ):
            raise Exp2ResumeError(
                "scorer-only retry receipt differs from its frozen submission"
            )

    def _validate_calibration_terminal(self, state: _ReplayState, receipt: Exp2CalibrationTerminalReceipt) -> None:
        if state.plan.study_kind != "calibration" or receipt.study_id != self.study_id:
            raise Exp2ResumeError("calibration terminal receipt belongs to another study")
        checks = {
            "plan_digest": state.plan.record_digest,
            "protocol_digest": state.protocol.record_digest,
            "apparatus_receipt_digest": state.plan.apparatus_receipt_digest,
            "scorer_digest": state.plan.scorer_digest,
            "runtime_digest": state.plan.runtime_digest,
            "memory_fixture_digest": state.plan.memory_fixture_digest,
        }
        if any(getattr(receipt, name) != expected for name, expected in checks.items()):
            raise Exp2ResumeError("calibration terminal receipt frozen identity drift")
        receipts = [self._last_receipt(state, "CALIBRATION", "H0", item.case_id) for item in state.protocol.calibration_cases]
        if any(item is None for item in receipts):
            raise Exp2ResumeError("calibration terminal receipt lacks case outcomes")
        values = [item for item in receipts if item is not None]
        if tuple(item.record_digest for item in values) != receipt.case_receipt_digests:
            raise Exp2ResumeError("calibration terminal receipt case receipt order drift")
        expected_status = "CALIBRATION_COMPLETE" if all(item.terminal_status == "official_terminal" for item in values) else "CALIBRATION_BLOCKED"
        if receipt.status != expected_status:
            raise Exp2ResumeError("calibration terminal status differs from case evidence")

    def _validate_source_bundle(self, state: _ReplayState, bundle: Exp2SourceProjectionBundle) -> None:
        if state.plan.study_kind != "resume_pilot" or bundle.study_id != self.study_id:
            raise Exp2ResumeError("source bundle belongs to another study")
        h0_receipts = [self._last_receipt(state, "H0", "H0", item.case_id) for item in state.protocol.h0_cases]
        if any(item is None for item in h0_receipts):
            raise Exp2ResumeError("source bundle was released before H0 terminal coverage")
        actual_bundle, expected_feasibility = self._build_source_bundle(state, [item for item in h0_receipts if item is not None])
        if bundle.to_dict() != actual_bundle.to_dict() or bundle.feasibility != expected_feasibility:
            raise Exp2ResumeError("source bundle feasibility or redacted projection drift")

    def _validate_pilot_terminal(
        self,
        state: _ReplayState,
        receipt: Exp2PilotTerminalReceipt,
    ) -> None:
        if state.plan.study_kind != "resume_pilot":
            raise Exp2ResumeError("pilot terminal receipt belongs to calibration")
        if (
            receipt.study_id != self.study_id
            or receipt.plan_digest != state.plan.record_digest
            or receipt.protocol_digest != state.protocol.record_digest
        ):
            raise Exp2ResumeError("pilot terminal frozen identity drift")
        values = [
            self._last_receipt(state, "H0", "H0", case.case_id)
            for case in state.protocol.h0_cases
        ]
        if any(item is None for item in values):
            raise Exp2ResumeError("pilot terminal receipt lacks H0 coverage")
        h0_receipts = [item for item in values if item is not None]
        if tuple(item.record_digest for item in h0_receipts) != (
            receipt.h0_case_receipt_digests
        ):
            raise Exp2ResumeError("pilot terminal H0 receipt order drift")
        if any(
            item.terminal_status != "official_terminal" for item in h0_receipts
        ):
            expected_reason = "h0_invalid"
        else:
            _, feasibility = self._build_source_bundle(state, h0_receipts)
            expected_reason = {
                "floor": "h0_floor",
                "saturation": "h0_saturation",
                "no_legal_adaptation_signal": "no_legal_adaptation_signal",
            }.get(feasibility)
            if expected_reason is None:
                raise Exp2ResumeError(
                    "passing H0 feasibility cannot produce a pilot terminal"
                )
        if receipt.reason != expected_reason:
            raise Exp2ResumeError("pilot terminal reason differs from H0 evidence")

    def _validate_candidate_transition(self, state: _ReplayState, receipt: Exp2CandidateTransitionReceipt) -> None:
        if state.state != "CANDIDATE_TRANSITION_AWAITING" or state.attribution is None or state.source_bundle is None:
            raise Exp2ResumeError("candidate transition is not awaiting attribution")
        if receipt.study_id != self.study_id or receipt.attribution_digest != state.attribution.record_digest or receipt.source_projection_bundle_digest != state.source_bundle.record_digest:
            raise Exp2ResumeError("candidate transition provenance binding drift")
        binding = receipt.binding
        if binding.parent_sha != state.plan.h0_subject_sha or binding.parent_tree != state.plan.h0_subject_tree:
            raise Exp2ResumeError("candidate transition parent differs from frozen H0")
        expected = {
            "operator_policy_digest": state.plan.operator_policy_digest,
            "memory_fixture_digest": state.plan.memory_fixture_digest,
            "operator_role_skill_digest": state.plan.operator_role_skill_digest,
            "execution_role_skill_digest": state.plan.execution_role_skill_digest,
            "runtime_digest": state.plan.runtime_digest,
        }
        if any(getattr(binding, name) != value for name, value in expected.items()):
            raise Exp2ResumeError("candidate transition frozen identity drift")
        if (
            tuple(receipt.requested_paths) != state.attribution.execution_scope
            or tuple(receipt.allowed_paths) != state.protocol.execution_allowlist
            or binding.allowlist_digest
            != digest_payload({"allowed_paths": list(receipt.allowed_paths)})
            or binding.scope_digest
            != digest_payload(
                {
                    "requested_paths": list(receipt.requested_paths),
                    "actual_paths": list(receipt.actual_paths),
                }
            )
        ):
            raise Exp2ResumeError(
                "candidate transition requested/allowed/actual scope drift"
            )
        eval_binding = _read_immutable_record(
            Path(receipt.eval_study_binding_path),
            "candidate Eval Study binding",
        )
        if (
            eval_binding.get("record_digest")
            != receipt.eval_study_binding_digest
            or eval_binding.get("kind") != "CANDIDATE"
            or eval_binding.get("study_id") != receipt.operator_study_id
            or eval_binding.get("line_id") != receipt.line_id
            or eval_binding.get("subject_sha") != binding.candidate_sha
            or eval_binding.get("subject_tree") != binding.candidate_tree
        ):
            raise Exp2ResumeError(
                "candidate transition Eval Study binding drift"
            )

    def _last_receipt(self, state: _ReplayState, stage: ResumeStage, arm: ResumeArm, case_id: str) -> Exp2CaseAttemptReceipt | None:
        receipts = state.receipts.get((stage, arm, case_id)) or []
        return receipts[-1] if receipts else None

    def _case_needs_retry(self, receipts: Sequence[Exp2CaseAttemptReceipt]) -> bool:
        return bool(receipts and receipts[-1].terminal_status == "scorer_infrastructure_invalid" and receipts[-1].scorer_retry_legal and not any(item.attempt_kind == "scorer_only_retry" for item in receipts))

    def _stage_complete(self, state: _ReplayState, stage: ResumeStage) -> bool:
        arm = self._stage_arm(stage)
        for case in self._stage_cases(state, stage):
            receipts = state.receipts.get((stage, arm, case.case_id)) or []
            if not receipts or self._case_needs_retry(receipts):
                return False
        return True

    def _build_source_bundle(self, state: _ReplayState, h0_receipts: Sequence[Exp2CaseAttemptReceipt]) -> tuple[Exp2SourceProjectionBundle, str]:
        h0_by_case = {item.case_id: item for item in h0_receipts}
        invalid = [item for item in h0_receipts if item.terminal_status != "official_terminal"]
        if invalid:
            raise Exp2ResumeError(
                "invalid H0 receipts cannot enter the source visibility bundle"
            )
        valid = [item for item in h0_receipts if item.terminal_status == "official_terminal"]
        resolved = sum(bool(item.resolved) for item in valid)
        unresolved = len(valid) - resolved
        source = [h0_by_case[item.case_id] for item in state.protocol.source_cases]
        legal_signal = any(item.resolved is False and item.failure_stage in {"execution", "visible_verifier"} for item in source)
        feasibility: Literal["passed", "saturation", "floor", "no_legal_adaptation_signal"]
        if resolved < 2:
            feasibility = "floor"
        elif unresolved < 2:
            feasibility = "saturation"
        elif not legal_signal:
            feasibility = "no_legal_adaptation_signal"
        else:
            feasibility = "passed"
        projections = tuple(
            Exp2SourceProjection(
                case_id=item.case_id,
                receipt_digest=item.record_digest,
                terminal_label="resolved" if item.resolved else "unresolved",
                failure_stage=item.failure_stage,
            )
            for item in source
        )
        h0_digest = digest_payload({"case_receipts": [item.record_digest for item in h0_receipts]})
        return (
            Exp2SourceProjectionBundle(
                self.study_id,
                h0_digest,
                feasibility,
                projections,
                created_at=max(item.terminal_at for item in h0_receipts),
            ),
            feasibility,
        )

    @staticmethod
    def _metrics_from_payload(payload: Mapping[str, Any], population: Literal["source", "transfer"]) -> Exp2PairedMetrics:
        raw = payload.get("metrics")
        if not isinstance(raw, Mapping):
            raise Exp2ResumeError("paired metrics event payload is missing")
        try:
            verify_record(raw)
        except BenchmarkContractError as exc:
            raise Exp2ResumeError("paired metrics event payload digest is invalid") from exc
        fields = {"schema", "population", "case_ids", "fixed_denominator", "cells", "h0_resolved", "h1_resolved", "invalid_any", "net_paired_gain", "record_digest"}
        if raw.get("schema") != "autobugfix-exp2-paired-metrics-v2" or set(raw) != fields or raw.get("population") != population:
            raise Exp2ResumeError("paired metrics event payload schema is invalid")
        cases = _tuple_strings(raw.get("case_ids") or (), "case_ids")
        cells = raw.get("cells")
        if not isinstance(cells, Mapping):
            raise Exp2ResumeError("paired metrics cells are invalid")
        return Exp2PairedMetrics(
            population=population,
            case_ids=cases,
            cells={str(key): int(value) for key, value in cells.items()},
            h0_resolved=int(raw.get("h0_resolved") or 0),
            h1_resolved=int(raw.get("h1_resolved") or 0),
            net_paired_gain=(float(raw["net_paired_gain"]) if raw.get("net_paired_gain") is not None else None),
        )

    def _validate_complete_h1_stage(self, state: _ReplayState, population: Literal["source", "transfer"], metrics: Exp2PairedMetrics) -> None:
        stage: ResumeStage = "H1_SOURCE" if population == "source" else "H1_TRANSFER"
        if not self._stage_complete(state, stage):
            raise Exp2ResumeError("paired metrics emitted before H1 stage terminal coverage")
        h0 = [self._last_receipt(state, "H0", "H0", case.case_id) for case in self._stage_cases(state, stage)]
        h1 = [self._last_receipt(state, stage, "H1", case.case_id) for case in self._stage_cases(state, stage)]
        expected = reduce_exp2_pairs([item for item in h0 if item is not None], [item for item in h1 if item is not None], population=population)
        if metrics.to_dict() != expected.to_dict():
            raise Exp2ResumeError("paired metrics differ from terminal case receipts")

    @staticmethod
    def _transfer_decision(metrics: Exp2PairedMetrics) -> str:
        if metrics.invalid_any:
            return "blocked_invalid"
        if metrics.cells["observed-regression"]:
            return "rollback"
        if metrics.cells["rescue"]:
            return "retain_transfer_rescue"
        return "retain_no_gain"

    def status(self) -> dict[str, Any]:
        state = self._replay()
        next_stage = self._expected_stage(state)
        return {
            "schema": "autobugfix-exp2-resume-status-v2",
            "study_id": self.study_id,
            "study_kind": state.plan.study_kind,
            "state": state.state,
            "next_stage": next_stage,
            "open_intents": [
                intent.to_dict()
                for key, intents in state.intents.items()
                if len(intents) > len(state.receipts.get(key) or [])
                for intent in intents[-1:]
            ],
            "terminal_receipt_count": sum(len(items) for items in state.receipts.values()),
            "execution_ready": state.protocol.execution_ready,
            "calibration_terminal_digest": state.calibration_terminal.record_digest if state.calibration_terminal else None,
            "pilot_terminal_digest": state.pilot_terminal.record_digest if state.pilot_terminal else None,
            "source_projection_bundle_digest": state.source_bundle.record_digest if state.source_bundle else None,
            "candidate_transition_digest": state.candidate_transition.record_digest if state.candidate_transition else None,
            "decision": state.decision,
            "rollback_authorization_digest": (
                state.rollback_authorization.record_digest
                if state.rollback_authorization
                else None
            ),
            "rollback_digest": state.rollback.record_digest if state.rollback else None,
            "report_digest": state.report_digest,
            "last_event_digest": state.last_event_digest,
        }

    def load_candidate_transition(self) -> Exp2CandidateTransitionReceipt | None:
        """Return the replay-verified candidate transition, when one is locked."""

        return self._replay().candidate_transition

    def candidate_handoff_context(self) -> dict[str, Any]:
        """Return only the trusted references needed by the Operator exporter."""

        state = self._replay()
        if (
            state.state != "CANDIDATE_TRANSITION_AWAITING"
            or state.attribution is None
            or state.source_bundle is None
        ):
            raise Exp2ResumeError(
                "Exp2 study is not awaiting an Operator candidate transition"
            )
        return {
            "study_id": self.study_id,
            "attribution_digest": state.attribution.record_digest,
            "source_projection_bundle_digest": state.source_bundle.record_digest,
            "execution_scope": list(state.attribution.execution_scope),
            "allowed_paths": list(state.protocol.execution_allowlist),
            "runtime_digest": state.plan.runtime_digest,
        }

    def rollback_authorization(self) -> dict[str, Any]:
        """Return the Eval-issued authorization required before Operator rollback."""

        state = self._replay()
        path = self.state_root / "rollback-authorization.yaml"
        if (
            state.state != "ROLLBACK_AWAITING"
            or state.rollback_authorization is None
            or path.is_symlink()
            or not path.is_file()
        ):
            raise Exp2ResumeError(
                "Exp2 study is not authorized for Operator rollback"
            )
        persisted = _read_immutable_record(
            path,
            "Exp2 rollback authorization",
        )
        if persisted != state.rollback_authorization.to_dict():
            raise Exp2ResumeError(
                "Exp2 rollback authorization artifact drift"
            )
        return {
            "authorization": persisted,
            "authorization_path": str(path.resolve()),
        }

    def _new_intent(self, state: _ReplayState, stage: ResumeStage, case: Exp2ResumeCase, kind: AttemptKind) -> Exp2CaseAttemptIntent:
        arm = self._stage_arm(stage)
        binding_digest = state.plan.h0_binding_digest if arm == "H0" else state.candidate_transition.binding.record_digest if state.candidate_transition else ""
        subject_sha = state.plan.h0_subject_sha if arm == "H0" else state.candidate_transition.binding.candidate_sha if state.candidate_transition else ""
        subject_tree = state.plan.h0_subject_tree if arm == "H0" else state.candidate_transition.binding.candidate_tree if state.candidate_transition else ""
        sequence = len(state.intents.get((stage, arm, case.case_id), [])) + 1
        run_hash = hashlib.sha256(f"{self.study_id}:{stage}:{arm}:{case.case_id}:{kind}:{sequence}".encode()).hexdigest()[:16]
        run_id = f"exp2v2-{stage.lower()}-{case.order:02d}-{run_hash}"
        previous = self._last_receipt(state, stage, arm, case.case_id)
        retry_of_receipt_digest = None
        frozen_submission_digest = None
        retry_source_output_root = None
        if kind == "scorer_only_retry":
            if previous is None or previous.submission_digest is None:
                raise Exp2ResumeError(
                    "scorer-only retry has no prior frozen submission"
                )
            retry_of_receipt_digest = previous.record_digest
            frozen_submission_digest = previous.submission_digest
            prior_intents = state.intents.get((stage, arm, case.case_id)) or []
            if not prior_intents:
                raise Exp2ResumeError(
                    "scorer-only retry has no prior execution intent"
                )
            retry_source_output_root = prior_intents[-1].output_root
        return Exp2CaseAttemptIntent(
            study_id=self.study_id,
            study_kind=state.plan.study_kind,
            stage=stage,
            arm=arm,
            case_id=case.case_id,
            slice=case.slice,
            run_id=run_id,
            output_root=str((Path(state.plan.artifact_root) / "runs" / run_id).resolve()),
            subject_sha=subject_sha,
            subject_tree=subject_tree,
            frozen_input_digest=self._frozen_input_digest(state, arm, binding_digest),
            binding_digest=binding_digest,
            attempt_kind=kind,
            retry_of_receipt_digest=retry_of_receipt_digest,
            frozen_submission_digest=frozen_submission_digest,
            retry_source_output_root=retry_source_output_root,
            predecessor_event_digest=state.last_event_digest,
        )

    def _open_intent(self, state: _ReplayState, stage: ResumeStage) -> Exp2CaseAttemptIntent | None:
        arm = self._stage_arm(stage)
        for case in self._stage_cases(state, stage):
            key = (stage, arm, case.case_id)
            intents = state.intents.get(key) or []
            receipts = state.receipts.get(key) or []
            if len(intents) > len(receipts):
                return intents[-1]
        return None

    def _next_case(self, state: _ReplayState, stage: ResumeStage) -> tuple[Exp2ResumeCase, AttemptKind] | None:
        arm = self._stage_arm(stage)
        for case in self._stage_cases(state, stage):
            receipts = state.receipts.get((stage, arm, case.case_id)) or []
            if not receipts:
                return case, "execution"
            if self._case_needs_retry(receipts):
                return case, "scorer_only_retry"
        return None

    def _receipt_from_result(self, intent: Exp2CaseAttemptIntent, result: Mapping[str, Any] | Exp2CaseAttemptReceipt) -> Exp2CaseAttemptReceipt:
        if isinstance(result, Exp2CaseAttemptReceipt):
            return result
        if "terminal_receipt" in result:
            raw = result["terminal_receipt"]
            if not isinstance(raw, Mapping):
                raise Exp2ResumeError("executor terminal_receipt must be a mapping")
            return Exp2CaseAttemptReceipt.from_dict(raw)
        report = result.get("report") if isinstance(result, Mapping) else None
        if report is None and result.get("official_result") is not None:
            report = result
        if isinstance(report, Mapping):
            return Exp2CaseAttemptReceipt.from_official_report(
                intent,
                report,
                writer_attempts=int(result.get("writer_attempts") or (0 if intent.attempt_kind == "scorer_only_retry" else 1)),
                failure_stage=str(result.get("failure_stage") or "unknown"),  # type: ignore[arg-type]
                image_digest=(str(result["image_digest"]) if result.get("image_digest") else None),
                runtime_digest=(str(result["runtime_digest"]) if result.get("runtime_digest") else None),
                usage_digest=(str(result["usage_digest"]) if result.get("usage_digest") else None),
            )
        raise Exp2ResumeError("executor result must carry an official report or terminal receipt")

    def _record_terminal(self, state: _ReplayState, intent: Exp2CaseAttemptIntent, receipt: Exp2CaseAttemptReceipt) -> dict[str, Any]:
        self._validate_receipt(state, intent, receipt)
        self._append(
            "case_attempt_terminal",
            receipt.to_dict(),
            expected_predecessor=state.last_event_digest,
        )
        state = self._replay()
        self._advance_if_complete(state, intent.stage)
        return self.status()

    def _advance_if_complete(self, state: _ReplayState, stage: ResumeStage) -> None:
        if not self._stage_complete(state, stage):
            return
        if stage == "CALIBRATION":
            receipts = [self._last_receipt(state, "CALIBRATION", "H0", item.case_id) for item in state.protocol.calibration_cases]
            values = [item for item in receipts if item is not None]
            terminal = Exp2CalibrationTerminalReceipt(
                study_id=self.study_id,
                plan_digest=state.plan.record_digest,
                protocol_digest=state.protocol.record_digest,
                apparatus_receipt_digest=state.plan.apparatus_receipt_digest,
                scorer_digest=state.plan.scorer_digest,
                runtime_digest=state.plan.runtime_digest,
                memory_fixture_digest=state.plan.memory_fixture_digest,
                case_receipt_digests=tuple(item.record_digest for item in values),
                status="CALIBRATION_COMPLETE" if all(item.terminal_status == "official_terminal" for item in values) else "CALIBRATION_BLOCKED",
                created_at=max(item.terminal_at for item in values),
            )
            self._write_once(
                self.state_root / "calibration-terminal-receipt.yaml",
                terminal.to_dict(),
            )
            self._append(
                "calibration_terminal",
                terminal.to_dict(),
                expected_predecessor=state.last_event_digest,
            )
        elif stage == "H0":
            receipts = [self._last_receipt(state, "H0", "H0", item.case_id) for item in state.protocol.h0_cases]
            values = [item for item in receipts if item is not None]
            if any(item.terminal_status != "official_terminal" for item in values):
                reason = "h0_invalid"
                decision = "blocked_invalid"
                bundle = None
            else:
                bundle, feasibility = self._build_source_bundle(state, values)
                reason = {
                    "floor": "h0_floor",
                    "saturation": "h0_saturation",
                    "no_legal_adaptation_signal": "no_legal_adaptation_signal",
                }.get(feasibility)
                decision = "no_signal"
            if bundle is not None and feasibility == "passed":
                self._write_once(
                    self.state_root / "source-projection-bundle.yaml",
                    bundle.to_dict(),
                )
                self._append(
                    "source_bundle_released",
                    bundle.to_dict(),
                    expected_predecessor=state.last_event_digest,
                )
            else:
                if reason is None:
                    raise Exp2ResumeError("unsupported H0 feasibility decision")
                terminal = Exp2PilotTerminalReceipt(
                    study_id=self.study_id,
                    plan_digest=state.plan.record_digest,
                    protocol_digest=state.protocol.record_digest,
                    reason=reason,  # type: ignore[arg-type]
                    decision=decision,  # type: ignore[arg-type]
                    h0_case_receipt_digests=tuple(
                        item.record_digest for item in values
                    ),
                )
                self._append(
                    "pilot_terminal",
                    terminal.to_dict(),
                    expected_predecessor=state.last_event_digest,
                )
        elif stage == "H1_SOURCE":
            metrics = self._reduce_population(state, "source")
            self._append(
                "source_replay_complete",
                {"metrics": metrics.to_dict()},
                expected_predecessor=state.last_event_digest,
            )
        elif stage == "H1_TRANSFER":
            metrics = self._reduce_population(state, "transfer")
            decision = self._transfer_decision(metrics)
            authorization = None
            if decision == "rollback":
                if state.candidate_transition is None:
                    raise Exp2ResumeError(
                        "rollback decision lacks a locked candidate"
                    )
                authorization = Exp2RollbackAuthorization(
                    study_id=self.study_id,
                    issuer="exp2-eval-coordinator-v2",
                    candidate_transition_digest=(
                        state.candidate_transition.record_digest
                    ),
                    transfer_metrics_digest=metrics.record_digest,
                    created_at=max(
                        self._last_receipt(
                            state,
                            "H1_TRANSFER",
                            "H1",
                            case.case_id,
                        ).terminal_at
                        for case in state.protocol.transfer_cases
                    ),
                )
                self._write_once(
                    self.state_root / "rollback-authorization.yaml",
                    authorization.to_dict(),
                )
            self._append(
                "transfer_decided",
                {
                    "metrics": metrics.to_dict(),
                    "decision": decision,
                    "rollback_authorization": (
                        authorization.to_dict() if authorization else None
                    ),
                },
                expected_predecessor=state.last_event_digest,
            )

    @staticmethod
    def _empty_metrics(population: Literal["source", "transfer"]) -> Exp2PairedMetrics:
        cases = tuple(item.case_id for item in _H0_CASES if item.slice == population)
        cells = {"both-pass": 0, "both-fail": 0, "rescue": 0, "observed-regression": 0, "invalid-H0-only": 0, "invalid-H1-only": 0, "both-invalid": len(cases)}
        return Exp2PairedMetrics(population, cases, cells, 0, 0, None)

    def _reduce_population(self, state: _ReplayState, population: Literal["source", "transfer"]) -> Exp2PairedMetrics:
        stage: ResumeStage = "H1_SOURCE" if population == "source" else "H1_TRANSFER"
        cases = self._stage_cases(state, stage)
        h0 = [self._last_receipt(state, "H0", "H0", item.case_id) for item in cases]
        h1 = [self._last_receipt(state, stage, "H1", item.case_id) for item in cases]
        return reduce_exp2_pairs([item for item in h0 if item is not None], [item for item in h1 if item is not None], population=population)

    def resume(
        self,
        executor: Callable[[Mapping[str, Any]], Mapping[str, Any] | Exp2CaseAttemptReceipt] | None = None,
        *,
        reconciler: Callable[[Mapping[str, Any]], Mapping[str, Any] | Exp2CaseAttemptReceipt | None] | None = None,
    ) -> dict[str, Any]:
        """Execute/reconcile one case only.  A completed Execution is never rerun."""

        state = self._replay()
        terminal = {"CALIBRATION_COMPLETE", "CALIBRATION_BLOCKED", "PILOT_COMPLETE", "ROLLBACK_AWAITING", "ROLLED_BACK", "BLOCKED", "REPORTED", "SOURCE_RELEASED", "CANDIDATE_TRANSITION_AWAITING"}
        if state.state in terminal:
            return {"status": "terminal" if state.state not in {"SOURCE_RELEASED", "CANDIDATE_TRANSITION_AWAITING", "ROLLBACK_AWAITING"} else "blocked", **self.status()}
        stage = self._expected_stage(state)
        if stage is None:
            return {"status": "blocked", **self.status()}
        open_intent = self._open_intent(state, stage)
        if open_intent is not None:
            if reconciler is None:
                return {"status": "reconciliation_required", **self.status()}
            outcome = reconciler(open_intent.to_dict())
            if outcome is None:
                return {"status": "reconciliation_required", **self.status()}
            receipt = self._receipt_from_result(open_intent, outcome)
            return self._record_terminal(state, open_intent, receipt)
        if executor is None:
            return {"status": "ready", **self.status()}
        if not state.protocol.execution_ready:
            raise Exp2ResumeError("Exp2 v2 protocol has no resolved OCI identities; execution is blocked before SDK dispatch")
        next_case = self._next_case(state, stage)
        if next_case is None:
            self._advance_if_complete(state, stage)
            return {"status": "advanced", **self.status()}
        case, kind = next_case
        intent = self._new_intent(state, stage, case, kind)
        self._validate_intent(state, intent)
        self._append(
            "case_attempt_started",
            intent.to_dict(),
            expected_predecessor=state.last_event_digest,
        )
        result = executor(intent.to_dict())
        receipt = self._receipt_from_result(intent, result)
        return self._record_terminal(self._replay(), intent, receipt)

    def record_attribution(
        self,
        attribution: Exp2AttributionHypothesis | Mapping[str, Any],
        *,
        operator_service: Any,
    ) -> dict[str, Any]:
        state = self._replay()
        item = attribution if isinstance(attribution, Exp2AttributionHypothesis) else Exp2AttributionHypothesis.from_dict(attribution)
        if operator_service is None or not hasattr(
            operator_service, "verify_exp2_attribution"
        ):
            raise Exp2ResumeError(
                "Exp2 attribution requires OperatorGovernanceService verification"
            )
        try:
            verified = operator_service.verify_exp2_attribution(item.to_dict())
        except Exception as exc:
            raise Exp2ResumeError(
                "Operator service rejected the Exp2 attribution"
            ) from exc
        if (
            not isinstance(verified, Mapping)
            or Exp2AttributionHypothesis.from_dict(verified).to_dict()
            != item.to_dict()
        ):
            raise Exp2ResumeError(
                "Operator service returned a different Exp2 attribution"
            )
        if state.state != "SOURCE_RELEASED" or state.source_bundle is None:
            raise Exp2ResumeError("Exp2 v2 coordinator is not awaiting source attribution")
        if item.study_id != self.study_id or item.source_projection_bundle_digest != state.source_bundle.record_digest:
            raise Exp2ResumeError("Exp2 v2 attribution does not bind the source bundle")
        if (
            state.plan.h0_binding_path is None
            or _read_immutable_record(
                Path(state.plan.h0_binding_path),
                "Exp2 H0 Study binding",
            ).get("study_id")
            != item.operator_study_id
        ):
            raise Exp2ResumeError(
                "Exp2 attribution belongs to another Operator Study"
            )
        if any(path not in state.protocol.execution_allowlist for path in item.execution_scope):
            raise Exp2ResumeError("Exp2 v2 attribution scope is not allowlisted by the frozen protocol")
        self._write_once(
            self.state_root / "attribution.yaml",
            item.to_dict(),
        )
        self._append(
            "attribution_recorded",
            item.to_dict(),
            expected_predecessor=state.last_event_digest,
        )
        return self.status()

    def record_candidate_transition(
        self,
        transition: Exp2CandidateTransitionReceipt | Mapping[str, Any],
        *,
        operator_service: Any,
    ) -> dict[str, Any]:
        """Lock only a receipt independently derived by the Operator service."""

        if operator_service is None or not hasattr(operator_service, "verify_exp2_candidate_transition"):
            raise Exp2ResumeError("candidate transition requires OperatorGovernanceService verification")
        item = transition if isinstance(transition, Exp2CandidateTransitionReceipt) else Exp2CandidateTransitionReceipt.from_dict(transition)
        try:
            verified = operator_service.verify_exp2_candidate_transition(item.to_dict())
        except Exception as exc:
            raise Exp2ResumeError("Operator service rejected candidate transition receipt") from exc
        if isinstance(verified, Mapping):
            verified_item = Exp2CandidateTransitionReceipt.from_dict(verified)
            if verified_item.to_dict() != item.to_dict():
                raise Exp2ResumeError("Operator service returned a different candidate transition receipt")
        state = self._replay()
        self._validate_candidate_transition(state, item)
        self._append(
            "candidate_transition_locked",
            item.to_dict(),
            expected_predecessor=state.last_event_digest,
        )
        return self.status()

    def record_rollback(
        self,
        rollback: Exp2RollbackReceipt | Mapping[str, Any],
        *,
        operator_service: Any,
    ) -> dict[str, Any]:
        if operator_service is None or not hasattr(operator_service, "verify_exp2_rollback_receipt"):
            raise Exp2ResumeError("rollback requires OperatorGovernanceService verification")
        item = rollback if isinstance(rollback, Exp2RollbackReceipt) else Exp2RollbackReceipt.from_dict(rollback)
        try:
            verified = operator_service.verify_exp2_rollback_receipt(item.to_dict())
        except Exception as exc:
            raise Exp2ResumeError("Operator service rejected rollback receipt") from exc
        if isinstance(verified, Mapping) and Exp2RollbackReceipt.from_dict(verified).to_dict() != item.to_dict():
            raise Exp2ResumeError("Operator service returned a different rollback receipt")
        state = self._replay()
        if (
            state.state != "ROLLBACK_AWAITING"
            or state.candidate_transition is None
            or state.rollback_authorization is None
            or item.candidate_transition_digest
            != state.candidate_transition.record_digest
            or item.rollback_authorization_digest
            != state.rollback_authorization.record_digest
        ):
            raise Exp2ResumeError("rollback receipt is not bound to the awaiting candidate")
        self._append(
            "rollback_recorded",
            item.to_dict(),
            expected_predecessor=state.last_event_digest,
        )
        return self.status()

    @staticmethod
    def _optional_sum(values: Sequence[int | float | None]) -> int | float | None:
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    def _population_metrics(
        self,
        state: _ReplayState,
        stage: ResumeStage,
    ) -> dict[str, Any]:
        arm = self._stage_arm(stage)
        cases = self._stage_cases(state, stage)
        case_ids = tuple(item.case_id for item in cases)
        all_intents = [
            intent
            for case_id in case_ids
            for intent in state.intents.get((stage, arm, case_id), ())
        ]
        receipt_groups = [
            state.receipts.get((stage, arm, case_id), ()) for case_id in case_ids
        ]
        attempt_receipts = [item for items in receipt_groups for item in items]
        terminal = [items[-1] for items in receipt_groups if items]
        official = [
            item for item in terminal if item.terminal_status == "official_terminal"
        ]
        invalid = [
            item for item in terminal if item.terminal_status != "official_terminal"
        ]
        scheduled = len(case_ids)
        started_cases = sum(bool(items) for items in (
            state.intents.get((stage, arm, case_id), ()) for case_id in case_ids
        ))
        normal_attempts = [
            item for item in all_intents if item.attempt_kind == "execution"
        ]
        scorer_retries = [
            item for item in all_intents if item.attempt_kind == "scorer_only_retry"
        ]
        reexecutions = sum(
            max(
                0,
                sum(
                    intent.attempt_kind == "execution"
                    for intent in state.intents.get((stage, arm, case_id), ())
                )
                - 1,
            )
            for case_id in case_ids
        )
        preflight_rejections = [
            item for item in terminal if item.terminal_status == "preflight_rejected"
        ]
        timestamps = []
        for item in all_intents:
            try:
                timestamps.append(datetime.fromisoformat(item.started_at.replace("Z", "+00:00")))
            except ValueError:
                pass
        terminal_timestamps = []
        for item in terminal:
            try:
                terminal_timestamps.append(datetime.fromisoformat(item.terminal_at.replace("Z", "+00:00")))
            except ValueError:
                pass
        wall_time = None
        if timestamps and terminal_timestamps:
            wall_time = max(
                0.0,
                (max(terminal_timestamps) - min(timestamps)).total_seconds(),
            )
        writer_attempts_by_case = {
            case_id: sum(
                item.writer_attempts
                for item in state.receipts.get((stage, arm, case_id), ())
            )
            for case_id in case_ids
        }
        first_attempt_resolved = sum(
            item.resolved is True
            and writer_attempts_by_case[item.case_id] == 1
            for item in official
        )
        second_writer_cases = sum(
            attempts >= 2 for attempts in writer_attempts_by_case.values()
        )
        loop_rescues = sum(item.loop_rescue is True for item in terminal)
        patch_receipts = [
            item for item in terminal if item.empty_patch is not None
        ]
        empty_patches = sum(item.empty_patch is True for item in patch_receipts)
        complete_receipts = sum(
            (
                item.report_digest is not None
                and item.submission_digest is not None
                and item.official_result_digest is not None
                and item.noninterference_digest is not None
                and item.execution_receipt_digest is not None
            )
            if item.terminal_status == "official_terminal"
            else item.failure_artifact_digest is not None
            for item in terminal
        )
        valid_noninterference = sum(
            item.noninterference_digest is not None for item in official
        )
        failure_stage_counts = {
            name: sum(item.failure_stage == name for item in terminal)
            for name in (
                "execution",
                "visible_verifier",
                "official_eval",
                "infrastructure",
                "unknown",
            )
        }
        return {
            "scheduled_cases": scheduled,
            "started_cases": started_cases,
            "terminal_cases": len(terminal),
            "official_terminal_cases": len(official),
            "invalid_terminal_cases": len(invalid),
            "started_coverage": started_cases / scheduled,
            "terminal_coverage": len(terminal) / scheduled,
            "official_coverage": len(official) / scheduled,
            "invalid_coverage": len(invalid) / scheduled,
            "normal_execution_attempts": len(normal_attempts),
            "scorer_only_retries": len(scorer_retries),
            "completed_case_reexecution_count": reexecutions,
            "preflight_rejections": len(preflight_rejections),
            "sdk_after_rejected_preflight": sum(
                item.sdk_call_occurred for item in preflight_rejections
            ),
            "complete_terminal_receipts": complete_receipts,
            "evidence_completeness_denominator": len(terminal),
            "evidence_completeness": (
                complete_receipts / len(terminal) if terminal else None
            ),
            "valid_noninterference_receipts": valid_noninterference,
            "noninterference_denominator": len(official),
            "noninterference_validity": (
                valid_noninterference / len(official) if official else None
            ),
            "writer_attempts": sum(
                item.writer_attempts for item in attempt_receipts
            ),
            "model_calls": sum(item.model_calls for item in attempt_receipts),
            "input_tokens": self._optional_sum(
                [item.input_tokens for item in attempt_receipts]
            ),
            "cached_input_tokens": self._optional_sum(
                [item.cached_input_tokens for item in attempt_receipts]
            ),
            "output_tokens": self._optional_sum(
                [item.output_tokens for item in attempt_receipts]
            ),
            "reasoning_tokens": self._optional_sum(
                [item.reasoning_tokens for item in attempt_receipts]
            ),
            "model_time_seconds": self._optional_sum(
                [item.model_time_seconds for item in attempt_receipts]
            ),
            "stage_wall_time_seconds": wall_time,
            "model_cost_usd": None,
            "pricing_snapshot_digest": None,
            "first_attempt_resolved": first_attempt_resolved,
            "first_attempt_resolved_denominator": len(official),
            "first_attempt_resolved_rate": (
                first_attempt_resolved / len(official) if official else None
            ),
            "second_writer_attempt_cases": second_writer_cases,
            "loop_rescue": loop_rescues,
            "loop_rescue_rate": (
                loop_rescues / second_writer_cases
                if second_writer_cases
                else None
            ),
            "failure_stage_counts": failure_stage_counts,
            "empty_patches": empty_patches,
            "frozen_submission_count": len(patch_receipts),
            "empty_patch_rate": (
                empty_patches / len(patch_receipts)
                if patch_receipts
                else None
            ),
            "changed_files": self._optional_sum(
                [item.changed_files for item in patch_receipts]
            ),
            "changed_lines": self._optional_sum(
                [item.changed_lines for item in patch_receipts]
            ),
        }

    def _run_metrics(self, state: _ReplayState) -> dict[str, Any]:
        populations: dict[str, Any] = {}
        if state.plan.study_kind == "calibration":
            populations["calibration"] = self._population_metrics(
                state, "CALIBRATION"
            )
        else:
            populations["h0"] = self._population_metrics(state, "H0")
            if any(key[0] == "H1_SOURCE" for key in state.intents):
                populations["source"] = self._population_metrics(
                    state, "H1_SOURCE"
                )
            if any(key[0] == "H1_TRANSFER" for key in state.intents):
                populations["transfer"] = self._population_metrics(
                    state, "H1_TRANSFER"
                )
        h0_receipts = [
            self._last_receipt(state, "H0", "H0", case.case_id)
            for case in state.protocol.h0_cases
        ] if state.plan.study_kind == "resume_pilot" else []
        h0_values = [item for item in h0_receipts if item is not None]
        h0_valid = len(h0_values) == 10 and all(
            item.terminal_status == "official_terminal" for item in h0_values
        )
        h0_resolved = sum(
            item.resolved is True
            for item in h0_values
            if item.terminal_status == "official_terminal"
        )
        return {
            "populations": populations,
            "h0_baseline": {
                "scheduled_cases": 10,
                "resolved": h0_resolved,
                "resolved_rate": h0_resolved / 10 if h0_valid else None,
                "apparatus_valid": h0_valid,
            } if state.plan.study_kind == "resume_pilot" else None,
            "source_paired": (
                state.source_metrics.to_dict() if state.source_metrics else None
            ),
            "transfer_paired": (
                state.transfer_metrics.to_dict() if state.transfer_metrics else None
            ),
            "incremental_h1_cost_per_transfer_rescue_usd": None,
        }

    def _report_payload(self, state: _ReplayState) -> dict[str, Any]:
        receipt_rows = []
        for stage, arm, case_id in sorted(state.receipts):
            for attempt_index, receipt in enumerate(
                state.receipts[(stage, arm, case_id)],
                start=1,
            ):
                receipt_rows.append({
                    "stage": stage,
                    "arm": arm,
                    "case_id": case_id,
                    "attempt_index": attempt_index,
                    "terminal_status": receipt.terminal_status,
                    "resolved": receipt.resolved,
                    "receipt_digest": receipt.record_digest,
                    "writer_attempts": receipt.writer_attempts,
                    "attempt_kind": receipt.attempt_kind,
                    "model_calls": receipt.model_calls,
                    "input_tokens": receipt.input_tokens,
                    "cached_input_tokens": receipt.cached_input_tokens,
                    "output_tokens": receipt.output_tokens,
                    "reasoning_tokens": receipt.reasoning_tokens,
                    "model_time_seconds": receipt.model_time_seconds,
                    "first_verifier_outcome": receipt.first_verifier_outcome,
                    "loop_rescue": receipt.loop_rescue,
                    "changed_files": receipt.changed_files,
                    "changed_lines": receipt.changed_lines,
                    "empty_patch": receipt.empty_patch,
                    "sdk_call_occurred": receipt.sdk_call_occurred,
                    "usage_digest": receipt.usage_digest,
                })
        composition = [item.to_dict() for item in (*state.protocol.calibration_cases, *state.protocol.h0_cases)]
        report = {
            "schema": "autobugfix-exp2-resume-report-v2",
            "study_id": self.study_id,
            "study_kind": state.plan.study_kind,
            "state_before_report": state.state,
            "decision": state.decision or ("calibration" if state.plan.study_kind == "calibration" else "blocked_invalid"),
            "frozen_identities": {
                "plan_digest": state.plan.record_digest,
                "protocol_digest": state.protocol.record_digest,
                "apparatus_sha": state.plan.apparatus_sha,
                "apparatus_tree": state.plan.apparatus_tree,
                "apparatus_receipt_digest": state.plan.apparatus_receipt_digest,
                "dataset_revision": state.protocol.dataset_revision,
                "scorer_digest": state.plan.scorer_digest,
                "runtime_digest": state.plan.runtime_digest,
                "memory_fixture_digest": state.plan.memory_fixture_digest,
                "operator_policy_digest": state.plan.operator_policy_digest,
                "operator_role_skill_digest": state.plan.operator_role_skill_digest,
                "execution_role_skill_digest": state.plan.execution_role_skill_digest,
                "execution_mode": state.protocol.execution_mode,
                "selected_images_digest": state.plan.selected_images_digest,
            },
            "case_composition": composition,
            "case_attempts": receipt_rows,
            "metrics": self._run_metrics(state),
            "source_paired": state.source_metrics.to_dict() if state.source_metrics else None,
            "transfer_paired": state.transfer_metrics.to_dict() if state.transfer_metrics else None,
            "reserve": "not_run",
            "live": "not_run",
            "pro": "not_run",
            "candidate_transition_digest": state.candidate_transition.record_digest if state.candidate_transition else None,
            "candidate_binding_digest": state.candidate_transition.binding.record_digest if state.candidate_transition else None,
            "pilot_terminal_digest": state.pilot_terminal.record_digest if state.pilot_terminal else None,
            "rollback_authorization_digest": (
                state.rollback_authorization.record_digest
                if state.rollback_authorization
                else None
            ),
            "rollback_receipt_digest": state.rollback.record_digest if state.rollback else None,
            "limitations": [
                "Calibration is apparatus evidence and has no capability rate.",
                "Source results are selection-exposed development evidence.",
                "Transfer results are three-repository optimizer-unexposed pilot evidence.",
                "Reserve, Live, and Pro were not run.",
                "No statistical or population-level claim is made.",
            ],
            "commands": [
                "autobugfix eval exp2 init --study-kind calibration ...",
                "autobugfix eval exp2 resume --study-id <calibration> --execute",
                "autobugfix eval exp2 init --study-kind resume_pilot ...",
                "autobugfix eval exp2 resume --study-id <pilot> --execute",
            ],
        }
        violations = lint_exp2_claims(yaml.safe_dump(report, sort_keys=False))
        if violations:
            raise Exp2ResumeError("Exp2 report claim lint failed: " + ", ".join(violations))
        return record_with_digest(report)

    @staticmethod
    def _markdown_report(payload: Mapping[str, Any]) -> str:
        return "\n".join(
            (
                "# Exp2 resume-first MVP report",
                "",
                f"Study: `{payload['study_id']}`",
                f"Decision: `{payload['decision']}`",
                "",
                "This is a bounded apparatus/development/transfer pilot. It makes no statistical, population-level, leaderboard, or production-safety claim.",
                "",
                "## Reproducibility",
                "",
                f"- Report digest: `{payload['record_digest']}`",
                f"- Protocol digest: `{payload['frozen_identities']['protocol_digest']}`",
                f"- Candidate transition: `{payload.get('candidate_transition_digest')}`",
                "",
                "## Limits",
                "",
                *[f"- {item}" for item in payload["limitations"]],
                "",
            )
        )

    def publish_report(self) -> dict[str, Any]:
        state = self._replay()
        if state.state not in {"CALIBRATION_COMPLETE", "CALIBRATION_BLOCKED", "PILOT_COMPLETE", "ROLLED_BACK", "BLOCKED"}:
            raise Exp2ResumeError("Exp2 report requires a terminal calibration or pilot state")
        payload = self._report_payload(state)
        reports = self.state_root / "reports"
        reports.mkdir(parents=True, mode=0o700, exist_ok=True)
        report_digest = str(payload["record_digest"])
        yaml_path = reports / f"{report_digest}.yaml"
        markdown_path = reports / f"{report_digest}.md"
        index_path = reports / "reproducibility-index.yaml"
        self._write_once(yaml_path, payload)
        markdown = self._markdown_report(payload)
        if markdown_path.exists():
            if markdown_path.is_symlink() or markdown_path.read_text(encoding="utf-8") != markdown:
                raise Exp2ResumeError("immutable Exp2 Markdown report already differs")
        else:
            markdown_path.write_text(markdown, encoding="utf-8")
        index = record_with_digest(
            {
                "schema": "autobugfix-exp2-reproducibility-index-v2",
                "study_id": self.study_id,
                "report_digest": report_digest,
                "report_yaml_sha256": digest_file(yaml_path),
                "report_markdown_sha256": digest_file(markdown_path),
                "event_chain_tip": state.last_event_digest,
                "case_receipt_digests": [row["receipt_digest"] for row in payload["case_attempts"]],
                "pilot_terminal_digest": payload.get("pilot_terminal_digest"),
                "rollback_authorization_digest": payload.get(
                    "rollback_authorization_digest"
                ),
                "candidate_transition_digest": payload.get("candidate_transition_digest"),
                "rollback_receipt_digest": payload.get("rollback_receipt_digest"),
            }
        )
        self._write_once(index_path, index)
        self._append(
            "report_published",
            {
                "report_digest": report_digest,
                "reproducibility_index_digest": index["record_digest"],
            },
            expected_predecessor=state.last_event_digest,
        )
        return {"report": payload, "reproducibility_index": index, "status": self.status()}
