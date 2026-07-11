from __future__ import annotations

import getpass
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.config import load_config
from autobugfix.evaluator import parse_evaluator_decision
from autobugfix.git_utils import GitError, rev_parse, run_git
from autobugfix.models import utc_now
from autobugfix.models import CodexRequest, CodexResult
from autobugfix.operator.approvals import (
    approval_signing_payload,
    github_approval,
    signed_approval_from_files,
    write_signing_payload,
)
from autobugfix.operator.guard import (
    TransitionGuard,
    TransitionGuardError,
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
from autobugfix.operator.models import (
    CheckRun,
    FeedbackPacket,
    GateSnapshot,
    OperatorApproval,
    OperatorRequest,
    OperatorTriage,
    PromotionRecord,
    ScopeRevision,
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
from autobugfix.operator.store import OperatorStore, OperatorStoreError
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy
from autobugfix.operator.validator import run_command_specs, run_validation_profiles
from autobugfix.operator.workspace import create_operator_workspace, recover_operator_workspace
from autobugfix.role_config import resolve_role


class OperatorGovernanceError(RuntimeError):
    pass


def _default_expiry(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            "schema": "autobugfix-machine-constitution-v3",
            "trusted": policy.trusted,
            "source": policy.source,
            "digest": digest_payload(policy.data),
            "operator_prompt_context": policy.data.get("operator_prompt_context") or "",
            "project": policy.data.get("project") or {},
            "loops": policy.data.get("loops") or {},
            "operator_roles": policy.data.get("operator_roles") or {},
            "hook_assignments": policy.data.get("hook_assignments") or {},
            "transition_contract": policy.data.get("transition_contract") or {},
        }

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
            if value.startswith(("sha256:", "uri:", "note:")):
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
        expires_at: str | None = None,
    ) -> OperatorRequest:
        triage = self.store.read_triage(triage_id)
        policy = self.policy()
        actor = self._actor(creator)
        identifier = request_id or self.store.next_id("request")
        branch_name = branch or self.config.operator.worktrees.branch_template.format(request_id=identifier)
        profiles = tuple(dict.fromkeys(validation_profiles or self.config.operator.verification.fast_profiles))
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
            base_sha=rev_parse(self.project_root, "HEAD"),
            creator=actor,
            constitution_digest=digest_payload(policy.data),
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
        minimums = policy.data.get("operator_runtime_minimums") or {}
        if bool(minimums.get("require_process_sandbox", True)) and not self.config.operator.verification.require_process_sandbox:
            violations.append("project config cannot disable the authoritative process sandbox")
        if not bool(minimums.get("verification_network_access", False)) and self.config.operator.verification.network_access:
            violations.append("project config cannot enable network for authoritative verification")
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
        result = self.backend.run(codex_request)
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

    def _run_writer_backend(
        self, request_id: str, run_id: str, request: CodexRequest
    ) -> CodexResult:
        if not isinstance(self.backend, CodexSDKBackend):
            return self.backend.run(request)
        root = request.raw_log_path.parent
        request_path = root / "sdk-request.json"
        result_path = root / "sdk-result.json"
        request_path.write_text(
            json.dumps(
                {
                    "role": request.role,
                    "prompt": request.prompt,
                    "cwd": str(request.cwd),
                    "sandbox": request.sandbox,
                    "model": request.model,
                    "timeout_seconds": request.timeout_seconds,
                    "developer_instructions": request.developer_instructions,
                    "raw_log_path": str(request.raw_log_path),
                    "stderr_log_path": str(request.stderr_log_path),
                    "approval_mode": request.approval_mode,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        request_path.chmod(0o600)
        worker_stdout = root / "sdk-worker.stdout.log"
        worker_stderr = root / "sdk-worker.stderr.log"
        request.raw_log_path.touch(exist_ok=True)
        request.stderr_log_path.touch(exist_ok=True)
        stdout_handle = worker_stdout.open("w", encoding="utf-8")
        stderr_handle = worker_stderr.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "autobugfix.codex_sdk_worker",
                "--request",
                str(request_path),
                "--result",
                str(result_path),
            ],
            cwd=self.project_root,
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=os.name != "nt",
        )
        started = time.monotonic()

        def terminate() -> None:
            if process.poll() is not None:
                return
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()

        stdout = ""
        stderr = ""
        try:
            while process.poll() is None:
                if self.store.read_writer_run(run_id).status == "CANCELLED":
                    terminate()
                    raise OperatorGovernanceError("WriterRun was cancelled by the trusted service")
                if request.timeout_seconds is not None and time.monotonic() - started > request.timeout_seconds:
                    terminate()
                    raise TimeoutError(f"WriterRun timed out after {request.timeout_seconds} seconds")
                time.sleep(0.25)
        finally:
            stdout_handle.close()
            stderr_handle.close()
            stdout = worker_stdout.read_text(encoding="utf-8")
            stderr = worker_stderr.read_text(encoding="utf-8")
            if stdout:
                with request.raw_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"kind": "sdk_worker_stdout", "text": stdout}) + "\n")
            if stderr:
                with request.stderr_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(stderr)
        if process.returncode != 0 or not result_path.is_file():
            raise OperatorGovernanceError(
                f"Codex SDK worker failed with exit {process.returncode}: {stderr.strip()}"
            )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        result_path.chmod(0o600)
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
            )
        try:
            result = self._run_writer_backend(request_id, run_id, codex_request)
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
        except Exception as exc:
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
        result = self.backend.run(codex_request)
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
