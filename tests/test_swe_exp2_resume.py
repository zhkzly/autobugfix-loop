from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
import yaml

import autobugfix.eval.benchmarks.service as benchmark_service
from autobugfix.cli import build_parser, main
from autobugfix.config import load_config
from autobugfix.eval.benchmarks.exp2_records import Exp2ContractError
from autobugfix.eval.benchmarks.exp2_resume import (
    EXP2_WRITER_SKILL_PATH,
    Exp2CalibrationTerminalReceipt,
    Exp2CandidateBinding,
    Exp2CandidateTransitionReceipt,
    Exp2CaseAttemptIntent,
    Exp2CaseAttemptReceipt,
    Exp2OciImageIdentity,
    Exp2ResumeCoordinator,
    Exp2ResumeError,
    Exp2ResumeProtocol,
    Exp2ResumeStudyPlan,
)
from autobugfix.eval.benchmarks.exp2_runtime import (
    Exp2EvalAuthority,
    Exp2EvalAuthorityError,
    build_exp2_resume_protocol,
)
import autobugfix.eval.benchmarks.exp2_runtime as exp2_runtime
from autobugfix.eval.benchmarks.models import (
    digest_file,
    digest_payload,
    record_with_digest,
)
from autobugfix.eval.benchmarks.service import (
    EvalBenchmarkService,
    EvalBenchmarkServiceError,
)
from autobugfix.eval.benchmarks.subject_broker import SWESubjectBroker
from autobugfix.eval.benchmarks.swe_models import SWEExperimentProtocol
from autobugfix.eval.benchmarks.swe_runtime import SWERuntimeError
from autobugfix.git_utils import rev_parse
from autobugfix.operator.service import OperatorGovernanceService
from tests.helpers import make_service_project, run

_EMPTY_MEMORY_SPEC = """schema: autobugfix-exp2-empty-memory-fixture-spec-v1
fixture_id: exp2-empty-memory-v1
active_entries: []
approved_skill_entries: []
"""
_EMPTY_MEMORY_DIGEST = OperatorGovernanceService.exp2_empty_memory_digest()


def _private_empty_memory_root(path: Path) -> Path:
    for directory in (
        path,
        path / "active",
        path / "skills",
        path / "skills/approved",
    ):
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
    return path


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _qualification_record(case_id: str) -> dict[str, Any]:
    return record_with_digest(
        {
            "schema": "autobugfix-swe-qualification-v5",
            "instance_id": case_id,
            "image_id": "sha256:" + _digest(f"local:{case_id}"),
            "eligible": True,
        }
    )


def _candidate_transition(
    tmp_path: Path,
    *,
    path: str,
    execution_skill_digest: str,
) -> tuple[SimpleNamespace, Exp2CandidateTransitionReceipt]:
    study_id = "exp2-skill-transition"
    attribution_digest = _digest("attribution")
    source_digest = _digest("source-bundle")
    request_digest = _digest("request")
    integration_digest = _digest("integration")
    binding = Exp2CandidateBinding(
        study_id=study_id,
        parent_sha=_sha("h0"),
        parent_tree=_sha("h0-tree"),
        candidate_sha=_sha("candidate"),
        candidate_tree=_sha("candidate-tree"),
        candidate_diff_digest=_digest("candidate-diff"),
        allowlist_digest=digest_payload({"allowed_paths": [path]}),
        scope_digest=digest_payload(
            {"requested_paths": [path], "actual_paths": [path]}
        ),
        operator_policy_digest=_digest("operator-policy"),
        memory_fixture_digest=_EMPTY_MEMORY_DIGEST,
        operator_role_skill_digest=_digest("operator-skill"),
        execution_role_skill_digest=execution_skill_digest,
        runtime_digest=_digest("runtime"),
        request_digest=request_digest,
        integration_digest=integration_digest,
    )
    eval_binding_path = tmp_path / "binding.yaml"
    eval_binding = record_with_digest(
        {
            "schema": "autobugfix-operator-study-binding-v1",
            "kind": "CANDIDATE",
            "study_id": "operator-study",
            "line_id": "line-one",
            "subject_sha": binding.candidate_sha,
            "subject_tree": binding.candidate_tree,
        }
    )
    eval_binding_path.write_text(
        yaml.safe_dump(eval_binding, sort_keys=False), encoding="utf-8"
    )
    receipt = Exp2CandidateTransitionReceipt(
        study_id=study_id,
        issuer="operator-governance-service-v4",
        attribution_digest=attribution_digest,
        source_projection_bundle_digest=source_digest,
        operator_study_id="operator-study",
        line_id="line-one",
        request_id="request-one",
        request_digest=request_digest,
        grant_id="grant-one",
        grant_digest=_digest("grant"),
        writer_run_id="writer-one",
        writer_run_digest=_digest("writer"),
        fast_check_digest=_digest("fast"),
        full_check_digest=_digest("full"),
        integration_id="integration-one",
        integration_digest=integration_digest,
        usage_digest=_digest("usage"),
        eval_study_binding_path=str(eval_binding_path.resolve()),
        eval_study_binding_digest=str(eval_binding["record_digest"]),
        requested_paths=(path,),
        allowed_paths=(path,),
        actual_paths=(path,),
        binding=binding,
    )
    state = SimpleNamespace(
        state="CANDIDATE_TRANSITION_AWAITING",
        attribution=SimpleNamespace(
            record_digest=attribution_digest,
            execution_scope=(path,),
        ),
        source_bundle=SimpleNamespace(record_digest=source_digest),
        plan=SimpleNamespace(
            h0_subject_sha=_sha("h0"),
            h0_subject_tree=_sha("h0-tree"),
            operator_policy_digest=_digest("operator-policy"),
            memory_fixture_digest=_EMPTY_MEMORY_DIGEST,
            operator_role_skill_digest=_digest("operator-skill"),
            execution_role_skill_digest=_digest("execution-skill"),
            runtime_digest=_digest("runtime"),
        ),
        protocol=SimpleNamespace(execution_allowlist=(path,)),
    )
    return state, receipt


def test_candidate_transition_allows_bound_execution_skill_change(
    tmp_path: Path,
) -> None:
    path = ".agents/role-skills/execution/writer/autobugfix-writer/SKILL.md"
    state, receipt = _candidate_transition(
        tmp_path,
        path=path,
        execution_skill_digest=_digest("candidate-execution-skill"),
    )
    coordinator = Exp2ResumeCoordinator(tmp_path / "state", receipt.study_id)

    coordinator._validate_candidate_transition(state, receipt)


def test_candidate_transition_rejects_unexplained_execution_skill_drift(
    tmp_path: Path,
) -> None:
    state, receipt = _candidate_transition(
        tmp_path,
        path="src/autobugfix/runner.py",
        execution_skill_digest=_digest("candidate-execution-skill"),
    )
    coordinator = Exp2ResumeCoordinator(tmp_path / "state", receipt.study_id)

    with pytest.raises(Exp2ResumeError, match="actual scope"):
        coordinator._validate_candidate_transition(state, receipt)


def _protocol(*, qualified: bool = True) -> Exp2ResumeProtocol:
    pending = Exp2ResumeProtocol(
        protocol_id="exp2-resume-mvp-v2",
        dataset_revision=_sha("dataset"),
        scorer_digest=_digest("scorer"),
        runtime_digest=_digest("runtime"),
        memory_fixture_spec_digest=hashlib.sha256(
            _EMPTY_MEMORY_SPEC.encode()
        ).hexdigest(),
        memory_fixture_digest=_EMPTY_MEMORY_DIGEST,
        operator_policy_digest=_digest("operator-policy"),
        operator_role_skill_digest=_digest("operator-skill"),
        execution_role_skill_digest=_digest("execution-skill"),
        model="gpt-5.4-mini",
        reasoning_effort="low",
        execution_mode="protected",
        max_attempts=2,
        timeout_seconds=900,
        case_concurrency=1,
        execution_allowlist=(EXP2_WRITER_SKILL_PATH,),
    )
    if not qualified:
        return pending
    cases = (*pending.calibration_cases, *pending.h0_cases)
    images = tuple(
        Exp2OciImageIdentity(
            case_id=case.case_id,
            image=f"swebench/sweb.eval.x86_64.{case.case_id}:latest",
            qualification_digest=str(
                _qualification_record(case.case_id)["record_digest"]
            ),
            manifest_digest=_digest(f"manifest:{case.case_id}"),
            config_digest=_digest(f"config:{case.case_id}"),
            layer_digests=(_digest(f"layer:{case.case_id}"),),
            local_image_id=_digest(f"local:{case.case_id}"),
            rootfs_diff_ids=(_digest(f"diff-id:{case.case_id}"),),
        )
        for case in cases
    )
    return replace(
        pending,
        oci_images=images,
        qualification_status="qualified",
    )


class _ProtocolBuildService:
    def __init__(
        self,
        tmp_path: Path,
        *,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        pending = _protocol(qualified=False)
        cases = (*pending.calibration_cases, *pending.h0_cases)
        self.case_ids = tuple(case.case_id for case in cases)
        self.dataset_revision = _sha("qualified-dataset")
        self.manifest_record_digest = _digest("verified-image-manifest")
        self.config = SimpleNamespace(
            eval=SimpleNamespace(
                benchmarks=SimpleNamespace(
                    trusted_case_root=(tmp_path / "trusted-eval").resolve()
                )
            )
        )
        self.inspect_calls: list[tuple[str, str]] = []
        self.image_cases: dict[str, str] = {}
        self.qualifications: dict[str, dict[str, Any]] = {}
        self.pins: dict[str, dict[str, str]] = {}
        self.docker_data: dict[str, dict[str, Any]] = {}
        self.overrides = dict(overrides or {})
        trusted_root = self.config.eval.benchmarks.trusted_case_root
        for case in cases:
            case_id = case.case_id
            image = f"sweb.eval.x86_64.{case_id}:latest"
            manifest_digest = _digest(f"manifest:{case_id}")
            local_image_id = _digest(f"local:{case_id}")
            config_digest = _digest(f"config:{case_id}")
            layer_digest = _digest(f"layer:{case_id}")
            diff_id = _digest(f"diff:{case_id}")
            source_ref = f"registry.example/{case_id}@sha256:{manifest_digest}"
            receipt_path = trusted_root / "imports" / case_id / "receipt.yaml"
            import_receipt = record_with_digest(
                {
                    "schema": "autobugfix-swe-pinned-image-import-v1",
                    "instance_id": case_id,
                    "source_ref": source_ref,
                    "manifest_digest": manifest_digest,
                    "manifest_record_digest": self.manifest_record_digest,
                    "local_image": image,
                    "local_image_id": f"sha256:{local_image_id}",
                    "config_digest": config_digest,
                    "layer_digests": [layer_digest],
                    "rootfs_diff_ids": [diff_id],
                    "platform": "linux/amd64",
                }
            )
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(
                yaml.safe_dump(import_receipt, sort_keys=False), encoding="utf-8"
            )
            qualification = record_with_digest(
                {
                    "schema": "autobugfix-swe-qualification-v5",
                    "instance_id": case_id,
                    "repository": case.repository,
                    "base_commit": _sha(f"base:{case_id}"),
                    "dataset_revision": self.dataset_revision,
                    "image": image,
                    "image_id": f"sha256:{local_image_id}",
                    "eligible": True,
                    "source_tree": _sha(f"tree:{case_id}"),
                    "source_digest": _digest(f"source:{case_id}"),
                    "image_source_mode": "pinned-official-import",
                    "image_source_ref": source_ref,
                    "image_source_manifest_digest": manifest_digest,
                    "image_source_receipt_digest": import_receipt["record_digest"],
                    "image_source_receipt_path": str(receipt_path),
                }
            )
            self.qualifications[case_id] = qualification
            self.pins[case_id] = {
                "source_ref": source_ref,
                "manifest_digest": manifest_digest,
            }
            self.image_cases[image] = case_id
            self.docker_data[case_id] = {
                "manifest_digest": manifest_digest,
                "local_image_id": local_image_id,
                "config_digest": config_digest,
                "layer_digest": layer_digest,
                "diff_id": diff_id,
            }

    def exp2_runtime_identity(self, protocol_path: Path) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-eval-runtime-identity-v2",
                "swe_protocol_sha256": digest_file(protocol_path),
                "dataset_revision": self.dataset_revision,
                "scorer_digest": _digest("scorer"),
                "runtime_digest": _digest("runtime"),
                "verified_image_instance_ids": list(self.case_ids),
                "verified_image_pins": [
                    {"instance_id": case_id, **self.pins[case_id]}
                    for case_id in self.case_ids
                ],
                "verified_image_manifest_digest": self.manifest_record_digest,
            }
        )

    def exp2_qualification_receipt(
        self,
        protocol_path: Path,
        instance_id: str,
    ) -> dict[str, Any]:
        del protocol_path
        payload = dict(self.qualifications[instance_id])
        payload.pop("record_digest")
        payload.update(self.overrides.get(instance_id, {}))
        return record_with_digest(payload)

    def inspect_swe(self, adapter: str, instance_id: str) -> dict[str, Any]:
        self.inspect_calls.append((adapter, instance_id))
        raise AssertionError("protocol construction must not inspect SWE instances")


