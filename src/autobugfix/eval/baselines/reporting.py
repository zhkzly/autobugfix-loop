from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
    verify_record,
)


class RawBaselineReportError(RuntimeError):
    pass


def _mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RawBaselineReportError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RawBaselineReportError(f"{label} must be a mapping")
    try:
        verify_record(data)
    except BenchmarkContractError as exc:
        raise RawBaselineReportError(f"invalid {label}: {exc}") from exc
    return dict(data)


def _json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawBaselineReportError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RawBaselineReportError(f"{label} must be a mapping")
    try:
        verify_record(data)
    except BenchmarkContractError as exc:
        raise RawBaselineReportError(f"invalid {label}: {exc}") from exc
    return dict(data)


def _mcnemar_exact(rescue: int, regression: int) -> float | None:
    discordant = rescue + regression
    if discordant == 0:
        return None
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(rescue, regression) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def _token_total(usage: Any) -> int | None:
    if not isinstance(usage, Mapping):
        return None
    for key in ("total_tokens", "totalTokens", "total_token_count"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for key in (
        "total",
        "total_token_usage",
        "totalTokenUsage",
        "token_usage",
    ):
        nested = _token_total(usage.get(key))
        if nested is not None:
            return nested
    return None


def _cohort_metrics(
    case_ids: Sequence[str],
    raw_cases: Mapping[str, Mapping[str, Any]],
    h0_cases: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    raw_passed = 0
    h0_passed = 0
    both_passed = 0
    both_failed = 0
    rescue = 0
    regression = 0
    rows: list[dict[str, Any]] = []
    for case_id in case_ids:
        raw_pass = raw_cases[case_id].get("decision") == "pass"
        h0_pass = h0_cases[case_id].get("decision") == "pass"
        raw_passed += int(raw_pass)
        h0_passed += int(h0_pass)
        both_passed += int(raw_pass and h0_pass)
        both_failed += int(not raw_pass and not h0_pass)
        rescue += int(raw_pass and not h0_pass)
        regression += int(h0_pass and not raw_pass)
        rows.append(
            {
                "case_id": case_id,
                "h0_decision": "pass" if h0_pass else "fail",
                "raw_decision": "pass" if raw_pass else "fail",
                "paired_outcome": (
                    "both_pass"
                    if raw_pass and h0_pass
                    else "raw_rescue"
                    if raw_pass
                    else "raw_regression"
                    if h0_pass
                    else "both_fail"
                ),
            }
        )
    count = len(case_ids)
    return {
        "case_count": count,
        "h0_passed_count": h0_passed,
        "h0_pass_rate": h0_passed / count if count else 0.0,
        "raw_passed_count": raw_passed,
        "raw_pass_rate": raw_passed / count if count else 0.0,
        "raw_minus_h0_absolute": (raw_passed - h0_passed) / count if count else 0.0,
        "both_passed_count": both_passed,
        "both_failed_count": both_failed,
        "raw_rescue_count": rescue,
        "raw_regression_count": regression,
        "mcnemar_exact_two_sided_p": _mcnemar_exact(rescue, regression),
        "cases": rows,
    }


def write_raw_baseline_report(run_dir: Path, h0_report_path: Path) -> Path:
    root = run_dir.resolve()
    summary = _mapping(root / "summary.yaml", "Raw run summary")
    binding = _mapping(root / "run-binding.yaml", "Raw run binding")
    h0 = _mapping(h0_report_path.resolve(), "H0 evaluation report")
    if summary.get("schema") != "autobugfix-raw-codex-run-summary-v1":
        raise RawBaselineReportError("unsupported Raw run summary schema")
    if binding.get("schema") != "autobugfix-raw-codex-run-binding-v1":
        raise RawBaselineReportError("unsupported Raw run binding schema")
    if h0.get("schema") != "autobugfix-formal-evaluation-report-v1":
        raise RawBaselineReportError("unsupported H0 report schema")
    if (
        summary.get("formal") is not True
        or summary.get("status") != "completed"
        or int(summary.get("expected_case_count") or 0) != 16
        or int(summary.get("completed_case_count") or 0) != 16
        or int(summary.get("harness_error_count") or 0) != 0
    ):
        raise RawBaselineReportError(
            "paired report requires a completed 16-case formal Raw run"
        )
    if str(binding.get("summary_digest") or "") != str(
        summary.get("record_digest") or ""
    ):
        raise RawBaselineReportError("Raw run binding disagrees with summary")
    if str(binding.get("h0_report_digest") or "") != str(
        h0.get("record_digest") or ""
    ):
        raise RawBaselineReportError(
            "H0 report differs from the report frozen for this Raw run"
        )

    raw_rows = summary.get("cases")
    h0_rows = h0.get("cases")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise RawBaselineReportError("Raw summary cases must be a list")
    if not isinstance(h0_rows, Sequence) or isinstance(h0_rows, (str, bytes)):
        raise RawBaselineReportError("H0 report cases must be a list")
    raw_cases = {
        str(item.get("case_id") or ""): dict(item)
        for item in raw_rows
        if isinstance(item, Mapping)
    }
    h0_cases = {
        str(item.get("case_id") or ""): dict(item)
        for item in h0_rows
        if isinstance(item, Mapping)
    }
    if len(raw_cases) != 16 or set(raw_cases) != set(h0_cases):
        raise RawBaselineReportError("Raw and H0 case identities differ")

    artifact_present = 0
    artifact_expected = 0
    usage_tokens: list[int] = []
    runtime_seconds = 0.0
    timeouts = 0
    invalid_paths = 0
    case_digests: list[dict[str, Any]] = []
    for case_id, row in raw_cases.items():
        case_dir = root / case_id
        report = _mapping(case_dir / "report.yaml", f"{case_id} report")
        submission = _mapping(
            case_dir / "submission.yaml", f"{case_id} submission"
        )
        oracle = _mapping(
            case_dir / "oracle-result.yaml", f"{case_id} oracle"
        )
        noninterference = _mapping(
            case_dir / "oracle-noninterference.yaml",
            f"{case_id} noninterference",
        )
        case_bundle_path = case_dir / "visible-input" / "case.json"
        case_bundle = _json_mapping(case_bundle_path, f"{case_id} visible bundle")
        patch = case_dir / "generated.diff"
        process_paths = {
            "worker_stdout": case_dir / "process" / "worker.stdout.log",
            "worker_stderr": case_dir / "process" / "worker.stderr.log",
            "sdk_request": case_dir
            / "process"
            / "untrusted-sdk-output"
            / "sdk"
            / "request.json",
            "sdk_events": case_dir
            / "process"
            / "untrusted-sdk-output"
            / "sdk"
            / "events.jsonl",
            "sdk_stderr": case_dir
            / "process"
            / "untrusted-sdk-output"
            / "sdk"
            / "stderr.log",
            "sdk_result": case_dir
            / "process"
            / "untrusted-sdk-output"
            / "sdk"
            / "process-result.json",
        }
        process_digests = submission.get("process_artifact_digests")
        if not isinstance(process_digests, Mapping):
            raise RawBaselineReportError(
                f"Raw submission has no process artifact binding: {case_id}"
            )
        expected_noninterference = noninterference.get("expected")
        if not isinstance(expected_noninterference, Mapping):
            raise RawBaselineReportError(
                f"Raw noninterference record has no expected binding: {case_id}"
            )
        process_artifacts_match = all(
            (
                expected == "missing" and not process_paths[name].exists()
            )
            or (
                isinstance(expected, str)
                and process_paths[name].is_file()
                and digest_file(process_paths[name]) == expected
            )
            for name, expected in process_digests.items()
            if name in process_paths
        ) and set(process_digests) == set(process_paths)
        if (
            report.get("record_digest") != row.get("record_digest")
            or report.get("case_id") != case_id
            or submission.get("case_id") != case_id
            or oracle.get("case_id") != case_id
            or noninterference.get("case_id") != case_id
            or report.get("submission_digest") != submission.get("record_digest")
            or report.get("oracle_digest") != oracle.get("record_digest")
            or report.get("noninterference_digest")
            != noninterference.get("record_digest")
            or oracle.get("submission_digest") != submission.get("record_digest")
            or noninterference.get("submission_digest")
            != submission.get("record_digest")
            or submission.get("manifest_digest")
            != binding.get("prepared_manifest_digest")
            or submission.get("case_bundle_digest")
            != case_bundle.get("record_digest")
            or noninterference.get("unchanged") is not True
            or digest_file(case_bundle_path)
            != expected_noninterference.get("case_bundle_sha256")
            or not process_artifacts_match
            or not patch.is_file()
            or digest_file(patch) != submission.get("patch_sha256")
        ):
            raise RawBaselineReportError(
                f"Raw case artifact binding failed: {case_id}"
            )
        required_paths = (
            patch,
            case_dir / "submission.yaml",
            case_dir / "oracle-result.yaml",
            case_dir / "oracle-noninterference.yaml",
            case_dir / "report.yaml",
            case_bundle_path,
            *process_paths.values(),
        )
        artifact_expected += len(required_paths)
        artifact_present += sum(path.is_file() for path in required_paths)
        token_total = _token_total(report.get("usage"))
        if token_total is not None:
            usage_tokens.append(token_total)
        runtime_seconds += float(report.get("runtime_seconds") or 0.0)
        timeouts += int(bool(report.get("timed_out")))
        invalid_paths += int(report.get("path_policy_passed") is not True)
        case_digests.append(
            {
                "case_id": case_id,
                "cohort": report.get("cohort"),
                "decision": report.get("decision"),
                "submission_digest": submission["record_digest"],
                "oracle_digest": oracle["record_digest"],
                "noninterference_digest": noninterference["record_digest"],
            }
        )

    primary = sorted(
        case_id
        for case_id, row in raw_cases.items()
        if row.get("cohort") == "primary"
    )
    development = sorted(
        case_id
        for case_id, row in raw_cases.items()
        if row.get("cohort") == "development"
    )
    if len(primary) != 13 or len(development) != 3:
        raise RawBaselineReportError("Raw cohort assignment is not 13/3")
    report = record_with_digest(
        {
            "schema": "autobugfix-raw-codex-comparison-report-v1",
            "run_id": summary["run_id"],
            "raw_summary_digest": summary["record_digest"],
            "raw_binding_digest": binding["record_digest"],
            "prepared_manifest_digest": binding["prepared_manifest_digest"],
            "h0_report_digest": h0["record_digest"],
            "runner_git_sha": binding["runner_git_sha"],
            "runner_source_digest": binding["runner_source_digest"],
            "prompt_template_digest": binding["prompt_template_digest"],
            "model": binding["model"],
            "sdk_version": binding["sdk_version"],
            "primary": _cohort_metrics(primary, raw_cases, h0_cases),
            "development": _cohort_metrics(
                development, raw_cases, h0_cases
            ),
            "all_cases": _cohort_metrics(
                sorted(raw_cases), raw_cases, h0_cases
            ),
            "raw_runtime_seconds": runtime_seconds,
            "raw_sdk_calls": 16,
            "raw_reported_total_tokens": (
                sum(usage_tokens) if len(usage_tokens) == 16 else None
            ),
            "raw_timeout_count": timeouts,
            "raw_path_policy_failure_count": invalid_paths,
            "artifact_completeness": (
                artifact_present / artifact_expected if artifact_expected else 0.0
            ),
            "case_digests": sorted(
                case_digests, key=lambda item: str(item["case_id"])
            ),
            "limitations": [
                "Three development cases were exposed during H0 harness development and are excluded from the primary comparison.",
                "This is a system-level direct-SDK versus Autobugfix comparison, not a compute-matched model-only ablation.",
                "The primary cohort has 13 cases and therefore limited statistical power.",
            ],
        }
    )
    destination = root / "raw-codex-comparison-report.yaml"
    rendered = yaml.safe_dump(report, sort_keys=False)
    if destination.exists():
        existing = destination.read_text(encoding="utf-8")
        if existing != rendered:
            raise RawBaselineReportError(
                "existing Raw comparison report differs from deterministic output"
            )
        return destination
    destination.write_text(rendered, encoding="utf-8")
    destination.chmod(0o600)
    return destination
