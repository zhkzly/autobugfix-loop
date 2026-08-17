from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.codex_sdk import (
    CodexSDKBackend,
    private_text_writer,
    write_private_bytes,
    write_private_text,
)
from autobugfix.config import load_config
from autobugfix.credential_guard import (
    credential_markers,
    redact_credential_leaks,
    snapshot_regular_files,
)
from autobugfix.evaluator import parse_evaluator_decision
from autobugfix.git_utils import GitError, git_common_dir, rev_parse, run_git
from autobugfix.memory.config import load_memory_config
from autobugfix.models import CodexRequest, CodexResult, utc_now
from autobugfix.operator.approvals import (
    approval_signing_payload,
    github_approval,
    signed_approval_from_files,
    write_signing_payload,
)
from autobugfix.operator.guard import (
    TransitionGuard,
    check_scope_authority,
    effective_request,
    max_risk,
)
from autobugfix.operator.metrics import (
    OperatorMetricsError,
    baseline_for_request,
    compare_baseline,
    derive_metric_receipt,
    portable_profile_values,
    read_baseline,
    record_baseline,
)
from autobugfix.operator.metering import (
    CallbackCodexBackend,
    MeteredCodexBackend,
    StudyCallContext,
)
from autobugfix.operator.models import (
    BudgetGrantRecord,
    BudgetRequestRecord,
    CheckpointRecord,
    CheckRun,
    ExperimentLineRecord,
    FeedbackPacket,
    GateSnapshot,
    IntegrationRecord,
    OperatorApproval,
    OperatorRequest,
    OperatorTriage,
    PromotionRecord,
    ScopeRevision,
    StudyEvidenceRecord,
    StudyMetricRecord,
    StudyRecord,
    UsageEntryRecord,
    WriterRun,
    digest_payload,
    is_expired,
)
from autobugfix.operator.policy import (
    PolicyDecision,
    collect_candidate_snapshot,
    evaluate_policy,
    layers_for_file,
)
from autobugfix.operator.projection import project_request
from autobugfix.operator.prompts import semantic_verifier_prompt, supervisor_prompt, writer_prompt
from autobugfix.operator.store import OperatorStore, OperatorStoreError, safe_id
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy
from autobugfix.operator.validator import run_command_specs, run_validation_profiles
from autobugfix.operator.workspace import create_operator_workspace, recover_operator_workspace
from autobugfix.role_config import resolve_role
from autobugfix.study_binding import validate_study_binding_shape


class OperatorGovernanceError(RuntimeError):
    pass


_METRIC_CONDITION = re.compile(r"^(<=|>=|==|!=|<|>)\s*(-?(?:\d+(?:\.\d*)?|\.\d+))$")


