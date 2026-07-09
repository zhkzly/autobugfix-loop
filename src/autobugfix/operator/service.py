from __future__ import annotations

import getpass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from autobugfix.git_utils import rev_parse
from autobugfix.operator.approvals import (
    OperatorApprovalError,
    approval_matches,
    approval_signing_payload,
    effective_approvals,
    github_approval,
    signed_approval_from_files,
    verify_external_approval,
    write_signing_payload,
)
from autobugfix.operator.metrics import read_baseline
from autobugfix.operator.models import (
    OperatorApproval,
    OperatorRequest,
    OperatorTriage,
    digest_payload,
    is_expired,
)
from autobugfix.operator.policy import collect_candidate_snapshot, evaluate_policy
from autobugfix.operator.projection import project_request
from autobugfix.operator.store import OperatorStore
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy
from autobugfix.operator.validator import validate_operator_request
from autobugfix.operator.workspace import create_operator_workspace


class OperatorGovernanceError(RuntimeError):
    pass


def _default_expiry(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds").replace("+00:00", "Z")


class OperatorGovernanceService:
    def __init__(
        self,
        project_root: Path | str = ".",
        *,
        trusted_ref: str | None = "origin/main",
        trusted_file: Path | None = None,
        bootstrap_policy: bool = False,
        allowed_signers: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = OperatorStore(self.project_root)
        self.trusted_ref = trusted_ref
        self.trusted_file = trusted_file
        self.bootstrap_policy = bootstrap_policy
        self.allowed_signers = allowed_signers

    def policy(self) -> TrustedPolicy:
        return load_trusted_policy(
            self.project_root,
            trusted_ref=self.trusted_ref,
            trusted_file=self.trusted_file,
            bootstrap=self.bootstrap_policy,
        )

    def _actor(self, actor: str | None) -> str:
        return actor or getpass.getuser()

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
        requested_risk: str = "low",
        validation_profiles: Iterable[str] = (),
        performance_baseline: str | None = None,
        creator: str | None = None,
        request_id: str | None = None,
        branch: str | None = None,
        expires_at: str | None = None,
    ) -> OperatorRequest:
        triage = self.store.read_triage(triage_id)
        actor = self._actor(creator)
        identifier = request_id or self.store.next_id("request")
        profiles = tuple(dict.fromkeys(validation_profiles or (primary_layer,)))
        request = OperatorRequest(
            request_id=identifier,
            summary=summary,
            primary_layer=primary_layer,
            secondary_layers=tuple(secondary_layers),
            requested_risk=requested_risk,
            triage_id=triage.triage_id,
            triage_digest=triage.triage_digest,
            evidence=triage.evidence,
            validation_profiles=profiles,
            performance_baseline=performance_baseline,
            branch=branch or f"operator/{identifier}",
            base_sha=rev_parse(self.project_root, "HEAD"),
            creator=actor,
            expires_at=expires_at or _default_expiry(),
        )
        self.store.write_request(request)
        self.store.append_event(
            request.request_id,
            "request_created",
            actor,
            {"request_digest": request.request_digest, "base_sha": request.base_sha},
        )
        return request

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
    ) -> OperatorApproval:
        request = self.store.read_request(request_id)
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
        allowed_paths: Iterable[str] = (),
        expires_at: str | None = None,
    ) -> Path:
        request = self.store.read_request(request_id)
        patch_digest: str | None = None
        head_sha: str | None = None
        if stage == "merge":
            workspace = self.store.read_workspace(request_id)
            constitution = self.policy().data
            snapshot = collect_candidate_snapshot(
                Path(workspace["path"]),
                request.base_sha,
                [str(item) for item in constitution.get("governance_metadata_paths") or []],
            )
            patch_digest = snapshot.patch_digest
        payload = approval_signing_payload(
            request,
            approver=approver,
            stage=stage,
            reason=reason,
            allowed_paths=allowed_paths,
            expires_at=expires_at or request.expires_at,
            patch_digest=patch_digest,
            head_sha=head_sha,
        )
        return write_signing_payload(output, payload)

    def import_signed_approval(
        self,
        request_id: str,
        *,
        payload_path: Path,
        signature_path: Path,
    ) -> OperatorApproval:
        request = self.store.read_request(request_id)
        approval = signed_approval_from_files(
            request,
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
        approval = github_approval(
            request,
            self.store.next_id("github"),
            repository=repository,
            pull_request=pull_request,
            review_id=review_id,
            constitution=self.policy().data,
            reason=reason,
            stage=stage,
        )
        self.store.write_approval(approval)
        self.store.append_event(
            request_id,
            "approval_recorded",
            approval.approver,
            {"approval_id": approval.approval_id, "kind": approval.kind, "stage": approval.stage},
        )
        return approval

    def preflight(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        request = self.store.read_request(request_id)
        triage = self.store.read_triage(request.triage_id)
        policy = self.policy()
        violations: list[str] = []
        if triage.triage_digest != request.triage_digest:
            violations.append("request triage digest mismatch")
        if is_expired(request.expires_at):
            violations.append("operator request has expired")
        if rev_parse(self.project_root, request.base_sha) != request.base_sha:
            violations.append("operator request base SHA is not canonical")
        protected = {str(item) for item in policy.data.get("protected_branches") or []}
        if request.branch in protected:
            violations.append(f"operator request branch is protected: {request.branch}")

        human_required = request.requested_risk == "constitutional" or not policy.trusted
        review_required = human_required or request.requested_risk in {"medium", "high"} or bool(request.secondary_layers)
        approvals = effective_approvals(self.store.read_approvals(request_id))
        valid: list[OperatorApproval] = []
        for approval in approvals:
            if approval.human_verified_kind:
                try:
                    verify_external_approval(approval, policy.data, allowed_signers=self.allowed_signers)
                except OperatorApprovalError as exc:
                    violations.append(f"invalid external approval {approval.approval_id}: {exc}")
                    continue
            valid.append(approval)
        if review_required and not any(
            approval_matches(
                item,
                request,
                files=(),
                require_human=human_required,
                stage="scope",
            )
            for item in valid
        ):
            violations.append("operator request requires a valid scope approval")

        allowed = not violations
        self.store.append_event(
            request_id,
            "authorized" if allowed else "review_required",
            self._actor(actor),
            {
                "allowed": allowed,
                "human_required": human_required,
                "review_required": review_required,
                "trusted_policy_source": policy.source,
                "trusted_policy": policy.trusted,
                "violations": violations,
            },
        )
        return {
            "allowed": allowed,
            "request_id": request_id,
            "request_digest": request.request_digest,
            "human_required": human_required,
            "review_required": review_required,
            "trusted_policy_source": policy.source,
            "trusted_policy": policy.trusted,
            "violations": violations,
        }

    def create_workspace(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        preflight = self.preflight(request_id, actor=actor)
        if not preflight["allowed"]:
            raise OperatorGovernanceError(f"operator preflight failed: {preflight['violations']}")
        request = self.store.read_request(request_id)
        metadata = create_operator_workspace(self.project_root, request, self.policy().data)
        self.store.write_workspace(request_id, metadata)
        self.store.append_event(request_id, "workspace_created", self._actor(actor), metadata)
        return metadata

    def postflight(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        request = self.store.read_request(request_id)
        workspace = self.store.read_workspace(request_id)
        policy = self.policy()
        approvals = self.store.read_approvals(request_id)
        decision = evaluate_policy(
            Path(workspace["path"]),
            request,
            approvals,
            constitution=policy.data,
            trusted_policy_source=policy.source,
            trusted_policy=policy.trusted,
            phase="postflight",
            allowed_signers=self.allowed_signers,
        )
        if decision.effective_risk in {"medium", "high", "constitutional"} and not request.performance_baseline:
            decision.violations.append("cross-layer or protected change requires a performance baseline")
            decision.allowed = False
        if decision.allowed:
            self.store.append_event(
                request_id,
                "postflight_completed",
                self._actor(actor),
                {
                    "patch_digest": decision.patch_digest,
                    "head_sha": decision.head_sha,
                    "effective_risk": decision.effective_risk,
                },
            )
        return decision.to_dict()

    def validate(
        self,
        request_id: str,
        *,
        current_metrics: Mapping[str, float] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        projection = self.projection(request_id)
        if projection.state not in {"PATCHED", "VALIDATION_FAILED", "VALIDATED"}:
            raise OperatorGovernanceError(f"cannot validate request from state {projection.state}")
        workspace = self.store.read_workspace(request_id)
        self.store.append_event(request_id, "validation_started", self._actor(actor), {})
        report = validate_operator_request(
            self.project_root,
            request_id,
            candidate_root=Path(workspace["path"]),
            trusted_ref=self.trusted_ref,
            trusted_file=self.trusted_file,
            bootstrap_policy=self.bootstrap_policy,
            run_profiles=True,
            current_metrics=current_metrics,
            allowed_signers=self.allowed_signers,
            phase="postflight",
        )
        policy = report["policy"]
        event_payload = {
            "validation_id": report["validation_id"],
            "validation_digest": report["validation_digest"],
            "patch_digest": policy["patch_digest"],
            "head_sha": policy["head_sha"],
            "violations": policy["violations"],
        }
        if policy["allowed"]:
            self.store.append_event(request_id, "validation_passed", self._actor(actor), event_payload)
            if policy["permission_class"] != "constitutional":
                self.store.append_event(request_id, "merge_ready", self._actor(actor), event_payload)
        else:
            self.store.append_event(request_id, "validation_failed", self._actor(actor), event_payload)
        return report

    def finalize(self, request_id: str, *, actor: str | None = None) -> dict[str, Any]:
        projection = self.projection(request_id)
        if projection.state != "VALIDATED" or not projection.validation_id:
            raise OperatorGovernanceError(f"cannot finalize request from state {projection.state}")
        request = self.store.read_request(request_id)
        workspace = self.store.read_workspace(request_id)
        policy = self.policy()
        decision = evaluate_policy(
            Path(workspace["path"]),
            request,
            self.store.read_approvals(request_id),
            constitution=policy.data,
            trusted_policy_source=policy.source,
            trusted_policy=policy.trusted,
            phase="merge",
            allowed_signers=self.allowed_signers,
        )
        validation = self.store.read_validation(request_id, projection.validation_id)
        validated_policy = validation.get("policy") or {}
        if validated_policy.get("patch_digest") != decision.patch_digest:
            decision.violations.append("candidate patch changed after validation")
            decision.allowed = False
        if validated_policy.get("head_sha") != decision.head_sha:
            decision.violations.append("candidate HEAD changed after validation")
            decision.allowed = False
        if not policy.trusted:
            decision.violations.append("bootstrap policy cannot produce merge-ready authority")
            decision.allowed = False
        if decision.allowed:
            self.store.append_event(
                request_id,
                "merge_ready",
                self._actor(actor),
                {
                    "validation_id": projection.validation_id,
                    "patch_digest": decision.patch_digest,
                    "head_sha": decision.head_sha,
                    "violations": [],
                },
            )
        return decision.to_dict()

    def revoke(self, request_id: str, *, actor: str | None = None, reason: str) -> dict[str, Any]:
        self.store.read_request(request_id)
        self.store.append_event(request_id, "revoked", self._actor(actor), {"reason": reason})
        return self.status(request_id)

    def export_bundle(self, request_id: str, *, output_root: Path | None = None) -> Path:
        request = self.store.read_request(request_id)
        triage = self.store.read_triage(request.triage_id)
        projection = self.projection(request_id)
        if projection.state not in {"VALIDATED", "MERGE_READY"} or not projection.validation_id:
            raise OperatorGovernanceError(f"cannot export authorization bundle from state {projection.state}")
        validation = self.store.read_validation(request_id, projection.validation_id)
        validation.pop("record_path", None)
        baseline = read_baseline(self.project_root, request.performance_baseline) if request.performance_baseline else None
        payload = {
            "schema": "autobugfix-operator-bundle-v2",
            "triage": triage.to_dict(),
            "request": request.to_dict(),
            "approvals": [item.to_dict() for item in self.store.read_approvals(request_id)],
            "events": [item.to_dict() for item in self.store.read_events(request_id)],
            "projection": projection.to_dict(),
            "validation": validation,
            "baseline": baseline,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        bundle = {**payload, "bundle_digest": digest_payload(payload)}
        if output_root is None:
            workspace = self.store.read_workspace(request_id)
            output_root = Path(workspace["path"])
        path = output_root.resolve() / ".autobugfix-governance" / request_id / "bundle.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")
        return path

    def projection(self, request_id: str):
        self.store.read_request(request_id)
        return project_request(request_id, self.store.read_events(request_id))

    def status(self, request_id: str) -> dict[str, Any]:
        request = self.store.read_request(request_id)
        return {
            "request": request.to_dict(),
            "projection": self.projection(request_id).to_dict(),
            "approvals": [item.to_dict() for item in self.store.read_approvals(request_id)],
        }
