from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path

import openai_codex

from raw_codex_sdk_baseline.models import CaseBundle, package_source_digest
from raw_codex_sdk_baseline.prompt import PROMPT_TEMPLATE_DIGEST
from raw_codex_sdk_baseline.runner import (
    APPROVAL_MODE,
    NETWORK_ACCESS,
    SANDBOX_MODE,
    run_case,
)


def _distribution_record_digest(distribution: str) -> str:
    record = metadata.distribution(distribution).read_text("RECORD")
    if not record:
        raise RuntimeError(f"{distribution} has no installed RECORD")
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="raw-codex-sdk-baseline",
        description="Run one direct Codex SDK coding turn in a prepared worktree.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("metadata")
    run = subparsers.add_parser("run")
    run.add_argument("--case-bundle", required=True, type=Path)
    run.add_argument("--worktree", required=True, type=Path)
    run.add_argument("--artifacts", required=True, type=Path)
    run.add_argument("--model", required=True)
    run.add_argument("--reasoning-effort", required=True)
    run.add_argument("--service-tier")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "metadata":
        print(
            json.dumps(
                {
                    "schema": "raw-codex-sdk-runner-metadata-v1",
                    "sdk_package": "openai-codex",
                    "sdk_version": openai_codex.__version__,
                    "sdk_record_digest": _distribution_record_digest("openai-codex"),
                    "cli_package": "openai-codex-cli-bin",
                    "cli_version": metadata.version("openai-codex-cli-bin"),
                    "cli_record_digest": _distribution_record_digest(
                        "openai-codex-cli-bin"
                    ),
                    "approval_mode": APPROVAL_MODE,
                    "sandbox": SANDBOX_MODE,
                    "network_access": NETWORK_ACCESS,
                    "prompt_template_digest": PROMPT_TEMPLATE_DIGEST,
                    "runner_package_digest": package_source_digest(),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    if args.command != "run":
        raise AssertionError(f"unhandled command: {args.command}")
    result = run_case(
        CaseBundle.from_json(args.case_bundle.resolve()),
        args.worktree.resolve(),
        args.artifacts.resolve(),
        model=str(args.model),
        reasoning_effort=str(args.reasoning_effort),
        service_tier=str(args.service_tier) if args.service_tier else None,
    )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