def _metric_condition_passes(actual: bool | int | float, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return float(actual) == float(expected)
    if not isinstance(expected, str):
        raise OperatorGovernanceError(
            "success contract values must be booleans, numbers, or numeric comparisons"
        )
    match = _METRIC_CONDITION.fullmatch(expected.strip())
    if match is None:
        raise OperatorGovernanceError(
            f"unsupported success contract comparison: {expected!r}"
        )
    operator, raw_threshold = match.groups()
    observed = float(actual)
    threshold = float(raw_threshold)
    return {
        "<": observed < threshold,
        "<=": observed <= threshold,
        "==": observed == threshold,
        "!=": observed != threshold,
        ">=": observed >= threshold,
        ">": observed > threshold,
    }[operator]


def _default_expiry(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _path_digest(path: Path) -> str:
    resolved = path.expanduser().resolve()
    digest = hashlib.sha256()
    if not resolved.exists():
        digest.update(b"missing")
        return digest.hexdigest()
    if resolved.is_file():
        digest.update(b"file\0")
        digest.update(resolved.read_bytes())
        return digest.hexdigest()
    digest.update(b"directory\0")
    for item in sorted(resolved.rglob("*"), key=lambda candidate: candidate.relative_to(resolved).as_posix()):
        relative = item.relative_to(resolved).as_posix()
        if item.is_symlink():
            digest.update(f"symlink\0{relative}\0{os.readlink(item)}\0".encode("utf-8"))
        elif item.is_file():
            digest.update(f"file\0{relative}\0".encode("utf-8"))
            digest.update(item.read_bytes())
            digest.update(b"\0")
        elif item.is_dir():
            digest.update(f"directory\0{relative}\0".encode("utf-8"))
    return digest.hexdigest()


def _manifest_digest(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return _path_digest(path)
    if isinstance(raw, Mapping):
        stored = raw.get("record_digest")
        payload = {key: value for key, value in raw.items() if key != "record_digest"}
        if isinstance(stored, str) and stored == digest_payload(payload):
            return stored
    return _path_digest(path)


def _worktree_content_digest(path: Path) -> str:
    resolved = path.expanduser().resolve()
    digest = hashlib.sha256(b"worktree-content\0")
    for item in sorted(
        resolved.rglob("*"),
        key=lambda candidate: candidate.relative_to(resolved).as_posix(),
    ):
        relative = item.relative_to(resolved).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if item.is_symlink():
            digest.update(f"symlink\0{relative}\0{os.readlink(item)}\0".encode("utf-8"))
        elif item.is_file():
            digest.update(f"file\0{relative}\0".encode("utf-8"))
            digest.update(item.read_bytes())
            digest.update(b"\0")
        elif item.is_dir():
            digest.update(f"directory\0{relative}\0".encode("utf-8"))
    return digest.hexdigest()


def _leased_request(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        request_id = str(args[0] if args else kwargs["request_id"])
        with self.store.request_lease(request_id):
            return method(self, *args, **kwargs)

    return wrapped


def _leased_promotion(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        promotion_id = str(args[0] if args else kwargs["promotion_id"])
        promotion = self.store.read_promotion(promotion_id)
        with self.store.request_lease(str(promotion["request_id"])):
            return method(self, *args, **kwargs)

    return wrapped


class OperatorGovernanceService:
    """Trusted command handler and sole owner of Operator state transitions."""

    def __init__(
        self,
        project_root: Path | str = ".",
        *,
        trusted_ref: str | None = None,
        trusted_file: Path | None = None,
        bootstrap_policy: bool = False,
        allowed_signers: Path | None = None,
        backend: CodexBackend | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = load_config(self.project_root)
        operator = self.config.operator
        self.store = OperatorStore(
            self.project_root,
            state_root=operator.state.root,
            artifact_root=operator.artifacts.root,
            database_name=operator.state.database_name,
            lease_timeout_seconds=operator.state.lease_timeout_seconds,
        )
        self.trusted_ref = trusted_ref if trusted_ref is not None else operator.experiments.trusted_ref
        self.trusted_file = trusted_file
        self.bootstrap_policy = bootstrap_policy
        self.allowed_signers = allowed_signers
        self.backend = backend or CodexSDKBackend()
        self.guard = TransitionGuard()

    def policy(self) -> TrustedPolicy:
        return load_trusted_policy(
            self.project_root,
            trusted_ref=self.trusted_ref,
            trusted_file=self.trusted_file,
            bootstrap=self.bootstrap_policy,
        )

    def governance_context(self) -> dict[str, Any]:
        policy = self.policy()
        return {
            "schema": f"autobugfix-machine-constitution-v{int(policy.data['version'])}",
            "trusted": policy.trusted,
            "source": policy.source,
            "digest": digest_payload(policy.data),
            "operator_prompt_context": policy.data.get("operator_prompt_context") or "",
            "project": policy.data.get("project") or {},
            "loops": policy.data.get("loops") or {},
            "operator_roles": policy.data.get("operator_roles") or {},
            "hook_assignments": policy.data.get("hook_assignments") or {},
            "transition_contract": policy.data.get("transition_contract") or {},
            "experiment_governance": policy.data.get("experiment_governance") or {},
        }

    def _experiment_role_snapshots(self) -> dict[str, dict[str, Any]]:
        role_names = (
            "writer",
            "evaluator",
            "eval_judge",
            "operator_supervisor",
            "operator_writer",
            "operator_verifier",
        )
        return {
            name: resolve_role(self.config, name).to_dict(self.project_root)
            for name in role_names
        }

    def _experiment_config_digests(
        self,
        primary_model: str,
        *,
        subject_root: Path | None = None,
    ) -> dict[str, str]:
        roles = self._experiment_role_snapshots()
        source_root = subject_root or self.project_root
        skill_files = {
            skill
            for role in roles.values()
            for skill in role.get("skill_paths") or []
        }
        skill_digests: dict[str, str] = {}
        for value in sorted(str(item) for item in skill_files):
            path = Path(value)
            if not path.is_absolute():
                path = source_root / path
            skill_digests[value] = _path_digest(path)
        config_snapshot = {
            "roles": roles,
            "config_implementation_digest": _path_digest(
                source_root / "src/autobugfix/config.py"
            ),
            "experiment_lines": {
                "branch_template": self.config.operator.experiment_lines.branch_template,
                "remote": self.config.operator.experiment_lines.remote,
                "update_timeout_seconds": self.config.operator.experiment_lines.update_timeout_seconds,
            },
            "budgets": {
                "allowed_waves": list(self.config.operator.budgets.allowed_waves),
                "allowed_primary_models": list(
                    self.config.operator.budgets.allowed_primary_models
                ),
                "max_calls_by_wave": self.config.operator.budgets.max_calls_by_wave,
                "default_case_concurrency": self.config.operator.budgets.default_case_concurrency,
                "max_case_concurrency": self.config.operator.budgets.max_case_concurrency,
                "allow_model_fallback": self.config.operator.budgets.allow_model_fallback,
            },
        }
        return {
            "role_config_digest": digest_payload({"roles": roles}),
            "config_digest": digest_payload(config_snapshot),
            "model_digest": digest_payload({"primary_model": primary_model}),
            "skills_digest": digest_payload({"skills": skill_digests}),
            "operator_role_skill_digest": digest_payload(
                {
                    "base": _path_digest(
                        source_root / ".agents/role-skills/base"
                    ),
                    "operator": _path_digest(
                        source_root / ".agents/role-skills/operator"
                    ),
                }
            ),
            "execution_role_skill_digest": digest_payload(
                {
                    "base": _path_digest(
                        source_root / ".agents/role-skills/base"
                    ),
                    "execution": _path_digest(
                        source_root / ".agents/role-skills/execution"
                    ),
                }
            ),
        }

    def _experiment_digests_at_subject(
        self,
        *,
        study_id: str,
        subject_sha: str,
        primary_model: str,
    ) -> dict[str, str]:
        worktree = (
            self.config.operator.experiment_lines.root
            / ".study-digests"
            / f"{study_id}-{self.store.next_id('digest')}"
        ).resolve()
        worktree.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_git(
                self.project_root,
                ["worktree", "add", "--detach", str(worktree), subject_sha],
            )
            return self._experiment_config_digests(
                primary_model,
                subject_root=worktree,
            )
        finally:
            if worktree.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(worktree)],
                    check=False,
                )
            if worktree.exists():
                shutil.rmtree(worktree)
                run_git(self.project_root, ["worktree", "prune"], check=False)

    def _write_exp2_operator_record(
        self,
        category: str,
        record: Mapping[str, Any],
    ) -> Path:
        record_digest = str(record.get("record_digest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", record_digest):
            raise OperatorGovernanceError(
                "Exp2 Operator record has no valid content digest"
            )
        root = (self.config.operator.artifacts.root / category).resolve()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / f"{record_digest}.yaml"
        serialized = yaml.safe_dump(dict(record), sort_keys=False)
        if path.exists():
            if path.is_symlink() or path.read_text(encoding="utf-8") != serialized:
                raise OperatorGovernanceError(
                    "content-addressed Exp2 Operator artifact already differs"
                )
            return path
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def exp2_role_skill_digests(
        self,
        *,
        subject_sha: str,
        primary_model: str,
    ) -> dict[str, str]:
        """Resolve the frozen Operator and Execution role-skill trees."""

        digests = self._experiment_digests_at_subject(
            study_id="exp2-protocol",
            subject_sha=subject_sha,
            primary_model=primary_model,
        )
        return {
            "operator_role_skill_digest": digests[
                "operator_role_skill_digest"
            ],
            "execution_role_skill_digest": digests[
                "execution_role_skill_digest"
            ],
        }

    @staticmethod
    def exp2_empty_memory_digest() -> str:
        """Digest the exact empty Memory tree materialized for a Study."""

        with tempfile.TemporaryDirectory(prefix="autobugfix-exp2-memory-") as raw:
            root = Path(raw)
            (root / "active").mkdir()
            (root / "skills/approved").mkdir(parents=True)
            return _path_digest(root)

    @staticmethod
    def _remove_release_tree(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if item.is_symlink():
                continue
            item.chmod(0o700 if item.is_dir() else 0o600)
        path.chmod(0o700)
        shutil.rmtree(path)

    def _materialize_checkpoint_release(
        self,
        *,
        study_id: str,
        checkpoint_name: str,
        subject_sha: str,
    ) -> Path:
        release = (
            self.config.operator.experiment_lines.checkpoint_root
            / study_id
            / checkpoint_name
        ).resolve()
        if release.exists() or release.is_symlink():
            raise OperatorGovernanceError(f"checkpoint release already exists: {release}")
        staging = (
            self.config.operator.experiment_lines.root
            / ".checkpoint-staging"
            / study_id
            / f"{checkpoint_name}-{self.store.next_id('materialize')}"
        ).resolve()
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_git(
                self.project_root,
                ["worktree", "add", "--detach", str(staging), subject_sha],
            )
            release.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging, release, ignore=shutil.ignore_patterns(".git"))
        except Exception:
            self._remove_release_tree(release)
            raise
        finally:
            if staging.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(staging)],
                    check=False,
                )
        for item in sorted(release.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if not item.is_symlink():
                item.chmod(0o555 if item.is_dir() else 0o444)
        release.chmod(0o555)
        return release

    def _materialize_study_memory_snapshot(
        self,
        *,
        study_id: str,
        source: Path,
    ) -> Path:
        snapshot = (
            self.config.operator.experiment_lines.checkpoint_root
            / study_id
            / "memory-H0"
        ).resolve()
        if snapshot.exists() or snapshot.is_symlink():
            raise OperatorGovernanceError(
                f"study memory snapshot already exists: {snapshot}"
            )
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.mkdir()
        (snapshot / "active").mkdir()
        (snapshot / "skills/approved").mkdir(parents=True)
        if source.exists():
            if not source.is_dir() or source.is_symlink():
                raise OperatorGovernanceError(
                    f"study memory root must be an unredirected directory: {source}"
                )
            approved_sources = (
                (source / "active", snapshot / "active"),
                (source / "skills/approved", snapshot / "skills/approved"),
            )
            for approved_source, destination in approved_sources:
                if not approved_source.exists():
                    continue
                if (
                    approved_source.is_symlink()
                    or not approved_source.is_dir()
                    or any(item.is_symlink() for item in approved_source.rglob("*"))
                ):
                    raise OperatorGovernanceError(
                        "approved Study Memory must not be a symlink or contain symlinks"
                    )
                shutil.copytree(
                    approved_source,
                    destination,
                    dirs_exist_ok=True,
                    symlinks=False,
                )
        for item in sorted(
            snapshot.rglob("*"),
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            if not item.is_symlink():
                item.chmod(0o555 if item.is_dir() else 0o444)
        snapshot.chmod(0o555)
        return snapshot

    def _materialize_study_manifest_snapshot(
        self,
        *,
        study_id: str,
        source: Path,
    ) -> Path:
        suffix = source.suffix if source.suffix else ".data"
        snapshot = (
            self.config.operator.experiment_lines.checkpoint_root
            / study_id
            / f"manifest-H0{suffix}"
        ).resolve()
        if snapshot.exists() or snapshot.is_symlink():
            raise OperatorGovernanceError(
                f"study manifest snapshot already exists: {snapshot}"
            )
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        temporary = snapshot.with_name(f".{snapshot.name}.{self.store.next_id('manifest')}.tmp")
        temporary.write_bytes(source.read_bytes())
        temporary.replace(snapshot)
        snapshot.chmod(0o444)
        return snapshot

    def _activate_experiment_release(self, study_id: str, release: Path) -> Path:
        active = (
            self.config.operator.experiment_lines.active_release_root.resolve() / study_id
        )
        active.parent.mkdir(parents=True, exist_ok=True)
        if active.exists() and not active.is_symlink():
            raise OperatorGovernanceError(
                f"active experiment release is not a symlink: {active}"
            )
        temporary = active.with_name(f".{active.name}.{self.store.next_id('activate')}.tmp")
        relative_target = os.path.relpath(release, active.parent)
        temporary.symlink_to(relative_target)
        temporary.replace(active)
        return active

    def _validated_study_memory_snapshot(self, study: StudyRecord) -> Path:
        if not study.memory_snapshot_path:
            raise OperatorGovernanceError(
                "study predates required frozen Memory snapshot authority"
            )
        snapshot = Path(study.memory_snapshot_path).resolve()
        expected_root = (
            self.config.operator.experiment_lines.checkpoint_root
            / study.study_id
        ).resolve()
        try:
            snapshot.relative_to(expected_root)
        except ValueError as exc:
            raise OperatorGovernanceError(
                "study Memory snapshot escaped the trusted checkpoint root"
            ) from exc
        if not snapshot.is_dir():
            raise OperatorGovernanceError("study Memory snapshot is missing")
        if _path_digest(snapshot) != study.memory_digest:
            raise OperatorGovernanceError("study Memory snapshot digest mismatch")
        return snapshot

    def _validated_study_manifest_snapshot(self, study: StudyRecord) -> Path:
        if not study.manifest_snapshot_path:
            raise OperatorGovernanceError(
                "study predates required frozen benchmark manifest authority"
            )
        snapshot = Path(study.manifest_snapshot_path).resolve()
        expected_root = (
            self.config.operator.experiment_lines.checkpoint_root
            / study.study_id
        ).resolve()
        try:
            snapshot.relative_to(expected_root)
        except ValueError as exc:
            raise OperatorGovernanceError(
                "study manifest snapshot escaped the trusted checkpoint root"
            ) from exc
        if not snapshot.is_file():
            raise OperatorGovernanceError("study manifest snapshot is missing")
        if _manifest_digest(snapshot) != study.manifest_digest:
            raise OperatorGovernanceError("study manifest snapshot digest mismatch")
        return snapshot

    def study_baseline_identity(self, study_id: str) -> dict[str, str]:
        study = self.store.read_study(study_id)
        self._validated_study_manifest_snapshot(study)
        return {
            "subject_sha": study.base_subject_sha,
            "subject_tree": rev_parse(
                self.project_root,
                f"{study.base_subject_sha}^{{tree}}",
            ),
            "harness_sha": study.harness_sha,
        }

    @staticmethod
    def _study_projection(study: StudyRecord) -> dict[str, Any]:
        data = study.to_dict()
        data.pop("memory_snapshot_path", None)
        data.pop("manifest_snapshot_path", None)
        return data

    @staticmethod
    def _metric_projection(metric: StudyMetricRecord) -> dict[str, Any]:
        data = metric.to_dict()
        data.pop("artifact_path", None)
        return data

    def study_metric_projection(self, metric: StudyMetricRecord) -> dict[str, Any]:
        return self._metric_projection(metric)

    @staticmethod
    def _load_study_metric_receipt(content: bytes) -> dict[str, Any]:
        try:
            data = yaml.safe_load(content.decode("utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise OperatorGovernanceError("study metric receipt is not valid YAML") from exc
        if not isinstance(data, dict):
            raise OperatorGovernanceError("study metric receipt must be a mapping")
        stored = data.get("receipt_digest")
        payload = {key: value for key, value in data.items() if key != "receipt_digest"}
        if not stored or stored != digest_payload(payload):
            raise OperatorGovernanceError("study metric receipt digest mismatch")
        metrics = data.get("metrics")
        if not isinstance(metrics, Mapping) or not metrics:
            raise OperatorGovernanceError(
                "study metric receipt requires non-empty aggregate metrics"
            )
        if any(
            not str(key).strip()
            or isinstance(value, (dict, list, tuple, set))
            or not isinstance(value, (bool, int, float, type(None)))
            for key, value in metrics.items()
        ):
            raise OperatorGovernanceError(
                "study metric receipt metrics must be aggregate scalar values"
            )
        return data

    @staticmethod
    def _baseline_metric_receipt(
        data: Mapping[str, Any],
        *,
        study: StudyRecord,
    ) -> None:
        allowed = {
            "schema",
            "study_id",
            "line_id",
            "subject_sha",
            "manifest_digest",
            "success_contract_digest",
            "metrics",
            "receipt_digest",
            "guard_run_id",
            "evidence_digest",
        }
        if unexpected := set(data) - allowed:
            raise OperatorGovernanceError(
                "H0 metric receipt contains non-aggregate fields: "
                + ", ".join(sorted(str(item) for item in unexpected))
            )
        expected = {
            "schema": "autobugfix-study-baseline-v1",
            "study_id": study.study_id,
            "line_id": study.line_id,
            "subject_sha": study.base_subject_sha,
            "manifest_digest": study.manifest_digest,
            "success_contract_digest": digest_payload(study.success_contract),
        }
        for key, value in expected.items():
            if data.get(key) != value:
                raise OperatorGovernanceError(
                    f"H0 metric receipt {key} does not match trusted study"
                )

    @staticmethod
    def _study_metric_receipt(
        data: Mapping[str, Any],
        *,
        study: StudyRecord,
        line: ExperimentLineRecord,
        grant: BudgetGrantRecord,
        require_success: bool = True,
    ) -> dict[str, Any]:
        allowed = {
            "schema",
            "study_id",
            "line_id",
            "subject_sha",
            "wave",
            "manifest_digest",
            "success_contract_digest",
            "budget_grant_id",
            "budget_digest",
            "success_contract_passed",
            "metrics",
            "receipt_digest",
            "guard_run_id",
            "evidence_digest",
        }
        if unexpected := set(data) - allowed:
            raise OperatorGovernanceError(
                "study metric receipt contains non-aggregate fields: "
                + ", ".join(sorted(str(item) for item in unexpected))
            )
        expected = {
            "schema": "autobugfix-study-metric-v1",
            "study_id": study.study_id,
            "line_id": line.line_id,
            "subject_sha": line.head_sha,
            "wave": grant.wave,
            "manifest_digest": study.manifest_digest,
            "success_contract_digest": digest_payload(study.success_contract),
            "budget_grant_id": grant.grant_id,
            "budget_digest": grant.grant_digest,
        }
        for key, value in expected.items():
            if data.get(key) != value:
                raise OperatorGovernanceError(
                    f"study metric receipt {key} does not match trusted state"
                )
        verdict = data.get("success_contract_passed")
        if not isinstance(verdict, bool):
            raise OperatorGovernanceError(
                "study metric receipt is missing a success-contract verdict"
            )
        if require_success and verdict is not True:
            raise OperatorGovernanceError("study metric receipt did not pass the success contract")
        return dict(data)

    def register_guard_metric_receipt(
        self,
        study_id: str,
        *,
        receipt_path: Path | str,
        kind: str,
    ) -> StudyMetricRecord:
        """Register a benchmark-Guard receipt; this method is intentionally not a CLI action."""

        if kind not in {"BASELINE", "CANDIDATE"}:
            raise OperatorGovernanceError("study metric kind must be BASELINE or CANDIDATE")
        study = self.store.read_study(study_id)
        self._validated_study_memory_snapshot(study)
        self._validated_study_manifest_snapshot(study)
        source = Path(receipt_path)
        if not source.is_absolute():
            source = self.project_root / source
        source = source.resolve()
        if not source.is_file():
            raise OperatorGovernanceError(f"study metric receipt does not exist: {source}")
        content = source.read_bytes()
        data = self._load_study_metric_receipt(content)
        line_id = study.line_id
        subject_sha = study.base_subject_sha
        grant_id: str | None = None
        budget_digest: str | None = None
        wave: int | None = None
        success_contract_passed: bool | None = None
        if kind == "BASELINE":
            if self.store.read_experiment_lines(study.study_id):
                raise OperatorGovernanceError("H0 metric must be registered before line initialization")
            self._baseline_metric_receipt(data, study=study)
        else:
            line = self.store.read_experiment_line(study.line_id)
            if line.status != "CLOSED":
                raise OperatorGovernanceError(
                    "candidate metric requires a terminal experiment line"
                )
            if any(
                item.kind == "CANDIDATE"
                for item in self.store.read_study_metrics(study.study_id)
            ):
                raise OperatorGovernanceError(
                    "candidate metric is already registered for this Study"
                )
            integrations = self.store.read_integrations(line.line_id)
            if (
                not integrations
                or integrations[-1].kind != "CANDIDATE"
                or integrations[-1].result_head_sha != line.head_sha
            ):
                raise OperatorGovernanceError(
                    "candidate metric requires the current candidate integration"
                )
            grants = self.store.read_budget_grants(study.study_id)
            if not grants:
                raise OperatorGovernanceError("candidate metric requires a study budget grant")
            grant = grants[-1]
            self._study_metric_receipt(
                data,
                study=study,
                line=line,
                grant=grant,
                require_success=False,
            )
            line_id = line.line_id
            subject_sha = line.head_sha
            grant_id = grant.grant_id
            budget_digest = grant.grant_digest
            wave = grant.wave
            success_contract_passed = bool(data["success_contract_passed"])
        artifact_path, artifact_sha = self.store.write_study_metric_artifact(
            content,
            filename=source.name,
        )
        metric = StudyMetricRecord(
            metric_id=self.store.next_id("study-metric"),
            study_id=study.study_id,
            line_id=line_id,
            kind=kind,
            subject_sha=subject_sha,
            manifest_digest=study.manifest_digest,
            success_contract_digest=digest_payload(study.success_contract),
            producer="benchmark_guard",
            artifact_path=str(artifact_path),
            artifact_sha256=artifact_sha,
            receipt_digest=str(data["receipt_digest"]),
            budget_grant_id=grant_id,
            budget_digest=budget_digest,
            wave=wave,
            success_contract_passed=success_contract_passed,
        )
        self.store.write_study_metric(metric)
        return metric

    def guard_study_binding(
        self,
        study_id: str,
        *,
        kind: str,
        terminalize: bool = False,
    ) -> dict[str, Any]:
        """Derive the non-authoritative binding that an external Guard signs."""

        if kind not in {"BASELINE", "OPTIMIZATION", "CANDIDATE"}:
            raise OperatorGovernanceError(
                "Guard study binding kind must be BASELINE, OPTIMIZATION, or CANDIDATE"
            )
        if terminalize and kind != "CANDIDATE":
            raise OperatorGovernanceError(
                "only a candidate Guard binding may terminalize a Study line"
            )
        study = self.store.read_study(study_id)
        self._validated_study_memory_snapshot(study)
        self._validated_study_manifest_snapshot(study)
        payload: dict[str, Any] = {
            "schema": "autobugfix-guard-study-binding-v1",
            "kind": kind,
            "study_id": study.study_id,
            "cohort_id": study.cohort_id,
            "line_id": study.line_id,
            "subject_sha": study.base_subject_sha,
            "subject_tree": rev_parse(
                self.project_root, f"{study.base_subject_sha}^{{tree}}"
            ),
            "line_generation": 0,
            "line_status": "NOT_INITIALIZED",
            "manifest_digest": study.manifest_digest,
            "success_contract_digest": digest_payload(study.success_contract),
            "harness_sha": study.harness_sha,
            "policy_digest": study.policy_digest,
            "role_config_digest": study.role_config_digest,
            "memory_digest": study.memory_digest,
            "primary_model": study.primary_model,
            "target_checkpoint_name": study.target_checkpoint_name,
            "budget_grant_id": None,
            "budget_digest": None,
            "wave": None,
        }
        if kind == "BASELINE":
            if self.store.read_experiment_lines(study.study_id):
                raise OperatorGovernanceError(
                    "H0 Guard binding must be created before line initialization"
                )
        else:
            line = self.store.read_experiment_line(study.line_id)
            if kind == "OPTIMIZATION" and line.status != "OPEN":
                raise OperatorGovernanceError(
                    "Optimization binding requires an open experiment line"
                )
            integrations = self.store.read_integrations(line.line_id)
            if kind == "CANDIDATE":
                if line.status == "OPEN":
                    if not terminalize:
                        raise OperatorGovernanceError(
                            "candidate binding must terminalize the experiment line before scoring"
                        )
                    if (
                        not integrations
                        or integrations[-1].kind != "CANDIDATE"
                        or integrations[-1].result_head_sha != line.head_sha
                    ):
                        raise OperatorGovernanceError(
                            "candidate Guard binding requires the current candidate integration"
                        )
                    line = self.store.close_experiment_line(
                        line.line_id,
                        expected_head_sha=line.head_sha,
                        expected_generation=line.generation,
                    )
                elif line.status != "CLOSED":
                    raise OperatorGovernanceError(
                        "candidate Guard binding requires a terminal experiment line"
                    )
                if any(
                    item.kind == "CANDIDATE"
                    for item in self.store.read_study_metrics(study.study_id)
                ):
                    raise OperatorGovernanceError(
                        "candidate Guard metric is already registered for this Study"
                    )
                if (
                    not integrations
                    or integrations[-1].kind != "CANDIDATE"
                    or integrations[-1].result_head_sha != line.head_sha
                ):
                    raise OperatorGovernanceError(
                        "terminal candidate binding lacks the current candidate integration"
                    )
            grants = self.store.read_budget_grants(study.study_id)
            if not grants:
                raise OperatorGovernanceError(
                    "candidate Guard binding requires a study budget grant"
                )
            grant = grants[-1]
            payload.update(
                {
                    "subject_sha": line.head_sha,
                    "subject_tree": rev_parse(
                        self.project_root, f"{line.head_sha}^{{tree}}"
                    ),
                    "line_generation": line.generation,
                    "line_status": line.status,
                    "budget_grant_id": grant.grant_id,
                    "budget_digest": grant.grant_digest,
                    "wave": grant.wave,
                }
            )
        record = {**payload, "record_digest": digest_payload(payload)}
        validate_study_binding_shape(record)
        return record

    def verify_guard_study_binding(
        self,
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-derive a binding from trusted Operator state without mutating it."""

        validate_study_binding_shape(binding)
        supplied = self._verified_operator_record(binding, label="Study binding")
        expected = self.guard_study_binding(
            str(supplied["study_id"]),
            kind=str(supplied["kind"]),
            terminalize=False,
        )
        if supplied != expected:
            raise OperatorGovernanceError(
                "Study binding differs from current trusted Operator state"
            )
        return expected

    def study_memory_snapshot(self, study_id: str, *, expected_digest: str) -> Path:
        study = self.store.read_study(study_id)
        snapshot = self._validated_study_memory_snapshot(study)
        if study.memory_digest != expected_digest:
            raise OperatorGovernanceError(
                "Study Memory digest differs from trusted Operator state"
            )
        return snapshot

    def validate_optimization_case_binding(
        self,
        binding: Mapping[str, Any],
        *,
        case_id: str,
        first_wave: int,
    ) -> BudgetGrantRecord:
        authoritative = self.verify_guard_study_binding(binding)
        if authoritative["kind"] != "OPTIMIZATION":
            raise OperatorGovernanceError(
                "visible case budget requires an Optimization Study binding"
            )
        grant_id = authoritative.get("budget_grant_id")
        if not isinstance(grant_id, str) or not grant_id:
            raise OperatorGovernanceError("Optimization binding has no budget grant")
        grant = self.store.read_budget_grant(grant_id)
        grants = self.store.read_budget_grants(str(authoritative["study_id"]))
        if (
            not grants
            or grants[-1].grant_id != grant.grant_id
            or grant.grant_digest != authoritative.get("budget_digest")
            or grant.wave != authoritative.get("wave")
            or case_id not in grant.case_ids
            or first_wave not in {3, 8, 16}
            or first_wave > grant.wave
        ):
            raise OperatorGovernanceError(
                "Optimization case is outside the current trusted budget wave"
            )
        return grant

    @staticmethod
    def _verified_operator_record(
        raw: Mapping[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        record = dict(raw)
        stored = record.pop("record_digest", None)
        if not isinstance(stored, str) or len(stored) != 64:
            raise OperatorGovernanceError(f"{label} lacks a valid record digest")
        if digest_payload(record) != stored:
            raise OperatorGovernanceError(f"{label} record digest mismatch")
        return {**record, "record_digest": stored}

    @staticmethod
    def _load_operator_yaml(path: Path, *, label: str) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise OperatorGovernanceError(f"{label} is missing or redirected: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise OperatorGovernanceError(f"{label} is not valid YAML") from exc
        if not isinstance(raw, Mapping):
            raise OperatorGovernanceError(f"{label} must be a mapping")
        return OperatorGovernanceService._verified_operator_record(raw, label=label)

    @staticmethod
    def _verified_eval_file(
        value: Any,
        expected_sha256: Any,
        *,
        trusted_root: Path,
        label: str,
    ) -> Path:
        path = Path(str(value or ""))
        if not path.is_absolute() or path.is_symlink():
            raise OperatorGovernanceError(
                f"{label} is not an unredirected absolute artifact"
            )
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise OperatorGovernanceError(f"{label} is missing") from exc
        if path != resolved or not resolved.is_file() or not resolved.is_relative_to(
            trusted_root
        ):
            raise OperatorGovernanceError(
                f"{label} is outside trusted Eval state or redirected"
            )
        observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if expected_sha256 != observed:
            raise OperatorGovernanceError(f"{label} digest mismatch")
        return resolved

    def _validate_swe_optimization_evidence(
        self,
        *,
        study: StudyRecord,
        report: Mapping[str, Any],
        official: Mapping[str, Any],
        trusted_root: Path,
    ) -> None:
        manifest = self._load_operator_yaml(
            self._validated_study_manifest_snapshot(study),
            label="frozen SWE Study manifest",
        )
        cases = manifest.get("optimization_cases")
        matches = []
        if isinstance(cases, list):
            for raw_case in cases:
                if not isinstance(raw_case, Mapping):
                    continue
                case = self._verified_operator_record(
                    raw_case,
                    label="frozen SWE Optimization case",
                )
                visible = case.get("visible_case")
                if (
                    isinstance(visible, Mapping)
                    and visible.get("case_token") == report.get("case_token")
                    and case.get("benchmark_instance_id")
                    == official.get("instance_id")
                ):
                    matches.append(case)
        if len(matches) != 1:
            raise OperatorGovernanceError(
                "SWE Optimization evidence is not a unique frozen manifest case"
            )
        if official.get("adapter") != "swebench_verified":
            raise OperatorGovernanceError(
                "SWE Optimization evidence did not use the Verified adapter"
            )
        command_raw = official.get("command")
        command = self._verified_operator_record(
            command_raw,
            label="SWE official scorer command",
        ) if isinstance(command_raw, Mapping) else {}
        argv = command.get("argv")
        exit_code = command.get("exit_code")
        timed_out = command.get("timed_out")
        if (
            not isinstance(argv, list)
            or not argv
            or "swebench.harness.run_evaluation" not in argv
            or not isinstance(timed_out, bool)
            or (exit_code is not None and not isinstance(exit_code, int))
            or command.get("passed") != (not timed_out and exit_code == 0)
        ):
            raise OperatorGovernanceError(
                "SWE official scorer command evidence is invalid"
            )
        for stream in ("stdout", "stderr"):
            self._verified_eval_file(
                command.get(f"{stream}_path"),
                command.get(f"{stream}_sha256"),
                trusted_root=trusted_root,
                label=f"SWE official scorer {stream}",
            )
        output_root = Path(str(official.get("output_root") or ""))
        if (
            not output_root.is_absolute()
            or output_root.is_symlink()
            or output_root.resolve() != output_root
            or not output_root.is_dir()
            or not output_root.is_relative_to(trusted_root)
        ):
            raise OperatorGovernanceError(
                "SWE official scorer output root is outside trusted Eval state"
            )
        report_path = official.get("report_path")
        if report_path == "missing":
            if official.get("report_sha256") != "missing" or not official.get(
                "harness_error"
            ):
                raise OperatorGovernanceError(
                    "SWE official scorer missing report is not a harness error"
                )
        else:
            self._verified_eval_file(
                report_path,
                official.get("report_sha256"),
                trusted_root=trusted_root,
                label="SWE official scorer report",
            )
        if official.get("passed") != (
            not bool(official.get("harness_error"))
            and official.get("resolved") is True
        ):
            raise OperatorGovernanceError(
                "SWE official scorer pass projection is inconsistent"
            )

    def register_study_evidence(
        self,
        study_id: str,
        *,
        binding_path: Path | str,
        artifact_path: Path | str,
    ) -> StudyEvidenceRecord:
        """Import one public Optimization result into trusted Operator evidence."""

        study = self.store.read_study(study_id)
        if not study.cohort_id:
            raise OperatorGovernanceError("Study lacks a frozen cohort identity")
        binding_source = Path(binding_path)
        if not binding_source.is_absolute():
            binding_source = self.project_root / binding_source
        binding = self._load_operator_yaml(
            binding_source,
            label="Optimization Study binding",
        )
        expected_binding = self.guard_study_binding(
            study.study_id,
            kind="OPTIMIZATION",
        )
        if binding != expected_binding:
            raise OperatorGovernanceError(
                "Optimization evidence binding does not match current Study authority"
            )

        source = Path(artifact_path)
        if not source.is_absolute():
            source = self.project_root / source
        if source.is_symlink():
            raise OperatorGovernanceError(
                "Optimization evidence artifact must not be a symbolic link"
            )
        source = source.resolve()
        trusted_root = self.config.eval.benchmarks.trusted_case_root.resolve()
        if not source.is_relative_to(trusted_root):
            raise OperatorGovernanceError(
                "Optimization evidence must originate in trusted Eval state"
            )
        report = self._load_operator_yaml(
            source,
            label="Optimization case report",
        )
        report_schema = report.get("schema")
        if (
            report_schema
            not in {
                "autobugfix-formal-optimization-case-v1",
                "autobugfix-swe-formal-case-v1",
            }
            or report.get("experiment_role") != "optimization"
            or report.get("study_binding_digest") != binding["record_digest"]
            or report.get("executed_subject_sha") != binding["subject_sha"]
            or report.get("executed_subject_tree") != binding["subject_tree"]
            or not isinstance(report.get("noninterference"), Mapping)
        ):
            raise OperatorGovernanceError(
                "Optimization case report does not prove the bound public execution"
            )
        if report_schema == "autobugfix-formal-optimization-case-v1":
            verification = report.get("verification")
            if (
                not isinstance(verification, Mapping)
                or not isinstance(verification.get("argv"), list)
                or not verification.get("argv")
                or not isinstance(verification.get("returncode"), int)
                or not isinstance(verification.get("passed"), bool)
                or any(
                    not isinstance(verification.get(key), str)
                    or len(verification[key]) != 64
                    for key in ("stdout_sha256", "stderr_sha256")
                )
                or report["noninterference"].get("passed") is not True
            ):
                raise OperatorGovernanceError(
                    "generic Optimization evidence lacks a real verification record"
                )
        else:
            official_raw = report.get("official_result")
            try:
                official = self._verified_operator_record(
                    official_raw,
                    label="SWE official scorer result",
                ) if isinstance(official_raw, Mapping) else {}
                noninterference = self._verified_operator_record(
                    report["noninterference"],
                    label="SWE noninterference receipt",
                )
            except OperatorGovernanceError:
                official = {}
                noninterference = {}
            if (
                official.get("schema") != "autobugfix-swe-official-result-v1"
                or official.get("instance_id") in {None, ""}
                or not isinstance(official.get("resolved"), bool)
                or not isinstance(official.get("harness_error"), str)
                or report.get("resolved") != official.get("resolved")
                or report.get("harness_error") != official.get("harness_error")
                or noninterference.get("schema")
                != "autobugfix-swe-noninterference-v1"
                or noninterference.get("case_token") != report.get("case_token")
                or noninterference.get("submission_digest")
                != report.get("submission_digest")
                or noninterference.get("official_result_digest")
                != official.get("record_digest")
                or noninterference.get("unchanged") is not True
            ):
                raise OperatorGovernanceError(
                    "SWE Optimization evidence lacks a verified official scorer chain"
                )
            self._validate_swe_optimization_evidence(
                study=study,
                report=report,
                official=official,
                trusted_root=trusted_root,
            )
        content = source.read_bytes()
        artifact, artifact_sha = self.store.write_study_evidence_artifact(
            content,
            study_id=study.study_id,
            filename=source.name,
        )
        evidence = StudyEvidenceRecord(
            evidence_id=self.store.next_id("study-evidence"),
            study_id=study.study_id,
            cohort_id=study.cohort_id,
            treatment=study.target_checkpoint_name,
            source_kind="optimization_case",
            subject_sha=str(binding["subject_sha"]),
            binding_digest=str(binding["record_digest"]),
            source_record_digest=str(report["record_digest"]),
            artifact_path=str(artifact),
            artifact_sha256=artifact_sha,
        )
        self.store.write_study_evidence(evidence)
        return evidence

    def register_exp2_h0_handoff(
        self,
        operator_study_id: str,
        *,
        binding_path: Path | str,
        metric_path: Path | str,
        source_projection_path: Path | str,
    ) -> dict[str, Any]:
        """Register H0 privately and expose only the source projection to Operator."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2SourceProjectionBundle,
        )

        study = self.store.read_study(operator_study_id)
        if not study.cohort_id:
            raise OperatorGovernanceError(
                "Exp2 Operator Study lacks a frozen cohort"
            )
        binding_source = Path(binding_path).resolve()
        binding = self._load_operator_yaml(
            binding_source,
            label="Exp2 H0 Study binding",
        )
        expected_binding = self.guard_study_binding(
            operator_study_id,
            kind="BASELINE",
        )
        if binding != expected_binding:
            raise OperatorGovernanceError(
                "Exp2 H0 binding differs from current Operator authority"
            )
        source = Path(source_projection_path)
        if source.is_symlink():
            raise OperatorGovernanceError(
                "Exp2 source projection cannot be redirected"
            )
        source = source.resolve()
        trusted_root = self.config.eval.benchmarks.trusted_case_root.resolve()
        if not source.is_file() or not source.is_relative_to(trusted_root):
            raise OperatorGovernanceError(
                "Exp2 source projection must originate in trusted Eval state"
            )
        raw_source = self._load_operator_yaml(
            source,
            label="Exp2 source projection bundle",
        )
        bundle = Exp2SourceProjectionBundle.from_dict(raw_source)
        content = source.read_bytes()
        artifact, artifact_sha = self.store.write_study_evidence_artifact(
            content,
            study_id=study.study_id,
            filename=source.name,
        )
        evidence = StudyEvidenceRecord(
            evidence_id=self.store.next_id("study-evidence"),
            study_id=study.study_id,
            cohort_id=study.cohort_id,
            treatment=study.target_checkpoint_name,
            source_kind="exp2_source_projection",
            subject_sha=str(binding["subject_sha"]),
            binding_digest=str(binding["record_digest"]),
            source_record_digest=bundle.record_digest,
            artifact_path=str(artifact),
            artifact_sha256=artifact_sha,
        )
        metric_source = Path(metric_path)
        if metric_source.is_symlink():
            raise OperatorGovernanceError(
                "Exp2 H0 metric cannot be redirected"
            )
        metric_source = metric_source.resolve()
        if not metric_source.is_relative_to(trusted_root):
            raise OperatorGovernanceError(
                "Exp2 H0 metric must originate in trusted Eval state"
            )
        metric_raw = self._load_study_metric_receipt(
            metric_source.read_bytes()
        )
        if (
            metric_raw.get("study_id") != study.study_id
            or metric_raw.get("subject_sha") != binding.get("subject_sha")
            or metric_raw.get("evidence_digest") != bundle.record_digest
            or metric_raw.get("guard_run_id") != bundle.study_id
            or metric_raw.get("metrics")
            != {
                "apparatus_valid": True,
                "h0_terminal_coverage": 1.0,
                "adaptation_feasible": True,
            }
        ):
            raise OperatorGovernanceError(
                "Exp2 H0 metric contains untrusted or audience-leaking fields"
            )
        self.store.write_study_evidence(evidence)
        metric = self.register_guard_metric_receipt(
            operator_study_id,
            receipt_path=metric_source,
            kind="BASELINE",
        )
        initialized = self.initialize_experiment_line(
            operator_study_id,
            metric_receipt_id=metric.metric_id,
        )
        return {
            "evidence": evidence.to_dict(),
            "evidence_reference": self.study_evidence_reference(evidence),
            "metric": metric.to_dict(),
            "line": initialized["line"],
            "checkpoint": initialized["checkpoint"],
        }

    @staticmethod
    def study_evidence_reference(evidence: StudyEvidenceRecord) -> str:
        return f"study-evidence:{evidence.evidence_id}"

    def _validated_study_evidence(
        self,
        study: StudyRecord,
        references: Iterable[str],
        *,
        expected_subject_sha: str,
    ) -> list[tuple[StudyEvidenceRecord, dict[str, Any]]]:
        if not study.cohort_id:
            raise OperatorGovernanceError("Study lacks a frozen cohort identity")
        values = tuple(references)
        if not values or any(not value.startswith("study-evidence:") for value in values):
            raise OperatorGovernanceError(
                "line-bound requests require only registered study-evidence references"
            )
        validated: list[tuple[StudyEvidenceRecord, dict[str, Any]]] = []
        for reference in values:
            evidence_id = safe_id(reference.removeprefix("study-evidence:"))
            record = self.store.read_study_evidence(evidence_id)
            if (
                record.study_id != study.study_id
                or record.cohort_id != study.cohort_id
                or record.treatment != study.target_checkpoint_name
                or record.source_kind
                not in {"optimization_case", "exp2_source_projection"}
                or record.subject_sha != expected_subject_sha
            ):
                raise OperatorGovernanceError(
                    "registered study evidence belongs to another Study, treatment, or subject"
                )
            report = self._load_operator_yaml(
                Path(record.artifact_path),
                label="registered Optimization evidence",
            )
            if record.source_kind == "exp2_source_projection":
                from autobugfix.eval.benchmarks.exp2_resume import (
                    Exp2SourceProjectionBundle,
                )

                bundle = Exp2SourceProjectionBundle.from_dict(report)
                if bundle.record_digest != record.source_record_digest:
                    raise OperatorGovernanceError(
                        "registered Exp2 source evidence digest drift"
                    )
            elif (
                report.get("record_digest") != record.source_record_digest
                or report.get("study_binding_digest") != record.binding_digest
                or report.get("executed_subject_sha") != record.subject_sha
                or report.get("experiment_role") != "optimization"
            ):
                raise OperatorGovernanceError(
                    "registered study evidence authority no longer matches its source record"
                )
            validated.append((record, report))
        return validated

    def register_signed_guard_metric(
        self,
        study_id: str,
        *,
        metric_path: Path | str,
        kind: str,
        guard_secret: str | bytes,
    ) -> StudyMetricRecord:
        """Verify a real Guard HMAC and import its aggregate as Study authority."""

        from autobugfix.eval.benchmarks.authority import GuardCodeIdentity
        from autobugfix.eval.benchmarks.guard import verify_signed_metric
        from autobugfix.eval.benchmarks.models import BenchmarkContractError

        source = Path(metric_path)
        if not source.is_absolute():
            source = self.project_root / source
        source = source.resolve()
        if not source.is_file():
            raise OperatorGovernanceError(
                f"signed Guard metric does not exist: {source}"
            )
        try:
            raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise OperatorGovernanceError(
                "signed Guard metric is not valid YAML"
            ) from exc
        if not isinstance(raw, Mapping):
            raise OperatorGovernanceError("signed Guard metric must be a mapping")
        try:
            verify_signed_metric(raw, guard_secret)
        except BenchmarkContractError as exc:
            raise OperatorGovernanceError(
                f"signed Guard metric authentication failed: {exc}"
            ) from exc
        if raw.get("schema") != "autobugfix-guard-metric-v2":
            raise OperatorGovernanceError("unsupported signed Guard metric schema")
        binding = raw.get("study_binding")
        if not isinstance(binding, Mapping):
            raise OperatorGovernanceError(
                "signed Guard metric is missing its Study binding"
            )
        expected_binding = self.guard_study_binding(
            study_id,
            kind=kind,
            terminalize=False,
        )
        if dict(binding) != expected_binding:
            raise OperatorGovernanceError(
                "signed Guard metric does not match current Study/line/budget authority"
            )
        study = self.store.read_study(study_id)
        raw_identity = raw.get("guard_code_identity")
        if not isinstance(raw_identity, Mapping):
            raise OperatorGovernanceError(
                "signed Guard metric is missing its control-plane identity"
            )
        try:
            code_identity = GuardCodeIdentity.from_dict(raw_identity)
        except BenchmarkContractError as exc:
            raise OperatorGovernanceError(
                f"signed Guard code identity is invalid: {exc}"
            ) from exc
        if (
            code_identity.trusted_commit != study.harness_sha
            or code_identity.machine_constitution_digest != study.policy_digest
        ):
            raise OperatorGovernanceError(
                "signed Guard metric was not produced by the frozen Study harness/policy"
            )
        if raw.get("executed_subject_sha") != expected_binding["subject_sha"]:
            raise OperatorGovernanceError(
                "signed Guard metric did not execute the bound Study subject SHA"
            )
        raw_metrics = raw.get("metrics")
        if not isinstance(raw_metrics, Mapping) or not raw_metrics:
            raise OperatorGovernanceError(
                "signed Guard metric requires aggregate scalar metrics"
            )
        metrics: dict[str, bool | int | float | None] = {}
        for key, value in raw_metrics.items():
            name = str(key).strip()
            if not name or not isinstance(value, (bool, int, float, type(None))):
                raise OperatorGovernanceError(
                    "signed Guard metrics must contain aggregate scalar values"
                )
            metrics[name] = value

        success: bool | None = None
        if kind == "CANDIDATE":
            baseline_records = [
                item
                for item in self.store.read_study_metrics(study.study_id)
                if item.kind == "BASELINE"
            ]
            if not baseline_records:
                raise OperatorGovernanceError(
                    "candidate Guard metric requires a registered H0 metric"
                )
            _, baseline_receipt = self._registered_metric_receipt(
                baseline_records[-1].metric_id
            )
            baseline_metrics = baseline_receipt.get("metrics") or {}
            if not isinstance(baseline_metrics, Mapping):
                raise OperatorGovernanceError("registered H0 aggregate is invalid")
            derived = dict(metrics)
            for key, value in metrics.items():
                baseline = baseline_metrics.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and isinstance(baseline, (int, float))
                    and not isinstance(baseline, bool)
                ):
                    derived[f"{key}_delta"] = float(value) - float(baseline)
            failed_conditions = []
            for key, expected in study.success_contract.items():
                actual = derived.get(str(key))
                if not isinstance(actual, (bool, int, float)):
                    raise OperatorGovernanceError(
                        f"success contract metric is absent from Guard aggregate: {key}"
                    )
                if not _metric_condition_passes(actual, expected):
                    failed_conditions.append(str(key))
            metrics = derived
            success = not failed_conditions

        normalized: dict[str, Any] = {
            "schema": (
                "autobugfix-study-baseline-v1"
                if kind == "BASELINE"
                else "autobugfix-study-metric-v1"
            ),
            "study_id": expected_binding["study_id"],
            "line_id": expected_binding["line_id"],
            "subject_sha": expected_binding["subject_sha"],
            "manifest_digest": expected_binding["manifest_digest"],
            "success_contract_digest": expected_binding[
                "success_contract_digest"
            ],
            "metrics": metrics,
            "guard_run_id": str(raw.get("run_id") or ""),
            "evidence_digest": str(raw.get("record_digest") or ""),
        }
        if kind == "CANDIDATE":
            normalized.update(
                {
                    "wave": expected_binding["wave"],
                    "budget_grant_id": expected_binding["budget_grant_id"],
                    "budget_digest": expected_binding["budget_digest"],
                    "success_contract_passed": success,
                }
            )
        normalized["receipt_digest"] = digest_payload(normalized)
        temporary = (
            self.config.operator.artifacts.root
            / "guard-import"
            / f"{raw['record_digest']}.yaml"
        )
        temporary.parent.mkdir(parents=True, exist_ok=True)
        if temporary.exists():
            existing = yaml.safe_load(temporary.read_text(encoding="utf-8")) or {}
            if existing != normalized:
                raise OperatorGovernanceError(
                    "content-addressed Guard import path contains different data"
                )
        else:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(yaml.safe_dump(normalized, sort_keys=False))
                stream.flush()
                os.fsync(stream.fileno())
        return self.register_guard_metric_receipt(
            study_id,
            receipt_path=temporary,
            kind=kind,
        )

    def _registered_metric_receipt(
        self,
        metric_id: str,
    ) -> tuple[StudyMetricRecord, dict[str, Any]]:
        metric = self.store.read_study_metric(metric_id)
        data = self._load_study_metric_receipt(Path(metric.artifact_path).read_bytes())
        if data.get("receipt_digest") != metric.receipt_digest:
            raise OperatorGovernanceError(
                "registered study metric record disagrees with its artifact"
            )
        return metric, data

    def create_study(
        self,
        *,
        study_id: str,
        purpose: str,
        manifest_path: Path | str,
        success_contract: Mapping[str, Any],
        base_ref: str | None = None,
        harness_ref: str | None = None,
        line_id: str | None = None,
        cohort_id: str | None = None,
        primary_model: str = "gpt-5.4-mini",
        target_checkpoint_name: str = "H_bug",
        memory_root: Path | str | None = None,
    ) -> StudyRecord:
        policy = self.policy()
        if not policy.trusted:
            raise OperatorGovernanceError("a study requires a trusted machine constitution")
        experiment_governance = policy.data.get("experiment_governance") or {}
        if not experiment_governance:
            raise OperatorGovernanceError("trusted machine constitution lacks experiment governance")
        trusted_models = {
            str(item)
            for item in (experiment_governance.get("budgets") or {}).get(
                "allowed_primary_models", ()
            )
        }
        if primary_model not in self.config.operator.budgets.allowed_primary_models:
            raise OperatorGovernanceError(
                f"study model is outside configured allowlist: {primary_model}"
            )
        if trusted_models and primary_model not in trusted_models:
            raise OperatorGovernanceError(
                f"study model is outside trusted constitution allowlist: {primary_model}"
            )
        manifest = Path(manifest_path)
        if not manifest.is_absolute():
            manifest = self.project_root / manifest
        manifest = manifest.resolve()
        if not manifest.is_file():
            raise OperatorGovernanceError(f"study manifest does not exist: {manifest}")
        resolved_base_ref = base_ref or self.trusted_ref or "HEAD"
        base_sha = rev_parse(self.project_root, resolved_base_ref)
        harness_sha = rev_parse(self.project_root, harness_ref or resolved_base_ref)
        identifier = safe_id(str(study_id))
        designated_line = safe_id(str(line_id or identifier))
        cohort = safe_id(str(cohort_id or identifier))
        digests = self._experiment_digests_at_subject(
            study_id=identifier,
            subject_sha=base_sha,
            primary_model=primary_model,
        )
        canonical_memory_path = load_memory_config(self.project_root).root
        if canonical_memory_path.is_symlink():
            raise OperatorGovernanceError(
                "canonical approved active Memory root must not be a symlink"
            )
        canonical_memory = canonical_memory_path.resolve()
        memory = Path(memory_root) if memory_root is not None else canonical_memory_path
        if not memory.is_absolute():
            memory = self.project_root / memory
        if memory.is_symlink():
            raise OperatorGovernanceError(
                "study Memory root must not be a symlink"
            )
        memory = memory.resolve()
        if memory != canonical_memory:
            raise OperatorGovernanceError(
                "study Memory must use the canonical approved active Memory root"
            )
        memory_snapshot: Path | None = None
        manifest_snapshot: Path | None = None
        try:
            memory_snapshot = self._materialize_study_memory_snapshot(
                study_id=identifier,
                source=memory,
            )
            manifest_snapshot = self._materialize_study_manifest_snapshot(
                study_id=identifier,
                source=manifest,
            )
            memory_digest = _path_digest(memory_snapshot)
            cohort_fields = {
                "base_subject_sha": base_sha,
                "harness_sha": harness_sha,
                "policy_digest": digest_payload(policy.data),
                "primary_model": primary_model,
                "role_config_digest": digests["role_config_digest"],
                "base_config_digest": digests["config_digest"],
                "base_model_digest": digests["model_digest"],
                "base_skills_digest": digests["skills_digest"],
                "memory_digest": memory_digest,
            }
            for existing in self.store.read_studies():
                if existing.cohort_id != cohort:
                    continue
                if existing.target_checkpoint_name == target_checkpoint_name:
                    raise OperatorGovernanceError(
                        "experiment cohort already has a study for "
                        f"{target_checkpoint_name}"
                    )
                for field, value in cohort_fields.items():
                    if getattr(existing, field) != value:
                        raise OperatorGovernanceError(
                            f"experiment cohort frozen H0 mismatch: {field}"
                        )
            study = StudyRecord(
                study_id=identifier,
                purpose=purpose,
                base_checkpoint_id=f"{identifier}-H0",
                base_subject_sha=base_sha,
                harness_sha=harness_sha,
                policy_digest=digest_payload(policy.data),
                line_id=designated_line,
                primary_model=primary_model,
                target_checkpoint_name=target_checkpoint_name,
                manifest_digest=_manifest_digest(manifest_snapshot),
                role_config_digest=digests["role_config_digest"],
                memory_digest=memory_digest,
                success_contract=dict(success_contract),
                cohort_id=cohort,
                base_config_digest=digests["config_digest"],
                base_model_digest=digests["model_digest"],
                base_skills_digest=digests["skills_digest"],
                memory_snapshot_path=str(memory_snapshot),
                manifest_snapshot_path=str(manifest_snapshot),
            )
            self.store.write_study(study)
        except Exception:
            if manifest_snapshot is not None:
                self._remove_release_tree(manifest_snapshot)
            if memory_snapshot is not None:
                self._remove_release_tree(memory_snapshot)
            raise
        return study

    def initialize_experiment_line(
        self,
        study_id: str,
        *,
        metric_receipt_id: str,
    ) -> dict[str, Any]:
        study = self.store.read_study(study_id)
        self._validated_study_memory_snapshot(study)
        self._validated_study_manifest_snapshot(study)
        policy = self.policy()
        if study.policy_digest != digest_payload(policy.data):
            raise OperatorGovernanceError("study machine constitution changed before line initialization")
        metric_record, metric_receipt = self._registered_metric_receipt(metric_receipt_id)
        if (
            metric_record.kind != "BASELINE"
            or metric_record.study_id != study.study_id
            or metric_record.line_id != study.line_id
            or metric_record.subject_sha != study.base_subject_sha
            or metric_record.manifest_digest != study.manifest_digest
            or metric_record.success_contract_digest != digest_payload(study.success_contract)
            or metric_record.producer != "benchmark_guard"
        ):
            raise OperatorGovernanceError("H0 metric authority does not match the trusted study")
        self._baseline_metric_receipt(metric_receipt, study=study)
        branch = self.config.operator.experiment_lines.branch_template.format(
            study_id=study.study_id
        )
        check_ref = run_git(self.project_root, ["check-ref-format", "--branch", branch], check=False)
        if check_ref.returncode != 0:
            raise OperatorGovernanceError(f"invalid experiment line branch: {branch}")
        protected = {str(item) for item in policy.data.get("protected_branches") or []}
        if branch in protected:
            raise OperatorGovernanceError(f"experiment line branch is protected: {branch}")
        reference = f"refs/heads/{branch}"
        if run_git(
            self.project_root,
            ["show-ref", "--verify", "--quiet", reference],
            check=False,
        ).returncode == 0:
            raise OperatorGovernanceError(f"experiment line branch already exists: {branch}")
        if rev_parse(self.project_root, study.base_subject_sha) != study.base_subject_sha:
            raise OperatorGovernanceError("study H0 SHA is not canonical")
        control_head = rev_parse(self.project_root, "HEAD")
        tree_sha = rev_parse(self.project_root, f"{study.base_subject_sha}^{{tree}}")
        if not all(
            (
                study.cohort_id,
                study.base_config_digest,
                study.base_model_digest,
                study.base_skills_digest,
            )
        ):
            raise OperatorGovernanceError("study lacks frozen H0 cohort digests")
        line = ExperimentLineRecord(
            line_id=study.line_id,
            study_id=study.study_id,
            branch=branch,
            base_sha=study.base_subject_sha,
            head_sha=study.base_subject_sha,
            generation=0,
            active_checkpoint_id=study.base_checkpoint_id,
            status="OPEN",
            remote=self.config.operator.experiment_lines.remote,
        )
        release = self._materialize_checkpoint_release(
            study_id=study.study_id,
            checkpoint_name="H0",
            subject_sha=study.base_subject_sha,
        )
        active: Path | None = None
        reference_created = False
        try:
            digests = self._experiment_config_digests(
                study.primary_model,
                subject_root=release,
            )
            expected_digests = {
                "role_config_digest": study.role_config_digest,
                "config_digest": study.base_config_digest,
                "model_digest": study.base_model_digest,
                "skills_digest": study.base_skills_digest,
            }
            for field, expected in expected_digests.items():
                if digests[field] != expected:
                    raise OperatorGovernanceError(
                        f"frozen H0 {field} changed before line initialization"
                    )
            checkpoint = CheckpointRecord(
                checkpoint_id=study.base_checkpoint_id,
                study_id=study.study_id,
                line_id=study.line_id,
                name="H0",
                subject_sha=study.base_subject_sha,
                tree_sha=tree_sha,
                harness_sha=study.harness_sha,
                policy_digest=study.policy_digest,
                config_digest=digests["config_digest"],
                model_digest=digests["model_digest"],
                skills_digest=digests["skills_digest"],
                memory_digest=study.memory_digest,
                manifest_digest=study.manifest_digest,
                budget_digest=digest_payload(
                    {"study_id": study.study_id, "state": "UNGRANTED"}
                ),
                metric_digest=metric_record.receipt_digest,
                release_path=str(release),
            )
            run_git(
                self.project_root,
                ["update-ref", reference, study.base_subject_sha, "0" * 40],
            )
            reference_created = True
            active = self._activate_experiment_release(study.study_id, release)
            self.store.initialize_experiment_line(line, checkpoint)
        except Exception:
            if reference_created:
                run_git(
                    self.project_root,
                    ["update-ref", "-d", reference, study.base_subject_sha],
                    check=False,
                )
            if active is not None and active.is_symlink() and active.resolve() == release:
                active.unlink()
            self._remove_release_tree(release)
            raise
        if rev_parse(self.project_root, "HEAD") != control_head:
            raise OperatorGovernanceError("experiment line initialization changed the control checkout")
        return {
            "study": self._study_projection(study),
            "line": line.to_dict(),
            "checkpoint": checkpoint.to_dict(),
        }

    def list_studies(self) -> list[dict[str, Any]]:
        return [self._study_projection(study) for study in self.store.read_studies()]

    def study_status(self, study_id: str) -> dict[str, Any]:
        study = self.store.read_study(study_id)
        lines = self.store.read_experiment_lines(study.study_id)
        checkpoints = self.store.read_checkpoints(study.study_id) if lines else []
        grants = self.store.read_budget_grants(study.study_id)
        return {
            "study": self._study_projection(study),
            "lines": [line.to_dict() for line in lines],
            "checkpoints": [checkpoint.to_dict() for checkpoint in checkpoints],
            "metrics": [
                self._metric_projection(metric)
                for metric in self.store.read_study_metrics(study.study_id)
            ],
            "budget_grants": [grant.to_dict() for grant in grants],
        }

    def list_experiment_lines(self, study_id: str | None = None) -> list[dict[str, Any]]:
        return [
            line.to_dict()
            for line in self.store.read_experiment_lines(study_id)
        ]

    def experiment_line_status(self, line_id: str) -> dict[str, Any]:
        line = self.store.read_experiment_line(line_id)
        return {
            "line": line.to_dict(),
            "study": self._study_projection(self.store.read_study(line.study_id)),
            "checkpoints": [
                checkpoint.to_dict()
                for checkpoint in self.store.read_checkpoints(line.study_id)
            ],
            "integrations": [
                integration.to_dict()
                for integration in self.store.read_integrations(line.line_id)
            ],
            "metrics": [
                self._metric_projection(metric)
                for metric in self.store.read_study_metrics(line.study_id)
            ],
        }

    def create_budget_request(
        self,
        study_id: str,
        *,
        wave: int,
        case_ids: Iterable[str],
        reason: str,
        requester: str | None = None,
        model: str | None = None,
        max_calls: int | None = None,
        max_writer_attempts: int | None = None,
        max_operator_revisions: int | None = None,
        wall_time_seconds: int | None = None,
        case_concurrency: int | None = None,
    ) -> BudgetRequestRecord:
        study = self.store.read_study(study_id)
        line = self.store.read_experiment_line(study.line_id)
        if line.status != "OPEN":
            raise OperatorGovernanceError("budget cannot be requested for a closed experiment line")
        if study.policy_digest != digest_payload(self.policy().data):
            raise OperatorGovernanceError("study machine constitution is stale")
        grants = self.store.read_budget_grants(study.study_id)
        expected_wave = 3 if not grants else {3: 8, 8: 16}.get(grants[-1].wave)
        if expected_wave is None:
            raise OperatorGovernanceError("study already has its final wave-16 budget")
        if wave != expected_wave:
            raise OperatorGovernanceError(
                f"budget wave must advance to {expected_wave}, got {wave}"
            )
        selected_cases = tuple(dict.fromkeys(str(item) for item in case_ids))
        if grants and not set(grants[-1].case_ids).issubset(selected_cases):
            raise OperatorGovernanceError("expanded budget must retain every previously granted case")
        selected_model = model or study.primary_model
        if selected_model != study.primary_model:
            raise OperatorGovernanceError("budget model must match the frozen study model")
        budget = self.config.operator.budgets
        if selected_model not in budget.allowed_primary_models:
            raise OperatorGovernanceError("budget model is outside configured allowlist")
        requested_calls = max_calls if max_calls is not None else budget.max_calls_by_wave[wave]
        requested_attempts = (
            max_writer_attempts
            if max_writer_attempts is not None
            else budget.default_max_writer_attempts
        )
        requested_revisions = (
            max_operator_revisions
            if max_operator_revisions is not None
            else budget.default_max_operator_revisions
        )
        requested_wall_time = (
            wall_time_seconds
            if wall_time_seconds is not None
            else budget.default_wall_time_seconds
        )
        requested_concurrency = (
            case_concurrency
            if case_concurrency is not None
            else budget.default_case_concurrency
        )
        ceilings = (
            (requested_calls, budget.max_calls_by_wave[wave], "max_calls"),
            (requested_attempts, budget.default_max_writer_attempts, "max_writer_attempts"),
            (
                requested_revisions,
                budget.default_max_operator_revisions,
                "max_operator_revisions",
            ),
            (requested_wall_time, budget.default_wall_time_seconds, "wall_time_seconds"),
            (requested_concurrency, budget.max_case_concurrency, "case_concurrency"),
        )
        for value, ceiling, field in ceilings:
            if value < 1 or value > ceiling:
                raise OperatorGovernanceError(
                    f"budget {field} must be between 1 and configured ceiling {ceiling}"
                )
        request = BudgetRequestRecord(
            budget_request_id=self.store.next_id("budget-request"),
            study_id=study.study_id,
            wave=wave,
            case_ids=selected_cases,
            model=selected_model,
            max_calls=requested_calls,
            max_writer_attempts=requested_attempts,
            max_operator_revisions=requested_revisions,
            wall_time_seconds=requested_wall_time,
            case_concurrency=requested_concurrency,
            reason=reason,
            requester=self._actor(requester),
            previous_grant_id=grants[-1].grant_id if grants else None,
        )
        self.store.write_budget_request(request)
        return request

    def approve_budget_grant(
        self,
        budget_request_id: str,
        *,
        approver: str,
        confirm_request_digest: str,
        approval_kind: str = "interactive",
    ) -> BudgetGrantRecord:
        request = self.store.read_budget_request(budget_request_id)
        if confirm_request_digest != request.budget_request_digest:
            raise OperatorGovernanceError("budget approval does not confirm the request digest")
        if approval_kind != "interactive":
            raise OperatorGovernanceError(
                "budget grants currently accept only digest-bound interactive human attestation"
            )
        study = self.store.read_study(request.study_id)
        if study.policy_digest != digest_payload(self.policy().data):
            raise OperatorGovernanceError("study machine constitution is stale")
        requests = [
            item
            for item in self.store.read_budget_requests(study.study_id)
            if item.wave == request.wave
        ]
        if not requests or requests[-1].budget_request_id != request.budget_request_id:
            raise OperatorGovernanceError("budget request was superseded by a newer request")
        grants = self.store.read_budget_grants(study.study_id)
        expected_wave = 3 if not grants else {3: 8, 8: 16}.get(grants[-1].wave)
        if request.wave != expected_wave:
            raise OperatorGovernanceError("budget request is stale for the current study wave")
        if request.previous_grant_id != (grants[-1].grant_id if grants else None):
            raise OperatorGovernanceError("budget request previous grant binding is stale")
        if grants and not set(grants[-1].case_ids).issubset(request.case_ids):
            raise OperatorGovernanceError("budget request dropped a previously granted case")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=request.wall_time_seconds)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        grant = BudgetGrantRecord(
            grant_id=self.store.next_id("budget-grant"),
            budget_request_id=request.budget_request_id,
            budget_request_digest=request.budget_request_digest,
            study_id=request.study_id,
            wave=request.wave,
            case_ids=request.case_ids,
            model=request.model,
            max_calls=request.max_calls,
            max_writer_attempts=request.max_writer_attempts,
            max_operator_revisions=request.max_operator_revisions,
            wall_time_seconds=request.wall_time_seconds,
            case_concurrency=request.case_concurrency,
            approved_by=approver,
            approval_kind=approval_kind,
            previous_grant_id=request.previous_grant_id,
            expires_at=expires_at,
        )
        self.store.write_budget_grant(grant)
        return grant

    def reserve_usage(
        self,
        grant_id: str,
        *,
        call_key: str,
        execution_id: str,
        role: str,
        model: str,
        case_id: str | None = None,
        attempt: int = 0,
        revision: int = 0,
    ) -> UsageEntryRecord:
        grant = self.store.read_budget_grant(grant_id)
        entry = UsageEntryRecord(
            usage_id=self.store.next_id("usage"),
            grant_id=grant.grant_id,
            study_id=grant.study_id,
            call_key=call_key,
            execution_id=execution_id,
            case_id=case_id,
            role=role,
            model=model,
            status="RESERVED",
            attempt=attempt,
            revision=revision,
        )
        return self.store.reserve_usage_entry(entry)

    def _retain_usage_log(self, entry: UsageEntryRecord, source: Path | None, name: str) -> str | None:
        if source is None or not source.is_file():
            return None
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        target = (
            self.store.artifact_root
            / "studies"
            / entry.study_id
            / "usage"
            / entry.usage_id
            / f"{sha}-{name}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(raw)
        temporary.replace(target)
        return str(target)

    def finalize_usage(
        self,
        usage_id: str,
        *,
        status: str,
        raw_log_path: Path | None = None,
        stderr_log_path: Path | None = None,
        result_id: str | None = None,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_seconds: float | None = None,
        error: str | None = None,
    ) -> UsageEntryRecord:
        if status not in {"COMPLETED", "INDETERMINATE"}:
            raise OperatorGovernanceError("usage finalization status must be terminal")
        current = self.store.read_usage_entry(usage_id)
        updated = replace(
            current,
            status=status,
            raw_log_path=self._retain_usage_log(current, raw_log_path, "raw.jsonl"),
            stderr_log_path=self._retain_usage_log(current, stderr_log_path, "stderr.log"),
            result_id=result_id,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration_seconds,
            error=error,
            finished_at=utc_now(),
        )
        return self.store.finalize_usage_entry(updated)

    def budget_status(self, study_id: str) -> dict[str, Any]:
        requests = self.store.read_budget_requests(study_id)
        grants = self.store.read_budget_grants(study_id)
        usage = [
            entry
            for grant in grants
            for entry in self.store.read_usage_entries(grant.grant_id)
        ]
        return {
            "study_id": study_id,
            "requests": [request.to_dict() for request in requests],
            "grants": [grant.to_dict() for grant in grants],
            "usage": [entry.to_dict() for entry in usage],
            "consumed_calls": len(usage),
            "running_calls": sum(entry.status == "RESERVED" for entry in usage),
        }

    def metered_codex_backend(
        self,
        *,
        grant_id: str,
        call_key: str,
        execution_id: str,
        case_id: str | None = None,
        attempt: int = 0,
        revision: int = 0,
        backend: CodexBackend | None = None,
    ) -> MeteredCodexBackend:
        return MeteredCodexBackend(
            backend or self.backend,
            self,
            StudyCallContext(
                grant_id=grant_id,
                call_key=call_key,
                execution_id=execution_id,
                case_id=case_id,
                attempt=attempt,
                revision=revision,
            ),
        )

    def create_checkpoint(
        self,
        line_id: str,
        *,
        metric_receipt_id: str,
        checkpoint_name: str | None = None,
    ) -> dict[str, Any]:
        with self.store.experiment_line_lease(line_id):
            line = self.store.read_experiment_line(line_id)
            study = self.store.read_study(line.study_id)
            self._validated_study_memory_snapshot(study)
            self._validated_study_manifest_snapshot(study)
            name = checkpoint_name or study.target_checkpoint_name
            if name != study.target_checkpoint_name:
                raise OperatorGovernanceError(
                    f"study target checkpoint is {study.target_checkpoint_name}, not {name}"
                )
            if line.status != "CLOSED":
                raise OperatorGovernanceError(
                    "checkpoint requires a terminally measured experiment line"
                )
            if study.policy_digest != digest_payload(self.policy().data):
                raise OperatorGovernanceError("study machine constitution is stale")
            checkpoints = self.store.read_checkpoints(study.study_id)
            if any(item.name == name for item in checkpoints):
                raise OperatorGovernanceError(f"checkpoint already exists: {name}")
            h0 = next((item for item in checkpoints if item.name == "H0"), None)
            if h0 is None or h0.subject_sha != study.base_subject_sha:
                raise OperatorGovernanceError("study has no valid H0 parent checkpoint")
            integrations = self.store.read_integrations(line.line_id)
            if (
                not integrations
                or integrations[-1].kind != "CANDIDATE"
                or integrations[-1].result_head_sha != line.head_sha
            ):
                raise OperatorGovernanceError(
                    "checkpoint requires the current line head to come from a candidate integration"
                )
            grants = self.store.read_budget_grants(study.study_id)
            if not grants:
                raise OperatorGovernanceError("checkpoint requires a completed budget wave")
            grant = grants[-1]
            usage = [
                entry
                for item in grants
                for entry in self.store.read_usage_entries(item.grant_id)
            ]
            if not usage or any(entry.status == "RESERVED" for entry in usage):
                raise OperatorGovernanceError(
                    "checkpoint requires terminal host-observed study usage"
                )
            metric_record, metric_receipt = self._registered_metric_receipt(
                metric_receipt_id
            )
            if metric_record.success_contract_passed is not True:
                raise OperatorGovernanceError(
                    "checkpoint metric did not satisfy the Study success contract"
                )
            if (
                metric_record.kind != "CANDIDATE"
                or metric_record.study_id != study.study_id
                or metric_record.line_id != line.line_id
                or metric_record.subject_sha != line.head_sha
                or metric_record.manifest_digest != study.manifest_digest
                or metric_record.success_contract_digest
                != digest_payload(study.success_contract)
                or metric_record.budget_grant_id != grant.grant_id
                or metric_record.budget_digest != grant.grant_digest
                or metric_record.wave != grant.wave
                or metric_record.producer != "benchmark_guard"
            ):
                raise OperatorGovernanceError(
                    "study metric authority does not match the current line and grant"
                )
            self._study_metric_receipt(
                metric_receipt,
                study=study,
                line=line,
                grant=grant,
            )
            release = self._materialize_checkpoint_release(
                study_id=study.study_id,
                checkpoint_name=name,
                subject_sha=line.head_sha,
            )
            active = (
                self.config.operator.experiment_lines.active_release_root.resolve()
                / study.study_id
            )
            previous_release = active.resolve() if active.is_symlink() else None
            release_activated = False
            try:
                digests = self._experiment_config_digests(
                    study.primary_model,
                    subject_root=release,
                )
                checkpoint = CheckpointRecord(
                    checkpoint_id=f"{study.study_id}-{name}",
                    study_id=study.study_id,
                    line_id=line.line_id,
                    name=name,
                    subject_sha=line.head_sha,
                    tree_sha=rev_parse(self.project_root, f"{line.head_sha}^{{tree}}"),
                    harness_sha=study.harness_sha,
                    policy_digest=study.policy_digest,
                    config_digest=digests["config_digest"],
                    model_digest=digests["model_digest"],
                    skills_digest=digests["skills_digest"],
                    memory_digest=study.memory_digest,
                    manifest_digest=study.manifest_digest,
                    budget_digest=grant.grant_digest,
                    metric_digest=metric_record.receipt_digest,
                    release_path=str(release),
                    parent_checkpoint_id=h0.checkpoint_id,
                    parent_subject_sha=h0.subject_sha,
                )
                updated_line = replace(
                    line,
                    generation=line.generation + 1,
                    active_checkpoint_id=checkpoint.checkpoint_id,
                    status="CLOSED",
                )
                self._activate_experiment_release(study.study_id, release)
                release_activated = True
                self.store.write_checkpoint_and_activate(
                    updated_line,
                    checkpoint,
                    expected_head_sha=line.head_sha,
                    expected_generation=line.generation,
                )
            except Exception:
                if release_activated:
                    if previous_release is not None:
                        self._activate_experiment_release(study.study_id, previous_release)
                    elif active.is_symlink():
                        active.unlink()
                self._remove_release_tree(release)
                raise
            return {
                "checkpoint": checkpoint.to_dict(),
                "line": updated_line.to_dict(),
                "active_release": str(active),
            }

    def rollback_experiment_line(
        self,
        line_id: str,
        checkpoint_id: str,
        *,
        reason: str,
        push_remote: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise OperatorGovernanceError("rollback reason is required")
        with self.store.experiment_line_lease(line_id):
            line = self.store.read_experiment_line(line_id)
            checkpoint = self.store.read_checkpoint(checkpoint_id)
            if checkpoint.line_id != line.line_id or checkpoint.study_id != line.study_id:
                raise OperatorGovernanceError("rollback checkpoint belongs to another line")
            if line.status not in {"OPEN", "CLOSED"}:
                raise OperatorGovernanceError("rollback requires a valid experiment line")
            current_tree = rev_parse(self.project_root, f"{line.head_sha}^{{tree}}")
            if current_tree == checkpoint.tree_sha:
                raise OperatorGovernanceError("experiment line already has the checkpoint tree")
            release = Path(checkpoint.release_path).resolve()
            expected_release_root = (
                self.config.operator.experiment_lines.checkpoint_root
                / line.study_id
            ).resolve()
            try:
                release.relative_to(expected_release_root)
            except ValueError as exc:
                raise OperatorGovernanceError(
                    "rollback checkpoint release escaped the trusted checkpoint root"
                ) from exc
            if not release.is_dir():
                raise OperatorGovernanceError("rollback checkpoint release is missing")
            study = self.store.read_study(line.study_id)
            policy = self.policy()
            if study.policy_digest != digest_payload(policy.data):
                raise OperatorGovernanceError("study machine constitution is stale")
            integrations = self.store.read_integrations(line.line_id)
            request_id = next(
                (
                    item.request_id
                    for item in reversed(integrations)
                    if item.request_id is not None
                ),
                None,
            )
            if request_id is None:
                raise OperatorGovernanceError("rollback has no request-bound integration evidence")
            grants = self.store.read_budget_grants(study.study_id)
            if not grants:
                raise OperatorGovernanceError("rollback has no study budget evidence")
            grant = grants[-1]
            if any(
                entry.status == "RESERVED"
                for item in grants
                for entry in self.store.read_usage_entries(item.grant_id)
            ):
                raise OperatorGovernanceError("rollback cannot run with active study calls")
            rollback_id = self.store.next_id("rollback")
            worktree = (
                self.config.operator.experiment_lines.root
                / line.line_id
                / rollback_id
            ).resolve()
            worktree.parent.mkdir(parents=True, exist_ok=True)
            artifact_ids: list[str] = []
            try:
                run_git(
                    self.project_root,
                    ["worktree", "add", "--detach", str(worktree), line.head_sha],
                )
                run_git(
                    worktree,
                    [
                        "-c",
                        "core.hooksPath=/dev/null",
                        "restore",
                        f"--source={checkpoint.subject_sha}",
                        "--staged",
                        "--worktree",
                        "--",
                        ".",
                    ],
                )
                restored_tree = run_git(worktree, ["write-tree"]).stdout.strip()
                if restored_tree != checkpoint.tree_sha:
                    raise OperatorGovernanceError(
                        "rollback staging tree does not match checkpoint tree"
                    )
                if _worktree_content_digest(worktree) != _worktree_content_digest(release):
                    raise OperatorGovernanceError(
                        "rollback checkpoint release content does not match its Git tree"
                    )
                if run_git(worktree, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
                    raise OperatorGovernanceError("rollback produced no tree change")
                commit_message = (
                    f"Restore experiment line to {checkpoint.name}\n\n"
                    f"Autobugfix-Rollback: {rollback_id}\n"
                    f"Autobugfix-Checkpoint: {checkpoint.checkpoint_id}\n"
                    f"Reason: {reason}"
                )
                run_git(
                    worktree,
                    [
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "user.name=Autobugfix Guard",
                        "-c",
                        "user.email=autobugfix-guard@localhost",
                        "commit",
                        "-m",
                        commit_message,
                    ],
                )
                result_head = rev_parse(worktree, "HEAD")
                result_tree = rev_parse(worktree, "HEAD^{tree}")
                if result_tree != checkpoint.tree_sha:
                    raise OperatorGovernanceError(
                        "rollback commit tree does not match checkpoint tree"
                    )
                profile = (policy.data.get("validation_profiles") or {}).get("full")
                if not isinstance(profile, dict) or not profile.get("commands"):
                    raise OperatorGovernanceError("trusted full validation profile is missing")
                results = run_command_specs(
                    worktree,
                    self.store.artifact_root
                    / request_id
                    / "rollbacks"
                    / rollback_id,
                    list(profile["commands"]),
                    values={
                        "base_sha": line.head_sha,
                        "head_sha": result_head,
                        "request_id": request_id,
                        "candidate_root": str(worktree),
                    },
                    default_timeout_seconds=int(profile.get("timeout_seconds", 300)),
                    name_prefix="rollback-full",
                    process_sandbox=self.config.operator.verification.process_sandbox,
                    require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                    network_access=self.config.operator.verification.network_access,
                    hidden_roots=(self.store.root, self.store.artifact_root),
                    read_only_binds=self._runtime_binds(worktree),
                )
                for item in results:
                    for stream in ("stdout_path", "stderr_path"):
                        reference = self.store.register_artifact_file(
                            request_id,
                            producer="rollback_guard",
                            trust_class="authoritative",
                            kind="rollback-check-log",
                            path=Path(item[stream]),
                        )
                        artifact_ids.append(reference.artifact_id)
                failed = [item for item in results if not item["passed"]]
                if failed:
                    raise OperatorGovernanceError(
                        "trusted rollback validation failed: "
                        + ", ".join(str(item["name"]) for item in failed)
                    )
                snapshot = collect_candidate_snapshot(worktree, line.head_sha)
                updated_line = replace(
                    line,
                    head_sha=result_head,
                    generation=line.generation + 1,
                    active_checkpoint_id=checkpoint.checkpoint_id,
                )
                integration = IntegrationRecord(
                    integration_id=rollback_id,
                    study_id=study.study_id,
                    line_id=line.line_id,
                    kind="ROLLBACK",
                    expected_head_sha=line.head_sha,
                    expected_generation=line.generation,
                    candidate_head_sha=checkpoint.subject_sha,
                    result_head_sha=result_head,
                    result_tree_sha=result_tree,
                    patch_digest=snapshot.patch_digest,
                    policy_digest=digest_payload(policy.data),
                    actor=self._actor(actor),
                    request_id=request_id,
                    check_run_id=integrations[-1].check_run_id,
                    budget_grant_id=grant.grant_id,
                    budget_digest=grant.grant_digest,
                    artifact_ids=tuple(artifact_ids),
                )
                active = (
                    self.config.operator.experiment_lines.active_release_root.resolve()
                    / study.study_id
                )
                previous_release = active.resolve() if active.is_symlink() else None
                self._activate_experiment_release(study.study_id, release)
                reference = f"refs/heads/{line.branch}"
                reference_updated = False
                try:
                    run_git(
                        self.project_root,
                        ["update-ref", reference, result_head, line.head_sha],
                    )
                    reference_updated = True
                    self.store.advance_experiment_line(updated_line, integration)
                except Exception:
                    if reference_updated:
                        run_git(
                            self.project_root,
                            ["update-ref", reference, line.head_sha, result_head],
                            check=False,
                        )
                    if previous_release is not None:
                        self._activate_experiment_release(study.study_id, previous_release)
                    elif active.is_symlink():
                        active.unlink()
                    raise
                remote_result: dict[str, Any] = {"requested": push_remote, "pushed": False}
                if push_remote:
                    if not line.remote:
                        raise OperatorGovernanceError(
                            "remote push requested but experiment line has no configured remote"
                        )
                    pushed = run_git(
                        self.project_root,
                        ["push", "--atomic", line.remote, f"{result_head}:{reference}"],
                        check=False,
                        timeout_seconds=(
                            self.config.operator.experiment_lines.update_timeout_seconds
                        ),
                    )
                    remote_result = {
                        "requested": True,
                        "pushed": pushed.returncode == 0,
                        "remote": line.remote,
                        "stderr": pushed.stderr.strip(),
                    }
                    if pushed.returncode != 0:
                        reconciliation = replace(
                            integration,
                            integration_id=self.store.next_id("reconciliation"),
                            kind="RECONCILIATION",
                            expected_head_sha=result_head,
                            expected_generation=updated_line.generation,
                        )
                        self.store.write_integration(reconciliation)
                        raise OperatorGovernanceError(
                            "local rollback succeeded but remote synchronization requires reconciliation"
                        )
                self.store.append_event(
                    request_id,
                    "experiment_line_rolled_back",
                    self._actor(actor),
                    {
                        "rollback_id": rollback_id,
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "line_generation": updated_line.generation,
                        "reason": reason,
                    },
                )
                return {
                    "integration": integration.to_dict(),
                    "line": updated_line.to_dict(),
                    "checkpoint": checkpoint.to_dict(),
                    "validation": results,
                    "active_release": str(active),
                    "remote": remote_result,
                }
            finally:
                if worktree.exists():
                    run_git(
                        self.project_root,
                        ["worktree", "remove", "--force", str(worktree)],
                        check=False,
                    )

    @_leased_request
    def integrate_candidate(
        self,
        request_id: str,
        *,
        grant_id: str,
        push_remote: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "VERIFIED")
        self.guard.require_no_active_run(
            projection.active_writer_run_id,
            projection.active_check_run_id,
        )
        request, scope_version = self._effective_request(request_id)
        if not request.experiment_line_id:
            raise OperatorGovernanceError("candidate integration requires a line-bound request")
        workspace = Path(self.store.read_workspace(request_id)["path"])
        if run_git(workspace, ["status", "--porcelain"]).stdout.strip():
            raise OperatorGovernanceError("candidate integration requires a clean worktree")
        snapshot = self._snapshot(request_id, request)
        if snapshot.patch_digest != projection.patch_digest or snapshot.head_sha != projection.head_sha:
            self.store.append_event(
                request_id,
                "request_reopened",
                "trusted-guard",
                {"reason": "patch_changed_before_integration"},
            )
            raise OperatorGovernanceError(
                "candidate changed after verification; request returned to ACTIVE"
            )
        full_checks = [
            check
            for check in self.store.read_check_runs(request_id)
            if check.mode == "full" and check.status == "PASSED"
        ]
        if not full_checks:
            raise OperatorGovernanceError("candidate integration requires a passing full CheckRun")
        check = full_checks[-1]
        if check.head_sha != snapshot.head_sha or check.patch_digest != snapshot.patch_digest:
            raise OperatorGovernanceError("latest full CheckRun is stale for candidate integration")
        validation = self.store.read_validation(request_id, check.check_id)
        if request.performance_baseline and not (validation.get("regression") or {}).get("ok"):
            raise OperatorGovernanceError("candidate integration lacks a passing experiment receipt")
        grant = self.store.read_budget_grant(grant_id)
        if (
            request.budget_grant_id != grant.grant_id
            or request.budget_grant_digest != grant.grant_digest
        ):
            raise OperatorGovernanceError(
                "candidate integration grant does not match the frozen request budget"
            )
        line = self.store.read_experiment_line(request.experiment_line_id)
        study = self.store.read_study(line.study_id)
        self._validated_study_memory_snapshot(study)
        self._validated_study_manifest_snapshot(study)
        grants = self.store.read_budget_grants(study.study_id)
        if not grants or grants[-1].grant_id != grant.grant_id:
            raise OperatorGovernanceError("candidate integration requires the current study budget grant")
        if grant.study_id != study.study_id or grant.model != study.primary_model:
            raise OperatorGovernanceError("candidate integration budget belongs to another study")
        if is_expired(grant.expires_at):
            raise OperatorGovernanceError("candidate integration budget grant has expired")
        usage = [
            entry
            for item in grants
            for entry in self.store.read_usage_entries(item.grant_id)
        ]
        if not usage:
            raise OperatorGovernanceError("candidate integration requires host-observed study usage")
        if any(entry.status == "RESERVED" for entry in usage):
            raise OperatorGovernanceError("candidate integration has running study SDK calls")
        if line.status != "OPEN":
            raise OperatorGovernanceError("candidate integration line is closed")
        if (
            request.base_sha != line.head_sha
            or request.experiment_line_generation != line.generation
        ):
            raise OperatorGovernanceError("candidate integration request is stale for the line")
        line_reference = f"refs/heads/{line.branch}"
        if rev_parse(self.project_root, line_reference) != line.head_sha:
            raise OperatorGovernanceError("experiment line Git ref disagrees with authority store")
        policy = self.policy()
        if study.policy_digest != digest_payload(policy.data):
            raise OperatorGovernanceError("study machine constitution is stale")
        integration_id = self.store.next_id("integration")
        integration_root = (
            self.config.operator.experiment_lines.root
            / line.line_id
            / integration_id
        ).resolve()
        integration_root.parent.mkdir(parents=True, exist_ok=True)
        artifact_ids: list[str] = []
        result_head: str | None = None
        result_tree: str | None = None
        decision: PolicyDecision | None = None
        results: list[dict[str, Any]] = []
        with self.store.experiment_line_lease(line.line_id):
            try:
                current_line = self.store.read_experiment_line(line.line_id)
                if (
                    current_line.head_sha != line.head_sha
                    or current_line.generation != line.generation
                ):
                    raise OperatorGovernanceError(
                        "experiment line advanced before integration lease acquisition"
                    )
                run_git(
                    self.project_root,
                    ["worktree", "add", "--detach", str(integration_root), line.head_sha],
                )
                run_git(
                    integration_root,
                    [
                        "-c",
                        "core.hooksPath=/dev/null",
                        "merge",
                        "--no-ff",
                        "--no-commit",
                        snapshot.head_sha,
                    ],
                )
                commit_message = (
                    f"Integrate Autobugfix candidate {request_id}\n\n"
                    f"Autobugfix-Integration: {integration_id}\n"
                    f"Autobugfix-Request: {request_id}"
                )
                run_git(
                    integration_root,
                    [
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "user.name=Autobugfix Guard",
                        "-c",
                        "user.email=autobugfix-guard@localhost",
                        "commit",
                        "-m",
                        commit_message,
                    ],
                )
                result_head = rev_parse(integration_root, "HEAD")
                result_tree = rev_parse(integration_root, "HEAD^{tree}")
                decision = evaluate_policy(
                    workspace,
                    request,
                    self.store.read_approvals(request_id),
                    constitution=policy.data,
                    trusted_policy_source=policy.source,
                    trusted_policy=policy.trusted,
                    phase="merge",
                    allowed_signers=self.allowed_signers,
                    scope_version=scope_version,
                )
                decision.required_profiles = list(check.profile_names)
                if not decision.allowed:
                    raise OperatorGovernanceError(
                        f"trusted integration policy rejected candidate: {decision.violations}"
                    )
                merge_snapshot = collect_candidate_snapshot(
                    integration_root,
                    request.base_sha,
                    [
                        str(item)
                        for item in policy.data.get("governance_metadata_paths") or []
                    ],
                )
                if (
                    decision.patch_digest != snapshot.patch_digest
                    or merge_snapshot.patch_digest != snapshot.patch_digest
                    or merge_snapshot.changed_files != snapshot.changed_files
                ):
                    raise OperatorGovernanceError(
                        "integration merge changed the verified candidate patch"
                    )
                results = run_validation_profiles(
                    integration_root,
                    self.project_root,
                    request_id,
                    integration_id,
                    decision,
                    policy.data,
                    log_root_override=(
                        self.store.artifact_root
                        / request_id
                        / "integrations"
                        / integration_id
                    ),
                    process_sandbox=self.config.operator.verification.process_sandbox,
                    require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                    network_access=self.config.operator.verification.network_access,
                    hidden_roots=(self.store.root, self.store.artifact_root),
                    read_only_binds=self._runtime_binds(integration_root),
                )
                failed = [item for item in results if not item["passed"]]
                for item in results:
                    for stream in ("stdout_path", "stderr_path"):
                        reference = self.store.register_artifact_file(
                            request_id,
                            producer="integration_guard",
                            trust_class="authoritative",
                            kind="integration-check-log",
                            path=Path(item[stream]),
                            check_run_id=check.check_id,
                            patch_digest=snapshot.patch_digest,
                        )
                        artifact_ids.append(reference.artifact_id)
                if failed:
                    raise OperatorGovernanceError(
                        "trusted integration validation failed: "
                        + ", ".join(str(item["name"]) for item in failed)
                    )
                if run_git(integration_root, ["status", "--porcelain"]).stdout.strip():
                    raise OperatorGovernanceError("integration validation changed the merge worktree")
                if rev_parse(integration_root, "HEAD") != result_head:
                    raise OperatorGovernanceError("integration HEAD changed during validation")
                updated_line = replace(
                    line,
                    head_sha=result_head,
                    generation=line.generation + 1,
                )
                integration = IntegrationRecord(
                    integration_id=integration_id,
                    study_id=study.study_id,
                    line_id=line.line_id,
                    kind="CANDIDATE",
                    expected_head_sha=line.head_sha,
                    expected_generation=line.generation,
                    candidate_head_sha=snapshot.head_sha,
                    result_head_sha=result_head,
                    result_tree_sha=result_tree,
                    patch_digest=snapshot.patch_digest,
                    policy_digest=digest_payload(policy.data),
                    actor=self._actor(actor),
                    request_id=request_id,
                    check_run_id=check.check_id,
                    budget_grant_id=grant.grant_id,
                    budget_digest=grant.grant_digest,
                    artifact_ids=tuple(artifact_ids),
                )
                observed_candidate = self._snapshot(request_id, request)
                if (
                    observed_candidate.head_sha != snapshot.head_sha
                    or observed_candidate.patch_digest != snapshot.patch_digest
                ):
                    raise OperatorGovernanceError(
                        "candidate changed while trusted integration was running"
                    )
                run_git(
                    self.project_root,
                    ["update-ref", line_reference, result_head, line.head_sha],
                )
                try:
                    self.store.advance_experiment_line(updated_line, integration)
                except Exception:
                    reverted = run_git(
                        self.project_root,
                        ["update-ref", line_reference, line.head_sha, result_head],
                        check=False,
                    )
                    if reverted.returncode != 0:
                        reconciliation = replace(
                            integration,
                            integration_id=self.store.next_id("reconciliation"),
                            kind="RECONCILIATION",
                        )
                        self.store.write_integration(reconciliation)
                    raise
                remote_result: dict[str, Any] = {"requested": push_remote, "pushed": False}
                if push_remote:
                    if not line.remote:
                        raise OperatorGovernanceError(
                            "remote push requested but experiment line has no configured remote"
                        )
                    pushed = run_git(
                        self.project_root,
                        ["push", "--atomic", line.remote, f"{result_head}:{line_reference}"],
                        check=False,
                        timeout_seconds=(
                            self.config.operator.experiment_lines.update_timeout_seconds
                        ),
                    )
                    remote_result = {
                        "requested": True,
                        "pushed": pushed.returncode == 0,
                        "remote": line.remote,
                        "stderr": pushed.stderr.strip(),
                    }
                    if pushed.returncode != 0:
                        reconciliation = replace(
                            integration,
                            integration_id=self.store.next_id("reconciliation"),
                            kind="RECONCILIATION",
                            expected_head_sha=result_head,
                            expected_generation=updated_line.generation,
                        )
                        self.store.write_integration(reconciliation)
                        raise OperatorGovernanceError(
                            "local integration succeeded but remote synchronization requires reconciliation"
                        )
                receipt = None
                try:
                    receipt = self.store.write_artifact(
                        request_id,
                        producer="integration_guard",
                        trust_class="authoritative",
                        kind="integration-receipt",
                        content=yaml.safe_dump(integration.to_dict(), sort_keys=False),
                        filename="integration-receipt.yaml",
                        check_run_id=check.check_id,
                        patch_digest=snapshot.patch_digest,
                    )
                except Exception as exc:
                    self.store.append_event(
                        request_id,
                        "integration_receipt_artifact_failed",
                        "trusted-guard",
                        {"integration_id": integration.integration_id, "error": str(exc)},
                    )
                self.store.append_event(
                    request_id,
                    "request_closed",
                    self._actor(actor),
                    {
                        "outcome": "integrated",
                        "integration_id": integration.integration_id,
                        "line_id": line.line_id,
                        "line_generation": updated_line.generation,
                    },
                )
                return {
                    "integration": integration.to_dict(),
                    "line": updated_line.to_dict(),
                    "receipt": receipt.to_dict() if receipt else None,
                    "validation": results,
                    "remote": remote_result,
                }
            finally:
                if integration_root.exists():
                    run_git(
                        self.project_root,
                        ["worktree", "remove", "--force", str(integration_root)],
                        check=False,
                    )

    def export_exp2_candidate_transition(
        self,
        *,
        operator_study_id: str,
        request_id: str,
        attribution_digest: str,
    ) -> dict[str, Any]:
        """Export one immutable Exp2 transition from trusted governance state."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2AttributionHypothesis,
            Exp2CandidateBinding,
            Exp2CandidateTransitionReceipt,
        )

        attribution_path = (
            self.config.operator.artifacts.root
            / "exp2-attributions"
            / f"{attribution_digest}.yaml"
        )
        if attribution_path.is_symlink() or not attribution_path.is_file():
            raise OperatorGovernanceError(
                "Exp2 transition attribution was not issued by Operator"
            )
        attribution_raw = yaml.safe_load(
            attribution_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(attribution_raw, Mapping):
            raise OperatorGovernanceError(
                "Exp2 transition attribution is invalid"
            )
        attribution = Exp2AttributionHypothesis.from_dict(
            self.verify_exp2_attribution(attribution_raw)
        )
        if attribution.operator_study_id != operator_study_id:
            raise OperatorGovernanceError(
                "Exp2 transition attribution belongs to another Operator Study"
            )

        request, _ = self._effective_request(request_id)
        if request.experiment_line_id is None:
            raise OperatorGovernanceError(
                "Exp2 transition requires a line-bound Operator request"
            )
        line = self.store.read_experiment_line(request.experiment_line_id)
        study = self.store.read_study(operator_study_id)
        if line.study_id != study.study_id or line.line_id != study.line_id:
            raise OperatorGovernanceError(
                "Exp2 transition request belongs to another Operator Study"
            )
        manifest = self._load_operator_yaml(
            Path(str(study.manifest_snapshot_path or "")),
            label="Exp2 Operator Study manifest",
        )
        subject_runtime = manifest.get("subject_runtime")
        if not isinstance(subject_runtime, Mapping):
            raise OperatorGovernanceError(
                "Exp2 Operator Study manifest lacks subject runtime authority"
            )
        runtime_digest = str(subject_runtime.get("record_digest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", runtime_digest):
            raise OperatorGovernanceError(
                "Exp2 Operator Study runtime digest is invalid"
            )
        integrations = [
            item
            for item in self.store.read_integrations(line.line_id)
            if item.kind == "CANDIDATE" and item.request_id == request_id
        ]
        if len(integrations) != 1:
            raise OperatorGovernanceError(
                "Exp2 transition requires exactly one request-bound candidate integration"
            )
        integration = integrations[0]
        if (
            line.head_sha != integration.result_head_sha
            or integration.expected_generation != 0
            or line.generation != 1
        ):
            raise OperatorGovernanceError(
                "Exp2 transition permits exactly one integrated revision"
            )
        grant = self.store.read_budget_grant(
            str(integration.budget_grant_id or "")
        )
        if (
            grant.study_id != study.study_id
            or grant.grant_digest != integration.budget_digest
            or request.budget_grant_id != grant.grant_id
            or request.budget_grant_digest != grant.grant_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 transition budget provenance is invalid"
            )
        writer_runs = [
            item
            for item in self.store.read_writer_runs(request_id)
            if item.status == "COMPLETED"
        ]
        checks = [
            item
            for item in self.store.read_check_runs(request_id)
            if item.status == "PASSED"
        ]
        fast_checks = [item for item in checks if item.mode == "fast"]
        full_checks = [item for item in checks if item.mode == "full"]
        if not writer_runs or not fast_checks or not full_checks:
            raise OperatorGovernanceError(
                "Exp2 transition lacks Writer, fast-check, or full-check evidence"
            )
        writer = writer_runs[-1]
        fast = fast_checks[-1]
        full = full_checks[-1]
        snapshot = self._snapshot(request_id, request)
        if (
            snapshot.patch_digest != integration.patch_digest
            or full.patch_digest != integration.patch_digest
            or full.head_sha != snapshot.head_sha
            or writer.patch_digest != integration.patch_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 transition Writer/check/integration evidence is stale"
            )
        usage = [
            entry
            for item in self.store.read_budget_grants(study.study_id)
            for entry in self.store.read_usage_entries(item.grant_id)
        ]
        if not usage or any(item.status == "RESERVED" for item in usage):
            raise OperatorGovernanceError(
                "Exp2 transition lacks terminal host-observed usage"
            )
        allowed = tuple(attribution.execution_scope)
        requested = tuple(request.planned_paths)
        actual = tuple(snapshot.changed_files)
        if (
            not allowed
            or not requested
            or not actual
            or not set(requested).issubset(allowed)
            or not set(actual).issubset(allowed)
        ):
            raise OperatorGovernanceError(
                "Exp2 transition changed paths escape the frozen Execution allowlist"
            )
        eval_binding = self.guard_study_binding(
            operator_study_id,
            kind="CANDIDATE",
            terminalize=True,
        )
        eval_binding_path = self._write_exp2_operator_record(
            "exp2-study-bindings",
            eval_binding,
        )
        candidate_digests = self._experiment_digests_at_subject(
            study_id=study.study_id,
            subject_sha=integration.result_head_sha,
            primary_model=study.primary_model,
        )
        binding = Exp2CandidateBinding(
            study_id=attribution.study_id,
            parent_sha=integration.expected_head_sha,
            parent_tree=rev_parse(
                self.project_root,
                f"{integration.expected_head_sha}^{{tree}}",
            ),
            candidate_sha=integration.result_head_sha,
            candidate_tree=integration.result_tree_sha,
            candidate_diff_digest=integration.patch_digest,
            allowlist_digest=digest_payload(
                {"allowed_paths": list(allowed)}
            ),
            scope_digest=digest_payload(
                {
                    "requested_paths": list(requested),
                    "actual_paths": list(actual),
                }
            ),
            operator_policy_digest=study.policy_digest,
            memory_fixture_digest=study.memory_digest,
            operator_role_skill_digest=candidate_digests[
                "operator_role_skill_digest"
            ],
            execution_role_skill_digest=candidate_digests[
                "execution_role_skill_digest"
            ],
            runtime_digest=runtime_digest,
            request_digest=request.request_digest,
            integration_digest=integration.to_dict()["record_digest"],
        )
        receipt = Exp2CandidateTransitionReceipt(
            study_id=attribution.study_id,
            issuer="operator-governance-service-v4",
            attribution_digest=attribution_digest,
            source_projection_bundle_digest=(
                attribution.source_projection_bundle_digest
            ),
            operator_study_id=study.study_id,
            line_id=line.line_id,
            request_id=request.request_id,
            request_digest=request.request_digest,
            grant_id=grant.grant_id,
            grant_digest=grant.grant_digest,
            writer_run_id=writer.run_id,
            writer_run_digest=writer.to_dict()["record_digest"],
            fast_check_digest=fast.to_dict()["record_digest"],
            full_check_digest=full.to_dict()["record_digest"],
            integration_id=integration.integration_id,
            integration_digest=integration.to_dict()["record_digest"],
            usage_digest=digest_payload(
                {"usage": [item.to_dict() for item in usage]}
            ),
            eval_study_binding_path=str(eval_binding_path),
            eval_study_binding_digest=str(eval_binding["record_digest"]),
            requested_paths=requested,
            allowed_paths=allowed,
            actual_paths=actual,
            binding=binding,
        )
        record = receipt.to_dict()
        path = self._write_exp2_operator_record(
            "exp2-candidate-transitions",
            record,
        )
        return {**record, "artifact_path": str(path)}

    def export_exp2_h0_binding(self, operator_study_id: str) -> dict[str, Any]:
        """Export the immutable pre-line H0 Study binding consumed by Exp2."""

        binding = self.guard_study_binding(
            operator_study_id,
            kind="BASELINE",
        )
        path = self._write_exp2_operator_record(
            "exp2-study-bindings",
            binding,
        )
        return {**binding, "artifact_path": str(path)}

    def export_exp2_attribution(
        self,
        *,
        exp2_study_id: str,
        operator_study_id: str,
        evidence_id: str,
        expected_mechanism: str,
        execution_scope: Iterable[str],
        validation_plan: Iterable[str],
        hypothesis: str,
    ) -> dict[str, Any]:
        """Bind an Operator-supervisor hypothesis to source-only Exp2 evidence."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2AttributionHypothesis,
            Exp2SourceProjectionBundle,
        )

        evidence = self.store.read_study_evidence(evidence_id)
        if (
            evidence.study_id != operator_study_id
            or evidence.source_kind != "exp2_source_projection"
        ):
            raise OperatorGovernanceError(
                "Exp2 attribution evidence belongs to another Study or audience"
            )
        raw = self._load_operator_yaml(
            Path(evidence.artifact_path),
            label="Exp2 source projection evidence",
        )
        bundle = Exp2SourceProjectionBundle.from_dict(raw)
        if bundle.record_digest != evidence.source_record_digest:
            raise OperatorGovernanceError(
                "Exp2 source projection evidence digest drift"
            )
        item = Exp2AttributionHypothesis(
            study_id=exp2_study_id,
            issuer="operator-governance-service-v4",
            operator_study_id=operator_study_id,
            evidence_id=evidence.evidence_id,
            evidence_digest=evidence.to_dict()["record_digest"],
            author_role="operator_supervisor",
            source_projection_bundle_digest=bundle.record_digest,
            supporting_receipt_digests=tuple(
                projection.receipt_digest
                for projection in bundle.projections
            ),
            expected_mechanism=expected_mechanism,
            execution_scope=tuple(execution_scope),
            validation_plan=tuple(validation_plan),
            hypothesis=hypothesis,
        )
        record = item.to_dict()
        path = self._write_exp2_operator_record(
            "exp2-attributions",
            record,
        )
        return {**record, "artifact_path": str(path)}

    def verify_exp2_attribution(
        self,
        attribution: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Verify a persisted source-only Exp2 attribution."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2AttributionHypothesis,
            Exp2SourceProjectionBundle,
        )

        item = Exp2AttributionHypothesis.from_dict(attribution)
        path = (
            self.config.operator.artifacts.root
            / "exp2-attributions"
            / f"{item.record_digest}.yaml"
        )
        if path.is_symlink() or not path.is_file():
            raise OperatorGovernanceError(
                "Exp2 attribution was not exported by Operator"
            )
        stored = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        evidence = self.store.read_study_evidence(item.evidence_id)
        source = self._load_operator_yaml(
            Path(evidence.artifact_path),
            label="Exp2 source projection evidence",
        )
        bundle = Exp2SourceProjectionBundle.from_dict(source)
        if (
            stored != item.to_dict()
            or evidence.study_id != item.operator_study_id
            or evidence.to_dict()["record_digest"] != item.evidence_digest
            or evidence.source_kind != "exp2_source_projection"
            or bundle.record_digest
            != item.source_projection_bundle_digest
            or tuple(
                projection.receipt_digest
                for projection in bundle.projections
            )
            != item.supporting_receipt_digests
        ):
            raise OperatorGovernanceError(
                "Exp2 attribution provenance no longer verifies"
            )
        return item.to_dict()

    def verify_exp2_candidate_transition(
        self,
        transition: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Revalidate an exported Exp2 transition against current trusted state."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2AttributionHypothesis,
            Exp2CandidateTransitionReceipt,
        )

        item = Exp2CandidateTransitionReceipt.from_dict(transition)
        stored_path = (
            self.config.operator.artifacts.root
            / "exp2-candidate-transitions"
            / f"{item.record_digest}.yaml"
        )
        if stored_path.is_symlink() or not stored_path.is_file():
            raise OperatorGovernanceError(
                "Exp2 candidate transition was not exported by Operator"
            )
        stored = yaml.safe_load(stored_path.read_text(encoding="utf-8")) or {}
        if stored != item.to_dict():
            raise OperatorGovernanceError(
                "Exp2 candidate transition differs from the Operator artifact"
            )
        attribution_path = (
            self.config.operator.artifacts.root
            / "exp2-attributions"
            / f"{item.attribution_digest}.yaml"
        )
        if attribution_path.is_symlink() or not attribution_path.is_file():
            raise OperatorGovernanceError(
                "Exp2 candidate transition attribution is missing"
            )
        attribution_raw = yaml.safe_load(
            attribution_path.read_text(encoding="utf-8")
        ) or {}
        if not isinstance(attribution_raw, Mapping):
            raise OperatorGovernanceError(
                "Exp2 candidate transition attribution is invalid"
            )
        attribution = Exp2AttributionHypothesis.from_dict(
            self.verify_exp2_attribution(attribution_raw)
        )
        if (
            attribution.study_id != item.study_id
            or attribution.operator_study_id != item.operator_study_id
            or attribution.source_projection_bundle_digest
            != item.source_projection_bundle_digest
            or tuple(attribution.execution_scope) != item.requested_paths
            or tuple(attribution.execution_scope) != item.allowed_paths
        ):
            raise OperatorGovernanceError(
                "Exp2 candidate transition attribution binding drift"
            )
        request = self.store.read_request(item.request_id)
        integration = self.store.read_integration(item.integration_id)
        grant = self.store.read_budget_grant(item.grant_id)
        writer = self.store.read_writer_run(item.writer_run_id)
        checks = self.store.read_check_runs(item.request_id)
        usage = [
            entry
            for candidate_grant in self.store.read_budget_grants(
                item.operator_study_id
            )
            for entry in self.store.read_usage_entries(candidate_grant.grant_id)
        ]
        study = self.store.read_study(item.operator_study_id)
        manifest = self._load_operator_yaml(
            Path(str(study.manifest_snapshot_path or "")),
            label="Exp2 Operator Study manifest",
        )
        subject_runtime = manifest.get("subject_runtime")
        if (
            request.request_digest != item.request_digest
            or integration.to_dict()["record_digest"]
            != item.integration_digest
            or integration.kind != "CANDIDATE"
            or integration.result_head_sha != item.binding.candidate_sha
            or integration.result_tree_sha != item.binding.candidate_tree
            or integration.expected_head_sha != item.binding.parent_sha
            or integration.patch_digest != item.binding.candidate_diff_digest
            or grant.grant_digest != item.grant_digest
            or writer.to_dict()["record_digest"] != item.writer_run_digest
            or not any(
                check.mode == "fast"
                and check.status == "PASSED"
                and check.to_dict()["record_digest"] == item.fast_check_digest
                for check in checks
            )
            or not any(
                check.mode == "full"
                and check.status == "PASSED"
                and check.to_dict()["record_digest"] == item.full_check_digest
                for check in checks
            )
            or digest_payload({"usage": [entry.to_dict() for entry in usage]})
            != item.usage_digest
            or not isinstance(subject_runtime, Mapping)
            or subject_runtime.get("record_digest")
            != item.binding.runtime_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 candidate transition provenance no longer verifies"
            )
        eval_binding_path = Path(item.eval_study_binding_path)
        if eval_binding_path.is_symlink() or not eval_binding_path.is_file():
            raise OperatorGovernanceError(
                "Exp2 candidate Eval binding is missing"
            )
        eval_binding = yaml.safe_load(
            eval_binding_path.read_text(encoding="utf-8")
        ) or {}
        if (
            not isinstance(eval_binding, Mapping)
            or eval_binding.get("record_digest")
            != item.eval_study_binding_digest
            or self.verify_guard_study_binding(eval_binding) != dict(eval_binding)
        ):
            raise OperatorGovernanceError(
                "Exp2 candidate Eval binding no longer verifies"
            )
        candidate_digests = self._experiment_digests_at_subject(
            study_id=item.operator_study_id,
            subject_sha=item.binding.candidate_sha,
            primary_model=grant.model,
        )
        if (
            candidate_digests["operator_role_skill_digest"]
            != item.binding.operator_role_skill_digest
            or candidate_digests["execution_role_skill_digest"]
            != item.binding.execution_role_skill_digest
            or item.binding.allowlist_digest
            != digest_payload({"allowed_paths": list(item.allowed_paths)})
            or item.binding.scope_digest
            != digest_payload(
                {
                    "requested_paths": list(item.requested_paths),
                    "actual_paths": list(item.actual_paths),
                }
            )
        ):
            raise OperatorGovernanceError(
                "Exp2 candidate transition scope or role-skill identity drift"
            )
        return item.to_dict()

    def export_exp2_rollback_receipt(
        self,
        transition: Mapping[str, Any],
        *,
        rollback_authorization_path: Path | str,
        reason: str,
        push_remote: bool = False,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Roll back an Exp2 candidate and export the trusted rollback receipt."""

        from autobugfix.eval.benchmarks.exp2_resume import (
            Exp2CandidateTransitionReceipt,
            Exp2RollbackAuthorization,
            Exp2RollbackReceipt,
        )

        candidate = Exp2CandidateTransitionReceipt.from_dict(
            self.verify_exp2_candidate_transition(transition)
        )
        authorization_source = Path(rollback_authorization_path)
        if authorization_source.is_symlink():
            raise OperatorGovernanceError(
                "Exp2 rollback authorization cannot be redirected"
            )
        authorization_source = authorization_source.resolve()
        trusted_exp2_root = (
            self.config.eval.benchmarks.trusted_case_root / "exp2"
        ).resolve()
        if (
            not authorization_source.is_file()
            or not authorization_source.is_relative_to(trusted_exp2_root)
        ):
            raise OperatorGovernanceError(
                "Exp2 rollback authorization is outside trusted Eval state"
            )
        raw_authorization = self._load_operator_yaml(
            authorization_source,
            label="Exp2 rollback authorization",
        )
        authorization = Exp2RollbackAuthorization.from_dict(raw_authorization)
        if (
            authorization.study_id != candidate.study_id
            or authorization.candidate_transition_digest
            != candidate.record_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 rollback authorization differs from the candidate"
            )
        study = self.store.read_study(candidate.operator_study_id)
        result = self.rollback_experiment_line(
            candidate.line_id,
            study.base_checkpoint_id,
            reason=reason,
            push_remote=push_remote,
            actor=actor,
        )
        integration = result.get("integration")
        line = result.get("line")
        if not isinstance(integration, Mapping) or not isinstance(line, Mapping):
            raise OperatorGovernanceError(
                "Exp2 rollback did not return trusted line evidence"
            )
        rollback_payload = {
            "schema": "autobugfix-exp2-operator-rollback-artifact-v2",
            "candidate_transition_digest": candidate.record_digest,
            "rollback_authorization_digest": authorization.record_digest,
            "rollback_authorization_path": str(authorization_source),
            "integration": dict(integration),
            "line": dict(line),
            "checkpoint": dict(result.get("checkpoint") or {}),
            "validation": list(result.get("validation") or []),
            "remote": dict(result.get("remote") or {}),
        }
        rollback_artifact = {
            **rollback_payload,
            "record_digest": digest_payload(rollback_payload),
        }
        self._write_exp2_operator_record(
            "exp2-rollback-artifacts",
            rollback_artifact,
        )
        receipt = Exp2RollbackReceipt(
            study_id=candidate.study_id,
            issuer="operator-governance-service-v4",
            candidate_transition_digest=candidate.record_digest,
            rollback_authorization_digest=authorization.record_digest,
            line_id=candidate.line_id,
            rollback_integration_id=str(integration["integration_id"]),
            rollback_integration_digest=str(integration["record_digest"]),
            post_rollback_head_sha=str(line["head_sha"]),
            post_rollback_tree_sha=str(integration["result_tree_sha"]),
            rollback_artifact_digest=str(
                rollback_artifact["record_digest"]
            ),
        )
        record = receipt.to_dict()
        path = self._write_exp2_operator_record(
            "exp2-rollback-receipts",
            record,
        )
        return {**record, "artifact_path": str(path)}

    def verify_exp2_rollback_receipt(
        self,
        rollback: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Verify a persisted Exp2 rollback against Git and Operator state."""

        from autobugfix.eval.benchmarks.exp2_resume import Exp2RollbackReceipt

        item = Exp2RollbackReceipt.from_dict(rollback)
        stored_path = (
            self.config.operator.artifacts.root
            / "exp2-rollback-receipts"
            / f"{item.record_digest}.yaml"
        )
        if stored_path.is_symlink() or not stored_path.is_file():
            raise OperatorGovernanceError(
                "Exp2 rollback receipt was not exported by Operator"
            )
        stored = yaml.safe_load(stored_path.read_text(encoding="utf-8")) or {}
        integration = self.store.read_integration(
            item.rollback_integration_id
        )
        line = self.store.read_experiment_line(item.line_id)
        artifact_path = (
            self.config.operator.artifacts.root
            / "exp2-rollback-artifacts"
            / f"{item.rollback_artifact_digest}.yaml"
        )
        if (
            stored != item.to_dict()
            or integration.kind != "ROLLBACK"
            or integration.to_dict()["record_digest"]
            != item.rollback_integration_digest
            or line.head_sha != item.post_rollback_head_sha
            or rev_parse(self.project_root, f"{line.head_sha}^{{tree}}")
            != item.post_rollback_tree_sha
            or artifact_path.is_symlink()
            or not artifact_path.is_file()
        ):
            raise OperatorGovernanceError(
                "Exp2 rollback receipt no longer verifies"
            )
        artifact = yaml.safe_load(
            artifact_path.read_text(encoding="utf-8")
        ) or {}
        if (
            not isinstance(artifact, Mapping)
            or self._verified_operator_record(
                artifact,
                label="Exp2 rollback artifact",
            )
            != dict(artifact)
            or artifact.get("record_digest")
            != item.rollback_artifact_digest
            or artifact.get("candidate_transition_digest")
            != item.candidate_transition_digest
            or artifact.get("rollback_authorization_digest")
            != item.rollback_authorization_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 rollback artifact differs from the receipt"
            )
        authorization_path = Path(
            str(artifact.get("rollback_authorization_path") or "")
        )
        authorization = self._load_operator_yaml(
            authorization_path,
            label="Exp2 rollback authorization",
        )
        if (
            authorization.get("record_digest")
            != item.rollback_authorization_digest
            or authorization.get("candidate_transition_digest")
            != item.candidate_transition_digest
        ):
            raise OperatorGovernanceError(
                "Exp2 rollback authorization no longer verifies"
            )
        return item.to_dict()

    def _actor(self, actor: str | None) -> str:
        return actor or getpass.getuser()

    def _runtime_binds(self, candidate_root: Path) -> tuple[tuple[Path, Path], ...]:
        runtime_venv = self.config.operator.verification.runtime_venv
        if runtime_venv is None or not runtime_venv.is_dir():
            return ()
        return ((runtime_venv, candidate_root / ".venv"),)

    def _validate_operator_role(self, role_name: str, role: Any) -> None:
        defaults = ((self.policy().data.get("static_invariants") or {}).get("role_defaults") or {})
        expected = defaults.get(role_name) or {}
        violations: list[str] = []
        if role.backend != "codex":
            violations.append(f"{role_name}.backend must be codex")
        for field in ("sandbox", "approval_mode"):
            required = expected.get(field)
            if required is not None and getattr(role, field) != required:
                violations.append(
                    f"{role_name}.{field} must be {required!r}, got {getattr(role, field)!r}"
                )
        required_skill = expected.get("required_skill")
        if required_skill and not any(str(path).endswith(str(required_skill)) for path in role.skill_paths):
            violations.append(f"{role_name} is missing required skill {required_skill!r}")
        if violations:
            raise OperatorGovernanceError("Operator role violates machine constitution: " + "; ".join(violations))

    def _validate_evidence(self, evidence: Iterable[str]) -> tuple[str, ...]:
        values = tuple(str(item) for item in evidence if str(item).strip())
        if not values:
            raise OperatorGovernanceError("at least one evidence reference is required")
        for value in values:
            if value.startswith(("sha256:", "uri:", "note:", "study-evidence:")):
                continue
            path = Path(value)
            if not path.is_absolute():
                path = self.project_root / path
            if not path.exists():
                raise OperatorGovernanceError(f"evidence path does not exist: {value}")
        return values

    def create_triage(
        self,
        *,
        summary: str,
        suspected_layers: Iterable[str],
        evidence: Iterable[str],
        confidence: str = "low",
        next_actions: Iterable[str] = (),
        creator: str | None = None,
        triage_id: str | None = None,
    ) -> OperatorTriage:
        triage = OperatorTriage(
            triage_id=triage_id or self.store.next_id("triage"),
            summary=summary,
            suspected_layers=tuple(suspected_layers),
            evidence=self._validate_evidence(evidence),
            creator=self._actor(creator),
            confidence=confidence,
            next_actions=tuple(next_actions),
        )
        self.store.write_triage(triage)
        return triage

    def create_request(
        self,
        *,
        triage_id: str,
        summary: str,
        primary_layer: str,
        secondary_layers: Iterable[str] = (),
        planned_paths: Iterable[str] = (),
        requested_risk: str = "low",
        validation_profiles: Iterable[str] = (),
        performance_baseline: str | None = None,
        creator: str | None = None,
        request_id: str | None = None,
        branch: str | None = None,
        experiment_line_id: str | None = None,
        budget_grant_id: str | None = None,
        base_ref: str | None = None,
        expires_at: str | None = None,
    ) -> OperatorRequest:
        if experiment_line_id and base_ref:
            raise OperatorGovernanceError("experiment_line_id and base_ref are mutually exclusive")
        triage = self.store.read_triage(triage_id)
        policy = self.policy()
        actor = self._actor(creator)
        identifier = request_id or self.store.next_id("request")
        branch_name = branch or self.config.operator.worktrees.branch_template.format(request_id=identifier)
        profiles = tuple(dict.fromkeys(validation_profiles or self.config.operator.verification.fast_profiles))
        line_generation: int | None = None
        budget_grant_digest: str | None = None
        if experiment_line_id:
            if not budget_grant_id:
                raise OperatorGovernanceError(
                    "experiment line binding requires a budget grant"
                )
            line = self.store.read_experiment_line(experiment_line_id)
            if line.status != "OPEN":
                raise OperatorGovernanceError(f"experiment line is not open: {experiment_line_id}")
            study = self.store.read_study(line.study_id)
            self._validated_study_memory_snapshot(study)
            self._validated_study_manifest_snapshot(study)
            if study.policy_digest != digest_payload(policy.data):
                raise OperatorGovernanceError("experiment line machine constitution is stale")
            git_head = rev_parse(self.project_root, f"refs/heads/{line.branch}")
            if git_head != line.head_sha:
                raise OperatorGovernanceError("experiment line Git ref disagrees with authority store")
            base_sha = line.head_sha
            line_generation = line.generation
            if budget_grant_id:
                grant = self.store.read_budget_grant(budget_grant_id)
                grants = self.store.read_budget_grants(study.study_id)
                if grant.study_id != study.study_id:
                    raise OperatorGovernanceError("budget grant belongs to another study")
                if not grants or grants[-1].grant_id != grant.grant_id:
                    raise OperatorGovernanceError("budget grant is not current for the study")
                if is_expired(grant.expires_at):
                    raise OperatorGovernanceError("budget grant has expired")
                budget_grant_digest = grant.grant_digest
            self._validated_study_evidence(
                study,
                triage.evidence,
                expected_subject_sha=line.head_sha,
            )
        elif budget_grant_id:
            raise OperatorGovernanceError("budget grant binding requires experiment_line_id")
        else:
            base_sha = rev_parse(self.project_root, base_ref or "HEAD")
        request = OperatorRequest(
            request_id=identifier,
            summary=summary,
            primary_layer=primary_layer,
            secondary_layers=tuple(secondary_layers),
            planned_paths=tuple(dict.fromkeys(str(item) for item in planned_paths)),
            requested_risk=requested_risk,
            triage_id=triage.triage_id,
            triage_digest=triage.triage_digest,
            evidence=triage.evidence,
            validation_profiles=profiles,
            performance_baseline=performance_baseline,
            branch=branch_name,
            base_sha=base_sha,
            creator=actor,
            constitution_digest=digest_payload(policy.data),
            experiment_line_id=experiment_line_id,
            experiment_line_generation=line_generation,
            budget_grant_id=budget_grant_id,
            budget_grant_digest=budget_grant_digest,
            expires_at=expires_at or _default_expiry(),
        )
        self.store.write_request(request)
        self.store.append_event(
            request.request_id,
            "request_created",
            actor,
            {
                "request_digest": request.request_digest,
                "base_sha": request.base_sha,
                "constitution_digest": request.constitution_digest,
                "experiment_line_id": request.experiment_line_id,
                "experiment_line_generation": request.experiment_line_generation,
                "budget_grant_id": request.budget_grant_id,
                "budget_grant_digest": request.budget_grant_digest,
            },
        )
        return request

    @_leased_request
    def add_reviewer_decision(
        self,
        request_id: str,
        *,
        reviewer: str,
        decision: str,
        reason: str,
        allowed_layers: Iterable[str] | None = None,
        allowed_paths: Iterable[str] = (),
        expires_at: str | None = None,
        scope_revision_id: str | None = None,
    ) -> OperatorApproval:
        request, scope_version = self._approval_target(request_id, scope_revision_id)
        if reviewer == request.creator:
            raise OperatorGovernanceError("request creator cannot independently review its own request")
        approval = OperatorApproval(
            approval_id=self.store.next_id("review"),
            request_id=request.request_id,
            request_digest=request.request_digest,
            base_sha=request.base_sha,
            approver=reviewer,
            kind="reviewer",
            stage="scope",
            decision=decision,
            reason=reason,
            allowed_layers=tuple(sorted(set(allowed_layers or request.declared_layers))),
            scope_version=scope_version,
            allowed_paths=tuple(allowed_paths),
            expires_at=expires_at or request.expires_at,
        )
        self.store.write_approval(approval)
        self.store.append_event(
            request_id,
            "approval_recorded",
            reviewer,
            {"approval_id": approval.approval_id, "kind": approval.kind, "decision": approval.decision},
        )
        return approval

    def create_approval_payload(
        self,
        request_id: str,
        output: Path,
        *,
        approver: str,
        stage: str,
        reason: str,
        allowed_layers: Iterable[str] | None = None,
        allowed_paths: Iterable[str] = (),
        expires_at: str | None = None,
        scope_revision_id: str | None = None,
    ) -> Path:
        effective, scope_version = self._approval_target(request_id, scope_revision_id)
        patch_digest: str | None = None
        head_sha: str | None = None
        if stage == "merge":
            snapshot = self._snapshot(request_id, effective)
            patch_digest = snapshot.patch_digest
            head_sha = snapshot.head_sha
        payload = approval_signing_payload(
            effective,
            approver=approver,
            stage=stage,
            reason=reason,
            allowed_layers=allowed_layers,
            allowed_paths=allowed_paths,
            expires_at=expires_at or effective.expires_at,
            patch_digest=patch_digest,
            head_sha=head_sha,
            scope_version=scope_version,
        )
        return write_signing_payload(output, payload)

    @_leased_request
    def import_signed_approval(
        self,
        request_id: str,
        *,
        payload_path: Path,
        signature_path: Path,
        scope_revision_id: str | None = None,
    ) -> OperatorApproval:
        effective, _ = self._approval_target(request_id, scope_revision_id)
        approval = signed_approval_from_files(
            effective,
            self.store.next_id("signed"),
            payload_path,
            signature_path,
            self.policy().data,
            allowed_signers=self.allowed_signers,
        )
        self.store.write_approval(approval)
        self.store.append_event(
            request_id,
            "approval_recorded",
            approval.approver,
            {"approval_id": approval.approval_id, "kind": approval.kind, "stage": approval.stage},
        )
        return approval

    @_leased_request
    def import_github_approval(
        self,
        request_id: str,
        *,
        repository: str,
        pull_request: int,
        review_id: int,
        reason: str,
        stage: str = "merge",
    ) -> OperatorApproval:
        request = self.store.read_request(request_id)
        effective, scope_version = effective_request(request, self.store.read_scope_revisions(request_id))
        approval = github_approval(
            effective,
            self.store.next_id("github"),
            repository=repository,
            pull_request=pull_request,
            review_id=review_id,
            constitution=self.policy().data,
            reason=reason,
            stage=stage,
            scope_version=scope_version,
        )
        self.store.write_approval(approval)
        self.store.append_event(
            request_id,
            "approval_recorded",
            approval.approver,
            {"approval_id": approval.approval_id, "kind": approval.kind, "stage": approval.stage},
        )
        return approval

    def projection(self, request_id: str):
        self.store.read_request(request_id)
        return project_request(request_id, self.store.read_events(request_id))

    def _request_for_revision(self, request_id: str, revision: ScopeRevision) -> OperatorRequest:
        base = self.store.read_request(request_id)
        if not revision.layers:
            raise OperatorGovernanceError("scope revision has no layers")
        return replace(
            base,
            primary_layer=revision.layers[0],
            secondary_layers=tuple(revision.layers[1:]),
            planned_paths=revision.paths,
            requested_risk=max_risk(base.requested_risk, revision.requested_risk),
        )

    def _approval_target(
        self, request_id: str, scope_revision_id: str | None
    ) -> tuple[OperatorRequest, int]:
        if scope_revision_id is None:
            return self._effective_request(request_id)
        revisions = self.store.read_scope_revisions(request_id)
        try:
            revision = next(item for item in revisions if item.revision_id == scope_revision_id)
        except StopIteration as exc:
            raise OperatorGovernanceError(f"unknown scope revision: {scope_revision_id}") from exc
        if revision.status not in {"PROPOSED", "APPROVED"}:
            raise OperatorGovernanceError(f"scope revision cannot receive approval: {revision.status}")
        return self._request_for_revision(request_id, revision), revision.version

    def _effective_request(self, request_id: str) -> tuple[OperatorRequest, int]:
        return effective_request(
            self.store.read_request(request_id), self.store.read_scope_revisions(request_id)
        )

    def preflight(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        request, scope_version = self._effective_request(request_id)
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "REQUESTED")
        triage = self.store.read_triage(request.triage_id)
        policy = self.policy()
        violations: list[str] = []
        if not policy.trusted:
            violations.append("bootstrap policy is feedback-only and cannot authorize an Operator request")
        if triage.triage_digest != request.triage_digest:
            violations.append("request triage digest mismatch")
        if request.constitution_digest != digest_payload(policy.data):
            violations.append("request trusted constitution digest is stale")
        if is_expired(request.expires_at):
            violations.append("operator request has expired")
        if rev_parse(self.project_root, request.base_sha) != request.base_sha:
            violations.append("operator request base SHA is not canonical")
        if request.branch in {str(item) for item in policy.data.get("protected_branches") or []}:
            violations.append(f"operator request branch is protected: {request.branch}")
        if request.experiment_line_id:
            line: ExperimentLineRecord | None = None
            try:
                line = self.store.read_experiment_line(request.experiment_line_id)
            except OperatorStoreError as exc:
                violations.append(str(exc))
            else:
                if line.status != "OPEN":
                    violations.append("operator request experiment line is closed")
                if (
                    line.head_sha != request.base_sha
                    or line.generation != request.experiment_line_generation
                ):
                    violations.append("operator request experiment line advanced after request creation")
                else:
                    try:
                        line_head = rev_parse(self.project_root, f"refs/heads/{line.branch}")
                    except GitError as exc:
                        violations.append(str(exc))
                    else:
                        if line_head != line.head_sha:
                            violations.append("experiment line Git ref disagrees with authority store")
            if not request.budget_grant_id:
                violations.append("line-bound operator request requires a frozen budget grant")
            elif line is not None:
                try:
                    grant = self.store.read_budget_grant(request.budget_grant_id)
                    study = self.store.read_study(line.study_id)
                    self._validated_study_memory_snapshot(study)
                    self._validated_study_manifest_snapshot(study)
                    grants = self.store.read_budget_grants(study.study_id)
                except (OperatorStoreError, OperatorGovernanceError) as exc:
                    violations.append(str(exc))
                else:
                    if grant.grant_digest != request.budget_grant_digest:
                        violations.append("operator request budget grant digest is stale")
                    if grant.study_id != study.study_id:
                        violations.append("operator request budget belongs to another study")
                    if not grants or grants[-1].grant_id != grant.grant_id:
                        violations.append("operator request budget grant is superseded")
                    if is_expired(grant.expires_at):
                        violations.append("operator request budget grant has expired")
                    try:
                        self._validated_study_evidence(
                            study,
                            request.evidence,
                            expected_subject_sha=request.base_sha,
                        )
                    except (OperatorStoreError, OperatorGovernanceError) as exc:
                        violations.append(str(exc))
        minimums = policy.data.get("operator_runtime_minimums") or {}
        if bool(minimums.get("require_process_sandbox", True)) and not self.config.operator.verification.require_process_sandbox:
            violations.append("project config cannot disable the authoritative process sandbox")
        if not bool(minimums.get("verification_network_access", False)) and self.config.operator.verification.network_access:
            violations.append("project config cannot enable network for authoritative verification")
        experiment_governance = policy.data.get("experiment_governance") or {}
        budget_minimums = experiment_governance.get("budgets") or {}
        configured_budget = self.config.operator.budgets
        required_waves = tuple(int(item) for item in budget_minimums.get("waves") or ())
        if required_waves and configured_budget.allowed_waves != required_waves:
            violations.append("project config cannot change trusted experiment budget waves")
        allowed_models = {str(item) for item in budget_minimums.get("allowed_primary_models") or []}
        if allowed_models and not set(configured_budget.allowed_primary_models).issubset(allowed_models):
            violations.append("project config enables a model outside the trusted experiment allowlist")
        maximum_concurrency = budget_minimums.get("max_case_concurrency")
        if maximum_concurrency is not None and (
            configured_budget.max_case_concurrency > int(maximum_concurrency)
            or configured_budget.default_case_concurrency > int(maximum_concurrency)
        ):
            violations.append("project config exceeds trusted experiment case concurrency")
        if str(budget_minimums.get("model_fallback", "forbidden")) == "forbidden" and (
            configured_budget.allow_model_fallback
        ):
            violations.append("project config cannot enable experiment model fallback")
        baseline_layers = {
            str(item) for item in policy.data.get("baseline_required_layers") or []
        }
        if request.declared_layers & baseline_layers:
            if not request.performance_baseline:
                violations.append("behavior-affecting scope requires a trusted performance baseline")
            else:
                try:
                    baseline = baseline_for_request(
                        self.project_root,
                        request.performance_baseline,
                        request.base_sha,
                    )
                except OperatorMetricsError as exc:
                    violations.append(str(exc))
                else:
                    configured = self.config.operator.experiments.profiles.get(
                        str(baseline["profile"])
                    )
                    if configured is None:
                        violations.append(
                            f"missing trusted experiment profile: {baseline['profile']}"
                        )
                    elif digest_payload(configured) != baseline["profile_digest"]:
                        violations.append(
                            "configured experiment profile does not match committed baseline contract"
                        )
        authority = check_scope_authority(
            request,
            self.store.read_approvals(request_id),
            policy.data,
            allowed_signers=self.allowed_signers,
            scope_version=scope_version,
        )
        violations.extend(authority.violations)
        return {
            "allowed": not violations,
            "request_id": request_id,
            "request_digest": request.request_digest,
            "scope_version": scope_version,
            "trusted_policy_source": policy.source,
            "authority": authority.to_dict(),
            "violations": violations,
        }

    @_leased_request
    def start(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        report = self.preflight(request_id, actor=actor)
        if not report["allowed"]:
            raise OperatorGovernanceError(f"operator start rejected: {report['violations']}")
        request, scope_version = self._effective_request(request_id)
        policy = self.policy()
        try:
            workspace = self.store.read_workspace(request_id)
            recovered = recover_operator_workspace(
                self.project_root,
                request,
                worktree_root=self.config.operator.worktrees.root,
            )
            if recovered is None or Path(workspace["path"]).resolve() != Path(recovered["path"]):
                raise OperatorGovernanceError("stored Operator workspace does not match Git reality")
        except OperatorStoreError:
            workspace = recover_operator_workspace(
                self.project_root,
                request,
                worktree_root=self.config.operator.worktrees.root,
            ) or create_operator_workspace(
                self.project_root,
                request,
                policy.data,
                worktree_root=self.config.operator.worktrees.root,
            )
        candidate_root = Path(workspace["path"]).resolve()
        for name, trusted_root in (
            ("state", self.store.root),
            ("artifact", self.store.artifact_root),
            ("integration", self.config.operator.experiment_lines.root),
            ("checkpoint", self.config.operator.experiment_lines.checkpoint_root),
            ("active experiment release", self.config.operator.experiment_lines.active_release_root),
        ):
            try:
                trusted_root.resolve().relative_to(candidate_root)
            except ValueError:
                continue
            raise OperatorGovernanceError(
                f"configured Operator {name} root must be outside the candidate worktree"
            )
        try:
            self.store.read_workspace(request_id)
        except OperatorStoreError:
            self.store.write_workspace(request_id, workspace)
        experiment = {
            "experiment_id": self.store.next_id("experiment"),
            "request_id": request_id,
            "status": "CREATED",
            "profile": self.config.operator.experiments.default_profile,
            "trusted_base_sha": request.base_sha,
            "candidate_branch": request.branch,
            "candidate_worktree": workspace["path"],
            "shadow_state_root": str(self.store.root / "experiments" / request_id),
            "scope_version": scope_version,
            "created_at": utc_now(),
        }
        existing_experiments = self.store.read_experiments(request_id)
        if existing_experiments:
            experiment = existing_experiments[-1]
        else:
            self.store.write_experiment(experiment)
        self.store.append_event(
            request_id,
            "request_activated",
            self._actor(actor),
            {
                "workspace_path": workspace["path"],
                "branch": workspace["branch"],
                "scope_version": scope_version,
                "experiment_id": experiment["experiment_id"],
            },
        )
        return {"request": request.to_dict(), "workspace": workspace, "experiment": experiment}

    def create_workspace(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        return self.start(request_id, actor=actor)["workspace"]

    def _experiment_profile(
        self,
        profile: str | None,
        values: Mapping[str, str] | None,
    ) -> tuple[str, Mapping[str, Any], dict[str, str]]:
        profile_name = profile or self.config.operator.experiments.default_profile
        try:
            profile_data = self.config.operator.experiments.profiles[profile_name]
        except KeyError as exc:
            raise OperatorGovernanceError(
                f"unknown Operator experiment profile: {profile_name}"
            ) from exc
        commands = profile_data.get("commands") or []
        if not isinstance(commands, list) or not commands:
            raise OperatorGovernanceError(f"experiment profile has no commands: {profile_name}")
        try:
            supplied = portable_profile_values(
                {str(key): str(value) for key, value in (values or {}).items()}
            )
        except OperatorMetricsError as exc:
            raise OperatorGovernanceError(str(exc)) from exc
        missing = [
            str(name)
            for name in profile_data.get("required_values") or []
            if str(name) not in supplied
        ]
        if missing:
            raise OperatorGovernanceError(
                f"experiment profile {profile_name} requires values: {', '.join(missing)}"
            )
        return profile_name, profile_data, supplied

    def capture_baseline(
        self,
        name: str,
        *,
        profile: str | None = None,
        values: Mapping[str, str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if not self.config.operator.experiments.enabled:
            raise OperatorGovernanceError("Operator experiments are disabled")
        profile_name, profile_data, supplied = self._experiment_profile(profile, values)
        trusted_ref = self.config.operator.experiments.trusted_ref
        base_sha = rev_parse(self.project_root, trusted_ref)
        baseline_id = self.store.next_id("baseline")
        workspace = (
            self.config.operator.worktrees.root / ".baselines" / baseline_id / "candidate"
        ).resolve()
        shadow_root = (self.store.root / "baselines" / baseline_id / "shadow").resolve()
        log_root = (self.store.artifact_root / "baselines" / baseline_id).resolve()
        shadow_root.mkdir(parents=True, exist_ok=False)
        results: list[dict[str, Any]] = []
        try:
            workspace.parent.mkdir(parents=True, exist_ok=True)
            run_git(
                self.project_root,
                ["worktree", "add", "--detach", str(workspace), base_sha],
                check=True,
            )
            snapshot = collect_candidate_snapshot(
                workspace,
                base_sha,
                [str(item) for item in self.policy().data.get("governance_metadata_paths") or []],
            )
            command_values = {
                "request_id": f"baseline-{name}",
                "base_sha": base_sha,
                "head_sha": snapshot.head_sha,
                "candidate_root": str(workspace),
                "shadow_state_root": str(shadow_root),
                **supplied,
            }
            results = run_command_specs(
                workspace,
                log_root,
                list(profile_data.get("commands") or []),
                values=command_values,
                default_timeout_seconds=int(profile_data.get("timeout_seconds", 1800)),
                name_prefix=profile_name,
                process_sandbox=self.config.operator.verification.process_sandbox,
                require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                network_access=bool(profile_data.get("network_access", False)),
                hidden_roots=(self.store.root, self.store.artifact_root),
                writable_roots=(shadow_root,),
                read_only_binds=self._runtime_binds(workspace),
            )
            failed = [
                str(item.get("name") or "command")
                for item in results
                if not item.get("passed")
            ]
            if not results or failed:
                detail = ", ".join(failed) if failed else "no commands executed"
                raise OperatorGovernanceError(
                    "trusted baseline profile did not pass: " + detail
                )
            receipt = derive_metric_receipt(
                source="trusted_baseline_experiment",
                profile=profile_name,
                values=supplied,
                base_sha=base_sha,
                head_sha=snapshot.head_sha,
                patch_digest=snapshot.patch_digest,
                command_results=results,
                profile_contract=profile_data,
            )
            path = record_baseline(
                self.project_root,
                name,
                receipt,
                profile_values=supplied,
                notes=notes,
            )
            return {"path": str(path), "baseline": read_baseline(self.project_root, name)}
        except Exception as exc:
            if isinstance(exc, OperatorGovernanceError):
                raise
            raise OperatorGovernanceError(f"trusted baseline capture failed: {exc}") from exc
        finally:
            if workspace.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(workspace)],
                    check=False,
                )

    def compare_experiment_baseline(self, request_id: str, name: str) -> dict[str, Any]:
        request, _ = self._effective_request(request_id)
        snapshot = self._snapshot(request_id, request)
        baseline = baseline_for_request(self.project_root, name, request.base_sha)
        matching = [
            item.get("metric_receipt")
            for item in self.store.read_experiments(request_id)
            if item.get("status") == "COMPLETED"
            and item.get("candidate_head_sha") == snapshot.head_sha
            and item.get("patch_digest") == snapshot.patch_digest
            and item.get("profile") == baseline["profile"]
            and (item.get("metric_receipt") or {}).get("input_digest")
            == baseline["input_digest"]
        ]
        if not matching:
            raise OperatorGovernanceError(
                "missing completed trusted experiment for the current candidate patch and baseline profile"
            )
        return compare_baseline(
            self.project_root,
            name,
            matching[-1],
            self.policy().data.get("metrics") or {},
            request_base_sha=request.base_sha,
        )

    @_leased_request
    def run_experiment(
        self,
        request_id: str,
        *,
        profile: str | None = None,
        values: Mapping[str, str] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.operator.experiments.enabled:
            raise OperatorGovernanceError("Operator experiments are disabled")
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "ACTIVE", "VERIFIED")
        self.guard.require_no_active_run(projection.active_writer_run_id, projection.active_check_run_id)
        request, scope_version = self._effective_request(request_id)
        workspace = Path(self.store.read_workspace(request_id)["path"])
        if run_git(workspace, ["status", "--porcelain"], check=True).stdout.strip():
            raise OperatorGovernanceError("shadow experiments require a committed and clean candidate")
        snapshot = self._snapshot(request_id, request)
        profile_name, profile_data, supplied = self._experiment_profile(profile, values)
        experiment_id = self.store.next_id("experiment")
        experiment_workspace = (
            self.config.operator.worktrees.root / ".experiments" / experiment_id / "candidate"
        ).resolve()
        shadow_root = (self.store.root / "experiments" / experiment_id / "shadow").resolve()
        shadow_root.mkdir(parents=True, exist_ok=False)
        record: dict[str, Any] = {
            "experiment_id": experiment_id,
            "request_id": request_id,
            "status": "RUNNING",
            "profile": profile_name,
            "trusted_base_sha": request.base_sha,
            "candidate_head_sha": snapshot.head_sha,
            "patch_digest": snapshot.patch_digest,
            "candidate_branch": request.branch,
            "candidate_worktree": str(experiment_workspace),
            "shadow_state_root": str(shadow_root),
            "scope_version": scope_version,
            "profile_values": supplied,
            "created_at": utc_now(),
            "started_at": utc_now(),
        }
        self.store.write_experiment(record)
        self.store.append_event(
            request_id,
            "experiment_started",
            self._actor(actor),
            {"experiment_id": experiment_id, "profile": profile_name},
        )
        results: list[dict[str, Any]] = []
        failure: str | None = None
        try:
            experiment_workspace.parent.mkdir(parents=True, exist_ok=True)
            run_git(
                self.project_root,
                ["worktree", "add", "--detach", str(experiment_workspace), snapshot.head_sha],
                check=True,
            )
            command_values = {
                "request_id": request_id,
                "base_sha": request.base_sha,
                "head_sha": snapshot.head_sha,
                "candidate_root": str(experiment_workspace),
                "shadow_state_root": str(shadow_root),
                **supplied,
            }
            results = run_command_specs(
                experiment_workspace,
                self.store.artifact_root / request_id / "experiments" / experiment_id,
                list(profile_data.get("commands") or []),
                values=command_values,
                default_timeout_seconds=int(profile_data.get("timeout_seconds", 1800)),
                name_prefix=profile_name,
                process_sandbox=self.config.operator.verification.process_sandbox,
                require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                network_access=bool(profile_data.get("network_access", False)),
                hidden_roots=(self.store.root, self.store.artifact_root),
                writable_roots=(shadow_root,),
                read_only_binds=self._runtime_binds(experiment_workspace),
            )
            for item in results:
                for stream in ("stdout_path", "stderr_path"):
                    self.store.register_artifact_file(
                        request_id,
                        producer="experiment_harness",
                        trust_class="authoritative",
                        kind="experiment-log",
                        path=Path(item[stream]),
                        patch_digest=snapshot.patch_digest,
                    )
        except Exception as exc:
            failure = str(exc)
        finally:
            if experiment_workspace.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(experiment_workspace)],
                    check=False,
                )
        current = self._snapshot(request_id, request)
        if current.patch_digest != snapshot.patch_digest or current.head_sha != snapshot.head_sha:
            failure = failure or "candidate changed while shadow experiment was running"
        receipt: dict[str, Any] | None = None
        if failure is None:
            try:
                receipt = derive_metric_receipt(
                    source="operator_candidate_experiment",
                    profile=profile_name,
                    values=supplied,
                    base_sha=request.base_sha,
                    head_sha=snapshot.head_sha,
                    patch_digest=snapshot.patch_digest,
                    command_results=results,
                    profile_contract=profile_data,
                )
            except OperatorMetricsError as exc:
                failure = str(exc)
        passed = bool(results) and all(bool(item["passed"]) for item in results)
        record.update(
            {
                "status": "FAILED" if failure else "COMPLETED",
                "finished_at": utc_now(),
                "failure": failure,
                "passed": passed,
                "command_results": results,
                "metric_receipt": receipt,
            }
        )
        self.store.update_experiment(record)
        self.store.append_event(
            request_id,
            "experiment_failed" if failure else "experiment_completed",
            "trusted-host",
            {"experiment_id": experiment_id, "failure": failure, "passed": passed},
        )
        return record

    def _snapshot(self, request_id: str, request: OperatorRequest | None = None):
        effective = request or self._effective_request(request_id)[0]
        workspace = self.store.read_workspace(request_id)
        constitution = self.policy().data
        return collect_candidate_snapshot(
            Path(workspace["path"]),
            effective.base_sha,
            [str(item) for item in constitution.get("governance_metadata_paths") or []],
        )

    def _evidence_view(self, request: OperatorRequest) -> list[dict[str, Any]]:
        if request.experiment_line_id:
            line = self.store.read_experiment_line(request.experiment_line_id)
            study = self.store.read_study(line.study_id)
            records = self._validated_study_evidence(
                study,
                request.evidence,
                expected_subject_sha=request.base_sha,
            )
            return [
                {
                    "reference": self.study_evidence_reference(record),
                    "source_kind": record.source_kind,
                    "source_record_digest": record.source_record_digest,
                    "content": yaml.safe_dump(report, sort_keys=False),
                }
                for record, report in records
            ]
        values: list[dict[str, Any]] = []
        for reference in request.evidence:
            item: dict[str, Any] = {"reference": reference}
            if not reference.startswith(("sha256:", "uri:", "note:")):
                path = Path(reference)
                if not path.is_absolute():
                    path = self.project_root / path
                if path.is_file() and path.stat().st_size <= 65536:
                    try:
                        item["content"] = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        item["content"] = "<binary evidence>"
            values.append(item)
        return values

    def _run_operator_role_backend(
        self,
        request_id: str,
        codex_request: CodexRequest,
        *,
        call_key: str,
        revision: int,
        backend: CodexBackend,
    ) -> CodexResult:
        request, _ = self._effective_request(request_id)
        if not request.experiment_line_id:
            return backend.run(codex_request)
        if not request.budget_grant_id:
            raise OperatorGovernanceError(
                "line-bound Operator role call requires a frozen budget grant"
            )
        line = self.store.read_experiment_line(request.experiment_line_id)
        study = self.store.read_study(line.study_id)
        explicit_request = replace(codex_request, model=study.primary_model)
        metered = self.metered_codex_backend(
            grant_id=request.budget_grant_id,
            call_key=call_key,
            execution_id=request_id,
            revision=revision,
            backend=backend,
        )
        return metered.run(explicit_request)

    def _next_operator_role_revision(self, request_id: str, role: str) -> int:
        request, _ = self._effective_request(request_id)
        if not request.budget_grant_id:
            return 1
        usage = self.store.read_usage_entries(request.budget_grant_id)
        return 1 + sum(
            entry.execution_id == request_id and entry.role == role
            for entry in usage
        )

    def writer_view(self, request_id: str) -> dict[str, Any]:
        request, scope_version = self._effective_request(request_id)
        projection = self.projection(request_id)
        workspace = self.store.read_workspace(request_id)
        checks = self.store.read_check_runs(request_id)
        constitution = self.governance_context()
        latest_check = None
        if checks:
            check = checks[-1]
            latest_check = {
                "check_id": check.check_id,
                "status": check.status,
                "mode": check.mode,
                "patch_digest": check.patch_digest,
                "scope_version": check.scope_version,
                "failures": list(check.failures),
                "commands": [
                    {
                        "name": item.get("name"),
                        "passed": item.get("passed"),
                        "exit_code": item.get("exit_code"),
                        "timed_out": item.get("timed_out"),
                    }
                    for item in check.command_results
                ],
            }
        return {
            "schema": "autobugfix-writer-view-v1",
            "request_id": request_id,
            "phase": projection.state,
            "task": {"summary": request.summary, "triage_id": request.triage_id},
            "evidence": self._evidence_view(request),
            "scope": {
                "version": scope_version,
                "layers": sorted(request.declared_layers),
                "paths": list(request.planned_paths),
                "risk": request.requested_risk,
            },
            "feedback": [item.to_dict() for item in self.store.read_feedback(request_id)],
            "latest_check": latest_check,
            "candidate": {"worktree": workspace["path"], "base_sha": request.base_sha, "branch": request.branch},
            "constitution": {
                "digest": constitution["digest"],
                "project": constitution["project"],
                "loops": constitution["loops"],
                "role": constitution["operator_roles"].get("operator_writer") or {},
            },
            "allowed_cli": ["writer task", "writer context", "writer scope", "writer feedback", "writer check-result"],
        }

    def supervisor_view(self, request_id: str) -> dict[str, Any]:
        request, scope_version = self._effective_request(request_id)
        projection = self.projection(request_id)
        gate = self.store.read_latest_gate(request_id)
        return {
            "schema": "autobugfix-operator-supervisor-view-v1",
            "constitution": self.governance_context(),
            "request": {
                "request_id": request_id,
                "summary": request.summary,
                "evidence": list(request.evidence),
                "scope_version": scope_version,
                "layers": sorted(request.declared_layers),
                "planned_paths": list(request.planned_paths),
                "risk": request.requested_risk,
            },
            "projection": projection.to_dict(),
            "gate": gate.to_dict() if gate else None,
            "feedback": [item.to_dict() for item in self.store.read_feedback(request_id)],
            "artifacts": [
                {
                    "artifact_id": item["artifact_id"],
                    "kind": item["kind"],
                    "producer": item["producer"],
                    "trust_class": item["trust_class"],
                    "patch_digest": item.get("patch_digest"),
                }
                for item in self.store.read_artifacts(request_id)
            ],
        }

    @_leased_request
    def run_supervisor(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "REQUESTED", "ACTIVE", "VERIFIED")
        self.guard.require_no_active_run(projection.active_writer_run_id, projection.active_check_run_id)
        role = resolve_role(self.config, "operator_supervisor")
        self._validate_operator_role("operator_supervisor", role)
        view = self.supervisor_view(request_id)
        diagnosis_id = self.store.next_id("diagnosis")
        root = self.store.artifact_root / request_id / "supervisor" / diagnosis_id
        root.mkdir(parents=True, exist_ok=False)
        raw_log = root / "raw.jsonl"
        stderr_log = root / "stderr.log"
        codex_request = build_codex_request(
            self.project_root,
            "operator_supervisor",
            supervisor_prompt(view),
            self.project_root,
            None,
            None,
            None,
            raw_log,
            stderr_log,
            resolved_role=role,
        )
        result = self._run_operator_role_backend(
            request_id,
            codex_request,
            call_key=f"{request_id}:supervisor:{diagnosis_id}",
            revision=self._next_operator_role_revision(
                request_id,
                "operator_supervisor",
            ),
            backend=self.backend,
        )
        artifact = self.store.write_artifact(
            request_id,
            producer="operator_supervisor",
            trust_class="advisory",
            kind="operator-diagnosis",
            content=result.text,
            filename="diagnosis.yaml",
            patch_digest=projection.patch_digest,
        )
        for kind, path in (("supervisor-raw", raw_log), ("supervisor-stderr", stderr_log)):
            if path.is_file():
                self.store.register_artifact_file(
                    request_id,
                    producer="codex_host",
                    trust_class="host_observed",
                    kind=kind,
                    path=path,
                    patch_digest=projection.patch_digest,
                )
        self.store.append_event(
            request_id,
            "supervisor_diagnosed",
            self._actor(actor),
            {"diagnosis_id": diagnosis_id, "artifact_id": artifact.artifact_id},
        )
        return {
            "diagnosis_id": diagnosis_id,
            "request_id": request_id,
            "phase": projection.state,
            "recommendation": result.text,
            "artifact": artifact.to_dict(),
        }

    def _writer_log_paths(self, request_id: str, run_id: str) -> tuple[Path, Path]:
        root = self.store.artifact_root / request_id / "writer-runs" / run_id
        root.mkdir(parents=True, exist_ok=False)
        return root / "raw.jsonl", root / "stderr.log"

    def _start_isolated_writer_worker(
        self,
        request: CodexRequest,
        worker_environment: dict[str, str],
        codex_home: Path,
    ) -> tuple[
        subprocess.Popen[str],
        Any,
        Any,
        CodexRequest,
        Path,
        Path,
        Path,
        Path,
    ]:
        private_root = codex_home / "operator-io"
        private_root.mkdir(mode=0o700, exist_ok=False)
        isolated_request = replace(
            request,
            raw_log_path=private_root / "raw.jsonl",
            stderr_log_path=private_root / "stderr.log",
        )
        request_path = private_root / "sdk-request.json"
        result_path = private_root / "sdk-result.json"
        write_private_text(
            request_path,
            json.dumps(
                self.backend.worker_request_payload(isolated_request), sort_keys=True
            ),
        )
        worker_stdout = private_root / "worker.stdout.log"
        worker_stderr = private_root / "worker.stderr.log"
        stdout_handle = private_text_writer(worker_stdout)
        stderr_handle = private_text_writer(worker_stderr)
        worker_argv = [
            sys.executable,
            "-m",
            "autobugfix.codex_sdk_worker",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        if self.backend.module_name:
            worker_argv.extend(("--module-name", self.backend.module_name))
        try:
            process = subprocess.Popen(
                self.backend.worker_launch_argv(
                    isolated_request,
                    worker_argv,
                    call_home=codex_home,
                ),
                cwd=self.project_root,
                env=worker_environment,
                text=True,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=os.name != "nt",
            )
        except BaseException:
            stdout_handle.close()
            stderr_handle.close()
            raise
        return (
            process,
            stdout_handle,
            stderr_handle,
            isolated_request,
            request_path,
            result_path,
            worker_stdout,
            worker_stderr,
        )

    def _run_writer_backend(
        self, request_id: str, run_id: str, request: CodexRequest
    ) -> CodexResult:
        if not isinstance(self.backend, CodexSDKBackend):
            return self.backend.run(request)
        root = request.raw_log_path.parent
        worker_environment = self.backend.prepare_worker_environment(request)
        codex_home = Path(worker_environment["CODEX_HOME"]).resolve()
        markers = credential_markers(codex_home / "auth.json", worker_environment)
        workspace_before = snapshot_regular_files(request.cwd.resolve())
        leak_paths: tuple[Path, ...] = ()
        try:
            (
                process,
                stdout_handle,
                stderr_handle,
                isolated_request,
                request_path,
                result_path,
                worker_stdout,
                worker_stderr,
            ) = self._start_isolated_writer_worker(
                request,
                worker_environment,
                codex_home,
            )
        except BaseException:
            self.backend._scrub_worker_credentials(codex_home)
            raise
        started = time.monotonic()

        def terminate() -> None:
            if process.poll() is not None:
                return
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    return
                process.wait(timeout=10)

        stdout = ""
        stderr = ""
        published_result = root / "sdk-result.json"
        try:
            while process.poll() is None:
                if self.store.read_writer_run(run_id).status == "CANCELLED":
                    terminate()
                    raise OperatorGovernanceError("WriterRun was cancelled by the trusted service")
                if request.timeout_seconds is not None and time.monotonic() - started > request.timeout_seconds:
                    terminate()
                    raise TimeoutError(f"WriterRun timed out after {request.timeout_seconds} seconds")
                time.sleep(0.25)
        except BaseException:
            terminate()
            raise
        finally:
            stdout_handle.close()
            stderr_handle.close()
            try:
                stdout = worker_stdout.read_text(encoding="utf-8")
                stderr = worker_stderr.read_text(encoding="utf-8")
                published = {
                    isolated_request.raw_log_path: request.raw_log_path,
                    isolated_request.stderr_log_path: request.stderr_log_path,
                    request_path: root / "sdk-request.json",
                    result_path: published_result,
                    worker_stdout: root / "sdk-worker.stdout.log",
                    worker_stderr: root / "sdk-worker.stderr.log",
                }
                leak_paths = redact_credential_leaks(
                    (request.cwd.resolve(), *published.keys()),
                    markers,
                    baseline=workspace_before,
                )
                for source, destination in published.items():
                    if source.is_file() and not source.is_symlink():
                        write_private_bytes(destination, source.read_bytes())
                if not request.raw_log_path.exists():
                    write_private_text(request.raw_log_path, "")
                if not request.stderr_log_path.exists():
                    write_private_text(request.stderr_log_path, "")
                if leak_paths:
                    with private_text_writer(request.stderr_log_path, "a") as error_log:
                        error_log.write(
                            "credential leakage guard redacted role-controlled output\n"
                        )
            finally:
                self.backend._scrub_worker_credentials(codex_home)
            if leak_paths:
                raise OperatorGovernanceError(
                    "Operator Writer attempted to publish bridged credential material; "
                    "affected files were redacted"
                )
        if process.returncode != 0 or not published_result.is_file():
            raise OperatorGovernanceError(
                f"Codex SDK worker failed with exit {process.returncode}: {stderr.strip()}"
            )
        data = json.loads(published_result.read_text(encoding="utf-8"))
        published_result.chmod(0o600)
        return CodexResult(
            text=str(data["text"]),
            raw=dict(data.get("raw") or {}),
            exit_code=int(data.get("exit_code", 0)),
        )

    def start_writer(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        with self.store.request_lease(request_id):
            projection = self.projection(request_id)
            self.guard.require_phase(projection.state, "ACTIVE")
            self.guard.require_no_active_run(
                projection.active_writer_run_id, projection.active_check_run_id
            )
            request, scope_version = self._effective_request(request_id)
            attempts = self.store.read_writer_runs(request_id)
            if len(attempts) >= self.config.operator.retry.max_attempts:
                raise OperatorGovernanceError("writer attempt budget exhausted")
            role = resolve_role(self.config, "operator_writer")
            self._validate_operator_role("operator_writer", role)
            view = self.writer_view(request_id)
            run_id = self.store.next_id("writer")
            raw_log, stderr_log = self._writer_log_paths(request_id, run_id)
            role_digest = digest_payload(role.to_dict(self.project_root))
            run = WriterRun(
                run_id=run_id,
                request_id=request_id,
                attempt=len(attempts) + 1,
                status="QUEUED",
                role_digest=role_digest,
                scope_version=scope_version,
                input_digest=digest_payload(view),
                base_sha=request.base_sha,
                feedback_ids=tuple(
                    item.feedback_id for item in self.store.read_feedback(request_id)
                ),
            )
            self.store.write_writer_run(run)
            running = replace(run, status="RUNNING", started_at=utc_now())
            self.store.update_writer_run(running)
            self.store.append_event(
                request_id,
                "writer_started",
                self._actor(actor),
                {"run_id": run_id, "attempt": run.attempt},
            )
            workspace = Path(self.store.read_workspace(request_id)["path"])
            codex_request = build_codex_request(
                self.project_root,
                "operator_writer",
                writer_prompt(view),
                workspace,
                None,
                None,
                None,
                raw_log,
                stderr_log,
                resolved_role=role,
                expected_git_common_dir=git_common_dir(self.project_root),
            )
        try:
            result = self._run_operator_role_backend(
                request_id,
                codex_request,
                call_key=f"{request_id}:writer:{run_id}",
                revision=self._next_operator_role_revision(
                    request_id,
                    "operator_writer",
                ),
                backend=CallbackCodexBackend(
                    lambda request: self._run_writer_backend(request_id, run_id, request)
                ),
            )
            with self.store.request_lease(request_id):
                current = self.store.read_writer_run(run_id)
                if current.status == "CANCELLED":
                    for kind, path in (("writer-raw", raw_log), ("writer-stderr", stderr_log)):
                        if path.is_file():
                            self.store.register_artifact_file(
                                request_id,
                                producer="codex_host",
                                trust_class="host_observed",
                                kind=kind,
                                path=path,
                                writer_run_id=run_id,
                            )
                    return current.to_dict()
                snapshot = self._snapshot(request_id, request)
                completed = replace(
                    running,
                    status="COMPLETED",
                    head_sha=snapshot.head_sha,
                    patch_digest=snapshot.patch_digest,
                    finished_at=utc_now(),
                )
                self.store.update_writer_run(completed)
                self.store.write_artifact(
                    request_id,
                    producer="operator_writer",
                    trust_class="advisory",
                    kind="writer-response",
                    content=result.text,
                    filename="response.md",
                    writer_run_id=run_id,
                    patch_digest=snapshot.patch_digest,
                )
                for kind, path in (("writer-raw", raw_log), ("writer-stderr", stderr_log)):
                    if path.is_file():
                        self.store.register_artifact_file(
                            request_id,
                            producer="codex_host",
                            trust_class="host_observed",
                            kind=kind,
                            path=path,
                            writer_run_id=run_id,
                            patch_digest=snapshot.patch_digest,
                        )
                self.store.append_event(
                    request_id,
                    "patch_observed",
                    "trusted-host",
                    {
                        "run_id": run_id,
                        "head_sha": snapshot.head_sha,
                        "patch_digest": snapshot.patch_digest,
                    },
                )
                self.store.append_event(
                    request_id, "writer_completed", "trusted-host", {"run_id": run_id}
                )
                return completed.to_dict()
        except BaseException as exc:
            with self.store.request_lease(request_id):
                current = self.store.read_writer_run(run_id)
                if current.status == "CANCELLED":
                    for kind, path in (("writer-raw", raw_log), ("writer-stderr", stderr_log)):
                        if path.is_file():
                            self.store.register_artifact_file(
                                request_id,
                                producer="codex_host",
                                trust_class="host_observed",
                                kind=kind,
                                path=path,
                                writer_run_id=run_id,
                            )
                    return current.to_dict()
                timed_out = isinstance(exc, TimeoutError)
                failed = replace(
                    running,
                    status="TIMED_OUT" if timed_out else "FAILED",
                    finished_at=utc_now(),
                    error=str(exc),
                )
                self.store.update_writer_run(failed)
                self.store.append_event(
                    request_id,
                    "writer_timed_out" if timed_out else "writer_failed",
                    "trusted-host",
                    {"run_id": run_id, "error": str(exc)},
                )
                feedback = FeedbackPacket(
                    feedback_id=self.store.next_id("feedback"),
                    request_id=request_id,
                    category="writer_failure",
                    summary="The trusted host could not complete the WriterRun.",
                    patch_digest=None,
                    writer_run_id=run_id,
                    failures=(str(exc),),
                    allowed_actions=("retry_writer", "inspect", "abandon"),
                )
                self.store.write_feedback(feedback)
                self.store.append_event(
                    request_id,
                    "feedback_published",
                    "trusted-host",
                    {"feedback_id": feedback.feedback_id},
                )
            if not isinstance(exc, Exception):
                raise
            raise OperatorGovernanceError(f"operator writer failed: {exc}") from exc

    def retry_writer(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        with self.store.request_lease(request_id):
            if not self.store.read_feedback(request_id):
                raise OperatorGovernanceError("writer retry requires trusted feedback")
        return self.start_writer(request_id, actor=actor)

    @_leased_request
    def commit_candidate(
        self,
        request_id: str,
        *,
        message: str,
        include_manifest: bool = True,
        actor: str | None = None,
    ) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "ACTIVE")
        self.guard.require_no_active_run(projection.active_writer_run_id, projection.active_check_run_id)
        request, _ = self._effective_request(request_id)
        workspace = Path(self.store.read_workspace(request_id)["path"])
        before = self._snapshot(request_id, request)
        if not before.changed_files:
            raise OperatorGovernanceError("candidate has no code changes to commit")
        manifest = self.export_bundle(request_id) if include_manifest else None
        run_git(workspace, ["add", "--all"], check=True)
        staged = run_git(workspace, ["diff", "--cached", "--quiet"], check=False)
        if staged.returncode == 0:
            raise OperatorGovernanceError("candidate has no staged changes to commit")
        run_git(workspace, ["commit", "-m", message], check=True)
        after = self._snapshot(request_id, request)
        if after.patch_digest != before.patch_digest:
            raise OperatorGovernanceError("candidate code patch changed while creating the trusted transport commit")
        self.store.append_event(
            request_id,
            "candidate_committed",
            self._actor(actor),
            {
                "head_sha": after.head_sha,
                "patch_digest": after.patch_digest,
                "manifest": str(manifest) if manifest else None,
            },
        )
        self.store.append_event(
            request_id,
            "patch_observed",
            "trusted-host",
            {"head_sha": after.head_sha, "patch_digest": after.patch_digest},
        )
        return {
            "request_id": request_id,
            "head_sha": after.head_sha,
            "patch_digest": after.patch_digest,
            "manifest": str(manifest) if manifest else None,
        }

    @_leased_request
    def cancel_writer(self, request_id: str, *, reason: str, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "ACTIVE")
        if not projection.active_writer_run_id:
            raise OperatorGovernanceError("request has no running WriterRun")
        run = self.store.read_writer_run(projection.active_writer_run_id)
        if run.status not in {"QUEUED", "RUNNING"}:
            raise OperatorGovernanceError(f"writer run cannot be cancelled from {run.status}")
        cancelled = replace(run, status="CANCELLED", finished_at=utc_now(), error=reason)
        self.store.update_writer_run(cancelled)
        self.store.append_event(
            request_id,
            "writer_cancelled",
            self._actor(actor),
            {"run_id": run.run_id, "reason": reason},
        )
        return cancelled.to_dict()

    def _semantic_review(
        self,
        request_id: str,
        check_id: str,
        request: OperatorRequest,
        patch_digest: str,
        diff_text: str,
        command_results: list[dict[str, Any]],
    ) -> tuple[str, str | None]:
        if not self.config.operator.verification.require_semantic_verifier:
            return "SKIPPED", None
        role = resolve_role(self.config, "operator_verifier")
        self._validate_operator_role("operator_verifier", role)
        root = self.store.artifact_root / request_id / "semantic" / check_id
        root.mkdir(parents=True, exist_ok=False)
        raw_log = root / "raw.jsonl"
        stderr_log = root / "stderr.log"
        view = {
            "request": {"summary": request.summary, "layers": sorted(request.declared_layers)},
            "patch_digest": patch_digest,
            "diff": diff_text,
            "deterministic_checks": command_results,
            "project_constitution": self.policy().data.get("project"),
            "loop_contracts": self.policy().data.get("loops"),
        }
        codex_request = build_codex_request(
            self.project_root,
            "operator_verifier",
            semantic_verifier_prompt(view),
            Path(self.store.read_workspace(request_id)["path"]),
            None,
            None,
            None,
            raw_log,
            stderr_log,
            resolved_role=role,
        )
        result = self._run_operator_role_backend(
            request_id,
            codex_request,
            call_key=f"{request_id}:verifier:{check_id}",
            revision=self._next_operator_role_revision(
                request_id,
                "operator_verifier",
            ),
            backend=self.backend,
        )
        decision = parse_evaluator_decision(result.text)
        reference = self.store.write_artifact(
            request_id,
            producer="operator_verifier",
            trust_class="semantic",
            kind="semantic-verdict",
            content=yaml.safe_dump(
                {"decision": decision.decision, "reason": decision.reason, "patch_digest": patch_digest},
                sort_keys=False,
            ),
            filename="verdict.yaml",
            check_run_id=check_id,
            patch_digest=patch_digest,
        )
        return ("PASS" if decision.passed else "FAIL"), reference.artifact_id

    @_leased_request
    def verify(
        self,
        request_id: str,
        *,
        mode: str = "full",
        actor: str | None = None,
    ) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "ACTIVE")
        self.guard.require_no_active_run(projection.active_writer_run_id, projection.active_check_run_id)
        request, scope_version = self._effective_request(request_id)
        workspace = Path(self.store.read_workspace(request_id)["path"])
        policy = self.policy()
        approvals = self.store.read_approvals(request_id)
        decision = evaluate_policy(
            workspace,
            request,
            approvals,
            constitution=policy.data,
            trusted_policy_source=policy.source,
            trusted_policy=policy.trusted,
            phase="postflight",
            allowed_signers=self.allowed_signers,
            scope_version=scope_version,
        )
        if not policy.trusted:
            decision.violations.append("bootstrap policy cannot produce VERIFIED authority")
            decision.allowed = False
        baseline_layers = {
            str(item) for item in policy.data.get("baseline_required_layers") or []
        }
        behavior_change = bool((set(decision.changed_layers) or request.declared_layers) & baseline_layers)
        if mode == "full" and behavior_change and not request.performance_baseline:
            decision.violations.append(
                "behavior-affecting change requires a trusted performance baseline"
            )
            decision.allowed = False
        extra_profiles = (
            self.config.operator.verification.fast_profiles
            if mode == "fast"
            else self.config.operator.verification.full_profiles
        )
        decision.required_profiles = sorted(set(decision.required_profiles) | set(extra_profiles))
        writer_runs = self.store.read_writer_runs(request_id)
        latest_writer = writer_runs[-1] if writer_runs else None
        if mode == "full":
            if latest_writer is None or latest_writer.status != "COMPLETED":
                decision.violations.append("full verification requires a completed trusted WriterRun")
                decision.allowed = False
            elif latest_writer.patch_digest != decision.patch_digest:
                decision.violations.append("candidate patch changed outside the latest completed WriterRun")
                decision.allowed = False
            if run_git(workspace, ["status", "--porcelain"], check=True).stdout.strip():
                decision.violations.append("full verification requires a committed and clean candidate worktree")
                decision.allowed = False
        check_id = self.store.next_id("check")
        run = CheckRun(
            check_id=check_id,
            request_id=request_id,
            writer_run_id=latest_writer.run_id if latest_writer else None,
            status="RUNNING",
            mode=mode,
            base_sha=decision.base_sha,
            head_sha=decision.head_sha,
            patch_digest=decision.patch_digest,
            scope_version=scope_version,
            profile_names=tuple(decision.required_profiles),
            started_at=utc_now(),
        )
        self.store.write_check_run(run)
        self.store.append_event(request_id, "check_started", self._actor(actor), {"check_id": check_id, "mode": mode})
        results: list[dict[str, Any]] = []
        failures = list(decision.violations)
        regression: dict[str, Any] | None = None
        semantic_status = "PENDING"
        semantic_artifact: str | None = None
        if not failures:
            validation_workspace = workspace
            temporary_workspace: Path | None = None
            try:
                if mode == "full":
                    temporary_workspace = (
                        self.config.operator.worktrees.root / ".verification" / check_id
                    ).resolve()
                    temporary_workspace.parent.mkdir(parents=True, exist_ok=True)
                    run_git(
                        self.project_root,
                        ["worktree", "add", "--detach", str(temporary_workspace), decision.head_sha],
                        check=True,
                    )
                    validation_workspace = temporary_workspace
                results = run_validation_profiles(
                    validation_workspace,
                    self.project_root,
                    request_id,
                    check_id,
                    decision,
                    policy.data,
                    log_root_override=self.store.artifact_root / request_id / "checks" / check_id,
                    process_sandbox=self.config.operator.verification.process_sandbox,
                    require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                    network_access=self.config.operator.verification.network_access,
                    hidden_roots=(self.store.root, self.store.artifact_root),
                    read_only_binds=self._runtime_binds(validation_workspace),
                )
                failures.extend(
                    f"validation command failed: {item['name']} (exit {item['exit_code']})"
                    for item in results
                    if not item["passed"]
                )
                for item in results:
                    for stream in ("stdout_path", "stderr_path"):
                        path = Path(item[stream])
                        self.store.register_artifact_file(
                            request_id,
                            producer="deterministic_verifier",
                            trust_class="authoritative",
                            kind="check-log",
                            path=path,
                            check_run_id=check_id,
                            patch_digest=decision.patch_digest,
                        )
            except Exception as exc:
                failures.append(f"trusted validation harness error: {exc}")
            finally:
                if temporary_workspace is not None and temporary_workspace.exists():
                    run_git(
                        self.project_root,
                        ["worktree", "remove", "--force", str(temporary_workspace)],
                        check=False,
                    )
            observed_after = self._snapshot(request_id, request)
            if (
                observed_after.patch_digest != decision.patch_digest
                or observed_after.head_sha != decision.head_sha
            ):
                failures.append("candidate changed while trusted verification was running")
        if not failures and mode == "full" and request.performance_baseline:
            try:
                baseline = baseline_for_request(
                    self.project_root,
                    request.performance_baseline,
                    request.base_sha,
                )
                matching_receipts = [
                    item.get("metric_receipt")
                    for item in self.store.read_experiments(request_id)
                    if item.get("status") == "COMPLETED"
                    and item.get("candidate_head_sha") == decision.head_sha
                    and item.get("patch_digest") == decision.patch_digest
                    and item.get("profile") == baseline["profile"]
                    and (item.get("metric_receipt") or {}).get("input_digest")
                    == baseline["input_digest"]
                ]
                if not matching_receipts:
                    raise OperatorMetricsError(
                        "missing completed trusted experiment for the current candidate patch and baseline profile"
                    )
                regression = compare_baseline(
                    self.project_root,
                    request.performance_baseline,
                    matching_receipts[-1],
                    policy.data.get("metrics") or {},
                    request_base_sha=request.base_sha,
                )
            except OperatorMetricsError as exc:
                regression = {"ok": False, "failures": [str(exc)], "comparisons": {}}
            if not regression["ok"]:
                failures.extend(str(item) for item in regression["failures"])
        diff_text = (
            run_git(
                workspace,
                ["diff", "--binary", request.base_sha, "--", *decision.changed_files],
                check=True,
            ).stdout
            if decision.changed_files
            else ""
        )
        self.store.write_artifact(
            request_id,
            producer="git_observer",
            trust_class="authoritative",
            kind="candidate-diff",
            content=diff_text,
            filename="candidate.diff",
            check_run_id=check_id,
            patch_digest=decision.patch_digest,
        )
        if not failures and mode == "full":
            try:
                semantic_status, semantic_artifact = self._semantic_review(
                    request_id, check_id, request, decision.patch_digest, diff_text, results
                )
                if semantic_status == "FAIL":
                    failures.append("read-only semantic verifier requested changes")
            except Exception as exc:
                semantic_status = "FAIL"
                failures.append(f"semantic verifier failed closed: {exc}")
        elif mode == "fast":
            semantic_status = "SKIPPED"
        final_status = "PASSED" if not failures else "FAILED"
        completed = replace(
            run,
            status=final_status,
            command_results=tuple(results),
            failures=tuple(failures),
            semantic_verdict_id=semantic_artifact,
            finished_at=utc_now(),
        )
        self.store.update_check_run(completed)
        gate = GateSnapshot(
            request_id=request_id,
            patch_digest=decision.patch_digest,
            scope_version=scope_version,
            scope="PASS" if not decision.violations else "FAIL",
            tests="PASS" if results and all(item["passed"] for item in results) else "FAIL",
            semantic=semantic_status,
            approval="PASS" if decision.allowed else "FAIL",
            merge="PENDING",
            check_run_id=check_id,
        )
        self.store.write_gate(gate)
        blocked_by = [
            name
            for name, value in (("scope", gate.scope), ("tests", gate.tests), ("semantic", gate.semantic), ("approval", gate.approval))
            if value == "FAIL"
        ]
        self.store.append_event(
            request_id,
            "check_passed" if not failures else "check_failed",
            "trusted-host",
            {"check_id": check_id, "failures": failures, "patch_digest": decision.patch_digest},
        )
        self.store.append_event(request_id, "gate_updated", "trusted-host", {"blocked_by": blocked_by})
        feedback: FeedbackPacket | None = None
        if failures:
            feedback = FeedbackPacket(
                feedback_id=self.store.next_id("feedback"),
                request_id=request_id,
                category="verification_failure",
                summary="Candidate did not satisfy the trusted verification contract.",
                patch_digest=decision.patch_digest,
                writer_run_id=completed.writer_run_id,
                check_run_id=check_id,
                failures=tuple(failures),
                allowed_actions=self.guard.feedback_actions(failures),
            )
            self.store.write_feedback(feedback)
            self.store.append_event(
                request_id, "feedback_published", "trusted-host", {"feedback_id": feedback.feedback_id}
            )
        elif mode == "full":
            self.store.append_event(
                request_id,
                "verification_accepted",
                "trusted-host",
                {"check_id": check_id, "patch_digest": decision.patch_digest, "head_sha": decision.head_sha},
            )
        report = {
            "check_run": completed.to_dict(),
            "gate": gate.to_dict(),
            "policy": decision.to_dict(),
            "regression": regression,
            "feedback": feedback.to_dict() if feedback else None,
        }
        self.store.write_validation(request_id, check_id, {"request_id": request_id, **report, "created_at": utc_now()})
        return report

    def postflight(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        return self.verify(request_id, mode="fast", actor=actor)

    def validate(
        self,
        request_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        return self.verify(request_id, mode="full", actor=actor)

    def advance(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        """Perform one policy-approved scheduler step without giving an agent state-write access."""
        projection = self.projection(request_id)
        if projection.state == "REQUESTED":
            return {"action": "start", "result": self.start(request_id, actor=actor)}
        if projection.state in {"VERIFIED", "CLOSED"}:
            return {"action": "blocked", "reason": f"request is {projection.state}", "status": self.status(request_id)}
        self.guard.require_no_active_run(projection.active_writer_run_id, projection.active_check_run_id)
        writer_runs = self.store.read_writer_runs(request_id)
        checks = self.store.read_check_runs(request_id)
        feedback = self.store.read_feedback(request_id)
        if not writer_runs:
            return {"action": "writer_start", "result": self.start_writer(request_id, actor=actor)}
        latest_writer = writer_runs[-1]
        if latest_writer.status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
            if len(writer_runs) - 1 >= self.config.operator.retry.max_auto_retries:
                return {"action": "blocked", "reason": "automatic retry budget exhausted", "status": self.status(request_id)}
            return {"action": "writer_retry", "result": self.retry_writer(request_id, actor=actor)}
        if latest_writer.status != "COMPLETED":
            return {"action": "blocked", "reason": f"WriterRun is {latest_writer.status}", "status": self.status(request_id)}
        matching_checks = [item for item in checks if item.patch_digest == latest_writer.patch_digest]
        latest_check = matching_checks[-1] if matching_checks else None
        if latest_check is None:
            return {"action": "verify_fast", "result": self.verify(request_id, mode="fast", actor=actor)}
        if latest_check.status == "FAILED":
            latest_feedback = feedback[-1] if feedback else None
            may_retry = latest_feedback is not None and "retry_writer" in latest_feedback.allowed_actions
            if not self.config.operator.retry.auto_retry_deterministic_failures or not may_retry:
                return {"action": "blocked", "reason": "verification requires Operator intervention", "status": self.status(request_id)}
            if len(writer_runs) - 1 >= self.config.operator.retry.max_auto_retries:
                return {"action": "blocked", "reason": "automatic retry budget exhausted", "status": self.status(request_id)}
            return {"action": "writer_retry", "result": self.retry_writer(request_id, actor=actor)}
        workspace = Path(self.store.read_workspace(request_id)["path"])
        if latest_check.mode == "fast" and run_git(workspace, ["status", "--porcelain"], check=True).stdout.strip():
            return {
                "action": "candidate_commit",
                "result": self.commit_candidate(
                    request_id,
                    message=f"Operator candidate {request_id}",
                    include_manifest=True,
                    actor=actor,
                ),
            }
        if latest_check.mode == "fast":
            request, _ = self._effective_request(request_id)
            if request.performance_baseline:
                baseline = baseline_for_request(
                    self.project_root,
                    request.performance_baseline,
                    request.base_sha,
                )
                snapshot = self._snapshot(request_id, request)
                matching = [
                    item
                    for item in self.store.read_experiments(request_id)
                    if item.get("status") == "COMPLETED"
                    and item.get("candidate_head_sha") == snapshot.head_sha
                    and item.get("patch_digest") == snapshot.patch_digest
                    and item.get("profile") == baseline["profile"]
                    and (item.get("metric_receipt") or {}).get("input_digest")
                    == baseline["input_digest"]
                ]
                if not matching:
                    return {
                        "action": "experiment_run",
                        "result": self.run_experiment(
                            request_id,
                            profile=str(baseline["profile"]),
                            values={
                                str(key): str(value)
                                for key, value in (baseline.get("profile_values") or {}).items()
                            },
                            actor=actor,
                        ),
                    }
            return {"action": "verify_full", "result": self.verify(request_id, mode="full", actor=actor)}
        return {"action": "blocked", "reason": "no legal automatic transition", "status": self.status(request_id)}

    @_leased_request
    def request_scope_change(
        self,
        request_id: str,
        *,
        add_layers: Iterable[str] = (),
        add_paths: Iterable[str] = (),
        requested_risk: str = "low",
        reason: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "REQUESTED", "ACTIVE", "VERIFIED")
        request, current_version = self._effective_request(request_id)
        new_layers = tuple(dict.fromkeys(str(item) for item in add_layers))
        new_paths = tuple(dict.fromkeys(str(item) for item in add_paths))
        if not new_layers and not new_paths:
            raise OperatorGovernanceError("scope change must add a layer or path")
        if new_layers and not new_paths:
            raise OperatorGovernanceError("adding a scope layer requires at least one planned path")
        if new_layers:
            constitution = self.policy().data
            missing_layer_paths = [
                layer
                for layer in new_layers
                if not any(layer in layers_for_file(constitution, path) for path in new_paths)
            ]
            if missing_layer_paths:
                raise OperatorGovernanceError(
                    "scope expansion has no matching planned path for layer(s): "
                    + ", ".join(missing_layer_paths)
                )
        layers = tuple(
            dict.fromkeys(
                [request.primary_layer, *request.secondary_layers, *new_layers]
            )
        )
        paths = tuple(dict.fromkeys([*request.planned_paths, *new_paths]))
        revision = ScopeRevision(
            revision_id=self.store.next_id("scope"),
            request_id=request_id,
            version=current_version + 1,
            status="PROPOSED",
            layers=layers,
            paths=paths,
            requested_risk=max_risk(request.requested_risk, requested_risk),
            reason=reason,
            creator=self._actor(actor),
        )
        self.store.write_scope_revision(revision)
        synthetic = replace(
            request,
            primary_layer=layers[0],
            secondary_layers=tuple(layers[1:]),
            planned_paths=paths,
            requested_risk=revision.requested_risk,
        )
        authority = check_scope_authority(
            synthetic,
            self.store.read_approvals(request_id),
            self.policy().data,
            allowed_signers=self.allowed_signers,
            scope_version=revision.version,
        )
        if authority.allowed:
            revision = replace(revision, status="APPROVED")
            self.store.update_scope_revision(revision)
            self.store.append_event(
                request_id,
                "scope_revision_approved",
                "trusted-guard",
                {"revision_id": revision.revision_id, "version": revision.version},
            )
            if projection.state == "VERIFIED":
                self.store.append_event(
                    request_id, "request_reopened", "trusted-guard", {"reason": "scope_changed"}
                )
        else:
            self.store.append_event(
                request_id,
                "scope_revision_proposed",
                self._actor(actor),
                {"revision_id": revision.revision_id, "version": revision.version, "violations": list(authority.violations)},
            )
        return {"revision": revision.to_dict(), "authority": authority.to_dict()}

    @_leased_request
    def activate_scope_revision(self, request_id: str, revision_id: str, *, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        revisions = self.store.read_scope_revisions(request_id)
        try:
            revision = next(item for item in revisions if item.revision_id == revision_id)
        except StopIteration as exc:
            raise OperatorGovernanceError(f"unknown scope revision: {revision_id}") from exc
        if revision.status != "PROPOSED":
            raise OperatorGovernanceError(f"scope revision is not pending: {revision.status}")
        synthetic = self._request_for_revision(request_id, revision)
        authority = check_scope_authority(
            synthetic,
            self.store.read_approvals(request_id),
            self.policy().data,
            allowed_signers=self.allowed_signers,
            scope_version=revision.version,
        )
        if not authority.allowed:
            raise OperatorGovernanceError(f"scope revision authority missing: {authority.violations}")
        approved = replace(
            revision,
            status="APPROVED",
            approval_ids=tuple(item.approval_id for item in self.store.read_approvals(request_id)),
        )
        self.store.update_scope_revision(approved)
        self.store.append_event(
            request_id,
            "scope_revision_approved",
            self._actor(actor),
            {"revision_id": revision_id, "version": revision.version},
        )
        if projection.state == "VERIFIED":
            self.store.append_event(request_id, "request_reopened", "trusted-guard", {"reason": "scope_changed"})
        return {"revision": approved.to_dict(), "authority": authority.to_dict()}

    @_leased_request
    def reopen(self, request_id: str, *, reason: str, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "VERIFIED")
        self.store.append_event(request_id, "request_reopened", self._actor(actor), {"reason": reason})
        return self.status(request_id)

    @_leased_request
    def close(self, request_id: str, *, outcome: str, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "REQUESTED", "ACTIVE", "VERIFIED")
        self.guard.require_no_active_run(
            projection.active_writer_run_id, projection.active_check_run_id
        )
        self.store.append_event(request_id, "request_closed", self._actor(actor), {"outcome": outcome})
        return self.status(request_id)

    @_leased_request
    def prepare_promotion(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        self.guard.require_phase(projection.state, "VERIFIED")
        request, _ = self._effective_request(request_id)
        snapshot = self._snapshot(request_id, request)
        if snapshot.patch_digest != projection.patch_digest or snapshot.head_sha != projection.head_sha:
            self.store.append_event(request_id, "request_reopened", "trusted-guard", {"reason": "patch_changed"})
            raise OperatorGovernanceError("candidate changed after verification; request returned to ACTIVE")
        if run_git(Path(self.store.read_workspace(request_id)["path"]), ["status", "--porcelain"], check=True).stdout.strip():
            raise OperatorGovernanceError("promotion requires a committed and clean candidate worktree")
        checks = [
            item
            for item in self.store.read_check_runs(request_id)
            if item.status == "PASSED" and item.mode == "full"
        ]
        if (
            not checks
            or checks[-1].patch_digest != snapshot.patch_digest
            or checks[-1].head_sha != snapshot.head_sha
        ):
            raise OperatorGovernanceError("promotion requires a current passing full CheckRun")
        policy_digest = digest_payload(self.policy().data)
        active_link = self.config.operator.promotion.active_release_link
        previous = os.readlink(active_link) if active_link.is_symlink() else None
        promotion = PromotionRecord(
            promotion_id=self.store.next_id("promotion"),
            request_id=request_id,
            status="PREPARED",
            before_sha=request.base_sha,
            candidate_head_sha=snapshot.head_sha,
            patch_digest=snapshot.patch_digest,
            policy_digest=policy_digest,
            check_run_ids=tuple(item.check_id for item in checks if item.patch_digest == snapshot.patch_digest),
            previous_active_release=previous,
        )
        data = promotion.to_dict()
        self.store.write_promotion(data)
        receipt = self.store.write_artifact(
            request_id,
            producer="promotion_guard",
            trust_class="authoritative",
            kind="promotion-receipt",
            content=yaml.safe_dump(data, sort_keys=False),
            filename="receipt.yaml",
            patch_digest=snapshot.patch_digest,
        )
        self.store.append_event(
            request_id,
            "promotion_prepared",
            self._actor(actor),
            {"promotion_id": promotion.promotion_id, "receipt_id": receipt.artifact_id},
        )
        return {"promotion": data, "receipt": receipt.to_dict()}

    @_leased_promotion
    def open_pull_request(
        self,
        promotion_id: str,
        *,
        title: str,
        body: str,
        base: str = "main",
        push: bool = True,
    ) -> dict[str, Any]:
        data = self.store.read_promotion(promotion_id)
        if data["status"] != "PREPARED":
            raise OperatorGovernanceError("promotion must be PREPARED before opening a PR")
        request, _ = self._effective_request(str(data["request_id"]))
        workspace = Path(self.store.read_workspace(request.request_id)["path"])
        if run_git(workspace, ["status", "--porcelain"], check=True).stdout.strip():
            raise OperatorGovernanceError("candidate worktree must be committed and clean before opening a PR")
        if rev_parse(workspace, "HEAD") != data["candidate_head_sha"]:
            raise OperatorGovernanceError("candidate HEAD changed after promotion preparation")
        manifest_path = f".autobugfix-governance/{request.request_id}/bundle.yaml"
        if run_git(workspace, ["cat-file", "-e", f"HEAD:{manifest_path}"], check=False).returncode != 0:
            raise OperatorGovernanceError("candidate commit is missing its advisory governance manifest")
        marker = f"Autobugfix-Request-Digest: {request.request_digest}"
        if marker not in body:
            raise OperatorGovernanceError(f"pull request body must contain {marker!r}")
        if push:
            run_git(workspace, ["push", "-u", "origin", request.branch], check=True)
        created = subprocess.run(
            ["gh", "pr", "create", "--head", request.branch, "--base", base, "--title", title, "--body", body],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        if created.returncode != 0:
            raise OperatorGovernanceError(f"GitHub PR creation failed: {created.stderr.strip()}")
        viewed = subprocess.run(
            ["gh", "pr", "view", request.branch, "--json", "number,url"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        if viewed.returncode != 0:
            raise OperatorGovernanceError(f"GitHub PR lookup failed: {viewed.stderr.strip()}")
        details = json.loads(viewed.stdout)
        data["status"] = "PR_OPEN"
        data["pull_request"] = int(details["number"])
        data = self.store.update_promotion(data)
        self.store.append_event(
            request.request_id,
            "promotion_pr_opened",
            "github",
            {"promotion_id": promotion_id, "pull_request": data["pull_request"], "url": details.get("url")},
        )
        return data

    @_leased_promotion
    def observe_merge(self, promotion_id: str, *, repository: str) -> dict[str, Any]:
        data = self.store.read_promotion(promotion_id)
        if data["status"] != "PR_OPEN" or not data.get("pull_request"):
            raise OperatorGovernanceError("promotion has no open pull request")
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/pulls/{int(data['pull_request'])}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise OperatorGovernanceError(f"GitHub merge lookup failed: {result.stderr.strip()}")
        pr = json.loads(result.stdout)
        if not pr.get("merged_at") or not pr.get("merge_commit_sha"):
            raise OperatorGovernanceError("pull request is not merged")
        if str((pr.get("head") or {}).get("sha") or "") != data["candidate_head_sha"]:
            raise OperatorGovernanceError("merged pull request head does not match verified candidate")
        request, scope_version = self._effective_request(str(data["request_id"]))
        workspace = Path(self.store.read_workspace(request.request_id)["path"])
        policy = self.policy()
        merge_decision = evaluate_policy(
            workspace,
            request,
            self.store.read_approvals(request.request_id),
            constitution=policy.data,
            trusted_policy_source=policy.source,
            trusted_policy=policy.trusted,
            phase="merge",
            allowed_signers=self.allowed_signers,
            expected_github_repository=repository,
            expected_pull_request=int(data["pull_request"]),
            scope_version=scope_version,
        )
        if not merge_decision.allowed:
            raise OperatorGovernanceError(
                f"merged candidate lacks current merge authority: {merge_decision.violations}"
            )
        data["status"] = "MERGED"
        data["merge_sha"] = str(pr["merge_commit_sha"])
        data["merge_policy_digest"] = digest_payload(merge_decision.to_dict())
        data = self.store.update_promotion(data)
        self.store.append_event(
            str(data["request_id"]),
            "promotion_merged",
            "github",
            {"promotion_id": promotion_id, "merge_sha": data["merge_sha"]},
        )
        return data

    @_leased_promotion
    def run_canary(self, promotion_id: str) -> dict[str, Any]:
        data = self.store.read_promotion(promotion_id)
        if data["status"] != "MERGED" or not data.get("merge_sha"):
            raise OperatorGovernanceError("promotion must be MERGED before canary")
        request = self.store.read_request(str(data["request_id"]))
        policy = self.policy()
        if data.get("policy_digest") != digest_payload(policy.data):
            raise OperatorGovernanceError("trusted constitution changed after promotion preparation")
        merge_sha = str(data["merge_sha"])
        try:
            rev_parse(self.project_root, merge_sha)
        except GitError:
            fetched = run_git(self.project_root, ["fetch", "origin", merge_sha], check=False)
            if fetched.returncode != 0:
                raise OperatorGovernanceError(f"merge commit is not available locally: {merge_sha}")
        canary_path = (
            self.config.operator.worktrees.root / ".canary" / promotion_id
        ).resolve()
        canary_path.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            self.project_root,
            ["worktree", "add", "--detach", str(canary_path), merge_sha],
            check=True,
        )
        data["status"] = "CANARY"
        data = self.store.update_promotion(data)
        results: list[dict[str, Any]] = []
        harness_failure: str | None = None
        try:
            snapshot = collect_candidate_snapshot(
                canary_path,
                request.base_sha,
                [str(item) for item in policy.data.get("governance_metadata_paths") or []],
            )
            decision = PolicyDecision(
                allowed=True,
                request_id=request.request_id,
                trusted_policy_source=policy.source,
                trusted_policy=policy.trusted,
                branch=snapshot.branch,
                base_sha=snapshot.base_sha,
                head_sha=snapshot.head_sha,
                patch_digest=snapshot.patch_digest,
                changed_files=snapshot.changed_files,
                metadata_files=snapshot.metadata_files,
                declared_layers=sorted(request.declared_layers),
                changed_layers={},
                required_profiles=list(self.config.operator.promotion.canary_profiles),
            )
            results = run_validation_profiles(
                canary_path,
                self.project_root,
                request.request_id,
                f"canary-{promotion_id}",
                decision,
                policy.data,
                log_root_override=self.store.artifact_root / request.request_id / "canary" / promotion_id,
                process_sandbox=self.config.operator.verification.process_sandbox,
                require_process_sandbox=self.config.operator.verification.require_process_sandbox,
                network_access=self.config.operator.verification.network_access,
                hidden_roots=(self.store.root, self.store.artifact_root),
                read_only_binds=self._runtime_binds(canary_path),
            )
            for item in results:
                for stream in ("stdout_path", "stderr_path"):
                    self.store.register_artifact_file(
                        request.request_id,
                        producer="canary_harness",
                        trust_class="authoritative",
                        kind="canary-log",
                        path=Path(item[stream]),
                        patch_digest=data["patch_digest"],
                    )
        except Exception as exc:
            harness_failure = str(exc)
        finally:
            if canary_path.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(canary_path)],
                    check=False,
                )
        if harness_failure or any(not item["passed"] for item in results):
            reason = harness_failure or "post-merge canary failed"
            data["status"] = "FAILED"
            data["rollback_reason"] = reason
            data = self.store.update_promotion(data)
            self.store.append_event(
                request.request_id,
                "promotion_canary_failed",
                "trusted-host",
                {"promotion_id": promotion_id, "results": results, "reason": reason},
            )
            if self.config.operator.promotion.auto_rollback_on_canary_failure:
                rollback = self.rollback(promotion_id, reason=reason, actor="trusted-host")
                return {**rollback, "results": results}
            return {"promotion": data, "results": results}
        release_root = self.config.operator.promotion.release_root
        release_path = (release_root / merge_sha).resolve()
        if not release_path.exists():
            release_path.parent.mkdir(parents=True, exist_ok=True)
            run_git(
                self.project_root,
                ["worktree", "add", "--detach", str(release_path), merge_sha],
                check=True,
            )
            for path in [release_path, *release_path.rglob("*")]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
        active = self.config.operator.promotion.active_release_link
        active.parent.mkdir(parents=True, exist_ok=True)
        temporary = active.with_name(f".{active.name}.{promotion_id}.tmp")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(release_path)
        os.replace(temporary, active)
        data["status"] = "ACTIVE"
        data["candidate_release"] = str(release_path)
        data["canary_results"] = results
        data = self.store.update_promotion(data)
        self.store.append_event(
            request.request_id,
            "promotion_activated",
            "trusted-host",
            {"promotion_id": promotion_id, "active_release": str(release_path)},
        )
        self.store.append_event(request.request_id, "request_closed", "trusted-host", {"outcome": "merged"})
        return {"promotion": data, "results": results}

    @_leased_promotion
    def rollback(self, promotion_id: str, *, reason: str, actor: str | None = None) -> dict[str, Any]:
        data = self.store.read_promotion(promotion_id)
        if data["status"] not in {"ACTIVE", "FAILED", "CANARY"}:
            raise OperatorGovernanceError(f"promotion cannot roll back from {data['status']}")
        previous = data.get("previous_active_release")
        active = self.config.operator.promotion.active_release_link
        target: Path | None = None
        if previous:
            target = Path(previous)
            if not target.is_absolute():
                target = (active.parent / target).resolve()
            if not target.exists():
                raise OperatorGovernanceError(f"last-known-good release does not exist: {target}")
            temporary = active.with_name(f".{active.name}.{promotion_id}.rollback")
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            temporary.symlink_to(target)
            os.replace(temporary, active)
        elif data["status"] == "ACTIVE":
            raise OperatorGovernanceError("active promotion has no recorded last-known-good release")
        data["status"] = "ROLLED_BACK"
        data["rollback_reason"] = reason
        data = self.store.update_promotion(data)
        request_id = str(data["request_id"])
        intent = self.store.write_artifact(
            request_id,
            producer="rollback_guard",
            trust_class="authoritative",
            kind="rollback-intent",
            content=yaml.safe_dump(
                {
                    "promotion_id": promotion_id,
                    "merge_sha": data.get("merge_sha"),
                    "restore_release": str(target) if target else None,
                    "reason": reason,
                    "git_strategy": "revert-pr",
                },
                sort_keys=False,
            ),
            filename="rollback.yaml",
        )
        self.store.append_event(
            request_id,
            "promotion_rolled_back",
            self._actor(actor),
            {"promotion_id": promotion_id, "artifact_id": intent.artifact_id, "reason": reason},
        )
        projection = self.projection(request_id)
        if projection.state != "CLOSED":
            self.store.append_event(request_id, "request_closed", "trusted-host", {"outcome": "rolled_back"})
        return {"promotion": data, "rollback_intent": intent.to_dict()}

    @_leased_promotion
    def open_revert_pull_request(
        self,
        promotion_id: str,
        *,
        title: str,
        body: str,
        base: str = "main",
        push: bool = True,
    ) -> dict[str, Any]:
        data = self.store.read_promotion(promotion_id)
        if data["status"] != "ROLLED_BACK" or not data.get("merge_sha"):
            raise OperatorGovernanceError("revert PR requires a rolled-back merged promotion")
        merge_sha = str(data["merge_sha"])
        branch = f"operator/revert/{promotion_id}"
        revert_path = (
            self.config.operator.worktrees.root / ".reverts" / promotion_id
        ).resolve()
        revert_path.parent.mkdir(parents=True, exist_ok=True)
        run_git(self.project_root, ["fetch", "origin", base], check=True)
        run_git(
            self.project_root,
            ["worktree", "add", "-b", branch, str(revert_path), f"origin/{base}"],
            check=True,
        )
        try:
            parents = run_git(
                revert_path, ["rev-list", "--parents", "-n", "1", merge_sha], check=True
            ).stdout.split()
            revert_args = ["revert", "--no-edit"]
            if len(parents) > 2:
                revert_args.extend(["-m", "1"])
            revert_args.append(merge_sha)
            reverted = run_git(revert_path, revert_args, check=False)
            if reverted.returncode != 0:
                run_git(revert_path, ["revert", "--abort"], check=False)
                raise OperatorGovernanceError(
                    f"Git revert requires manual resolution: {reverted.stderr.strip()}"
                )
            if push:
                run_git(revert_path, ["push", "-u", "origin", branch], check=True)
            created = subprocess.run(
                ["gh", "pr", "create", "--head", branch, "--base", base, "--title", title, "--body", body],
                cwd=revert_path,
                text=True,
                capture_output=True,
                check=False,
            )
            if created.returncode != 0:
                raise OperatorGovernanceError(f"revert PR creation failed: {created.stderr.strip()}")
            viewed = subprocess.run(
                ["gh", "pr", "view", branch, "--json", "number,url"],
                cwd=revert_path,
                text=True,
                capture_output=True,
                check=False,
            )
            if viewed.returncode != 0:
                raise OperatorGovernanceError(f"revert PR lookup failed: {viewed.stderr.strip()}")
            details = json.loads(viewed.stdout)
            data["revert_branch"] = branch
            data["revert_pull_request"] = int(details["number"])
            data["revert_url"] = details.get("url")
            data = self.store.update_promotion(data)
            self.store.append_event(
                str(data["request_id"]),
                "promotion_revert_pr_opened",
                "github",
                {
                    "promotion_id": promotion_id,
                    "pull_request": data["revert_pull_request"],
                    "branch": branch,
                },
            )
            return data
        finally:
            if revert_path.exists():
                run_git(
                    self.project_root,
                    ["worktree", "remove", "--force", str(revert_path)],
                    check=False,
                )

    @_leased_request
    def export_bundle(self, request_id: str, *, output_root: Path | None = None) -> Path:
        request = self.store.read_request(request_id)
        projection = self.projection(request_id)
        if projection.state not in {"ACTIVE", "VERIFIED", "CLOSED"}:
            raise OperatorGovernanceError(f"cannot export bundle from phase {projection.state}")
        triage = self.store.read_triage(request.triage_id)
        payload = {
            "schema": "autobugfix-operator-bundle-v3",
            "authority": "advisory_transport_revalidated_by_trusted_base",
            "triage": triage.to_dict(),
            "request": request.to_dict(),
            "events": [item.to_dict() for item in self.store.read_events(request_id)],
            "projection": projection.to_dict(),
            "approvals": [item.to_dict() for item in self.store.read_approvals(request_id)],
            "scope_revisions": [item.to_dict() for item in self.store.read_scope_revisions(request_id)],
            "writer_runs": [item.to_dict() for item in self.store.read_writer_runs(request_id)],
            "check_runs": [item.to_dict() for item in self.store.read_check_runs(request_id)],
            "gate": self.store.read_latest_gate(request_id).to_dict() if self.store.read_latest_gate(request_id) else None,
            "promotions": self.store.read_promotions(request_id),
            "exported_at": utc_now(),
        }
        bundle = {**payload, "bundle_digest": digest_payload(payload)}
        root = output_root or Path(self.store.read_workspace(request_id)["path"])
        path = root.resolve() / ".autobugfix-governance" / request_id / "bundle.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")
        return path

    def finalize(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        return self.prepare_promotion(request_id, actor=actor)

    def revoke(self, request_id: str, *, actor: str | None = None, reason: str) -> dict[str, Any]:
        return self.close(request_id, outcome=f"revoked: {reason}", actor=actor)

    def status(self, request_id: str) -> dict[str, Any]:
        request = self.store.read_request(request_id)
        effective, scope_version = self._effective_request(request_id)
        gate = self.store.read_latest_gate(request_id)
        return {
            "request": request.to_dict(),
            "effective_scope": {
                "version": scope_version,
                "layers": sorted(effective.declared_layers),
                "paths": list(effective.planned_paths),
                "risk": effective.requested_risk,
            },
            "projection": self.projection(request_id).to_dict(),
            "writer_runs": [item.to_dict() for item in self.store.read_writer_runs(request_id)],
            "check_runs": [item.to_dict() for item in self.store.read_check_runs(request_id)],
            "gate": gate.to_dict() if gate else None,
            "feedback": [item.to_dict() for item in self.store.read_feedback(request_id)],
            "approvals": [item.to_dict() for item in self.store.read_approvals(request_id)],
            "scope_revisions": [item.to_dict() for item in self.store.read_scope_revisions(request_id)],
            "experiments": self.store.read_experiments(request_id),
            "promotions": self.store.read_promotions(request_id),
        }

    def audit(self, request_id: str) -> dict[str, Any]:
        request, _ = self._effective_request(request_id)
        projection = self.projection(request_id)
        violations: list[str] = []
        if request.constitution_digest != digest_payload(self.policy().data):
            violations.append("request constitution digest is stale")
        try:
            workspace_data = self.store.read_workspace(request_id)
        except OperatorStoreError:
            workspace_data = None
        if projection.state in {"ACTIVE", "VERIFIED"} and workspace_data is None:
            violations.append("active request is missing its real worktree record")
        if workspace_data is not None:
            workspace = Path(workspace_data["path"]).resolve()
            if run_git(workspace, ["rev-parse", "--is-inside-work-tree"], check=False).stdout.strip() != "true":
                violations.append("workspace record is not a real Git worktree")
            try:
                workspace.relative_to(self.config.operator.worktrees.root.resolve())
            except ValueError:
                violations.append("workspace is outside the configured Operator worktree root")
            for name, trusted_root in (("state", self.store.root), ("artifact", self.store.artifact_root)):
                try:
                    trusted_root.resolve().relative_to(workspace)
                except ValueError:
                    continue
                violations.append(f"trusted {name} root is inside the candidate worktree")
        writer_runs = self.store.read_writer_runs(request_id)
        running_writers = [item.run_id for item in writer_runs if item.status in {"QUEUED", "RUNNING"}]
        if running_writers != ([projection.active_writer_run_id] if projection.active_writer_run_id else []):
            violations.append("WriterRun status does not match the event projection")
        check_runs = self.store.read_check_runs(request_id)
        running_checks = [item.check_id for item in check_runs if item.status in {"PENDING", "RUNNING"}]
        if running_checks != ([projection.active_check_run_id] if projection.active_check_run_id else []):
            violations.append("CheckRun status does not match the event projection")
        for artifact in self.store.read_artifacts(request_id):
            path = Path(str(artifact["path"])).resolve()
            try:
                path.relative_to(self.store.artifact_root.resolve())
            except ValueError:
                violations.append(f"artifact escaped configured root: {artifact['artifact_id']}")
                continue
            if not path.is_file():
                violations.append(f"artifact is missing: {artifact['artifact_id']}")
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                violations.append(f"artifact digest mismatch: {artifact['artifact_id']}")
        self.store.read_promotions(request_id)
        return {
            "request_id": request_id,
            "allowed": not violations,
            "phase": projection.state,
            "event_count": len(self.store.read_events(request_id)),
            "artifact_count": len(self.store.read_artifacts(request_id)),
            "violations": violations,
        }