def _build_protocol_from_qualified_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Exp2ResumeProtocol, _ProtocolBuildService, list[str]]:
    service = _ProtocolBuildService(tmp_path, overrides=overrides)

    class _ProtocolBuildOperator:
        def __init__(self, project_root: Path) -> None:
            del project_root

        def exp2_role_skill_digests(self, **_: Any) -> dict[str, str]:
            return {
                "operator_role_skill_digest": _digest("operator-role-skill"),
                "execution_role_skill_digest": _digest("execution-role-skill"),
            }

        def exp2_empty_memory_digest(self) -> str:
            return _EMPTY_MEMORY_DIGEST

        def governance_context(self) -> dict[str, str]:
            return {"digest": _digest("operator-policy")}

    docker_images: list[str] = []

    def fake_run_command(command: list[str], **kwargs: Any) -> SimpleNamespace:
        image = command[3]
        docker_images.append(image)
        case_id = service.image_cases[image]
        metadata = service.docker_data[case_id]
        stdout_path = Path(kwargs["artifact_dir"]) / "stdout.json"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(
            json.dumps(
                {
                    "Id": f"sha256:{metadata['local_image_id']}",
                    "Descriptor": {
                        "digest": f"sha256:{metadata['manifest_digest']}"
                    },
                    "RootFS": {"Layers": [f"sha256:{metadata['diff_id']}"]},
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(passed=True, stdout_path=stdout_path)

    monkeypatch.setattr(exp2_runtime, "EvalBenchmarkService", lambda _: service)
    monkeypatch.setattr(
        exp2_runtime, "OperatorGovernanceService", _ProtocolBuildOperator
    )
    monkeypatch.setattr(exp2_runtime.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(exp2_runtime, "run_command", fake_run_command)
    memory_fixture = tmp_path / "empty-memory.yaml"
    memory_fixture.write_text(_EMPTY_MEMORY_SPEC, encoding="utf-8")
    protocol = build_exp2_resume_protocol(
        tmp_path,
        protocol_id="receipt-only-build",
        swe_protocol_path=(
            Path(__file__).resolve().parents[1]
            / "benchmarks/swe-experiment-2-resume-mvp-v2.yaml"
        ),
        empty_memory_fixture_path=memory_fixture,
        execution_allowlist=(EXP2_WRITER_SKILL_PATH,),
        artifact_root=tmp_path / "protocol-artifacts",
    )
    return protocol, service, docker_images


def test_protocol_build_uses_replayed_qualification_metadata_without_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, service, docker_images = _build_protocol_from_qualified_receipts(
        tmp_path, monkeypatch
    )

    assert protocol.qualification_status == "qualified"
    assert service.inspect_calls == []
    assert docker_images == [
        service.qualifications[case_id]["image"] for case_id in service.case_ids
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"pallets__flask-5014": {"repository": "wrong/repository"}},
        {"pallets__flask-5014": {"dataset_revision": _sha("wrong-dataset")}},
    ],
)
def test_protocol_build_rejects_mismatched_qualification_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: Mapping[str, Mapping[str, Any]],
) -> None:
    with pytest.raises(
        Exp2EvalAuthorityError, match="replay-qualified case metadata drift"
    ):
        _build_protocol_from_qualified_receipts(
            tmp_path, monkeypatch, overrides=overrides
        )


def _exp2_qualified_instance_inputs(
    *,
    qualification_overrides: Mapping[str, Any] | None = None,
) -> tuple[SimpleNamespace, dict[str, Any], str]:
    instance_id = "pallets__flask-5014"
    snapshot_sha = _digest("verified-snapshot")
    row = {
        "instance_id": instance_id,
        "repo": "pallets/flask",
        "base_commit": _sha("flask-base"),
        "problem_statement": "Flask should preserve an explicit empty blueprint name.",
        "hints_text": "Public reproduction steps.",
        "created_at": "2023-01-01T00:00:00Z",
        "patch": "diff --git a/flask/app.py b/flask/app.py\n",
        "test_patch": "diff --git a/tests/test_blueprints.py b/tests/test_blueprints.py\n",
        "FAIL_TO_PASS": ["tests/test_blueprints.py::test_empty_name"],
        "PASS_TO_PASS": ["tests/test_basic.py::test_basic"],
        "version": "2.2",
    }

    class _Runner:
        adapter = "swebench_verified"
        snapshot = SimpleNamespace(
            dataset="princeton-nlp/SWE-bench_Verified",
            revision=_sha("verified-revision"),
            sha256=snapshot_sha,
        )
        runtime = SimpleNamespace(
            evaluator_runtime_id="sha256:" + _digest("evaluator"),
        )

        def __init__(self) -> None:
            self.load_instance_calls = 0

        def _row(self, selected: str) -> dict[str, Any]:
            assert selected == instance_id
            return dict(row)

        def load_instance(self, *_: Any, **__: Any) -> None:
            self.load_instance_calls += 1
            raise AssertionError("Exp2 must not re-run Verified inspection")

    qualification_payload: dict[str, Any] = {
        "schema": "autobugfix-swe-qualification-v5",
        "qualification_contract_digest": _digest("qualification-contract"),
        "adapter": "swebench_verified",
        "role": "optimization",
        "instance_id": instance_id,
        "repository": row["repo"],
        "base_commit": row["base_commit"],
        "language": "py",
        "dataset": _Runner.snapshot.dataset,
        "dataset_revision": _Runner.snapshot.revision,
        "dataset_snapshot_sha256": snapshot_sha,
        "evaluator_runtime_id": _Runner.runtime.evaluator_runtime_id,
        "image": f"sweb.eval.x86_64.{instance_id}:latest",
        "eligible": True,
    }
    qualification_payload.update(qualification_overrides or {})
    return _Runner(), record_with_digest(qualification_payload), instance_id


def test_exp2_instance_reconstruction_never_calls_verified_inspection() -> None:
    runner, qualification, instance_id = _exp2_qualified_instance_inputs()
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)

    instance, replayed = service._exp2_verified_instance_from_qualification(
        runner,
        instance_id,
        qualification,
        expected_qualification_contract_digest=str(
            qualification["qualification_contract_digest"]
        ),
    )

    assert runner.load_instance_calls == 0
    assert replayed == qualification
    assert instance.instance_id == instance_id
    assert instance.repository == qualification["repository"]
    assert instance.base_commit == qualification["base_commit"]
    assert instance.language == qualification["language"]
    assert instance.docker_image == qualification["image"]
    assert instance.problem_statement.startswith("Flask should preserve")
    assert instance.fail_to_pass == (
        "tests/test_blueprints.py::test_empty_name",
    )


@pytest.mark.parametrize(
    "qualification_overrides",
    [
        {"repository": "wrong/repository"},
        {"dataset_snapshot_sha256": _digest("drifted-snapshot")},
        {"evaluator_runtime_id": "sha256:" + _digest("drifted-evaluator")},
        {"image": "sweb.eval.x86_64.wrong:latest"},
    ],
)
def test_exp2_instance_reconstruction_rejects_qualification_drift(
    qualification_overrides: Mapping[str, Any],
) -> None:
    runner, qualification, instance_id = _exp2_qualified_instance_inputs(
        qualification_overrides=qualification_overrides
    )
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)

    with pytest.raises(
        EvalBenchmarkServiceError,
        match="Exp2 qualification instance metadata drift",
    ):
        service._exp2_verified_instance_from_qualification(
            runner,
            instance_id,
            qualification,
            expected_qualification_contract_digest=_digest(
                "qualification-contract"
            ),
        )

    assert runner.load_instance_calls == 0


def test_exp2_instance_reconstruction_wraps_snapshot_row_runtime_error() -> None:
    runner, qualification, instance_id = _exp2_qualified_instance_inputs()
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)

    def fail_row(_: str) -> dict[str, Any]:
        raise SWERuntimeError("pinned dataset row is unavailable")

    runner._row = fail_row
    with pytest.raises(
        EvalBenchmarkServiceError,
        match="Exp2 qualification instance metadata is invalid",
    ):
        service._exp2_verified_instance_from_qualification(
            runner,
            instance_id,
            qualification,
            expected_qualification_contract_digest=_digest(
                "qualification-contract"
            ),
        )

    assert runner.load_instance_calls == 0


def test_exp2_instance_reconstruction_wraps_snapshot_row_os_error() -> None:
    runner, qualification, instance_id = _exp2_qualified_instance_inputs()
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)

    def fail_row(_: str) -> dict[str, Any]:
        raise OSError("pinned dataset snapshot cannot be read")

    runner._row = fail_row
    with pytest.raises(
        EvalBenchmarkServiceError,
        match="Exp2 qualification instance metadata is invalid",
    ):
        service._exp2_verified_instance_from_qualification(
            runner,
            instance_id,
            qualification,
            expected_qualification_contract_digest=_digest(
                "qualification-contract"
            ),
        )

    assert runner.load_instance_calls == 0


def _real_exp2_swe_protocol_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "benchmarks/swe-experiment-2-resume-mvp-v2.yaml"
    )


