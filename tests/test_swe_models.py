from __future__ import annotations

from pathlib import Path

import pytest

from autobugfix.eval.benchmarks.models import BenchmarkContractError
from autobugfix.eval.benchmarks.service import (
    EvalBenchmarkService,
    EvalBenchmarkServiceError,
)
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
    assert protocol.holdout_excluded_instances == (
        "formbricks__formbricks-6413",
    )
    assert protocol.codex_runtime.reasoning_effort == "low"
    assert protocol.codex_runtime.sdk_version == "0.144.4"
    assert protocol.codex_runtime.cli_version == "0.144.4"
    assert len(protocol.protocol_digest) == 64
    assert len(protocol.qualification_contract_digest) == 64
    assert len(protocol.subject_runtime_contract_digest) == 64
    assert protocol.verified_image_mode == "local-build"


def test_resume_protocol_uses_pinned_official_images() -> None:
    baseline = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    )
    resume = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2-resume-mvp-v2.yaml"
    )

    assert resume.verified_image_mode == "pinned-official-import"
    assert (
        resume.qualification_contract_digest
        != baseline.qualification_contract_digest
    )


def test_experiment_protocol_requires_explicit_holdout_exposure_ledger() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    ).to_dict()
    protocol["holdout"]["excluded_instances"] = []  # type: ignore[index]

    with pytest.raises(BenchmarkContractError, match="exposure exclusion ledger"):
        SWEExperimentProtocol.from_dict(protocol)


def test_experiment_protocol_rejects_upstream_drift() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    ).to_dict()
    protocol["upstreams"]["swebench_commit"] = "0" * 40  # type: ignore[index]

    with pytest.raises(BenchmarkContractError, match="upstream identity drift"):
        SWEExperimentProtocol.from_dict(protocol)


def test_qualification_contract_excludes_subject_treatment() -> None:
    baseline = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    )
    changed = baseline.to_dict()
    changed["codex_runtime"]["reasoning_effort"] = "medium"  # type: ignore[index]
    changed_protocol = SWEExperimentProtocol.from_dict(changed)

    assert changed_protocol.protocol_digest != baseline.protocol_digest
    assert (
        changed_protocol.subject_runtime_contract_digest
        != baseline.subject_runtime_contract_digest
    )
    assert (
        changed_protocol.qualification_contract_digest
        == baseline.qualification_contract_digest
    )


def test_eligible_qualification_requires_gold_and_null_evidence() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2.yaml"
    )
    image_id = "sha256:" + "a" * 64
    record = {
        "eligible": True,
        "official_attempts": [
            {
                "attempt": 1,
                "record_digest": "1" * 64,
                "record_path": "/gold-1.yaml",
                "resolved": True,
                "harness_error": "",
                "image_id": image_id,
            },
            {
                "attempt": 2,
                "record_digest": "2" * 64,
                "record_path": "/gold-2.yaml",
                "resolved": True,
                "harness_error": "",
                "image_id": image_id,
            },
        ],
        "null_attempt": {
            "record_digest": "3" * 64,
            "record_path": "/null.yaml",
            "resolved": False,
            "harness_error": "",
            "image_id": image_id,
        },
        "official_result_digest": "2" * 64,
        "official_result_path": "/gold-2.yaml",
        "image_id": image_id,
        "image_source_mode": "local-build",
        "source_path": "/source",
        "source_tree": "b" * 40,
        "source_digest": "c" * 64,
    }

    EvalBenchmarkService._validate_swe_qualification_semantics(
        record,
        protocol=protocol,
        adapter="swebench_verified",
    )

    record["null_attempt"] = {
        "record_digest": "3" * 64,
        "record_path": "/null.yaml",
        "resolved": True,
        "harness_error": "",
        "image_id": image_id,
    }
    with pytest.raises(EvalBenchmarkServiceError, match="contradicts"):
        EvalBenchmarkService._validate_swe_qualification_semantics(
            record,
            protocol=protocol,
            adapter="swebench_verified",
        )


def test_pinned_qualification_rejects_another_case_source_ref() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        ROOT / "benchmarks/swe-experiment-2-resume-mvp-v2.yaml"
    )
    image_id = "sha256:" + "a" * 64
    expected_digest = "b" * 64
    expected_ref = (
        "swebench/sweb.eval.x86_64.owner_1776_expected-1"
        f"@sha256:{expected_digest}"
    )
    record = {
        "eligible": True,
        "official_attempts": [
            {
                "attempt": attempt,
                "record_digest": str(attempt) * 64,
                "record_path": f"/gold-{attempt}.yaml",
                "resolved": True,
                "harness_error": "",
                "image_id": image_id,
            }
            for attempt in (1, 2)
        ],
        "null_attempt": {
            "record_digest": "3" * 64,
            "record_path": "/null.yaml",
            "resolved": False,
            "harness_error": "",
            "image_id": image_id,
        },
        "official_result_digest": "2" * 64,
        "official_result_path": "/gold-2.yaml",
        "image_id": image_id,
        "image_source_mode": "pinned-official-import",
        "image_source_ref": (
            "swebench/sweb.eval.x86_64.owner_1776_wrong-1"
            f"@sha256:{expected_digest}"
        ),
        "image_source_manifest_digest": expected_digest,
        "image_source_receipt_digest": "4" * 64,
        "image_source_receipt_path": "/import.yaml",
        "source_path": "/source",
        "source_tree": "c" * 40,
        "source_digest": "d" * 64,
    }

    with pytest.raises(EvalBenchmarkServiceError, match="pinned official"):
        EvalBenchmarkService._validate_swe_qualification_semantics(
            record,
            protocol=protocol,
            adapter="swebench_verified",
            expected_image_pin={
                "source_ref": expected_ref,
                "manifest_digest": expected_digest,
            },
        )


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
