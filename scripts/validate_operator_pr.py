from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

import yaml

from autobugfix.operator.bundle import read_bundle, validate_bundle
from autobugfix.operator.guard import effective_request
from autobugfix.operator.models import OperatorRequest, ScopeRevision
from autobugfix.operator.trusted import load_trusted_policy


def _git_value(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one Operator-governed pull request from trusted base code.")
    parser.add_argument("--trusted-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-request", required=True, type=int)
    parser.add_argument("--allowed-signers")
    parser.add_argument("--runtime-venv")
    parser.add_argument("--expected-guard-sha", required=True)
    parser.add_argument("--policy-only", action="store_true")
    parser.add_argument(
        "--skip-live-experiment",
        action="store_true",
        help="Run deterministic admission profiles but defer credentialed live experiments.",
    )
    args = parser.parse_args()

    trusted_root = Path(args.trusted_root).resolve()
    candidate_root = Path(args.candidate_root).resolve()
    guard_root = Path(__file__).resolve().parents[1]
    guard_sha = _git_value(guard_root, "rev-parse", "HEAD")
    if guard_sha != args.expected_guard_sha:
        print(
            f"Guard runtime SHA {guard_sha} does not match expected workflow SHA "
            f"{args.expected_guard_sha}"
        )
        return 1
    guard_lock = guard_root / "uv.lock"
    if not guard_lock.is_file():
        print(f"Guard runtime lockfile does not exist: {guard_lock}")
        return 1
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
    runtime_venv = (
        Path(args.runtime_venv).resolve()
        if args.runtime_venv
        else guard_root / ".venv"
    )
    if not args.policy_only and not runtime_venv.is_dir():
        print(f"trusted runtime venv does not exist: {runtime_venv}")
        return 1
    if not args.policy_only and runtime_venv != (guard_root / ".venv").resolve():
        print(f"trusted runtime venv must be Guard-owned: {guard_root / '.venv'}")
        return 1
    base_request = OperatorRequest.from_dict(request_data)
    revisions = [ScopeRevision.from_dict(item) for item in bundle.get("scope_revisions") or []]
    request, _scope_version = effective_request(base_request, revisions)
    report = validate_bundle(
        bundles[0],
        candidate_root,
        policy,
        allowed_signers=allowed_signers,
        run_profiles=not args.policy_only,
        run_experiments=not args.policy_only and not args.skip_live_experiment,
        expected_base_sha=args.expected_base_sha,
        expected_github_repository=args.repository,
        expected_pull_request=args.pull_request,
        trusted_baseline_root=trusted_root,
        runtime_venv=runtime_venv,
    )
    report["guard_runtime"] = {
        "sha": guard_sha,
        "tree": _git_value(guard_root, "rev-parse", "HEAD^{tree}"),
        "lock_sha256": _sha256(guard_lock),
        "runtime_venv": "guard/.venv",
    }
    report_path = (
        trusted_root
        / ".autobugfix/operator-pr"
        / request.request_id
        / "admission-report.yaml"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    print(yaml.safe_dump(report, sort_keys=False).strip())
    return 0 if report["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
