from __future__ import annotations

import json
from pathlib import Path

import pytest

from autobugfix.cli import build_parser
from autobugfix.eval.swe_holdout_guard import (
    SWEHoldoutCandidate,
    SWEHoldoutCohortError,
    advance_holdout_cohort,
    discover_operator_visible_live_ids,
)


def _candidate(index: int, language: str) -> SWEHoldoutCandidate:
    return SWEHoldoutCandidate(
        instance_id=f"private-case-{index}",
        repository=f"private/repository-{index}",
        language=language,
    )


def _record(candidate: SWEHoldoutCandidate, *, eligible: bool) -> dict[str, object]:
    return {
        "instance_id": candidate.instance_id,
        "repository": candidate.repository,
        "language": candidate.language,
        "eligible": eligible,
    }


def test_holdout_cohort_qualifies_six_repositories_and_four_languages() -> None:
    candidates = [
        _candidate(index, language)
        for index, language in enumerate(
            ("go", "java", "rust", "js", "cpp", "c", "go", "java", "rust")
        )
    ]
    attempted: list[SWEHoldoutCandidate] = []
    progress = []

    result = advance_holdout_cohort(
        candidates,
        (),
        protocol_digest="a" * 64,
        guard_secret="guard-secret-for-tests",
        optimization_repositories={"public/repository"},
        excluded_ids=set(),
        max_candidates=12,
        qualify=lambda candidate: attempted.append(candidate) is None,
        progress=progress.append,
    )

    assert result.eligible_count == 6
    assert result.repository_count == 6
    assert result.language_count >= 4
    assert len(attempted) == 6
    assert progress[-1] == result


def test_holdout_cohort_resumes_without_retrying_attempted_or_visible_cases() -> None:
    candidates = [
        _candidate(index, language)
        for index, language in enumerate(
            ("go", "java", "rust", "js", "cpp", "c", "python", "ruby")
        )
    ]
    existing = [
        _record(candidates[0], eligible=True),
        _record(candidates[1], eligible=True),
        _record(candidates[2], eligible=False),
    ]
    attempted: list[SWEHoldoutCandidate] = []

    result = advance_holdout_cohort(
        candidates,
        existing,
        protocol_digest="b" * 64,
        guard_secret="guard-secret-for-tests",
        optimization_repositories=set(),
        excluded_ids={candidates[3].instance_id},
        max_candidates=8,
        qualify=lambda candidate: attempted.append(candidate) is None,
    )

    assert result.eligible_count == 6
    assert result.new_attempt_count == 4
    assert not {
        candidates[0].instance_id,
        candidates[1].instance_id,
        candidates[2].instance_id,
        candidates[3].instance_id,
    } & {item.instance_id for item in attempted}


def test_holdout_cohort_rejects_infeasible_language_isolation() -> None:
    candidates = [_candidate(index, "python") for index in range(8)]

    with pytest.raises(SWEHoldoutCohortError, match="repository/language isolation"):
        advance_holdout_cohort(
            candidates,
            (),
            protocol_digest="c" * 64,
            guard_secret="guard-secret-for-tests",
            optimization_repositories=set(),
            excluded_ids=set(),
            max_candidates=8,
            qualify=lambda candidate: True,
        )


def test_holdout_cohort_rejects_operator_visible_existing_identity() -> None:
    candidates = [
        _candidate(index, language)
        for index, language in enumerate(("go", "java", "rust", "js", "cpp", "c"))
    ]

    with pytest.raises(SWEHoldoutCohortError, match="Operator-visible identity"):
        advance_holdout_cohort(
            candidates,
            (_record(candidates[0], eligible=True),),
            protocol_digest="d" * 64,
            guard_secret="guard-secret-for-tests",
            optimization_repositories=set(),
            excluded_ids={candidates[0].instance_id},
            max_candidates=8,
            qualify=lambda candidate: True,
        )


def test_visible_identity_audit_catches_paths_and_nested_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "visible"
    path_identity = "private-case-path"
    record_identity = "private-case-record"
    (root / "interrupted" / path_identity).mkdir(parents=True)
    record = root / "reports" / "report.yaml"
    record.parent.mkdir(parents=True)
    record.write_text(
        "outer:\n  instance_id: private-case-record\n",
        encoding="utf-8",
    )

    observed = discover_operator_visible_live_ids(
        root,
        {path_identity, record_identity, "private-case-never-visible"},
    )

    assert observed == {path_identity, record_identity}


def test_holdout_public_progress_contains_no_case_identity() -> None:
    candidates = [
        _candidate(index, language)
        for index, language in enumerate(("go", "java", "rust", "js", "cpp", "c"))
    ]
    result = advance_holdout_cohort(
        candidates,
        (),
        protocol_digest="e" * 64,
        guard_secret="guard-secret-for-tests",
        optimization_repositories=set(),
        excluded_ids=set(),
        max_candidates=6,
        qualify=lambda candidate: True,
    )
    encoded = json.dumps(result.aggregate(), sort_keys=True)

    for candidate in candidates:
        assert candidate.instance_id not in encoded
        assert candidate.repository not in encoded
    assert set(result.aggregate()) == {
        "attempted_count",
        "new_attempt_count",
        "eligible_count",
        "repository_count",
        "language_count",
    }


def test_cli_exposes_one_guard_cohort_command_without_instance_argument() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "eval",
            "benchmark",
            "qualify-swe-holdout-cohort",
            "--protocol",
            "benchmarks/swe-experiment-2.yaml",
            "--guard-root",
            "/secure/guard",
        ]
    )

    assert args.benchmark_action == "qualify-swe-holdout-cohort"
    assert args.max_candidates == 24
    assert not hasattr(args, "instance")