def _exp2_replay_runner(
    protocol: SWEExperimentProtocol,
    case_ids: tuple[str, ...],
) -> tuple[SimpleNamespace, dict[str, dict[str, Any]]]:
    snapshot = SimpleNamespace(
        dataset="princeton-nlp/SWE-bench_Verified",
        revision=_sha("verified-revision"),
        sha256=_digest("verified-snapshot"),
    )
    runtime = SimpleNamespace(
        evaluator_runtime_id="sha256:" + _digest("evaluator"),
        config=SimpleNamespace(swebench_commit=_sha("swebench-harness")),
    )
    rows: dict[str, dict[str, Any]] = {}
    qualifications: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        repository = f"example/{case_id}"
        image = f"sweb.eval.x86_64.{case_id}:latest"
        rows[case_id] = {
            "instance_id": case_id,
            "repo": repository,
            "base_commit": _sha(f"base:{case_id}"),
            "problem_statement": f"Public issue for {case_id}.",
            "hints_text": "Public reproduction steps.",
            "created_at": "2023-01-01T00:00:00Z",
            "patch": "diff --git a/src.py b/src.py\n",
            "test_patch": "diff --git a/tests.py b/tests.py\n",
            "FAIL_TO_PASS": ["tests.py::test_case"],
            "PASS_TO_PASS": ["tests.py::test_stable"],
            "version": "1.0",
        }
        qualifications[case_id] = record_with_digest(
            {
                "schema": "autobugfix-swe-qualification-v5",
                "qualification_contract_digest": protocol.qualification_contract_digest,
                "adapter": "swebench_verified",
                "role": "optimization",
                "instance_id": case_id,
                "repository": repository,
                "base_commit": rows[case_id]["base_commit"],
                "language": "py",
                "dataset": snapshot.dataset,
                "dataset_revision": snapshot.revision,
                "dataset_snapshot_sha256": snapshot.sha256,
                "evaluator_runtime_id": runtime.evaluator_runtime_id,
                "image": image,
                "image_id": "sha256:" + _digest(f"image:{case_id}"),
                "source_path": f"/qualified/{case_id}",
                "source_tree": _sha(f"source-tree:{case_id}"),
                "source_digest": _digest(f"source:{case_id}"),
                "docker_authority_digest": _digest("docker-authority"),
                "eligible": True,
            }
        )
    runner = SimpleNamespace(
        adapter="swebench_verified",
        snapshot=snapshot,
        runtime=runtime,
        load_instance_calls=0,
        row_calls=[],
    )

    def row(selected: str) -> dict[str, Any]:
        runner.row_calls.append(selected)
        return dict(rows[selected])

    def load_instance(*_: Any, **__: Any) -> None:
        runner.load_instance_calls += 1
        raise AssertionError("Exp2 must not invoke Verified inspection")

    def subject_runtime_identity(_: Any) -> dict[str, Any]:
        return record_with_digest(
            {"schema": "test-exp2-subject-runtime-v1"}
        )

    runner._row = row
    runner.load_instance = load_instance
    runner.runtime.subject_runtime_identity = subject_runtime_identity
    return runner, qualifications


def test_exp2_development_replay_branch_skips_inspection_but_generic_path_keeps_it(
    tmp_path: Path,
) -> None:
    protocol_path = _real_exp2_swe_protocol_path()
    protocol = SWEExperimentProtocol.from_yaml(protocol_path)
    case_id = "pallets__flask-5014"
    runner, qualifications = _exp2_replay_runner(protocol, (case_id,))
    trusted_root = tmp_path / "trusted-eval"
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(trusted_case_root=trusted_root)
        )
    )
    service._swe_adapter = lambda _: runner
    materialized_cases: list[str] = []

    def validate_source(
        _: Any,
        instance: Any,
        qualification: Mapping[str, Any],
        __: Path,
    ) -> SimpleNamespace:
        materialized_cases.append(instance.instance_id)
        return SimpleNamespace(
            source_digest=qualification["source_digest"],
            image_id=qualification["image_id"],
        )

    class _GenericInspectionCalled(RuntimeError):
        pass

    class _StopAfterReplay(RuntimeError):
        pass

    def generic_load_instance(*_: Any, **__: Any) -> None:
        runner.load_instance_calls += 1
        raise _GenericInspectionCalled

    class _Store:
        @staticmethod
        def write_swe_record(*_: Any) -> Path:
            raise _StopAfterReplay

    runner.load_instance = generic_load_instance
    service._validate_swe_qualification_source = validate_source
    service.store = _Store()

    with pytest.raises(_GenericInspectionCalled):
        service._run_swe_development_case(
            protocol_path,
            "swebench_verified",
            case_id,
            run_id="generic-development",
            out_root=trusted_root / "runs",
            exp2_qualification=None,
        )

    with pytest.raises(_StopAfterReplay):
        service._run_swe_development_case(
            protocol_path,
            "swebench_verified",
            case_id,
            run_id="exp2-calibration",
            out_root=trusted_root / "runs",
            exp2_qualification=qualifications[case_id],
        )

    observed_calibration_qualifications: list[Mapping[str, Any]] = []

    def run_calibration_branch(*_: Any, **kwargs: Any) -> None:
        observed_calibration_qualifications.append(
            kwargs["exp2_qualification"]
        )
        raise _StopAfterReplay

    service.exp2_qualification_receipt = lambda _path, _instance: qualifications[
        case_id
    ]
    service._run_swe_development_case = run_calibration_branch
    with pytest.raises(_StopAfterReplay):
        service.run_swe_exp2_calibration_case(
            protocol_path,
            adapter="swebench_verified",
            instance_id=case_id,
            run_id="calibration-wrapper",
            memory_root=_private_empty_memory_root(tmp_path / "empty-memory"),
        )

    assert runner.load_instance_calls == 1
    assert runner.row_calls == [case_id]
    assert materialized_cases == [case_id]
    assert observed_calibration_qualifications == [qualifications[case_id]]


def test_exp2_calibration_memory_must_be_the_validated_fixture_root(
    tmp_path: Path,
) -> None:
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(
                trusted_case_root=(tmp_path / "trusted-eval").resolve()
            )
        )
    )
    service.exp2_qualification_receipt = (
        lambda _path, _instance: _qualification_record("pallets__flask-5014")
    )
    captured: dict[str, Any] = {}

    class _StopAfterCapture(RuntimeError):
        pass

    def development_case(*_: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        raise _StopAfterCapture

    service._run_swe_development_case = development_case
    fixture_root = _private_empty_memory_root(tmp_path / "empty-memory")

    with pytest.raises(_StopAfterCapture):
        service.run_swe_exp2_calibration_case(
            _real_exp2_swe_protocol_path(),
            adapter="swebench_verified",
            instance_id="pallets__flask-5014",
            run_id="calibration-memory",
            memory_root=fixture_root,
        )
    assert captured["memory_snapshot"] == fixture_root

    stray_root = tmp_path / "stray-memory"
    stray_root.mkdir()
    (stray_root / "active").mkdir()
    (stray_root / "notes.txt").write_text("not a fixture\n", encoding="utf-8")
    captured.clear()
    with pytest.raises(
        EvalBenchmarkServiceError, match="frozen empty fixture"
    ):
        service.run_swe_exp2_calibration_case(
            _real_exp2_swe_protocol_path(),
            adapter="swebench_verified",
            instance_id="pallets__flask-5014",
            run_id="calibration-memory",
            memory_root=stray_root,
        )
    assert captured == {}


def test_exp2_development_case_threads_memory_snapshot_to_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = _real_exp2_swe_protocol_path()
    protocol = SWEExperimentProtocol.from_yaml(protocol_path)
    case_id = "pallets__flask-5014"
    runner, qualifications = _exp2_replay_runner(protocol, (case_id,))
    trusted_root = tmp_path / "trusted-eval"

    def validate_source(
        _: Any,
        instance: Any,
        qualification: Mapping[str, Any],
        __: Path,
    ) -> SimpleNamespace:
        del instance
        return SimpleNamespace(
            source_digest=qualification["source_digest"],
            image_id=qualification["image_id"],
        )

    class _Store:
        @staticmethod
        def write_swe_record(*_: Any, **__: Any) -> Path:
            return trusted_root / "visible" / "record.yaml"

    class _StopAtBroker(RuntimeError):
        pass

    captured: dict[str, Any] = {}

    class _BrokerStub:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def run(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            raise _StopAtBroker

    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.project_root = tmp_path
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(trusted_case_root=trusted_root)
        )
    )
    service._swe_adapter = lambda _: runner
    service._validate_swe_qualification_source = validate_source
    service.store = _Store()
    monkeypatch.setattr(benchmark_service, "SWESubjectBroker", _BrokerStub)
    monkeypatch.setattr(
        benchmark_service, "rev_parse", lambda *_: _sha("h0-subject-tree")
    )
    fixture_root = _private_empty_memory_root(tmp_path / "empty-memory")

    with pytest.raises(_StopAtBroker):
        service._run_swe_development_case(
            protocol_path,
            "swebench_verified",
            case_id,
            run_id="exp2-calibration",
            out_root=trusted_root / "runs",
            exp2_qualification=qualifications[case_id],
            memory_snapshot=fixture_root,
        )

    assert captured["memory_snapshot"] == fixture_root


def test_exp2_manifest_preparation_replays_all_qualified_cases_without_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = _real_exp2_swe_protocol_path()
    protocol = SWEExperimentProtocol.from_yaml(protocol_path)
    selected = tuple(item.instance_id for item in protocol.optimization_cases)
    runner, qualifications = _exp2_replay_runner(protocol, selected)
    trusted_root = tmp_path / "trusted-eval"
    source_checks: list[str] = []

    def validate_source(
        _: Any,
        instance: Any,
        qualification: Mapping[str, Any],
        __: Path,
    ) -> SimpleNamespace:
        source_checks.append(instance.instance_id)
        return SimpleNamespace(
            source_digest=qualification["source_digest"],
            image_id=qualification["image_id"],
        )

    class _Store:
        manifest: dict[str, Any]
        rows: list[dict[str, Any]]

        def write_visible_yaml(
            self,
            _: str,
            __: str,
            payload: Mapping[str, Any],
        ) -> Path:
            self.manifest = dict(payload)
            return trusted_root / "visible" / "manifest.yaml"

        def write_visible_jsonl_rows(
            self,
            _: str,
            __: str,
            rows: list[dict[str, Any]],
        ) -> Path:
            self.rows = list(rows)
            return trusted_root / "visible" / "optimization.jsonl"

        @staticmethod
        def write_swe_record(*_: Any) -> Path:
            return trusted_root / "swe" / "preparation.yaml"

    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.project_root = tmp_path
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(trusted_case_root=trusted_root)
        )
    )
    service._swe_adapter = lambda _: runner
    service._swe_qualification_pool = lambda _protocol, _adapter: list(
        qualifications.values()
    )
    service._validate_swe_qualification_source = validate_source
    service.guard_authority = lambda: SimpleNamespace(
        to_dict=lambda: record_with_digest(
            {"schema": "test-exp2-guard-identity-v1"}
        )
    )
    store = _Store()
    service.store = store
    monkeypatch.setattr(
        benchmark_service,
        "rev_parse",
        lambda *_: _sha("h0-subject-tree"),
    )

    prepared = service.prepare_exp2_resume_manifest(protocol_path)

    assert prepared["optimization_count"] == len(selected)
    assert runner.load_instance_calls == 0
    assert runner.row_calls == list(selected)
    assert source_checks == list(selected)
    assert all(
        set(case)
        == {
            "schema",
            "benchmark_instance_id",
            "qualification_digest",
            "visible_case",
            "record_digest",
        }
        for case in store.rows
    )
    assert all(
        "gold_patch" not in case["visible_case"]
        and "test_patch" not in case["visible_case"]
        for case in store.rows
    )


