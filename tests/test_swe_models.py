from __future__ import annotations

from pathlib import Path

import pytest

from autobugfix.eval.benchmarks.models import BenchmarkContractError
from autobugfix.eval.benchmarks.swe_models import (
    SWEExperimentProtocol,
    SWESubmission,
    SWEVisibleCase,
)
from autobugfix.eval.benchmarks.swe_submission import (
    SWESubmissionAuthority,
    write_evidence_manifest,
)


ROOT = Path(__file__).parents[1]


def visible_case_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_token": "visible-1",
        "benchmark": "swebench_verified",
        "dataset_revision": "c" * 40,
        "harness_commit": "d" * 40,
        "repository": "owner/repo",
        "base_commit": "e" * 40,
        "language": "python",
        "task_type": "bugfix",
        "problem_statement": "Fix the documented behavior.",
        "public_hints": [],
        "attachments": [],
        "first_wave": 3,
        "source_snapshot_digest": "f" * 64,
        "verifier_profile": "python-public",
    }


def test_experiment_protocol_pins_h0_upstreams_and_split() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    )

    assert protocol.optimization_count == 10
    assert protocol.holdout_count == 6
    assert protocol.model == "gpt-5.4-mini"
    assert len(protocol.protocol_digest) == 64


def test_experiment_protocol_rejects_upstream_drift() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    ).to_dict()
    protocol["upstreams"]["swebench_commit"] = "0" * 40  # type: ignore[index]

    with pytest.raises(BenchmarkContractError, match="upstream identity drift"):
        SWEExperimentProtocol.from_dict(protocol)


def test_visible_case_rejects_private_oracle_fields() -> None:
    row = visible_case_data()
    row["gold_patch"] = "secret"

    with pytest.raises(BenchmarkContractError, match="unsupported fields"):
        SWEVisibleCase.from_dict(row)


def test_visible_case_round_trip_is_digest_bound() -> None:
    case = SWEVisibleCase.from_dict(visible_case_data())
    encoded = case.to_dict()

    assert SWEVisibleCase.from_dict(encoded) == case
    encoded["problem_statement"] = "forged"
    with pytest.raises(BenchmarkContractError, match="digest mismatch"):
        SWEVisibleCase.from_dict(encoded)


def test_submission_binds_patch_and_subject() -> None:
    patch = "diff --git a/a.py b/a.py\n"
    import hashlib

    submission = SWESubmission(
        case_token="visible-1",
        subject_sha="a" * 40,
        subject_tree="b" * 40,
        base_commit="c" * 40,
        patch=patch,
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        events_sha256="d" * 64,
        task_sha256="e" * 64,
        subject_request_digest="1" * 64,
        visible_case_digest="2" * 64,
        source_snapshot_digest="3" * 64,
        config_digest="4" * 64,
        skills_digest="5" * 64,
        execution_ledger_digest="6" * 64,
        evidence_manifest_digest="7" * 64,
        frozen_at="2026-07-12T00:00:00Z",
    )

    assert len(submission.record["record_digest"]) == 64
    with pytest.raises(BenchmarkContractError, match="patch digest mismatch"):
        SWESubmission(
            case_token="visible-1",
            subject_sha="a" * 40,
            subject_tree="b" * 40,
            base_commit="c" * 40,
            patch=patch,
            patch_sha256="0" * 64,
            events_sha256="d" * 64,
            task_sha256="e" * 64,
            subject_request_digest="1" * 64,
            visible_case_digest="2" * 64,
            source_snapshot_digest="3" * 64,
            config_digest="4" * 64,
            skills_digest="5" * 64,
            execution_ledger_digest="6" * 64,
            evidence_manifest_digest="7" * 64,
            frozen_at="2026-07-12T00:00:00Z",
        )


def test_submission_authority_freezes_and_rejects_patch_mutation(
    tmp_path: Path,
) -> None:
    patch = "diff --git a/a.py b/a.py\n"
    import hashlib

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / "events.jsonl").write_text("{}\n", encoding="utf-8")
    evidence_manifest = write_evidence_manifest(evidence_root)
    submission = SWESubmission(
        case_token="visible-1",
        subject_sha="a" * 40,
        subject_tree="b" * 40,
        base_commit="c" * 40,
        patch=patch,
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        events_sha256="d" * 64,
        task_sha256="e" * 64,
        subject_request_digest="1" * 64,
        visible_case_digest="2" * 64,
        source_snapshot_digest="3" * 64,
        config_digest="4" * 64,
        skills_digest="5" * 64,
        execution_ledger_digest="6" * 64,
        evidence_manifest_digest=str(evidence_manifest["record_digest"]),
        frozen_at="2026-07-12T00:00:00Z",
    )
    authority = SWESubmissionAuthority(tmp_path / "trusted")
    frozen = authority.freeze("swebench_verified", submission, evidence_root)
    before = frozen.identity()

    receipt = authority.noninterference_receipt(
        frozen,
        before,
        official_result_digest="f" * 64,
    )
    assert receipt["unchanged"] is True
    assert authority.load(frozen.root).submission == submission

    frozen.patch_path.chmod(0o600)
    frozen.patch_path.write_text("forged", encoding="utf-8")
    with pytest.raises(BenchmarkContractError, match="patch digest mismatch"):
        frozen.identity()
