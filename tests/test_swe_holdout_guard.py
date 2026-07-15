from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from autobugfix.cli import build_parser
from autobugfix.eval.swe_holdout_guard import (
    SWEHoldoutCandidate,
    SWEHoldoutCohortError,
    advance_holdout_cohort,
    discover_git_exposed_values,
    discover_operator_visible_live_ids,
    discover_operator_visible_live_repositories,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


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


def test_visible_identity_audit_catches_unstructured_logs(tmp_path: Path) -> None:
    root = tmp_path / "visible"
    root.mkdir()
    identity = "owner__repository-1234"
    (root / "writer.stderr.log").write_text(
        f"previously attempted {identity} before interruption\n",
        encoding="utf-8",
    )

    assert discover_operator_visible_live_ids(root, {identity}) == {identity}


def test_visible_repository_audit_excludes_unseen_instance_from_seen_repo(
    tmp_path: Path,
) -> None:
    root = tmp_path / "visible"
    root.mkdir()
    repository = "owner/shared-repository"
    (root / "operator.log").write_text(
        f"previous case came from {repository}\n",
        encoding="utf-8",
    )

    assert discover_operator_visible_live_repositories(
        root,
        {repository, "owner/unseen-repository"},
    ) == {repository}


def test_git_exposure_audit_includes_deleted_reachable_history(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "guard@example.invalid")
    _git(root, "config", "user.name", "Guard Test")
    exposed = "owner__repository-9876"
    evidence = root / "operator-note.txt"
    evidence.write_text(f"attempted {exposed}\n", encoding="utf-8")
    _git(root, "add", "operator-note.txt")
    _git(root, "commit", "-m", "record visible evidence")
    evidence.unlink()
    _git(root, "add", "-u")
    _git(root, "commit", "-m", "remove visible projection")

    assert discover_git_exposed_values(root, {exposed, "never-seen"}) == {exposed}


def test_git_exposure_audit_includes_untracked_operator_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "guard@example.invalid")
    _git(root, "config", "user.name", "Guard Test")
    (root / "README.md").write_text("control\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initialize control repository")
    exposed = "owner__untracked-repository-1234"
    (root / "new-adapter.py").write_text(
        f'CASE = "{exposed}"\n',
        encoding="utf-8",
    )

    assert discover_git_exposed_values(root, {exposed, "never-seen"}) == {exposed}


def test_git_exposure_audit_scans_symlink_target_without_following_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "guard@example.invalid")
    _git(root, "config", "user.name", "Guard Test")
    exposed = "owner__linked-repository-1234"
    (root / "case-link").symlink_to(exposed)

    assert discover_git_exposed_values(root, {exposed, "never-seen"}) == {exposed}


def test_holdout_cohort_skips_every_instance_from_visible_repository() -> None:
    candidates = [
        SWEHoldoutCandidate(
            instance_id=f"case-{index}",
            repository=("owner/seen" if index < 2 else f"owner/repo-{index}"),
            language=language,
        )
        for index, language in enumerate(
            ("go", "java", "rust", "js", "cpp", "c", "python", "ruby")
        )
    ]
    attempted: list[SWEHoldoutCandidate] = []

    result = advance_holdout_cohort(
        candidates,
        (),
        protocol_digest="f" * 64,
        guard_secret="guard-secret-for-tests",
        optimization_repositories=set(),
        excluded_ids=set(),
        excluded_repositories={"owner/seen"},
        max_candidates=8,
        qualify=lambda candidate: attempted.append(candidate) is None,
    )

    assert result.eligible_count == 6
    assert all(candidate.repository != "owner/seen" for candidate in attempted)


def test_visible_identity_audit_catches_sqlite_and_binary_state(tmp_path: Path) -> None:
    root = tmp_path / "visible"
    root.mkdir()
    sqlite_identity = "owner__repository-4321"
    database = sqlite3.connect(root / "operator.sqlite3")
    database.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
    database.execute("INSERT INTO evidence VALUES (?)", (sqlite_identity,))
    database.commit()
    database.close()
    binary_identity = "owner__repository-9876"
    (root / "opaque.bin").write_bytes(b"\x00record:" + binary_identity.encode() + b"\xff")

    assert discover_operator_visible_live_ids(
        root,
        {sqlite_identity, binary_identity, "never-visible"},
    ) == {sqlite_identity, binary_identity}


def test_visible_identity_audit_rejects_symlinked_evidence(tmp_path: Path) -> None:
    root = tmp_path / "visible"
    root.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("private-case\n", encoding="utf-8")
    (root / "linked.log").symlink_to(outside)

    with pytest.raises(SWEHoldoutCohortError, match="symbolic link"):
        discover_operator_visible_live_ids(root, {"private-case"})


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