def test_exp2_formal_run_replays_current_qualification_and_binds_protocol(
    tmp_path: Path,
) -> None:
    protocol_path = _real_exp2_swe_protocol_path()
    protocol = SWEExperimentProtocol.from_yaml(protocol_path)
    case_id = protocol.optimization_cases[0].instance_id
    runner, qualifications = _exp2_replay_runner(protocol, (case_id,))
    qualification = qualifications[case_id]
    trusted_root = tmp_path / "trusted-eval"
    source_checks: list[str] = []

    def validate_source(
        _: Any,
        instance: Any,
        qualified: Mapping[str, Any],
        __: Path,
    ) -> SimpleNamespace:
        source_checks.append(instance.instance_id)
        return SimpleNamespace(
            source_digest=qualified["source_digest"],
            image_id=qualified["image_id"],
        )

    code_identity = benchmark_service.GuardCodeIdentity(
        trusted_ref="refs/heads/main",
        trusted_commit=_sha("trusted-commit"),
        source_tree=_sha("trusted-tree"),
        machine_constitution_digest=_digest("constitution"),
        harness_digest=_digest("harness"),
    )
    visible = record_with_digest(
        {
            "schema_version": 1,
            "case_token": "opt-replay-case",
            "benchmark": "swebench_verified",
            "dataset_revision": runner.snapshot.revision,
            "harness_commit": runner.runtime.config.swebench_commit,
            "repository": qualification["repository"],
            "base_commit": qualification["base_commit"],
            "language": qualification["language"],
            "task_type": "bugfix",
            "problem_statement": "Public issue for the formal replay.",
            "public_hints": [],
            "attachments": [],
            "first_wave": 3,
            "source_snapshot_digest": qualification["source_digest"],
            "verifier_profile": "swe-visible-v1",
        }
    )
    selected = record_with_digest(
        {
            "schema": "autobugfix-swe-optimization-case-v1",
            "benchmark_instance_id": case_id,
            "qualification_digest": qualification["record_digest"],
            "visible_case": visible,
        }
    )
    public = {
        "protocol_digest": protocol.protocol_digest,
        "qualification_contract_digest": protocol.qualification_contract_digest,
        "codex_runtime": protocol.codex_runtime.to_dict(),
        "subject_runtime": record_with_digest(
            {"schema": "test-exp2-subject-runtime-v1"}
        ),
        "evaluator_runtime_id": runner.runtime.evaluator_runtime_id,
        "guard": {"code_identity": code_identity.to_dict()},
        "optimization_cases": [selected],
    }
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(trusted_case_root=trusted_root)
        )
    )
    service._load_swe_public_manifest = lambda _: public
    service._load_swe_study_binding = lambda *_args, **_kwargs: {
        "kind": "BASELINE",
        "study_id": "study",
    }
    service.guard_authority = lambda: code_identity
    service._swe_adapter = lambda _: runner
    service._swe_qualification_pool = lambda _protocol, _adapter: [qualification]
    service._validate_swe_qualification_source = validate_source
    captured: dict[str, Any] = {}

    def execute_formal(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "executed"}

    service._execute_formal_swe_case = execute_formal

    mismatched_payload = dict(selected)
    mismatched_payload.pop("record_digest")
    mismatched_payload["qualification_digest"] = _digest("different-record")
    public["optimization_cases"] = [record_with_digest(mismatched_payload)]
    with pytest.raises(EvalBenchmarkServiceError, match="qualification drift"):
        service.run_swe_exp2_case(
            tmp_path / "visible.yaml",
            swe_protocol_path=protocol_path,
            case_selector=case_id,
            study_binding_path=tmp_path / "binding.yaml",
            out_root=trusted_root / "formal-runs",
            run_id="formal-mismatch",
            expected_binding_kinds={"BASELINE"},
        )

    drifted_protocol_data = yaml.safe_load(
        protocol_path.read_text(encoding="utf-8")
    )
    assert isinstance(drifted_protocol_data, dict)
    drifted_protocol_data["protocol_id"] = "different-exp2-protocol"
    drifted_protocol_path = tmp_path / "drifted-swe-protocol.yaml"
    drifted_protocol_path.write_text(
        yaml.safe_dump(drifted_protocol_data, sort_keys=False),
        encoding="utf-8",
    )
    public["optimization_cases"] = [selected]
    with pytest.raises(EvalBenchmarkServiceError, match="manifest protocol drift"):
        service.run_swe_exp2_case(
            tmp_path / "visible.yaml",
            swe_protocol_path=drifted_protocol_path,
            case_selector=case_id,
            study_binding_path=tmp_path / "binding.yaml",
            out_root=trusted_root / "formal-runs",
            run_id="formal-protocol-drift",
            expected_binding_kinds={"BASELINE"},
        )

    result = service.run_swe_exp2_case(
        tmp_path / "visible.yaml",
        swe_protocol_path=protocol_path,
        case_selector=case_id,
        study_binding_path=tmp_path / "binding.yaml",
        out_root=trusted_root / "formal-runs",
        run_id="formal-replay",
        expected_binding_kinds={"BASELINE"},
    )

    assert result == {"status": "executed"}
    assert runner.load_instance_calls == 0
    assert runner.row_calls == [case_id]
    assert source_checks == [case_id]
    assert captured["instance"].instance_id == case_id
    assert captured["materialized"].source_digest == qualification["source_digest"]


def test_exp2_scorer_retry_replays_qualification_without_verified_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_path = _real_exp2_swe_protocol_path()
    protocol = SWEExperimentProtocol.from_yaml(protocol_path)
    case_id = "pallets__flask-5014"
    runner, qualifications = _exp2_replay_runner(protocol, (case_id,))
    qualification = qualifications[case_id]
    trusted_root = tmp_path / "trusted-eval"
    submission_digest = _digest("frozen-submission")
    source_root = trusted_root / "source-run"
    subject_root = source_root / "subject-run"
    evidence_root = subject_root / "frozen-execution-evidence"
    command = record_with_digest({"schema": "test-command-v1"})
    broker = record_with_digest(
        {
            "schema": "test-exp2-broker-v1",
            "submission_digest": submission_digest,
            "executed_subject_sha": _sha("h0-subject"),
            "executed_subject_tree": _sha("h0-tree"),
            "execution_mode": "protected",
            "sdk_call_receipt_digests": [_digest("sdk-call")],
            "command": command,
            "task_worktree_path": str((subject_root / "task").resolve()),
        }
    )
    ledger = record_with_digest({"schema": "test-exp2-ledger-v1"})
    subject_binding = record_with_digest(
        {
            "schema": "test-exp2-subject-binding-v1",
            "memory_digest": _EMPTY_MEMORY_DIGEST,
        }
    )
    subject_root.mkdir(parents=True)
    evidence_root.mkdir()
    (subject_root / "broker-result.yaml").write_text(
        yaml.safe_dump(broker, sort_keys=False),
        encoding="utf-8",
    )
    (evidence_root / "execution-ledger.json").write_text(
        json.dumps(ledger),
        encoding="utf-8",
    )
    (evidence_root / "subject-binding.yaml").write_text(
        yaml.safe_dump(subject_binding, sort_keys=False),
        encoding="utf-8",
    )
    (
        trusted_root
        / "swe"
        / "submissions"
        / "swebench_verified"
        / "authority"
        / submission_digest
    ).mkdir(parents=True)

    frozen_submission = SimpleNamespace(
        record={"record_digest": submission_digest},
        base_commit=qualification["base_commit"],
        subject_sha=_sha("h0-subject"),
        subject_tree=_sha("h0-tree"),
    )
    frozen = SimpleNamespace(
        submission=frozen_submission,
        identity=lambda: {"submission_digest": submission_digest},
    )

    class _SubmissionAuthority:
        def __init__(self, _: Path):
            pass

        @staticmethod
        def load(_: Path) -> SimpleNamespace:
            return frozen

    class _OfficialResult:
        resolved = False
        harness_error = False

        @staticmethod
        def to_dict() -> dict[str, Any]:
            return record_with_digest(
                {
                    "schema": "autobugfix-swe-official-result-v1",
                    "instance_id": case_id,
                    "resolved": False,
                    "harness_error": False,
                }
            )

    def image_id(_: Any, __: Path, *, allow_pull: bool) -> str:
        assert allow_pull is False
        return str(qualification["image_id"])

    def score(instance: Any, *_: Any, **__: Any) -> _OfficialResult:
        assert instance.instance_id == case_id
        runner.score_calls += 1
        return _OfficialResult()

    def noninterference(
        _: Any,
        __: Mapping[str, Any],
        *,
        official_result_digest: str,
    ) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-swe-noninterference-v1",
                "submission_digest": submission_digest,
                "official_result_digest": official_result_digest,
                "unchanged": True,
            }
        )

    runner.image_id = image_id
    runner.score = score
    runner.score_calls = 0
    service = EvalBenchmarkService.__new__(EvalBenchmarkService)
    service.config = SimpleNamespace(
        eval=SimpleNamespace(
            benchmarks=SimpleNamespace(trusted_case_root=trusted_root)
        )
    )
    service._swe_adapter = lambda _: runner
    service.exp2_qualification_receipt = lambda _path, _instance: qualification
    service.submission_noninterference = noninterference
    monkeypatch.setattr(
        benchmark_service,
        "SWESubmissionAuthority",
        _SubmissionAuthority,
    )

    report = service.rescore_swe_exp2_submission(
        protocol_path,
        instance_id=case_id,
        submission_digest=submission_digest,
        source_run_root=source_root,
        out_root=trusted_root / "retry-runs",
        run_id="retry-replay",
    )

    assert report["schema"] == "autobugfix-exp2-scorer-retry-report-v2"
    assert runner.load_instance_calls == 0
    assert runner.row_calls == [case_id]
    assert runner.score_calls == 1


def _write_protocol(tmp_path: Path, protocol: Exp2ResumeProtocol) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "source-protocol.yaml"
    path.write_text(
        yaml.safe_dump(protocol.to_dict(), sort_keys=False),
        encoding="utf-8",
    )
    return path


