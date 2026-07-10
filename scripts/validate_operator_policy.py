from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from autobugfix.operator.bundle import validate_bundle
from autobugfix.operator.trusted import load_trusted_policy
from autobugfix.operator.validator import validate_operator_request


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the trusted Autobugfix Operator Governance v3 gate.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request-id")
    source.add_argument("--bundle")
    parser.add_argument("--project-root", default=".", help="Root containing runtime request records")
    parser.add_argument("--candidate-root", help="Git worktree to validate; defaults to project root")
    parser.add_argument("--trusted-ref", default="origin/main")
    parser.add_argument("--trusted-file")
    parser.add_argument("--bootstrap-policy", action="store_true")
    parser.add_argument("--allowed-signers")
    parser.add_argument("--policy-only", action="store_true")
    parser.add_argument("--phase", choices=["postflight", "merge"], default="merge")
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    candidate_root = Path(args.candidate_root or args.project_root).resolve()
    trusted_file = Path(args.trusted_file).resolve() if args.trusted_file else None
    allowed_signers = Path(args.allowed_signers).resolve() if args.allowed_signers else None
    policy = load_trusted_policy(
        project_root,
        trusted_ref=args.trusted_ref,
        trusted_file=trusted_file,
        bootstrap=args.bootstrap_policy,
    )
    if args.bundle:
        report = validate_bundle(
            Path(args.bundle).resolve(),
            candidate_root,
            policy,
            allowed_signers=allowed_signers,
            run_profiles=not args.policy_only,
        )
        allowed = report["allowed"]
    else:
        metrics: dict[str, float] = {}
        for item in args.metric:
            key, value = item.split("=", 1)
            metrics[key] = float(value)
        report = validate_operator_request(
            project_root,
            args.request_id,
            candidate_root=candidate_root,
            trusted_policy=policy,
            run_profiles=not args.policy_only,
            current_metrics=metrics,
            allowed_signers=allowed_signers,
            phase=args.phase,
            record=not args.no_record,
        )
        allowed = report["policy"]["allowed"]
    print(yaml.safe_dump(report, sort_keys=False).strip())
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
