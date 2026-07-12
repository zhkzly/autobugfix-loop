from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.artifacts import write_yaml
from autobugfix.eval.benchmarks.models import (
    digest_file,
    record_with_digest,
    verify_record,
)


class EvaluationReportError(RuntimeError):
    pass


def _mapping(path: Path, label: str) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise EvaluationReportError(f"{label} must be a mapping: {path}")
    return dict(data)


def _timestamp(value: object) -> datetime:
    text = str(value or "")
    if not text:
        raise EvaluationReportError("evaluation timestamp must not be empty")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total < 1:
        raise EvaluationReportError("Wilson interval requires at least one case")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (proportion + z * z / (2 * total)) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def build_evaluation_report(run_dir: Path) -> dict[str, Any]:
    root = run_dir.resolve()
    summary_path = root / "summary.yaml"
    subject_path = root / "subject-noninterference.yaml"
    if not summary_path.is_file() or not subject_path.is_file():
        raise EvaluationReportError(
            "formal evaluation requires summary and subject noninterference artifacts"
        )
    summary = _mapping(summary_path, "evaluation summary")
    subject = _mapping(subject_path, "subject noninterference receipt")
    verify_record(subject)
    if subject.get("unchanged") is not True:
        raise EvaluationReportError("frozen H0 changed during evaluation")

    case_dirs = sorted(
        path for path in root.iterdir() if path.is_dir() and (path / "report.yaml").is_file()
    )
    expected_count = int(summary.get("case_count") or 0)
    if expected_count < 1 or len(case_dirs) != expected_count:
        raise EvaluationReportError(
            "completed case count differs from evaluation summary"
        )

    cases: list[dict[str, Any]] = []
    subject_shas: set[str] = set()
    for case_dir in case_dirs:
        case_report = _mapping(case_dir / "report.yaml", "case report")
        submission = _mapping(case_dir / "submission.yaml", "submission")
        oracle = _mapping(case_dir / "oracle-result.yaml", "oracle result")
        noninterference = _mapping(
            case_dir / "oracle-noninterference.yaml",
            "oracle noninterference receipt",
        )
        verify_record(submission)
        verify_record(noninterference)
        case_id = str(case_report.get("case_id") or "")
        if (
            not case_id
            or case_dir.name != case_id
            or submission.get("case_id") != case_id
            or noninterference.get("case_id") != case_id
        ):
            raise EvaluationReportError(
                f"case artifact identity mismatch: {case_dir}"
            )
        if noninterference.get("unchanged") is not True:
            raise EvaluationReportError(
                f"official scorer changed frozen submission: {case_id}"
            )
        events_path = next(
            (case_dir / "control/.autobugfix/tasks").glob("*/events.jsonl"),
            None,
        )
        if events_path is None:
            raise EvaluationReportError(f"case has no event stream: {case_id}")
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not events:
            raise EvaluationReportError(f"case event stream is empty: {case_id}")
        iterations = int(submission.get("iterations") or 0)
        if iterations < 1:
            raise EvaluationReportError(f"case has no Writer attempt: {case_id}")
        logs_root = events_path.parent / "logs"
        writer_calls = len(list(logs_root.glob("writer-*.sdk-request.json")))
        evaluator_calls = len(list(logs_root.glob("evaluator-*.sdk-request.json")))
        decision = str(case_report.get("decision") or "")
        official_passed = case_report.get("oracle_passed") is True
        visible_passed = case_report.get("execution_verifier_passed") is True
        runtime_seconds = (
            _timestamp(oracle.get("finished_at"))
            - _timestamp(events[0].get("timestamp"))
        ).total_seconds()
        subject_sha = str(submission.get("subject_sha") or "")
        subject_shas.add(subject_sha)
        cases.append(
            {
                "case_id": case_id,
                "decision": decision,
                "failure_stage": case_report.get("failure_stage"),
                "iterations": iterations,
                "writer_calls": writer_calls,
                "evaluator_calls": evaluator_calls,
                "sdk_calls": writer_calls + evaluator_calls,
                "visible_verifier_passed": visible_passed,
                "official_evaluator_passed": official_passed,
                "verifier_oracle_agree": visible_passed == official_passed,
                "runtime_seconds": runtime_seconds,
                "submission_digest": submission["record_digest"],
                "noninterference_digest": noninterference["record_digest"],
            }
        )

    passed = sum(item["decision"] == "pass" for item in cases)
    failed = sum(item["decision"] == "fail" for item in cases)
    errors = sum(item["decision"] == "error" for item in cases)
    if (
        passed != int(summary.get("passed_count") or 0)
        or failed != int(summary.get("failed_count") or 0)
        or errors != int(summary.get("harness_error_count") or 0)
    ):
        raise EvaluationReportError(
            "case decisions differ from evaluation summary"
        )
    first_attempt_passed = sum(
        item["decision"] == "pass" and item["iterations"] == 1 for item in cases
    )
    loop_rescued = sum(
        item["decision"] == "pass" and item["iterations"] > 1 for item in cases
    )
    retry_cases = sum(item["iterations"] > 1 for item in cases)
    false_positives = sum(
        item["visible_verifier_passed"]
        and not item["official_evaluator_passed"]
        for item in cases
    )
    false_negatives = sum(
        not item["visible_verifier_passed"]
        and item["official_evaluator_passed"]
        for item in cases
    )
    runtimes = [float(item["runtime_seconds"]) for item in cases]
    lower, upper = _wilson_interval(passed, len(cases))
    failure_stages = Counter(
        str(item["failure_stage"])
        for item in cases
        if item["failure_stage"] is not None
    )
    return record_with_digest(
        {
            "schema": "autobugfix-formal-evaluation-report-v1",
            "run_id": summary.get("run_id"),
            "prepared_manifest_digest": subject.get(
                "prepared_manifest_digest"
            ),
            "subject_sha": next(iter(subject_shas)) if len(subject_shas) == 1 else "mixed",
            "source_summary_sha256": digest_file(summary_path),
            "started_at": summary.get("started_at"),
            "finished_at": summary.get("finished_at"),
            "runtime_seconds": float(summary.get("runtime_seconds") or 0.0),
            "case_count": len(cases),
            "passed_count": passed,
            "failed_count": failed,
            "harness_error_count": errors,
            "pass_rate": passed / len(cases),
            "pass_rate_wilson_95": {"lower": lower, "upper": upper},
            "first_attempt_passed_count": first_attempt_passed,
            "first_attempt_pass_rate": first_attempt_passed / len(cases),
            "loop_rescued_count": loop_rescued,
            "loop_absolute_gain": loop_rescued / len(cases),
            "retry_case_count": retry_cases,
            "retry_rescue_rate": (
                loop_rescued / retry_cases if retry_cases else None
            ),
            "writer_attempt_count": sum(item["iterations"] for item in cases),
            "sdk_call_count": sum(item["sdk_calls"] for item in cases),
            "verifier_oracle_agreement_count": sum(
                item["verifier_oracle_agree"] for item in cases
            ),
            "verifier_false_positive_count": false_positives,
            "verifier_false_negative_count": false_negatives,
            "noninterference_passed_count": len(cases),
            "artifact_completeness": float(
                summary.get("artifact_completeness") or 0.0
            ),
            "mean_case_runtime_seconds": statistics.mean(runtimes),
            "median_case_runtime_seconds": statistics.median(runtimes),
            "slowest_cases": sorted(
                (
                    {
                        "case_id": item["case_id"],
                        "runtime_seconds": item["runtime_seconds"],
                    }
                    for item in cases
                ),
                key=lambda item: float(item["runtime_seconds"]),
                reverse=True,
            )[:5],
            "failure_stages": dict(sorted(failure_stages.items())),
            "cases": cases,
        }
    )


def write_evaluation_report(run_dir: Path) -> Path:
    root = run_dir.resolve()
    path = root / "evaluation-report.yaml"
    write_yaml(path, build_evaluation_report(root))
    return path
