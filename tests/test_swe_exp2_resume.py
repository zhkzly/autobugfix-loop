from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml

from autobugfix.config import load_config
from autobugfix.eval.benchmarks.exp2_records import Exp2ContractError
from autobugfix.eval.benchmarks.exp2_resume import (
    Exp2CalibrationTerminalReceipt,
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
)
from autobugfix.eval.benchmarks.models import (
    digest_file,
    digest_payload,
    record_with_digest,
)
from autobugfix.eval.benchmarks.service import EvalBenchmarkServiceError
from autobugfix.eval.benchmarks.subject_broker import SWESubjectBroker
from autobugfix.eval.benchmarks.swe_models import SWEExperimentProtocol
from autobugfix.git_utils import rev_parse
from tests.helpers import make_service_project, run


_EMPTY_MEMORY_SPEC = """schema: autobugfix-exp2-empty-memory-fixture-spec-v1
fixture_id: exp2-empty-memory-v1
active_entries: []
approved_skill_entries: []
"""


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _qualification_record(case_id: str) -> dict[str, Any]:
    return record_with_digest(
        {
            "schema": "autobugfix-swe-qualification-v4",
            "instance_id": case_id,
            "image_id": "sha256:" + _digest(f"config:{case_id}"),
            "eligible": True,
        }
    )


def _protocol(*, qualified: bool = True) -> Exp2ResumeProtocol:
    pending = Exp2ResumeProtocol(
        protocol_id="exp2-resume-mvp-v2",
        dataset_revision=_sha("dataset"),
        scorer_digest=_digest("scorer"),
        runtime_digest=_digest("runtime"),
        memory_fixture_spec_digest=hashlib.sha256(
            _EMPTY_MEMORY_SPEC.encode()
        ).hexdigest(),
        memory_fixture_digest=_digest("empty-memory"),
        operator_policy_digest=_digest("operator-policy"),
        operator_role_skill_digest=_digest("operator-skill"),
        execution_role_skill_digest=_digest("execution-skill"),
        model="gpt-5.4-mini",
        reasoning_effort="low",
        execution_mode="protected",
        max_attempts=2,
        timeout_seconds=900,
        case_concurrency=1,
        execution_allowlist=("src/autobugfix/eval/benchmarks",),
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
        )
        for case in cases
    )
    return replace(
        pending,
        oci_images=images,
        qualification_status="qualified",
    )


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
        memory_root = project_root / ".autobugfix-memory"
        guard_root = tmp_path / "guard"
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
            "memory_digest": _digest("empty-memory"),
            "image_digest": _digest(f"config:{intent.case_id}"),
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
        self.execute_calls = 0
        self.rescore_calls = 0
        self.submissions: dict[str, dict[str, str]] = {}

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
            *(str(path.resolve()) for path in additional_hidden_paths),
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
                    "/tmp",
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
                    str(path.resolve()) for path in additional_hidden_paths
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

    @staticmethod
    def _official_report(
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
            "memory_digest": _digest("empty-memory"),
            "image_digest": _digest(f"config:{case_id}"),
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
    ) -> dict[str, Any]:
        del protocol_path, adapter
        assert execution_mode == "protected"
        assert len(additional_hidden_paths) == 1
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
        if self.mode == "report_then_raise":
            self.mode = "normal"
            raise EvalBenchmarkServiceError("forced post-report interruption")
        return report

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