def _plan(
    tmp_path: Path,
    protocol: Exp2ResumeProtocol,
    *,
    study_id: str,
    study_kind: str = "calibration",
    calibration_terminal: Path | None = None,
    project_root: Path | None = None,
    apparatus_receipt_path: Path | None = None,
) -> Exp2ResumeStudyPlan:
    protocol_path = _write_protocol(tmp_path, protocol)
    swe_protocol_path = tmp_path / "swe-protocol.yaml"
    swe_protocol_path.write_text(
        "schema_version: 3\nprotocol_id: test-swe-protocol\n",
        encoding="utf-8",
    )
    memory_fixture_path = tmp_path / "empty-memory-fixture.yaml"
    memory_fixture_path.write_text(_EMPTY_MEMORY_SPEC, encoding="utf-8")
    source_check = record_with_digest(
        {
            "schema": "autobugfix-exp2-source-check-v2",
            "name": "unit",
            "argv": ["pytest"],
            "passed": True,
            "exit_code": 0,
        }
    )
    source_check_path = tmp_path / "source-check.yaml"
    source_check_path.write_text(
        yaml.safe_dump(source_check, sort_keys=False),
        encoding="utf-8",
    )
    if apparatus_receipt_path is None:
        apparatus_sha = (
            rev_parse(project_root, "HEAD")
            if project_root is not None
            else _sha("apparatus")
        )
        apparatus_tree = (
            rev_parse(project_root, "HEAD^{tree}")
            if project_root is not None
            else _sha("apparatus-tree")
        )
        apparatus_payload = {
            "schema": "autobugfix-exp2-apparatus-receipt-v2",
            "apparatus_sha": apparatus_sha,
            "apparatus_tree": apparatus_tree,
            "protocol_digest": protocol.record_digest,
            "swe_protocol_sha256": digest_file(swe_protocol_path),
            "scorer_digest": protocol.scorer_digest,
            "runtime_digest": protocol.runtime_digest,
            "memory_fixture_spec_digest": protocol.memory_fixture_spec_digest,
            "memory_fixture_digest": protocol.memory_fixture_digest,
            "operator_policy_digest": protocol.operator_policy_digest,
            "operator_role_skill_digest": protocol.operator_role_skill_digest,
            "execution_role_skill_digest": protocol.execution_role_skill_digest,
            "source_checks": [
                {
                    "path": str(source_check_path.resolve()),
                    "sha256": digest_file(source_check_path),
                    "size": source_check_path.stat().st_size,
                    "receipt_digest": source_check["record_digest"],
                }
            ],
            "git_status": "",
        }
        apparatus = record_with_digest(apparatus_payload)
        apparatus_path = tmp_path / "apparatus-receipt.yaml"
        apparatus_path.write_text(
            yaml.safe_dump(apparatus, sort_keys=False),
            encoding="utf-8",
        )
    else:
        apparatus_path = apparatus_receipt_path
        apparatus = yaml.safe_load(
            apparatus_path.read_text(encoding="utf-8")
        )
    terminal_digest = None
    public_manifest_path = None
    public_manifest_digest = None
    h0_binding_path = None
    if calibration_terminal is not None:
        raw = yaml.safe_load(calibration_terminal.read_text(encoding="utf-8"))
        terminal_digest = Exp2CalibrationTerminalReceipt.from_dict(raw).record_digest
        public_manifest = record_with_digest(
            {"schema": "autobugfix-swe-sealed-manifest-v2"}
        )
        public_source = tmp_path / "public-manifest.yaml"
        public_source.write_text(
            yaml.safe_dump(public_manifest, sort_keys=False),
            encoding="utf-8",
        )
        public_manifest_path = str(public_source.resolve())
        public_manifest_digest = str(public_manifest["record_digest"])
        h0_binding = record_with_digest(
            {
                "schema": "autobugfix-guard-study-binding-v1",
                "kind": "BASELINE",
                "subject_sha": _sha("h0"),
                "subject_tree": _sha("h0-tree"),
                "memory_digest": protocol.memory_fixture_digest,
                "primary_model": protocol.model,
            }
        )
        binding_source = tmp_path / "h0-binding.yaml"
        binding_source.write_text(
            yaml.safe_dump(h0_binding, sort_keys=False),
            encoding="utf-8",
        )
        h0_binding_path = str(binding_source.resolve())
    if project_root is None:
        disposable_root = tmp_path / "disposable"
        artifact_root = tmp_path / "artifacts"
        eval_root = tmp_path / "eval"
        operator_root = tmp_path / "operator"
        memory_root = tmp_path / "memory"
        guard_root = tmp_path / "guard"
    else:
        config = load_config(project_root)
        disposable_root = tmp_path / "disposable"
        artifact_root = (
            config.eval.benchmarks.trusted_case_root / "exp2" / study_id / "runs"
        )
        eval_root = config.eval.benchmarks.trusted_case_root
        operator_root = config.operator.state.root
        memory_root = tmp_path / "memory"
        guard_root = tmp_path / "guard"
    _private_empty_memory_root(memory_root)
    return Exp2ResumeStudyPlan(
        study_id=study_id,
        study_kind=study_kind,  # type: ignore[arg-type]
        protocol_path=str(protocol_path.resolve()),
        protocol_digest=protocol.record_digest,
        swe_protocol_path=str(swe_protocol_path.resolve()),
        swe_protocol_sha256=digest_file(swe_protocol_path),
        apparatus_sha=str(apparatus["apparatus_sha"]),
        apparatus_tree=str(apparatus["apparatus_tree"]),
        apparatus_receipt_path=str(apparatus_path.resolve()),
        apparatus_receipt_digest=str(apparatus["record_digest"]),
        h0_subject_sha=_sha("h0"),
        h0_subject_tree=_sha("h0-tree"),
        h0_binding_digest=(
            str(h0_binding["record_digest"])
            if calibration_terminal is not None
            else _digest("h0-binding")
        ),
        scorer_digest=protocol.scorer_digest,
        runtime_digest=protocol.runtime_digest,
        memory_fixture_spec_path=str(memory_fixture_path.resolve()),
        memory_fixture_digest=protocol.memory_fixture_digest,
        operator_policy_digest=protocol.operator_policy_digest,
        operator_role_skill_digest=protocol.operator_role_skill_digest,
        execution_role_skill_digest=protocol.execution_role_skill_digest,
        selected_images_digest=digest_payload(
            {"oci_images": [item.to_dict() for item in protocol.oci_images]}
        ),
        disposable_root=str(disposable_root.resolve()),
        artifact_root=str(artifact_root.resolve()),
        eval_root=str(eval_root.resolve()),
        operator_root=str(operator_root.resolve()),
        memory_root=str(memory_root.resolve()),
        guard_root=str(guard_root.resolve()),
        public_manifest_path=public_manifest_path,
        public_manifest_digest=public_manifest_digest,
        h0_binding_path=h0_binding_path,
        calibration_terminal_receipt_path=(
            str(calibration_terminal.resolve())
            if calibration_terminal is not None
            else None
        ),
        calibration_terminal_receipt_digest=terminal_digest,
    )


def _coordinator(
    tmp_path: Path,
    *,
    study_id: str = "calibration-v2",
    study_kind: str = "calibration",
    protocol: Exp2ResumeProtocol | None = None,
    calibration_terminal: Path | None = None,
    project_root: Path | None = None,
    apparatus_receipt_path: Path | None = None,
) -> Exp2ResumeCoordinator:
    frozen = protocol or _protocol()
    plan = _plan(
        tmp_path,
        frozen,
        study_id=study_id,
        study_kind=study_kind,
        calibration_terminal=calibration_terminal,
        project_root=project_root,
        apparatus_receipt_path=apparatus_receipt_path,
    )
    coordinator = Exp2ResumeCoordinator(tmp_path / "state", study_id)
    coordinator.initialize(plan, frozen)
    return coordinator


def _official_report(
    intent: Exp2CaseAttemptIntent,
    *,
    resolved: bool,
    submission_digest: str | None = None,
    failure_stage: str = "unknown",
    memory_digest: str | None = None,
) -> dict[str, Any]:
    submission = submission_digest or _digest(f"submission:{intent.run_id}")
    official = record_with_digest(
        {
            "schema": "autobugfix-swe-official-result-v1",
            "instance_id": intent.case_id,
            "resolved": resolved,
            "harness_error": False,
        }
    )
    noninterference = record_with_digest(
        {
            "schema": "autobugfix-swe-noninterference-v1",
            "submission_digest": submission,
            "official_result_digest": official["record_digest"],
            "unchanged": True,
        }
    )
    execution = record_with_digest(
        {
            "schema": "autobugfix-exp2-execution-receipt-v1",
            "execution_mode": "protected",
            "direct_sdk_in_process": False,
            "outer_bubblewrap": True,
            "workspace_only_preflight_digest": None,
            "execution_ledger_digest": _digest(f"usage:{intent.run_id}"),
        }
    )
    return record_with_digest(
        {
            "schema": "autobugfix-swe-formal-case-v2",
            "instance_id": intent.case_id,
            "executed_subject_sha": intent.subject_sha,
            "executed_subject_tree": intent.subject_tree,
            "subject_runtime_digest": _digest("runtime"),
            "memory_digest": memory_digest or _EMPTY_MEMORY_DIGEST,
            "image_digest": _digest(f"local:{intent.case_id}"),
            "submission_digest": submission,
            "official_result": official,
            "noninterference": noninterference,
            "execution_receipt": execution,
            "failure_stage": failure_stage,
        }
    )


def _invalid_receipt(
    intent: Exp2CaseAttemptIntent,
    *,
    status: str = "preflight_rejected",
    submission_digest: str | None = None,
) -> Exp2CaseAttemptReceipt:
    scorer_failure = status == "scorer_infrastructure_invalid"
    return Exp2CaseAttemptReceipt(
        study_id=intent.study_id,
        issuer="eval-benchmark-service-v2",
        stage=intent.stage,
        arm=intent.arm,
        case_id=intent.case_id,
        slice=intent.slice,
        started_event_digest=intent.record_digest,
        run_id=intent.run_id,
        attempt_kind=intent.attempt_kind,
        terminal_status=status,  # type: ignore[arg-type]
        subject_sha=intent.subject_sha,
        subject_tree=intent.subject_tree,
        frozen_input_digest=intent.frozen_input_digest,
        binding_digest=intent.binding_digest,
        execution_mode="protected",
        sdk_call_occurred=(
            scorer_failure and intent.attempt_kind == "execution"
        ),
        failure_artifact_digest=_digest(f"failure:{intent.run_id}"),
        submission_digest=submission_digest,
        failure_stage="infrastructure",
        writer_attempts=(1 if intent.attempt_kind == "execution" else 0),
        frozen_submission=scorer_failure,
        scorer_retry_legal=(
            scorer_failure and intent.attempt_kind == "execution"
        ),
    )


def _execute_official(
    raw_intent: Mapping[str, Any], *, resolved: bool = False
) -> Mapping[str, Any]:
    intent = Exp2CaseAttemptIntent.from_dict(raw_intent)
    return _official_report(intent, resolved=resolved)


def _complete_calibration(tmp_path: Path) -> tuple[Exp2ResumeCoordinator, Path]:
    coordinator = _coordinator(tmp_path)
    coordinator.resume(_execute_official)
    coordinator.resume(_execute_official)
    terminal_path = coordinator.state_root / "calibration-terminal-receipt.yaml"
    assert terminal_path.is_file()
    return coordinator, terminal_path


def test_v2_record_parser_rejects_missing_fields() -> None:
    protocol = _protocol()
    raw = protocol.to_dict()
    raw.pop("case_concurrency")
    raw = record_with_digest(
        {key: value for key, value in raw.items() if key != "record_digest"}
    )

    with pytest.raises(Exp2ContractError, match="missing fields"):
        Exp2ResumeProtocol.from_dict(raw)


def test_build_plan_v2_cli_requires_dedicated_memory_root() -> None:
    argv = [
        "eval",
        "exp2",
        "build-plan-v2",
        "--study-id",
        "calibration-v2",
        "--study-kind",
        "calibration",
        "--protocol-v2",
        "protocol.yaml",
        "--swe-protocol",
        "swe-protocol.yaml",
        "--apparatus-receipt",
        "apparatus.yaml",
        "--empty-memory-fixture",
        "empty-memory.yaml",
        "--disposable-root",
        "/tmp/exp2-disposable",
        "--guard-root",
        "/tmp/exp2-guard",
        "--out",
        "plan.yaml",
    ]

    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)

    args = build_parser().parse_args(
        [*argv, "--memory-root", "/tmp/exp2-empty-memory"]
    )
    assert args.memory_root == "/tmp/exp2-empty-memory"


def test_build_plan_v2_cli_cannot_write_inside_empty_memory_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory_root = _private_empty_memory_root(tmp_path / "empty-memory")
    output = memory_root / "plan.yaml"

    result = main(
        [
            "eval",
            "exp2",
            "build-plan-v2",
            "--study-id",
            "calibration-v2",
            "--study-kind",
            "calibration",
            "--protocol-v2",
            "unused-protocol.yaml",
            "--swe-protocol",
            "unused-swe-protocol.yaml",
            "--apparatus-receipt",
            "unused-apparatus.yaml",
            "--empty-memory-fixture",
            "unused-empty-memory.yaml",
            "--memory-root",
            str(memory_root),
            "--disposable-root",
            str(tmp_path / "disposable"),
            "--guard-root",
            str(tmp_path / "guard"),
            "--out",
            str(output),
        ]
    )

    assert result == 1
    assert "must not mutate the empty Memory root" in capsys.readouterr().err
    assert not output.exists()
    assert (
        OperatorGovernanceService.validate_exp2_empty_memory_root(memory_root)
        == _EMPTY_MEMORY_DIGEST
    )


