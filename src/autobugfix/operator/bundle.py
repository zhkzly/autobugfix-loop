from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from autobugfix.config import load_config
from autobugfix.operator.approvals import OperatorApprovalError, verify_external_approval
from autobugfix.operator.guard import effective_request
from autobugfix.operator.metrics import (
    OperatorMetricsError,
    baseline_for_request,
    compare_baseline,
    derive_metric_receipt,
)
from autobugfix.operator.models import (
    OperatorApproval,
    OperatorEvent,
    OperatorRequest,
    OperatorTriage,
    ScopeRevision,
    digest_payload,
)
from autobugfix.operator.policy import evaluate_policy
from autobugfix.operator.projection import OperatorProjectionError, project_request
from autobugfix.operator.trusted import TrustedPolicy
from autobugfix.operator.validator import run_command_specs, run_validation_profiles


class OperatorBundleError(RuntimeError):
    pass


def read_bundle(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or data.get("schema") != "autobugfix-operator-bundle-v3":
        raise OperatorBundleError(f"invalid Operator advisory manifest: {path}")
    stored = data.get("bundle_digest")
    payload = {key: value for key, value in data.items() if key != "bundle_digest"}
    if stored != digest_payload(payload):
        raise OperatorBundleError(f"Operator manifest digest mismatch: {path}")
    return data


def _externally_verifiable_approvals(
    approvals: Iterable[OperatorApproval],
    trusted_policy: TrustedPolicy,
    *,
    allowed_signers: Path | None,
    repository: str | None,
    pull_request: int | None,
) -> tuple[list[OperatorApproval], list[str]]:
    trusted: list[OperatorApproval] = []
    violations: list[str] = []
    for approval in approvals:
        if approval.kind not in {"human_signed", "github"}:
            continue
        try:
            verify_external_approval(
                approval,
                trusted_policy.data,
                allowed_signers=allowed_signers,
                expected_github_repository=repository,
                expected_pull_request=pull_request,
            )
        except OperatorApprovalError as exc:
            violations.append(f"invalid external approval {approval.approval_id}: {exc}")
            continue
        trusted.append(approval)
    return trusted, violations


def validate_bundle(
    bundle_path: Path,
    candidate_root: Path,
    trusted_policy: TrustedPolicy,
    *,
    allowed_signers: Path | None = None,
    run_profiles: bool = True,
    run_experiments: bool = True,
    expected_base_sha: str | None = None,
    expected_github_repository: str | None = None,
    expected_pull_request: int | None = None,
    trusted_baseline_root: Path | None = None,
    extra_approvals: Iterable[OperatorApproval] = (),
    runtime_venv: Path | None = None,
) -> dict[str, Any]:
    """Re-derive merge authority without trusting candidate-authored status claims."""
    bundle = read_bundle(bundle_path)
    triage = OperatorTriage.from_dict(bundle["triage"])
    base_request = OperatorRequest.from_dict(bundle["request"])
    revisions = [ScopeRevision.from_dict(item) for item in bundle.get("scope_revisions") or []]
    request, scope_version = effective_request(base_request, revisions)
    manifest_approvals = [OperatorApproval.from_dict(item) for item in bundle.get("approvals") or []]
    approvals, approval_violations = _externally_verifiable_approvals(
        [*manifest_approvals, *extra_approvals],
        trusted_policy,
        allowed_signers=allowed_signers,
        repository=expected_github_repository,
        pull_request=expected_pull_request,
    )
    violations = list(approval_violations)
    if not trusted_policy.trusted:
        violations.append("remote admission requires a trusted base constitution")
    if expected_base_sha and request.base_sha != expected_base_sha:
        violations.append(
            f"request base SHA {request.base_sha} does not match trusted PR base {expected_base_sha}"
        )
    if triage.triage_id != request.triage_id or triage.triage_digest != request.triage_digest:
        violations.append("manifest triage does not match request")
    if request.constitution_digest != digest_payload(trusted_policy.data):
        violations.append("manifest request is bound to a different machine constitution")
    expected_manifest = (
        candidate_root.resolve()
        / ".autobugfix-governance"
        / request.request_id
        / "bundle.yaml"
    )
    if bundle_path.resolve() != expected_manifest:
        violations.append("manifest path does not match its request id")

    local_claim: dict[str, Any]
    try:
        events = [OperatorEvent.from_dict(item) for item in bundle.get("events") or []]
        projection = project_request(request.request_id, events)
        local_claim = projection.to_dict()
    except (KeyError, ValueError, OperatorProjectionError) as exc:
        local_claim = {"valid": False, "error": str(exc)}

    decision = evaluate_policy(
        candidate_root.resolve(),
        request,
        approvals,
        constitution=trusted_policy.data,
        trusted_policy_source=trusted_policy.source,
        trusted_policy=trusted_policy.trusted,
        phase="merge",
        allowed_signers=allowed_signers,
        expected_github_repository=expected_github_repository,
        expected_pull_request=expected_pull_request,
        scope_version=scope_version,
    )
    violations.extend(decision.violations)
    command_results: list[dict[str, Any]] = []
    experiment_results: list[dict[str, Any]] = []
    metric_receipt: dict[str, Any] | None = None
    regression: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    baseline_layers = {
        str(item) for item in trusted_policy.data.get("baseline_required_layers") or []
    }
    behavior_change = bool(
        (set(decision.changed_layers) or request.declared_layers) & baseline_layers
    )
    authority_root = trusted_baseline_root.resolve() if trusted_baseline_root else None
    if run_profiles and authority_root is None:
        violations.append("remote admission profiles require a trusted base checkout")
    if behavior_change:
        if not request.performance_baseline:
            violations.append("behavior-affecting change requires a trusted performance baseline")
        elif authority_root is None:
            violations.append("remote admission requires a trusted baseline checkout")
        else:
            try:
                baseline = baseline_for_request(
                    authority_root,
                    request.performance_baseline,
                    request.base_sha,
                )
            except OperatorMetricsError as exc:
                violations.append(str(exc))
    if run_profiles and not violations:
        validation_id = f"trusted-pr-{request.request_id}"
        runtime_binds = (
            ((runtime_venv.resolve(), candidate_root.resolve() / ".venv"),)
            if runtime_venv is not None and runtime_venv.is_dir()
            else ()
        )
        log_owner = authority_root or candidate_root.resolve()
        authority_state = log_owner / ".autobugfix/operator-pr" / request.request_id
        authority_hidden_root = log_owner / ".autobugfix"
        try:
            command_results = run_validation_profiles(
                candidate_root.resolve(),
                log_owner,
                request.request_id,
                validation_id,
                decision,
                trusted_policy.data,
                log_root_override=authority_state / "validation",
                hidden_roots=(authority_hidden_root,),
                read_only_binds=runtime_binds,
            )
            if any(not item["passed"] for item in command_results):
                violations.append("one or more trusted-base validation commands failed")
        except Exception as exc:
            violations.append(f"trusted-base validation harness failed: {exc}")

        if not violations and baseline is not None and run_experiments:
            profile = baseline["profile_contract"]
            profile_values = {
                str(key): str(value)
                for key, value in (baseline.get("profile_values") or {}).items()
            }
            shadow_root = authority_state / "experiment-shadow"
            shadow_root.mkdir(parents=True, exist_ok=True)
            try:
                config = load_config(log_owner)
                experiment_results = run_command_specs(
                    candidate_root.resolve(),
                    authority_state / "experiment",
                    list(profile.get("commands") or []),
                    values={
                        "request_id": request.request_id,
                        "base_sha": decision.base_sha,
                        "head_sha": decision.head_sha,
                        "candidate_root": str(candidate_root.resolve()),
                        "shadow_state_root": str(shadow_root),
                        **profile_values,
                    },
                    default_timeout_seconds=int(profile.get("timeout_seconds", 1800)),
                    name_prefix=str(baseline["profile"]),
                    process_sandbox=config.operator.verification.process_sandbox,
                    require_process_sandbox=config.operator.verification.require_process_sandbox,
                    network_access=bool(profile.get("network_access", False)),
                    hidden_roots=(authority_hidden_root,),
                    writable_roots=(shadow_root,),
                    read_only_binds=runtime_binds,
                )
                metric_receipt = derive_metric_receipt(
                    source="trusted_pr_admission_experiment",
                    profile=str(baseline["profile"]),
                    values=profile_values,
                    base_sha=decision.base_sha,
                    head_sha=decision.head_sha,
                    patch_digest=decision.patch_digest,
                    command_results=experiment_results,
                    profile_contract=profile,
                )
                regression = compare_baseline(
                    authority_root,
                    request.performance_baseline,
                    metric_receipt,
                    trusted_policy.data.get("metrics") or {},
                    request_base_sha=request.base_sha,
                )
                if not regression["ok"]:
                    violations.extend(str(item) for item in regression["failures"])
            except Exception as exc:
                violations.append(f"trusted baseline experiment failed: {exc}")
    return {
        "allowed": not violations and decision.allowed and trusted_policy.trusted,
        "manifest": str(bundle_path),
        "manifest_authority": "advisory_only",
        "bundle_digest": bundle["bundle_digest"],
        "request_id": request.request_id,
        "request_digest": request.request_digest,
        "scope_version": scope_version,
        "local_claim": local_claim,
        "policy": decision.to_dict(),
        "trusted_external_approvals": [item.to_dict() for item in approvals],
        "command_results": command_results,
        "experiment_results": experiment_results,
        "experiments_deferred": bool(run_profiles and baseline is not None and not run_experiments),
        "metric_receipt": metric_receipt,
        "regression": regression,
        "violations": violations,
    }
