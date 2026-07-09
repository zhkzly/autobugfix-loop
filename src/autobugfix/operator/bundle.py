from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.operator.metrics import compare_baseline
from autobugfix.operator.models import (
    OperatorApproval,
    OperatorEvent,
    OperatorRequest,
    OperatorTriage,
    digest_payload,
)
from autobugfix.operator.policy import evaluate_policy
from autobugfix.operator.projection import project_request
from autobugfix.operator.trusted import TrustedPolicy
from autobugfix.operator.validator import run_validation_profiles


class OperatorBundleError(RuntimeError):
    pass


def read_bundle(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or data.get("schema") != "autobugfix-operator-bundle-v2":
        raise OperatorBundleError(f"invalid operator authorization bundle: {path}")
    stored = data.pop("bundle_digest", None)
    if stored != digest_payload(data):
        raise OperatorBundleError(f"operator bundle digest mismatch: {path}")
    data["bundle_digest"] = stored
    return data


def validate_bundle(
    bundle_path: Path,
    candidate_root: Path,
    trusted_policy: TrustedPolicy,
    *,
    allowed_signers: Path | None = None,
    run_profiles: bool = True,
    expected_base_sha: str | None = None,
    expected_github_repository: str | None = None,
    expected_pull_request: int | None = None,
    trusted_baseline_root: Path | None = None,
) -> dict[str, Any]:
    bundle = read_bundle(bundle_path)
    triage = OperatorTriage.from_dict(bundle["triage"])
    request = OperatorRequest.from_dict(bundle["request"])
    approvals = [OperatorApproval.from_dict(item) for item in bundle.get("approvals") or []]
    events = [OperatorEvent.from_dict(item) for item in bundle.get("events") or []]
    projection = project_request(request.request_id, events)
    violations: list[str] = []
    if expected_base_sha and request.base_sha != expected_base_sha:
        violations.append(
            f"request base SHA {request.base_sha} does not match trusted PR base {expected_base_sha}"
        )
    if triage.triage_id != request.triage_id or triage.triage_digest != request.triage_digest:
        violations.append("bundle triage does not match request")
    if projection.state not in {"VALIDATED", "MERGE_READY"}:
        violations.append(f"bundle projection is not validated: {projection.state}")

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
    )
    violations.extend(decision.violations)
    validation = bundle.get("validation") or {}
    validation_payload = {key: value for key, value in validation.items() if key != "validation_digest"}
    if validation.get("validation_digest") != digest_payload(validation_payload):
        violations.append("bundle validation digest mismatch")
    validated_policy = validation.get("policy") or {}
    if validated_policy.get("patch_digest") != decision.patch_digest:
        violations.append("bundle validation patch digest does not match candidate")
    if not all(bool(item.get("passed")) for item in validation.get("command_results") or []):
        violations.append("bundle contains a failed validation command")
    regression = validation.get("regression")
    if request.performance_baseline:
        baseline_root = (trusted_baseline_root or candidate_root).resolve()
        baseline_file = baseline_root / ".autobugfix-baselines" / f"{request.performance_baseline}.yaml"
        if not baseline_file.is_file():
            violations.append(f"trusted base is missing required baseline: {request.performance_baseline}")
        else:
            regression = compare_baseline(
                baseline_root,
                request.performance_baseline,
                validation.get("current_metrics") or {},
                trusted_policy.data.get("metrics") or {},
            )
            if not regression["ok"]:
                violations.extend(str(item) for item in regression["failures"])

    validation_id = f"trusted-{request.request_id}"
    command_results = []
    if run_profiles and not violations:
        command_results = run_validation_profiles(
            candidate_root.resolve(),
            candidate_root.resolve(),
            request.request_id,
            validation_id,
            decision,
            trusted_policy.data,
        )
        if any(not item["passed"] for item in command_results):
            violations.append("one or more trusted CI validation commands failed")
    return {
        "allowed": not violations and decision.allowed and trusted_policy.trusted,
        "bundle": str(bundle_path),
        "bundle_digest": bundle["bundle_digest"],
        "request_id": request.request_id,
        "request_digest": request.request_digest,
        "policy": decision.to_dict(),
        "command_results": command_results,
        "regression": regression,
        "violations": violations,
    }