@pytest.mark.parametrize(
    "scope",
    [
        ("src/autobugfix/runner.py",),
        ("src/autobugfix/prompts.py",),
        (".agents/role-skills/execution/**",),
        (EXP2_WRITER_SKILL_PATH, "src/autobugfix/runner.py"),
    ],
)
def test_resume_protocol_rejects_non_writer_skill_treatment(
    scope: tuple[str, ...],
) -> None:
    with pytest.raises(Exp2ContractError, match="single Writer skill"):
        replace(_protocol(qualified=False), execution_allowlist=scope)


def test_resume_mvp_swe_protocol_matches_frozen_h0_cohort() -> None:
    protocol = SWEExperimentProtocol.from_yaml(
        Path("benchmarks/swe-experiment-2-resume-mvp-v2.yaml")
    )

    assert tuple(item.instance_id for item in protocol.optimization_cases) == (
        "astropy__astropy-13398",
        "django__django-10097",
        "matplotlib__matplotlib-24627",
        "pydata__xarray-2905",
        "sympy__sympy-13091",
        "mwaskom__seaborn-3187",
        "psf__requests-6028",
        "pytest-dev__pytest-10051",
        "scikit-learn__scikit-learn-13439",
        "sphinx-doc__sphinx-9229",
    )


def test_unqualified_protocol_blocks_before_executor(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, protocol=_protocol(qualified=False))
    called = False

    def executor(_: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal called
        called = True
        raise AssertionError("executor must not be called")

    with pytest.raises(Exp2ResumeError, match="no resolved OCI identities"):
        coordinator.resume(executor)

    assert called is False
    assert coordinator.status()["open_intents"] == []


def test_calibration_executes_one_case_per_resume_and_stays_terminal(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    calls: list[str] = []

    def executor(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        calls.append(intent.case_id)
        return _official_report(intent, resolved=False)

    first = coordinator.resume(executor)
    assert first["state"] == "CALIBRATION_RUNNING"
    assert first["terminal_receipt_count"] == 1

    second = coordinator.resume(executor)
    assert second["state"] == "CALIBRATION_COMPLETE"
    assert second["terminal_receipt_count"] == 2
    assert calls == ["pallets__flask-5014", "pylint-dev__pylint-4970"]

    terminal = coordinator.resume(executor)
    assert terminal["status"] == "terminal"
    assert terminal["state"] == "CALIBRATION_COMPLETE"
    assert len(calls) == 2


def test_coordinator_rejects_executor_memory_override_authority(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    fixture_digest = coordinator.load_plan().memory_fixture_digest

    def executor(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        return {
            "report": _official_report(
                intent,
                resolved=False,
                memory_digest=_digest("broker-memory-input"),
            ),
            "memory_digest": fixture_digest,
        }

    with pytest.raises(
        Exp2ResumeError, match="cannot override the official report"
    ):
        coordinator.resume(executor)

    assert coordinator.status()["terminal_receipt_count"] == 0


def test_coordinator_receipt_binds_report_memory_without_override(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    fixture_digest = coordinator.load_plan().memory_fixture_digest

    def executor(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        return {"report": _official_report(intent, resolved=False)}

    result = coordinator.resume(executor)
    assert result["terminal_receipt_count"] == 1
    events = [
        json.loads(line)
        for line in coordinator.events_path.read_text(encoding="utf-8").splitlines()
    ]
    receipts = [
        Exp2CaseAttemptReceipt.from_dict(event["payload"])
        for event in events
        if event["kind"] == "case_attempt_terminal"
    ]
    assert len(receipts) == 1
    assert receipts[0].memory_digest == fixture_digest


def test_study_lease_prevents_concurrent_dispatch_and_is_process_exclusive(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    calls = 0

    def executor(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _official_report(
            Exp2CaseAttemptIntent.from_dict(raw), resolved=False
        )

    child = """
from pathlib import Path
import sys
from autobugfix.eval.benchmarks.exp2_resume import Exp2ResumeCoordinator
c = Exp2ResumeCoordinator(Path(sys.argv[1]), sys.argv[2])
with c._study_lease() as acquired:
    print("acquired" if acquired else "busy")
"""
    with coordinator._study_lease() as acquired:
        assert acquired is True
        concurrent = coordinator.resume(executor)
        observed = subprocess.run(
            [
                sys.executable,
                "-c",
                child,
                str(coordinator.state_root),
                coordinator.study_id,
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )

    assert concurrent["status"] == "in_progress"
    assert observed.stdout.strip() == "busy"
    assert calls == 0
    completed = coordinator.resume(executor)
    assert completed["terminal_receipt_count"] == 1
    assert calls == 1


def test_initialize_is_lease_guarded_and_never_replaces_existing_state(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    plan = Exp2ResumeStudyPlan.from_dict(
        yaml.safe_load(coordinator.plan_path.read_text(encoding="utf-8"))
    )
    protocol = Exp2ResumeProtocol.from_dict(
        yaml.safe_load(coordinator.protocol_path.read_text(encoding="utf-8"))
    )
    concurrent = Exp2ResumeCoordinator(
        coordinator.state_root, coordinator.study_id
    )

    with coordinator._study_lease() as acquired:
        assert acquired is True
        with pytest.raises(Exp2ResumeError, match="initialization is in progress"):
            concurrent.initialize(plan, protocol)

    with pytest.raises(Exp2ResumeError, match="state root already exists"):
        concurrent.initialize(plan, protocol)


def test_open_intent_requires_trusted_reconciliation_without_reexecution(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    attempts = 0

    def interrupted(_: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("controlled interruption")

    with pytest.raises(RuntimeError, match="controlled interruption"):
        coordinator.resume(interrupted)

    blocked = coordinator.resume(interrupted)
    assert blocked["status"] == "reconciliation_required"
    assert attempts == 1
    open_intent = Exp2CaseAttemptIntent.from_dict(blocked["open_intents"][0])

    reconciled = coordinator.resume(
        interrupted,
        reconciler=lambda raw: _official_report(
            Exp2CaseAttemptIntent.from_dict(raw), resolved=False
        ),
    )
    assert reconciled["terminal_receipt_count"] == 1
    assert reconciled["open_intents"] == []
    assert attempts == 1
    assert open_intent.case_id == "pallets__flask-5014"


def test_scorer_retry_binds_submission_and_never_reexecutes_writer(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    kinds: list[str] = []
    frozen_submission = _digest("frozen-submission")

    def executor(raw: Mapping[str, Any]) -> Any:
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        kinds.append(intent.attempt_kind)
        if intent.case_id == "pallets__flask-5014" and len(kinds) == 1:
            return _invalid_receipt(
                intent,
                status="scorer_infrastructure_invalid",
                submission_digest=frozen_submission,
            )
        if intent.attempt_kind == "scorer_only_retry":
            assert intent.frozen_submission_digest == frozen_submission
            assert intent.retry_of_receipt_digest is not None
            return _official_report(
                intent,
                resolved=False,
                submission_digest=frozen_submission,
            )
        return _official_report(intent, resolved=False)

    coordinator.resume(executor)
    coordinator.resume(executor)
    complete = coordinator.resume(executor)

    assert complete["state"] == "CALIBRATION_COMPLETE"
    assert kinds == ["execution", "scorer_only_retry", "execution"]
    events = [
        json.loads(line)
        for line in coordinator.events_path.read_text(encoding="utf-8").splitlines()
    ]
    receipts = [
        Exp2CaseAttemptReceipt.from_dict(event["payload"])
        for event in events
        if event["kind"] == "case_attempt_terminal"
    ]
    retry = next(item for item in receipts if item.attempt_kind == "scorer_only_retry")
    assert retry.sdk_call_occurred is False
    assert retry.writer_attempts == 0
    assert retry.submission_digest == frozen_submission


def test_invalid_calibration_is_terminal_and_cannot_authorize_pilot(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path / "calibration")

    def executor(raw: Mapping[str, Any]) -> Any:
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        if intent.case_id == "pallets__flask-5014":
            return _invalid_receipt(intent)
        return _official_report(intent, resolved=False)

    coordinator.resume(executor)
    result = coordinator.resume(executor)
    assert result["state"] == "CALIBRATION_BLOCKED"
    terminal_path = coordinator.state_root / "calibration-terminal-receipt.yaml"

    protocol = coordinator.load_protocol()
    plan = _plan(
        tmp_path / "pilot",
        protocol,
        study_id="pilot-v2",
        study_kind="resume_pilot",
        calibration_terminal=terminal_path,
    )
    pilot = Exp2ResumeCoordinator(tmp_path / "pilot-state", "pilot-v2")
    with pytest.raises(Exp2ResumeError, match="identities are incompatible"):
        pilot.initialize(plan, protocol)


def test_h0_invalid_produces_replayable_terminal_record(tmp_path: Path) -> None:
    calibration, terminal_path = _complete_calibration(tmp_path / "calibration")
    protocol = calibration.load_protocol()
    pilot_root = tmp_path / "pilot"
    pilot = _coordinator(
        pilot_root,
        study_id="pilot-v2",
        study_kind="resume_pilot",
        protocol=protocol,
        calibration_terminal=terminal_path,
        apparatus_receipt_path=Path(
            calibration.load_plan().apparatus_receipt_path
        ),
    )
    calls = 0

    def executor(raw: Mapping[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        if calls == 1:
            return _invalid_receipt(intent)
        return _official_report(intent, resolved=calls % 2 == 0)

    result: Mapping[str, Any] = {}
    for _ in range(10):
        result = pilot.resume(executor)

    assert calls == 10
    assert result["state"] == "BLOCKED"
    assert result["decision"] == "blocked_invalid"
    assert result["pilot_terminal_digest"] is not None
    assert pilot.status()["state"] == "BLOCKED"
    assert pilot.resume(executor)["status"] == "terminal"


def test_h0_pass_releases_only_the_source_pair(tmp_path: Path) -> None:
    calibration, terminal_path = _complete_calibration(tmp_path / "calibration")
    protocol = calibration.load_protocol()
    pilot = _coordinator(
        tmp_path / "pilot",
        study_id="pilot-v2",
        study_kind="resume_pilot",
        protocol=protocol,
        calibration_terminal=terminal_path,
        apparatus_receipt_path=Path(
            calibration.load_plan().apparatus_receipt_path
        ),
    )
    calls = 0

    def executor(raw: Mapping[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        intent = Exp2CaseAttemptIntent.from_dict(raw)
        return _official_report(
            intent,
            resolved=calls % 2 == 0,
            failure_stage=(
                "visible_verifier"
                if intent.case_id == "astropy__astropy-13398"
                else "official_eval"
            ),
        )

    result: Mapping[str, Any] = {}
    for _ in range(10):
        result = pilot.resume(executor)

    assert result["state"] == "SOURCE_RELEASED"
    bundle_path = pilot.state_root / "source-projection-bundle.yaml"
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    serialized = yaml.safe_dump(bundle)
    assert [item["case_id"] for item in bundle["projections"]] == [
        "astropy__astropy-13398",
        "django__django-10097",
    ]
    assert "matplotlib__matplotlib-24627" not in serialized
    assert "mwaskom__seaborn-3187" not in serialized


def test_tampered_event_chain_is_rejected(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.resume(_execute_official)
    lines = coordinator.events_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    event["payload"]["case_id"] = "tampered-case"
    lines[-1] = json.dumps(event)
    coordinator.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(Exp2ResumeError, match="journal line"):
        coordinator.status()


def test_torn_final_event_is_ignored_then_durably_repaired(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    calls = 0

    def executor(raw: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        return _official_report(
            Exp2CaseAttemptIntent.from_dict(raw),
            resolved=False,
        )

    coordinator.resume(executor)
    with coordinator.events_path.open("ab") as stream:
        stream.write(b'{"schema":"torn"')

    assert coordinator.status()["terminal_receipt_count"] == 1
    result = coordinator.resume(executor)

    assert result["state"] == "CALIBRATION_COMPLETE"
    assert calls == 2
    assert coordinator.events_path.read_bytes().endswith(b"\n")


def test_calibration_report_publishes_fixed_population_metrics(
    tmp_path: Path,
) -> None:
    coordinator, _ = _complete_calibration(tmp_path)

    published = coordinator.publish_report()
    report = published["report"]
    population = report["metrics"]["populations"]["calibration"]

    assert population["scheduled_cases"] == 2
    assert population["terminal_coverage"] == 1.0
    assert population["official_coverage"] == 1.0
    assert population["completed_case_reexecution_count"] == 0
    assert population["sdk_after_rejected_preflight"] == 0
    assert population["model_calls"] == 2
    assert report["reserve"] == "not_run"
    assert report["live"] == "not_run"
    assert report["pro"] == "not_run"
    assert published["status"]["state"] == "REPORTED"


class _FakeExp2EvalService:
    def __init__(self, project_root: Path, *, mode: str):
        self.project_root = project_root
        self.config = load_config(project_root)
        self.mode = mode
        self.expected_additional_hidden_paths: set[Path] = set()
        self.execute_calls = 0
        self.rescore_calls = 0
        self.formal_swe_protocol_paths: list[Path] = []
        self.submissions: dict[str, dict[str, str]] = {}
        self.report_memory_digest: str | None = None
        self.expected_memory_root: Path | None = None

    def exp2_runtime_identity(self, protocol_path: Path) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-eval-runtime-identity-v2",
                "swe_protocol_sha256": digest_file(protocol_path),
                "swe_protocol_digest": _digest("swe-protocol"),
                "dataset_revision": _sha("dataset"),
                "dataset_snapshot_sha256": _digest("dataset-snapshot"),
                "scorer_digest": _digest("scorer"),
                "runtime_digest": _digest("runtime"),
                "model": "gpt-5.4-mini",
                "reasoning_effort": "low",
                "max_attempts": 2,
                "timeout_seconds": 900,
                "case_concurrency": 1,
            }
        )

    def exp2_qualification_receipt(
        self,
        protocol_path: Path,
        instance_id: str,
    ) -> dict[str, Any]:
        del protocol_path
        return _qualification_record(instance_id)

    @staticmethod
    def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(dict(payload), sort_keys=False),
            encoding="utf-8",
        )

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), sort_keys=True) + "\n", encoding="utf-8")

    def _execution_evidence(
        self,
        *,
        root: Path,
        run_id: str,
        case_id: str,
        disposable_root: Path,
        submission_digest: str,
        additional_hidden_paths: tuple[Path, ...] = (),
    ) -> dict[str, Any]:
        subject_root = root / "subject-run"
        evidence_root = subject_root / "frozen-execution-evidence"
        raw_evidence = disposable_root / f"raw-evidence-{run_id}"
        workspace = subject_root
        task_worktree = subject_root / "target" / "worktrees" / "task"
        task_worktree.mkdir(parents=True, exist_ok=True)
        authority_roots = [
            self.project_root.resolve(),
            self.config.eval.benchmarks.trusted_case_root.resolve(),
            self.config.operator.state.root.resolve(),
            (self.project_root / ".autobugfix-memory").resolve(),
        ]
        preflight = record_with_digest(
            {
                "schema": "autobugfix-swe-workspace-only-preflight-v1",
                "execution_mode": "workspace_only",
                "workspace_root": str(workspace.resolve()),
                "disposable_root": str(disposable_root.resolve()),
                "artifact_root": str(subject_root.resolve()),
                "authority_roots": [str(path) for path in authority_roots],
                "environment_keys": ["HOME", "PATH"],
                "credential_keys": [],
                "credential_paths_checked": [],
                "temporary_root": str((workspace / "tmp").resolve()),
                "direct_sdk_in_process": True,
                "sdk_bubblewrap": False,
                "outer_bubblewrap": False,
            }
        )
        ledger = record_with_digest(
            {
                "schema": "autobugfix-swe-execution-ledger-v1",
                "phase": "completed",
                "max_attempts": 2,
                "writer_calls": 1,
                "verifier_calls": 1,
                "evaluator_calls": 1,
                "patch_sha256": _digest(f"patch:{run_id}"),
                "events": [],
            }
        )
        hidden_paths = [
            *(str(path) for path in authority_roots),
            *(
                str(path.resolve())
                for path in (
                    additional_hidden_paths[1:]
                    if self.mode == "missing_dedicated_hidden"
                    else additional_hidden_paths
                )
            ),
            str(Path.home().resolve()),
        ]
        sdk = record_with_digest(
            {
                "schema": "autobugfix-swe-codex-call-receipt-v1",
                "execution_mode": "protected",
                "sdk_in_process": False,
                "sdk_bubblewrap": True,
                "cwd": str(task_worktree.resolve()),
                "expected_task_worktree": str(task_worktree.resolve()),
                "hidden_paths": hidden_paths,
                "hidden_paths_digest": hashlib.sha256(
                    json.dumps(
                        sorted(hidden_paths), separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
        command = record_with_digest(
            {
                "name": "fake-subject-execution",
                "argv": [
                    "/usr/bin/bwrap",
                    "--tmpfs",
                    (
                        "/var/empty"
                        if self.mode == "missing_additional_mask"
                        else "/tmp"
                    ),
                    "--",
                    "python",
                ],
                "passed": True,
            }
        )
        case_token = "dev-" + hashlib.sha256(
            f"swebench_verified:{case_id}".encode("utf-8")
        ).hexdigest()[:24]
        broker = record_with_digest(
            {
                "schema": "autobugfix-swe-subject-broker-v1",
                "case_token": case_token,
                "executed_subject_sha": _sha("h0"),
                "executed_subject_tree": _sha("h0-tree"),
                "submission_digest": submission_digest,
                "execution_ledger_digest": ledger["record_digest"],
                "command": command,
                "task_worktree_path": str(task_worktree.resolve()),
                "execution_mode": "protected",
                "sdk_call_receipt_digests": [sdk["record_digest"]],
                "additional_hidden_paths": sorted(
                    str(path.resolve())
                    for path in (
                        additional_hidden_paths[1:]
                        if self.mode == "missing_dedicated_hidden"
                        else additional_hidden_paths
                    )
                ),
            }
        )
        self._write_json(subject_root / "workspace-only-preflight.json", preflight)
        self._write_json(raw_evidence / "execution-ledger.json", ledger)
        self._write_json(
            raw_evidence / "codex-broker" / "call-1" / "receipt.json",
            sdk,
        )
        evidence_manifest = SWESubjectBroker._build_evidence_tree(
            evidence_root,
            {
                "execution-ledger.json": raw_evidence
                / "execution-ledger.json",
                "codex-broker": raw_evidence / "codex-broker",
            },
        )
        self._write_yaml(subject_root / "broker-result.yaml", broker)
        execution = record_with_digest(
            {
                "schema": "autobugfix-exp2-execution-receipt-v1",
                "execution_mode": "protected",
                "direct_sdk_in_process": False,
                "outer_bubblewrap": True,
                "broker_command_digest": command["record_digest"],
                "broker_result_digest": broker["record_digest"],
                "task_worktree_path": str(task_worktree.resolve()),
                "workspace_only_preflight_digest": None,
                "execution_ledger_digest": ledger["record_digest"],
                "sdk_call_receipt_digests": [sdk["record_digest"]],
            }
        )
        self.submissions[submission_digest] = {
            "case_token": case_token,
            "subject_sha": _sha("h0"),
            "subject_tree": _sha("h0-tree"),
            "evidence_manifest_digest": str(
                evidence_manifest["record_digest"]
            ),
        }
        return {
            "preflight": preflight,
            "ledger": ledger,
            "broker": broker,
            "execution": execution,
        }

    def _official_report(
        self,
        *,
        case_id: str,
        run_id: str,
        submission_digest: str,
        execution: Mapping[str, Any],
        source_run_root: Path | None = None,
    ) -> dict[str, Any]:
        official = record_with_digest(
            {
                "schema": "autobugfix-swe-official-result-v1",
                "instance_id": case_id,
                "resolved": False,
                "harness_error": False,
            }
        )
        noninterference = record_with_digest(
            {
                "schema": "autobugfix-swe-noninterference-v1",
                "submission_digest": submission_digest,
                "official_result_digest": official["record_digest"],
                "unchanged": True,
            }
        )
        payload: dict[str, Any] = {
            "schema": "autobugfix-exp2-calibration-case-v1",
            "instance_id": case_id,
            "executed_subject_sha": _sha("h0"),
            "executed_subject_tree": _sha("h0-tree"),
            "subject_runtime_digest": _digest("runtime"),
            "memory_digest": self.report_memory_digest or _EMPTY_MEMORY_DIGEST,
            "image_digest": _digest(f"local:{case_id}"),
            "submission_digest": submission_digest,
            "official_result": official,
            "noninterference": noninterference,
            "execution_receipt": dict(execution),
        }
        if source_run_root is not None:
            payload["schema"] = "autobugfix-exp2-scorer-retry-report-v2"
            payload["source_run_root"] = str(source_run_root.resolve())
        return record_with_digest(payload)

    def run_swe_exp2_calibration_case(
        self,
        protocol_path: Path,
        *,
        adapter: str,
        instance_id: str,
        run_id: str,
        execution_mode: str,
        disposable_root: Path,
        out_root: Path,
        additional_hidden_paths: tuple[Path, ...],
        memory_root: Path,
    ) -> dict[str, Any]:
        del protocol_path, adapter
        assert execution_mode == "protected"
        assert memory_root == self.expected_memory_root
        assert {
            path.resolve() for path in additional_hidden_paths
        } == self.expected_additional_hidden_paths
        self.execute_calls += 1
        root = out_root / run_id
        submission = _digest(f"submission:{run_id}")
        evidence = self._execution_evidence(
            root=root,
            run_id=run_id,
            case_id=instance_id,
            disposable_root=disposable_root,
            submission_digest=submission,
            additional_hidden_paths=additional_hidden_paths,
        )
        if self.mode == "scorer_failure":
            self.mode = "normal"
            raise EvalBenchmarkServiceError("forced scorer interruption")
        report = self._official_report(
            case_id=instance_id,
            run_id=run_id,
            submission_digest=submission,
            execution=evidence["execution"],
        )
        self._write_yaml(root / "exp2-calibration-case-report.yaml", report)
        if self.mode == "mutate_during_run":
            self.mode = "normal"
            (
                Path(self.expected_memory_root)
                / "active"
                / "stray.md"
            ).write_text(
                "same-user mutation after validation\n",
                encoding="utf-8",
            )
        if self.mode == "report_then_raise":
            self.mode = "normal"
            raise EvalBenchmarkServiceError("forced post-report interruption")
        return report

    def run_swe_exp2_case(
        self,
        public_manifest_path: Path,
        *,
        swe_protocol_path: Path,
        case_selector: str,
        study_binding_path: Path,
        out_root: Path,
        run_id: str,
        expected_binding_kinds: set[str],
        execution_mode: str,
        disposable_root: Path,
        additional_hidden_paths: tuple[Path, ...],
    ) -> dict[str, Any]:
        del public_manifest_path, study_binding_path, expected_binding_kinds
        assert execution_mode == "protected"
        assert {
            path.resolve() for path in additional_hidden_paths
        } == self.expected_additional_hidden_paths
        self.formal_swe_protocol_paths.append(swe_protocol_path.resolve())
        self.execute_calls += 1
        root = out_root / run_id
        submission = _digest(f"submission:{run_id}")
        evidence = self._execution_evidence(
            root=root,
            run_id=run_id,
            case_id=case_selector,
            disposable_root=disposable_root,
            submission_digest=submission,
            additional_hidden_paths=additional_hidden_paths,
        )
        report = self._official_report(
            case_id=case_selector,
            run_id=run_id,
            submission_digest=submission,
            execution=evidence["execution"],
        )
        payload = dict(report)
        payload.pop("record_digest")
        payload["schema"] = "autobugfix-swe-formal-case-v2"
        return record_with_digest(payload)

    def verify_exp2_frozen_submission(
        self,
        *,
        submission_digest: str,
        expected_case_token: str,
        expected_subject_sha: str,
        expected_subject_tree: str,
    ) -> dict[str, Any]:
        observed = self.submissions[submission_digest]
        assert {
            key: observed[key]
            for key in ("case_token", "subject_sha", "subject_tree")
        } == {
            "case_token": expected_case_token,
            "subject_sha": expected_subject_sha,
            "subject_tree": expected_subject_tree,
        }
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-frozen-submission-verification-v2",
                "submission_digest": submission_digest,
                **observed,
                "patch_summary": {
                    "changed_files": 1,
                    "additions": 1,
                    "deletions": 0,
                    "changed_lines": 1,
                    "empty_patch": False,
                    "patch_sha256": _digest("fake-patch"),
                },
                "frozen_identity": {
                    "evidence_manifest_digest": observed[
                        "evidence_manifest_digest"
                    ]
                },
            }
        )

    def rescore_swe_exp2_submission(
        self,
        protocol_path: Path,
        *,
        instance_id: str,
        submission_digest: str,
        source_run_root: Path,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        del protocol_path
        self.rescore_calls += 1
        source = source_run_root / "subject-run"
        broker = yaml.safe_load(
            (source / "broker-result.yaml").read_text(encoding="utf-8")
        )
        preflight = json.loads(
            (source / "workspace-only-preflight.json").read_text(encoding="utf-8")
        )
        ledger = json.loads(
            (
                source
                / "frozen-execution-evidence"
                / "execution-ledger.json"
            ).read_text(encoding="utf-8")
        )
        execution = record_with_digest(
            {
                "schema": "autobugfix-exp2-execution-receipt-v1",
                "execution_mode": "protected",
                "direct_sdk_in_process": False,
                "outer_bubblewrap": True,
                "broker_command_digest": broker["command"]["record_digest"],
                "broker_result_digest": broker["record_digest"],
                "task_worktree_path": broker["task_worktree_path"],
                "workspace_only_preflight_digest": None,
                "execution_ledger_digest": ledger["record_digest"],
                "sdk_call_receipt_digests": broker[
                    "sdk_call_receipt_digests"
                ],
            }
        )
        report = self._official_report(
            case_id=instance_id,
            run_id=run_id,
            submission_digest=submission_digest,
            execution=execution,
            source_run_root=source_run_root,
        )
        self._write_yaml(out_root / run_id / "scorer-retry-report.yaml", report)
        return report


class _FakeExp2OperatorService:
    @staticmethod
    def governance_context() -> dict[str, Any]:
        return {"digest": _digest("operator-policy")}

    @staticmethod
    def exp2_role_skill_digests(**_: Any) -> dict[str, str]:
        return {
            "operator_role_skill_digest": _digest("operator-skill"),
            "execution_role_skill_digest": _digest("execution-skill"),
        }

    @staticmethod
    def validate_exp2_empty_memory_root(memory_root: Path) -> str:
        return OperatorGovernanceService.validate_exp2_empty_memory_root(
            memory_root
        )


def _fake_image_gate(
    intent: Exp2CaseAttemptIntent,
    identity: Exp2OciImageIdentity,
) -> dict[str, Any]:
    return record_with_digest(
        {
            "schema": "autobugfix-exp2-image-gate-v2",
            "study_id": intent.study_id,
            "case_id": intent.case_id,
            "run_id": intent.run_id,
            "image": identity.image,
            "image_identity_digest": digest_payload(identity.to_dict()),
            "qualification_digest": identity.qualification_digest,
            "manifest_digest": identity.manifest_digest,
            "config_digest": identity.config_digest,
            "layer_digests": list(identity.layer_digests),
            "local_image_id": identity.local_image_id,
            "rootfs_diff_ids": list(identity.rootfs_diff_ids),
            "platform": identity.platform,
            "command_digest": _digest(f"image-gate:{intent.run_id}"),
        }
    )


def _service_bound_authority(
    tmp_path: Path,
    *,
    mode: str,
) -> tuple[Exp2ResumeCoordinator, Exp2EvalAuthority, _FakeExp2EvalService]:
    service_root = tmp_path / "service"
    service_root.mkdir()
    project_root, _ = make_service_project(service_root)
    (project_root / ".gitignore").write_text(
        ".autobugfix/\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
    )
    run(["git", "init", "-b", "main", str(project_root)])
    run(["git", "-C", str(project_root), "config", "user.email", "test@example.com"])
    run(["git", "-C", str(project_root), "config", "user.name", "Test User"])
    run(
        [
            "git",
            "-C",
            str(project_root),
            "add",
            ".gitignore",
        ]
    )
    run(
        [
            "git",
            "-C",
            str(project_root),
            "add",
            "-f",
            ".autobugfix/config.yaml",
        ]
    )
    run(["git", "-C", str(project_root), "commit", "-m", "test apparatus"])
    coordinator = _coordinator(
        tmp_path / "study",
        project_root=project_root,
    )
    plan = coordinator.load_plan()
    for path in (
        Path(plan.disposable_root),
        Path(plan.operator_root),
        Path(plan.memory_root),
        Path(plan.guard_root),
    ):
        path.mkdir(parents=True, exist_ok=True)
    service = _FakeExp2EvalService(project_root, mode=mode)
    service.expected_additional_hidden_paths = {
        Path(plan.memory_root).resolve(),
        Path(plan.guard_root).resolve(),
    }
    service.expected_memory_root = Path(plan.memory_root)
    authority = Exp2EvalAuthority(
        project_root,
        coordinator,
        service=service,  # type: ignore[arg-type]
        operator_service=_FakeExp2OperatorService(),  # type: ignore[arg-type]
        image_gate_resolver=_fake_image_gate,
    )
    return coordinator, authority, service


def test_service_bound_resume_adopts_report_after_interruption(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="report_then_raise",
    )

    with pytest.raises(Exp2EvalAuthorityError, match="post-report interruption"):
        authority.resume(execute=True)

    assert coordinator.status()["open_intents"]
    result = authority.resume(execute=True)
    assert result["terminal_receipt_count"] == 1
    assert result["open_intents"] == []
    assert service.execute_calls == 1
    completed = authority.resume(execute=True)
    assert completed["state"] == "CALIBRATION_COMPLETE"
    report = coordinator.publish_report()["report"]
    metrics = report["metrics"]["populations"]["calibration"]
    assert metrics["complete_terminal_receipts"] == 2
    assert metrics["valid_noninterference_receipts"] == 2
    assert metrics["empty_patch_rate"] == 0.0
    assert metrics["changed_files"] == 2
    assert metrics["changed_lines"] == 2


def test_service_bound_report_carries_fixture_digest_and_receipt_binds_it(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="report_then_raise",
    )
    plan = coordinator.load_plan()
    service.report_memory_digest = plan.memory_fixture_digest

    with pytest.raises(Exp2EvalAuthorityError, match="post-report interruption"):
        authority.resume(execute=True)

    result = authority.resume(execute=True)
    assert result["terminal_receipt_count"] == 1
    assert result["open_intents"] == []
    events = [
        json.loads(line)
        for line in coordinator.events_path.read_text(encoding="utf-8").splitlines()
    ]
    receipts = [
        Exp2CaseAttemptReceipt.from_dict(event["payload"])
        for event in events
        if event["kind"] == "case_attempt_terminal"
    ]
    assert len(receipts) == 1
    assert receipts[0].memory_digest == plan.memory_fixture_digest


@pytest.mark.parametrize(
    "fabricated_digest",
    [
        _digest("unrelated-memory-input"),
        digest_payload({"files": []}),
    ],
)
def test_service_bound_rejects_report_memory_without_fixture_derivation(
    tmp_path: Path,
    fabricated_digest: str,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="normal",
    )
    del coordinator
    service.report_memory_digest = fabricated_digest

    with pytest.raises(Exp2EvalAuthorityError, match="frozen empty fixture"):
        authority.resume(execute=True)

    assert service.execute_calls == 1


def test_service_bound_rejects_memory_root_mutated_during_execution(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="mutate_during_run",
    )
    del coordinator, service

    with pytest.raises(
        Exp2EvalAuthorityError, match="frozen empty fixture"
    ):
        authority.resume(execute=True)


def test_service_bound_scorer_retry_reuses_frozen_submission(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="scorer_failure",
    )

    with pytest.raises(Exp2EvalAuthorityError, match="scorer interruption"):
        authority.resume(execute=True)

    reconciled = authority.resume(execute=True)
    assert reconciled["terminal_receipt_count"] == 1
    retried = authority.resume(execute=True)
    assert retried["terminal_receipt_count"] == 2
    assert service.execute_calls == 1
    assert service.rescore_calls == 1

    events = [
        json.loads(line)
        for line in coordinator.events_path.read_text(encoding="utf-8").splitlines()
    ]
    intents = [
        Exp2CaseAttemptIntent.from_dict(event["payload"])
        for event in events
        if event["kind"] == "case_attempt_started"
    ]
    assert [item.attempt_kind for item in intents] == [
        "execution",
        "scorer_only_retry",
    ]
    assert intents[1].retry_source_output_root == intents[0].output_root


def test_service_bound_rejects_dirty_apparatus_before_dispatch(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="normal",
    )
    del coordinator
    (authority.project_root / "dirty.py").write_text(
        "# uncommitted apparatus drift\n",
        encoding="utf-8",
    )

    with pytest.raises(Exp2EvalAuthorityError, match="clean apparatus"):
        authority.resume(execute=True)

    assert service.execute_calls == 0


def test_service_bound_rejects_redirected_empty_memory_before_dispatch(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="normal",
    )
    del coordinator
    memory_root = Path(authority.plan.memory_root)
    redirected_target = memory_root.with_name("redirected-memory-target")
    memory_root.rename(redirected_target)
    memory_root.symlink_to(redirected_target, target_is_directory=True)

    with pytest.raises(Exp2EvalAuthorityError, match="absolute real directory"):
        authority.resume(execute=True)

    assert service.execute_calls == 0


def test_service_bound_rejects_redirected_guard_before_dispatch(
    tmp_path: Path,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode="normal",
    )
    del coordinator
    guard_root = Path(authority.plan.guard_root)
    redirected_target = guard_root.with_name("redirected-guard-target")
    guard_root.rename(redirected_target)
    guard_root.symlink_to(redirected_target, target_is_directory=True)

    with pytest.raises(
        Exp2EvalAuthorityError,
        match="absolute real protected directory",
    ):
        authority.resume(execute=True)

    assert service.execute_calls == 0


@pytest.mark.parametrize(
    "mode",
    ["missing_dedicated_hidden", "missing_additional_mask"],
)
def test_service_bound_rejects_missing_dedicated_memory_broker_proof(
    tmp_path: Path,
    mode: str,
) -> None:
    coordinator, authority, service = _service_bound_authority(
        tmp_path,
        mode=mode,
    )
    del coordinator

    with pytest.raises(Exp2EvalAuthorityError, match="outer Bubblewrap proof"):
        authority.resume(execute=True)

    assert service.execute_calls == 1
