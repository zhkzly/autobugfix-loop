from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from autobugfix.operator.bundle import read_bundle, validate_bundle
from autobugfix.operator.trusted import load_trusted_policy


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
    request = bundle.get("request") or {}
    if request.get("base_sha") != args.expected_base_sha:
        print(
            f"request base SHA {request.get('base_sha')} does not match trusted PR base {args.expected_base_sha}"
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
    )
    print(yaml.safe_dump(report, sort_keys=False).strip())
    return 0 if report["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
