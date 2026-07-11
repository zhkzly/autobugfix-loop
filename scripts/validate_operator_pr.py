from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml

from autobugfix.operator.bundle import read_bundle, validate_bundle
from autobugfix.operator.approvals import OperatorApprovalError, github_approval
from autobugfix.operator.guard import effective_request
from autobugfix.operator.models import OperatorRequest, ScopeRevision
from autobugfix.operator.trusted import load_trusted_policy


def _github_review_approvals(
    repository: str,
    pull_request: int,
    request: OperatorRequest,
    policy: dict[str, object],
    scope_version: int,
):
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/pulls/{pull_request}/reviews", "--paginate", "--slurp"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GitHub review listing failed: {result.stderr.strip()}")
    reviews = json.loads(result.stdout)
    if reviews and isinstance(reviews, list) and isinstance(reviews[0], list):
        reviews = [item for page in reviews for item in page]
    if not isinstance(reviews, list):
        raise RuntimeError("GitHub review listing returned a non-list payload")
    approvals = []
    for review in reviews:
        if not isinstance(review, dict) or str(review.get("state") or "").upper() != "APPROVED":
            continue
        review_id = int(review["id"])
        for stage in ("scope", "merge"):
            try:
                approvals.append(
                    github_approval(
                        request,
                        f"github-pr-{pull_request}-{review_id}-{stage}",
                        repository=repository,
                        pull_request=pull_request,
                        review_id=review_id,
                        constitution=policy,
                        reason="trusted GitHub PR review",
                        stage=stage,
                        scope_version=scope_version,
                    )
                )
            except OperatorApprovalError:
                continue
    return approvals


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one Operator-governed pull request from trusted base code.")
    parser.add_argument("--trusted-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--allowed-signers")
    parser.add_argument("--policy-only", action="store_true")
    args = parser.parse_args()

    trusted_root = Path(args.trusted_root).resolve()
    candidate_root = Path(args.candidate_root).resolve()
    bundles = sorted(candidate_root.glob(".autobugfix-governance/*/bundle.yaml"))
    if len(bundles) != 1:
        print(f"expected exactly one Operator authorization bundle, found {len(bundles)}")
        return 1
    bundle = read_bundle(bundles[0])
    request_data = bundle.get("request") or {}
    if request_data.get("base_sha") != args.expected_base_sha:
        print(
            f"request base SHA {request_data.get('base_sha')} does not match trusted PR base {args.expected_base_sha}"
        )
        return 1
    policy = load_trusted_policy(
        trusted_root,
        trusted_ref=None,
        trusted_file=trusted_root / "src/autobugfix/operator/constitution.yaml",
    )
    configured_signers = trusted_root / str(
        (policy.data.get("approval") or {}).get("allowed_signers_file")
        or ".github/autobugfix-allowed-signers"
    )
    allowed_signers = (
        Path(args.allowed_signers).resolve()
        if args.allowed_signers
        else configured_signers
        if configured_signers.is_file()
        else None
    )
    base_request = OperatorRequest.from_dict(request_data)
    revisions = [ScopeRevision.from_dict(item) for item in bundle.get("scope_revisions") or []]
    request, scope_version = effective_request(base_request, revisions)
    github_approvals = _github_review_approvals(
        args.repository,
        args.pull_request,
        request,
        policy.data,
        scope_version,
    )
    report = validate_bundle(
        bundles[0],
        candidate_root,
        policy,
        allowed_signers=allowed_signers,
        run_profiles=not args.policy_only,
        expected_base_sha=args.expected_base_sha,
        expected_github_repository=args.repository,
        expected_pull_request=args.pull_request,
        trusted_baseline_root=trusted_root,
        extra_approvals=github_approvals,
        runtime_venv=trusted_root / ".venv",
    )
    print(yaml.safe_dump(report, sort_keys=False).strip())
    return 0 if report["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
