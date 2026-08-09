from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from autobugfix.eval.benchmarks.exp2_records import (
    Exp2ApparatusReceipt,
    Exp2Arm,
    Exp2AttributionRecord,
    Exp2CohortAudit,
    Exp2ContractError,
    Exp2EmptyMemoryFixture,
    Exp2ExecutionMode,
    Exp2HoldoutBurnRecord,
    Exp2PolicyRecord,
    Exp2PublicRegressionGate,
    Exp2ResultProjection,
    Exp2SealedAggregate,
    Exp2StageName,
    Exp2StageReceipt,
    Exp2StudyPlan,
    projection_digest,
    reduce_paired_public,
    validate_wave_schedule,
)
from autobugfix.eval.benchmarks.models import (
    digest_payload,
    record_with_digest,
    verify_record,
)


class Exp2CoordinatorError(RuntimeError):
    pass


StageExecutor = Callable[
    [Exp2StageName, tuple[str, ...], Path, Exp2Arm],
    Sequence[Mapping[str, Any]],
]


_STAGE_TRANSITIONS: dict[str, tuple[str, str, Exp2Arm]] = {
    "H0_CALIBRATION": ("PREPARED", "H0_CALIBRATED", "H0"),
    "H0_PUBLIC": ("H0_CALIBRATED", "H0_COMPLETE", "H0"),
    "H1A_PUBLIC": ("H0_COMPLETE", "ATTRIBUTION_AWAITING", "H1"),
    "H1B_PUBLIC": ("H1B_LOCKED", "ATTRIBUTION_AWAITING", "H1"),
    "PUBLIC_REPLAY": ("H1C_LOCKED", "PUBLIC_GATE_AWAITING", "H1"),
}


def _write_yaml_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        yaml.safe_dump(dict(payload), sort_keys=False), encoding="utf-8"
    )
    os.replace(temporary, path)


class Exp2Coordinator:
    """Trusted study-stage coordinator for the execution-only pilot.

    The coordinator owns only the immutable study ledger.  Candidate branch,
    budget, diagnosis, and promotion state remain owned by Operator services.
    A stage executor is injected by the CLI/service boundary so this class
    cannot accidentally acquire a second candidate state machine.
    """

    def __init__(self, state_root: Path, study_id: str):
        self.state_root = state_root.resolve()
        self.study_id = study_id
        self.plan_path = self.state_root / "plan.yaml"
        self.ledger_path = self.state_root / "ledger.yaml"
        self.events_path = self.state_root / "events.jsonl"

    def initialize(self, plan: Exp2StudyPlan) -> dict[str, Any]:
        if plan.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 plan study_id differs from coordinator")
        self._validate_plan_references(plan)
        if self.plan_path.exists():
            existing = self.load_plan()
            if existing.to_dict() != plan.to_dict():
                raise Exp2CoordinatorError(
                    "Exp2 plan is immutable after initialization"
                )
        else:
            _write_yaml_once(self.plan_path, plan.to_dict())
        if not self.ledger_path.exists() and self.events_path.exists():
            raise Exp2CoordinatorError(
                "Exp2 event journal exists without its ledger; refusing to adopt it"
            )
        if not self.ledger_path.exists():
            _write_yaml_once(self.ledger_path, self._new_ledger(plan))
        return self.status()

    @staticmethod
    def _read_record(path: Path, label: str) -> Mapping[str, Any]:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file():
            raise Exp2CoordinatorError(f"Exp2 {label} is missing or redirected")
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2CoordinatorError(f"Exp2 {label} is invalid")
        return raw

    def _validate_plan_references(self, plan: Exp2StudyPlan) -> None:
        try:
            audit = Exp2CohortAudit.from_dict(
                self._read_record(Path(plan.cohort_audit_path), "cohort audit")
            )
        except Exp2ContractError as exc:
            raise Exp2CoordinatorError(str(exc)) from exc
        if audit.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 cohort audit study_id differs")
        if audit.calibration_case_ids != plan.calibration_case_ids:
            raise Exp2CoordinatorError("Exp2 cohort calibration schedule drift")
        if audit.public_case_ids != plan.public_case_ids:
            raise Exp2CoordinatorError("Exp2 cohort public schedule drift")
        try:
            policy = Exp2PolicyRecord.from_dict(
                self._read_record(Path(plan.policy_path), "policy")
            )
        except Exp2ContractError as exc:
            raise Exp2CoordinatorError(str(exc)) from exc
        if policy.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 policy study_id differs")
        try:
            fixture = Exp2EmptyMemoryFixture.from_yaml(
                Path(plan.empty_memory_fixture_path)
            )
        except Exp2ContractError as exc:
            raise Exp2CoordinatorError(str(exc)) from exc
        if policy is not None and fixture is not None:
            if policy.memory_fixture_digest != fixture.record_digest:
                raise Exp2CoordinatorError(
                    "Exp2 policy and empty Memory fixture digests differ"
                )
        try:
            apparatus = Exp2ApparatusReceipt.from_dict(
                self._read_record(
                    Path(plan.apparatus_receipt_path), "apparatus receipt"
                )
            )
        except Exp2ContractError as exc:
            raise Exp2CoordinatorError(str(exc)) from exc
        if apparatus.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 apparatus study_id differs")
        if apparatus.execution_mode != plan.execution_mode:
            raise Exp2CoordinatorError("Exp2 apparatus execution mode differs")
        if apparatus.memory_fixture_digest != fixture.record_digest:
            raise Exp2CoordinatorError(
                "Exp2 apparatus and empty Memory fixture digests differ"
            )
        if apparatus.operator_role_skill_digest != policy.operator_role_skill_digest:
            raise Exp2CoordinatorError(
                "Exp2 apparatus and policy Operator skill digests differ"
            )

    def load_plan(self) -> Exp2StudyPlan:
        if self.plan_path.is_symlink() or not self.plan_path.is_file():
            raise Exp2CoordinatorError("Exp2 plan is missing")
        try:
            plan = Exp2StudyPlan.from_yaml(self.plan_path)
        except Exp2ContractError as exc:
            raise Exp2CoordinatorError(str(exc)) from exc
        self._validate_plan_references(plan)
        return plan

    def _new_ledger(self, plan: Exp2StudyPlan) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "autobugfix-exp2-ledger-v1",
                "study_id": self.study_id,
                "plan_digest": plan.plan_digest,
                "state": "PREPARED",
                "awaiting_after": None,
                "receipt_digests": [],
                "attribution_digest": None,
                "event_sequence": 0,
            }
        )

    def _load_ledger(self) -> dict[str, Any]:
        if self.ledger_path.is_symlink() or not self.ledger_path.is_file():
            raise Exp2CoordinatorError("Exp2 ledger is missing")
        raw = yaml.safe_load(self.ledger_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise Exp2CoordinatorError("Exp2 ledger is invalid")
        try:
            verify_record(raw)
        except Exception as exc:
            raise Exp2CoordinatorError("Exp2 ledger digest is invalid") from exc
        if raw.get("schema") != "autobugfix-exp2-ledger-v1":
            raise Exp2CoordinatorError("unsupported Exp2 ledger schema")
        if raw.get("study_id") != self.study_id:
            raise Exp2CoordinatorError("Exp2 ledger study_id differs from coordinator")
        if raw.get("plan_digest") != self.load_plan().plan_digest:
            raise Exp2CoordinatorError("Exp2 ledger plan binding drift")
        events = self._load_events()
        event_sequence = raw.get("event_sequence")
        if type(event_sequence) is not int or event_sequence < 0:
            raise Exp2CoordinatorError("Exp2 ledger event sequence is invalid")
        if event_sequence > len(events):
            raise Exp2CoordinatorError(
                "Exp2 ledger claims events that are missing from the journal"
            )
        current = dict(raw)
        if event_sequence < len(events):
            current = self._reconcile_ledger(current, events[event_sequence:])
            return self._save_ledger(current)
        return current

    def _save_ledger(self, ledger: Mapping[str, Any]) -> dict[str, Any]:
        saved = record_with_digest(
            {key: value for key, value in ledger.items() if key != "record_digest"}
        )
        _write_yaml_once(
            self.ledger_path,
            saved,
        )
        return saved

    def _load_events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        previous_digest: str | None = None
        for sequence, line in enumerate(
            self.events_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                raise Exp2CoordinatorError("Exp2 event journal contains a blank line")
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise Exp2CoordinatorError(
                    f"Exp2 event journal line {sequence} is not JSON"
                ) from exc
            if not isinstance(raw, Mapping):
                raise Exp2CoordinatorError(
                    f"Exp2 event journal line {sequence} is not a mapping"
                )
            try:
                verify_record(raw)
            except Exception as exc:
                raise Exp2CoordinatorError(
                    f"Exp2 event journal line {sequence} digest is invalid"
                ) from exc
            if raw.get("schema") != "autobugfix-exp2-event-v1":
                raise Exp2CoordinatorError("unsupported Exp2 event schema")
            if raw.get("study_id") != self.study_id:
                raise Exp2CoordinatorError(
                    "Exp2 event study_id differs from coordinator"
                )
            if raw.get("sequence") != sequence:
                raise Exp2CoordinatorError("Exp2 event sequence is not contiguous")
            if raw.get("previous_digest") != previous_digest:
                raise Exp2CoordinatorError("Exp2 event journal chain is broken")
            payload = raw.get("payload")
            if not isinstance(payload, Mapping):
                raise Exp2CoordinatorError("Exp2 event payload is invalid")
            self._validate_event_payload(str(raw.get("kind") or ""), payload)
            event = dict(raw)
            events.append(event)
            previous_digest = str(event["record_digest"])
        return events

    def _validate_event_payload(self, kind: str, payload: Mapping[str, Any]) -> None:
        if kind == "stage_completed":
            raw_receipt = payload.get("stage_receipt")
            raw_projections = payload.get("projections")
            if not isinstance(raw_receipt, Mapping) or not isinstance(
                raw_projections, list
            ):
                raise Exp2CoordinatorError("Exp2 stage event payload is incomplete")
            try:
                receipt = Exp2StageReceipt.from_dict(raw_receipt)
                projections = tuple(
                    Exp2ResultProjection.from_dict(item)
                    for item in raw_projections
                    if isinstance(item, Mapping)
                )
            except (Exp2ContractError, TypeError, ValueError) as exc:
                raise Exp2CoordinatorError(
                    "Exp2 stage event contains an invalid receipt"
                ) from exc
            if len(projections) != len(raw_projections):
                raise Exp2CoordinatorError(
                    "Exp2 stage event contains a non-mapping projection"
                )
            if receipt.study_id != self.study_id:
                raise Exp2CoordinatorError("Exp2 stage receipt study_id differs")
            if tuple(item.case_id for item in projections) != receipt.case_ids:
                raise Exp2CoordinatorError("Exp2 stage projection cases drift")
            if tuple(projection_digest(item) for item in projections) != tuple(
                receipt.projection_digests
            ):
                raise Exp2CoordinatorError("Exp2 stage projection digests drift")
            if any(
                item.arm != receipt.arm or item.stage != receipt.stage
                for item in projections
            ):
                raise Exp2CoordinatorError("Exp2 stage projection binding drift")
            return
        if kind == "attribution_recorded":
            try:
                record = Exp2AttributionRecord.from_dict(payload)
            except (Exp2ContractError, TypeError, ValueError) as exc:
                raise Exp2CoordinatorError(
                    "Exp2 attribution event contains an invalid record"
                ) from exc
            if record.study_id != self.study_id:
                raise Exp2CoordinatorError("Exp2 attribution study_id differs")
            return
        if kind == "sealed_aggregate_recorded":
            raw_aggregate = payload.get("aggregate")
            if not isinstance(raw_aggregate, Mapping):
                raise Exp2CoordinatorError("Exp2 sealed aggregate event is incomplete")
            try:
                aggregate = Exp2SealedAggregate.from_dict(raw_aggregate)
            except (Exp2ContractError, TypeError, ValueError) as exc:
                raise Exp2CoordinatorError(
                    "Exp2 sealed aggregate event contains an invalid record"
                ) from exc
            if aggregate.study_id != self.study_id:
                raise Exp2CoordinatorError("Exp2 sealed aggregate study_id differs")
            return
        if kind == "public_gate_recorded":
            raw_gate = payload.get("gate")
            if not isinstance(raw_gate, Mapping):
                raise Exp2CoordinatorError("Exp2 public gate event is incomplete")
            try:
                gate = Exp2PublicRegressionGate.from_dict(raw_gate)
            except (Exp2ContractError, TypeError, ValueError) as exc:
                raise Exp2CoordinatorError(
                    "Exp2 public gate event contains an invalid record"
                ) from exc
            if gate.study_id != self.study_id:
                raise Exp2CoordinatorError("Exp2 public gate study_id differs")
            return
        if kind == "holdout_burned":
            raw_burn = payload.get("burn")
            if not isinstance(raw_burn, Mapping):
                raise Exp2CoordinatorError("Exp2 Holdout burn event is incomplete")
            try:
                burn = Exp2HoldoutBurnRecord.from_dict(raw_burn)
            except (Exp2ContractError, TypeError, ValueError) as exc:
                raise Exp2CoordinatorError(
                    "Exp2 Holdout burn event contains an invalid record"
                ) from exc
            if burn.study_id != self.study_id:
                raise Exp2CoordinatorError("Exp2 Holdout burn study_id differs")
            return
        raise Exp2CoordinatorError(f"unsupported Exp2 event kind: {kind}")

    def _reconcile_ledger(
        self, ledger: dict[str, Any], events: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Complete a ledger write interrupted after its journal append.

        The journal is append-only and validated before this method runs.  A
        caller can therefore recover the one normal crash window without
        inventing a stage result or rerunning an officially scored case.
        """

        plan = self.load_plan()
        updated = dict(ledger)
        for event in events:
            kind = str(event.get("kind") or "")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise Exp2CoordinatorError("Exp2 event payload is invalid")
            if kind == "stage_completed":
                receipt_raw = payload.get("stage_receipt")
                if not isinstance(receipt_raw, Mapping):
                    raise Exp2CoordinatorError("Exp2 stage receipt is missing")
                receipt = Exp2StageReceipt.from_dict(receipt_raw)
                transition = _STAGE_TRANSITIONS.get(receipt.stage)
                if transition is None:
                    raise Exp2CoordinatorError("Exp2 journal contains an unknown stage")
                expected_previous, next_state, arm = transition
                if updated.get("state") != expected_previous:
                    raise Exp2CoordinatorError(
                        "Exp2 journal stage cannot be replayed from ledger state"
                    )
                if receipt.study_id != self.study_id or receipt.arm != arm:
                    raise Exp2CoordinatorError("Exp2 journal stage binding drift")
                if receipt.case_ids != self._stage_cases(plan, receipt.stage):
                    raise Exp2CoordinatorError("Exp2 journal case schedule drift")
                if receipt.execution_mode != plan.execution_mode:
                    raise Exp2CoordinatorError("Exp2 journal execution mode drift")
                receipt_digest = receipt.to_dict()["record_digest"]
                recorded = list(updated.get("receipt_digests") or [])
                if receipt_digest in recorded:
                    raise Exp2CoordinatorError("Exp2 journal stage is duplicated")
                recorded.append(receipt_digest)
                updated.update(
                    {
                        "state": next_state,
                        "awaiting_after": receipt.stage
                        if next_state == "ATTRIBUTION_AWAITING"
                        else None,
                        "receipt_digests": recorded,
                    }
                )
            elif kind == "attribution_recorded":
                record = Exp2AttributionRecord.from_dict(payload)
                awaiting = str(updated.get("awaiting_after") or "")
                if updated.get("state") != "ATTRIBUTION_AWAITING":
                    raise Exp2CoordinatorError(
                        "Exp2 journal attribution cannot be replayed from ledger state"
                    )
                if record.stage != awaiting or record.arm != "H1":
                    raise Exp2CoordinatorError("Exp2 journal attribution binding drift")
                updated.update(
                    {
                        "state": (
                            "H1B_LOCKED" if awaiting == "H1A_PUBLIC" else "H1C_LOCKED"
                        ),
                        "awaiting_after": None,
                        "attribution_digest": record.record_digest,
                    }
                )
            elif kind == "public_gate_recorded":
                raw_gate = payload.get("gate")
                if not isinstance(raw_gate, Mapping):
                    raise Exp2CoordinatorError("Exp2 public gate is missing")
                gate = Exp2PublicRegressionGate.from_dict(raw_gate)
                if updated.get("state") != "PUBLIC_GATE_AWAITING":
                    raise Exp2CoordinatorError(
                        "Exp2 journal public gate cannot be replayed from ledger state"
                    )
                updated["state"] = "SEALED_UNLOCKED" if gate.passed else "BLOCKED"
            elif kind == "holdout_burned":
                raw_burn = payload.get("burn")
                if not isinstance(raw_burn, Mapping):
                    raise Exp2CoordinatorError("Exp2 Holdout burn is missing")
                burn = Exp2HoldoutBurnRecord.from_dict(raw_burn)
                updated["state"] = "BLOCKED"
                updated["holdout_burn_digest"] = burn.record_digest
            elif kind == "sealed_aggregate_recorded":
                raw_aggregate = payload.get("aggregate")
                if not isinstance(raw_aggregate, Mapping):
                    raise Exp2CoordinatorError("Exp2 sealed aggregate is missing")
                aggregate = Exp2SealedAggregate.from_dict(raw_aggregate)
                if updated.get("state") != "SEALED_UNLOCKED":
                    raise Exp2CoordinatorError(
                        "Exp2 journal sealed aggregate cannot be replayed from ledger state"
                    )
                if aggregate.treatment_lock_digest != updated.get("public_gate_digest"):
                    raise Exp2CoordinatorError(
                        "Exp2 sealed aggregate is not bound to the public treatment lock"
                    )
                updated["state"] = (
                    "BLOCKED"
                    if aggregate.regression_count or aggregate.invalid_count
                    else "HOLDOUT_COMPLETE"
                )
                updated["sealed_aggregate_digest"] = aggregate.record_digest
            else:
                raise Exp2CoordinatorError(f"unsupported Exp2 event kind: {kind}")
            updated["event_sequence"] = int(event["sequence"])
        return updated

    def _append_event(self, kind: str, payload: Mapping[str, Any]) -> str:
        events = self._load_events()
        previous = str(events[-1].get("record_digest") or "") if events else None
        sequence = len(events) + 1
        event = record_with_digest(
            {
                "schema": "autobugfix-exp2-event-v1",
                "study_id": self.study_id,
                "sequence": sequence,
                "kind": kind,
                "previous_digest": previous,
                "payload": dict(payload),
            }
        )
        self.events_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(
            self.events_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return str(event["record_digest"])

    def status(self) -> dict[str, Any]:
        ledger = self._load_ledger() if self.ledger_path.exists() else None
        if ledger is None:
            return {
                "study_id": self.study_id,
                "state": "UNINITIALIZED",
                "next_action": "initialize",
            }
        return {
            "study_id": self.study_id,
            "state": ledger["state"],
            "awaiting_after": ledger.get("awaiting_after"),
            "receipt_digests": list(ledger.get("receipt_digests") or []),
            "attribution_digest": ledger.get("attribution_digest"),
            "public_gate_digest": ledger.get("public_gate_digest"),
            "holdout_burn_digest": ledger.get("holdout_burn_digest"),
            "sealed_aggregate_digest": ledger.get("sealed_aggregate_digest"),
            "next_action": self._next_action(
                str(ledger["state"]), ledger.get("awaiting_after")
            ),
            "ledger_digest": ledger["record_digest"],
        }

    @staticmethod
    def _next_action(state: str, awaiting_after: str | None) -> str:
        if state == "PREPARED":
            return "run_h0_calibration"
        if state == "H0_CALIBRATED":
            return "run_h0_public_baseline"
        if state == "H0_COMPLETE":
            return "run_h1a_public"
        if state == "ATTRIBUTION_AWAITING":
            return f"provide_attribution_after_{str(awaiting_after).lower()}"
        if state == "H1B_LOCKED":
            return "run_h1b_public"
        if state == "H1C_LOCKED":
            return "run_public_replay"
        if state == "PUBLIC_GATE_AWAITING":
            return "record_public_regression_gate"
        if state == "SEALED_UNLOCKED":
            return "run_guard_sealed_holdout"
        if state == "HOLDOUT_COMPLETE":
            return "report"
        return "terminal"

    def _stage_cases(
        self,
        plan: Exp2StudyPlan,
        stage: Exp2StageName,
    ) -> tuple[str, ...]:
        if stage == "H0_CALIBRATION":
            return plan.calibration_case_ids
        if stage == "H0_PUBLIC":
            return plan.public_case_ids
        if stage == "H1A_PUBLIC":
            return plan.public_case_ids[:2]
        if stage == "H1B_PUBLIC":
            return plan.public_case_ids[2:5]
        if stage == "PUBLIC_REPLAY":
            return plan.public_case_ids
        raise Exp2CoordinatorError(
            "sealed Holdout IDs remain Guard-private and are not coordinator input"
        )

    def _stage_binding(self, plan: Exp2StudyPlan, arm: Exp2Arm) -> Path:
        return Path(
            plan.h0_binding_path if arm == "H0" else plan.candidate_binding_path
        )

    def _stage_mode(self, plan: Exp2StudyPlan) -> Exp2ExecutionMode:
        return plan.execution_mode

    def budget_allocation(self, wave: int) -> dict[str, Any]:
        """Return the opaque Operator namespace without mutating Operator state."""

        plan = self.load_plan()
        allocations = validate_wave_schedule(plan.public_case_ids)
        allocation = allocations.get(wave)
        if allocation is None:
            raise Exp2CoordinatorError("Exp2 budget wave must be 3, 8, or 16")
        return allocation.to_dict()

    @staticmethod
    def _execution_receipt(
        report: Mapping[str, Any], execution_mode: Exp2ExecutionMode
    ) -> Mapping[str, Any]:
        raw = report.get("execution_receipt")
        if not isinstance(raw, Mapping):
            raise Exp2CoordinatorError(
                "Exp2 report has no trusted execution-mode receipt"
            )
        try:
            verify_record(raw)
        except Exception as exc:
            raise Exp2CoordinatorError(
                "Exp2 execution-mode receipt digest is invalid"
            ) from exc
        if raw.get("schema") != "autobugfix-exp2-execution-receipt-v1":
            raise Exp2CoordinatorError("Exp2 execution-mode receipt schema is invalid")
        if raw.get("execution_mode") != execution_mode:
            raise Exp2CoordinatorError(
                "Exp2 execution-mode receipt differs from the requested mode"
            )
        direct = raw.get("direct_sdk_in_process")
        outer = raw.get("outer_bubblewrap")
        if type(direct) is not bool or type(outer) is not bool:
            raise Exp2CoordinatorError(
                "Exp2 execution-mode receipt flags must be boolean"
            )
        if execution_mode == "workspace_only":
            if direct is not True or outer is not False:
                raise Exp2CoordinatorError(
                    "workspace-only stage lacks direct SDK/Bubblewrap-free proof"
                )
            preflight = raw.get("workspace_only_preflight_digest")
            if not isinstance(preflight, str) or len(preflight) != 64 or any(
                character not in "0123456789abcdef" for character in preflight
            ):
                raise Exp2CoordinatorError(
                    "workspace-only stage lacks a valid preflight digest"
                )
        elif direct is not False or outer is not True:
            raise Exp2CoordinatorError(
                "protected stage execution flags differ from the frozen contract"
            )
        for field_name in ("broker_result_digest", "broker_command_digest"):
            value = raw.get(field_name)
            if not isinstance(value, str) or len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise Exp2CoordinatorError(
                    f"Exp2 execution receipt {field_name} is invalid"
                )
        if not isinstance(raw.get("task_worktree_path"), str) or not str(
            raw.get("task_worktree_path")
        ).strip():
            raise Exp2CoordinatorError(
                "Exp2 execution receipt has no task worktree identity"
            )
        return raw

    @staticmethod
    def _frozen_input_digest(
        report: Mapping[str, Any], execution_mode: Exp2ExecutionMode
    ) -> str:
        Exp2Coordinator._execution_receipt(report, execution_mode)
        required = (
            "protocol_digest",
            "subject_runtime_contract_digest",
            "subject_runtime_digest",
            "evaluator_runtime_id",
            "memory_digest",
            "role_config_digest",
            "policy_digest",
        )
        if any(not str(report.get(field) or "") for field in required):
            raise Exp2CoordinatorError(
                "Exp2 report is missing a frozen cross-arm input identity"
            )
        runtime = report.get("codex_runtime")
        if not isinstance(runtime, Mapping):
            raise Exp2CoordinatorError("Exp2 report Codex runtime identity is missing")
        return digest_payload(
            {
                "schema": "autobugfix-exp2-frozen-inputs-v1",
                "protocol_digest": report["protocol_digest"],
                "subject_runtime_contract_digest": report[
                    "subject_runtime_contract_digest"
                ],
                "subject_runtime_digest": report["subject_runtime_digest"],
                "evaluator_runtime_id": report["evaluator_runtime_id"],
                "codex_runtime": dict(runtime),
                "memory_digest": report["memory_digest"],
                "role_config_digest": report["role_config_digest"],
                "policy_digest": report["policy_digest"],
                "execution_mode": execution_mode,
                "case_concurrency": 1,
            }
        )

    def record_stage(
        self,
        *,
        stage: Exp2StageName,
        reports: Sequence[Mapping[str, Any]],
        subject_sha: str,
        execution_mode: Exp2ExecutionMode,
    ) -> dict[str, Any]:
        plan = self.load_plan()
        ledger = self._load_ledger()
        transition = _STAGE_TRANSITIONS.get(stage)
        if transition is None:
            raise Exp2CoordinatorError(f"unsupported Exp2 stage: {stage}")
        expected_previous, next_state, arm = transition
        if ledger.get("state") != expected_previous:
            raise Exp2CoordinatorError(
                f"Exp2 stage {stage} requires {expected_previous}, got {ledger.get('state')}"
            )
        expected_cases = self._stage_cases(plan, stage)
        if len(reports) != len(expected_cases):
            raise Exp2CoordinatorError(
                f"Exp2 stage {stage} requires {len(expected_cases)} reports"
            )
        projections = tuple(
            Exp2ResultProjection.from_report(
                report,
                study_id=self.study_id,
                arm=arm,
                stage=stage,
                case_id=expected_cases[index],
            )
            for index, report in enumerate(reports)
        )
        observed_cases = tuple(item.case_id for item in projections)
        if observed_cases != expected_cases:
            raise Exp2CoordinatorError(
                f"Exp2 stage {stage} case order differs from the frozen schedule"
            )
        if any(item.executed_subject_sha != subject_sha for item in projections):
            raise Exp2CoordinatorError(
                "Exp2 stage executed subject SHA is inconsistent"
            )
        frozen_input_digests = tuple(
            self._frozen_input_digest(report, execution_mode) for report in reports
        )
        if any(digest != frozen_input_digests[0] for digest in frozen_input_digests):
            raise Exp2CoordinatorError(
                "Exp2 stage reports do not share frozen cross-arm inputs"
            )
        binding_digests = tuple(
            str(report.get("study_binding_digest") or "") for report in reports
        )
        if not binding_digests or any(
            not digest or digest != binding_digests[0] for digest in binding_digests
        ):
            raise Exp2CoordinatorError(
                "Exp2 stage reports do not share one frozen Study binding"
            )
        if execution_mode != self._stage_mode(plan):
            raise Exp2CoordinatorError("Exp2 stage execution mode differs from plan")
        execution_receipts = tuple(
            self._execution_receipt(report, execution_mode) for report in reports
        )
        direct_sdk_in_process = tuple(
            bool(receipt["direct_sdk_in_process"])
            for receipt in execution_receipts
        )
        outer_bubblewrap = tuple(
            bool(receipt["outer_bubblewrap"]) for receipt in execution_receipts
        )
        preflight_digests = tuple(
            str(receipt["workspace_only_preflight_digest"])
            for receipt in execution_receipts
            if execution_mode == "workspace_only"
        )
        if arm == "H1":
            h0_receipts = [
                item
                for item in self._stage_receipts()
                if item.get("stage") == "H0_PUBLIC"
            ]
            if not h0_receipts:
                raise Exp2CoordinatorError(
                    "H1 stage requires a frozen H0 public input receipt"
                )
            if frozen_input_digests[0] != h0_receipts[-1].get("frozen_input_digest"):
                raise Exp2CoordinatorError(
                    "H1 stage changed a frozen protocol/runtime/Memory input"
                )
        prior_arm_cases = {
            (str(receipt.get("arm") or ""), str(case_id))
            for receipt in self._stage_receipts()
            for case_id in receipt.get("case_ids") or []
        }
        same_arm_retry = {(arm, case_id) for case_id in observed_cases}.intersection(
            prior_arm_cases
        )
        if stage != "PUBLIC_REPLAY" and same_arm_retry:
            raise Exp2CoordinatorError(
                "same-case official retry is forbidden in the public loop"
            )
        if stage == "H1A_PUBLIC":
            h0_receipts = [
                item
                for item in self._stage_receipts()
                if item.get("stage") == "H0_PUBLIC"
            ]
            if h0_receipts and subject_sha == h0_receipts[-1].get("subject_sha"):
                raise Exp2CoordinatorError("H1A candidate must differ from frozen H0")
        if stage == "H1B_PUBLIC":
            if not ledger.get("attribution_digest"):
                raise Exp2CoordinatorError(
                    "H1B requires the preceding attribution receipt"
                )
            h1a_receipts = [
                item
                for item in self._stage_receipts()
                if item.get("stage") == "H1A_PUBLIC"
            ]
            if h1a_receipts:
                previous = h1a_receipts[-1]
                if subject_sha == previous.get("subject_sha"):
                    raise Exp2CoordinatorError(
                        "H1B requires a new Operator candidate after attribution"
                    )
                if binding_digests[0] == previous.get("binding_digest"):
                    raise Exp2CoordinatorError(
                        "H1B requires a new frozen candidate binding"
                    )
        if stage == "PUBLIC_REPLAY":
            if not ledger.get("attribution_digest"):
                raise Exp2CoordinatorError(
                    "public replay requires the preceding attribution receipt"
                )
            h1b_receipts = [
                item
                for item in self._stage_receipts()
                if item.get("stage") == "H1B_PUBLIC"
            ]
            if not h1b_receipts:
                raise Exp2CoordinatorError(
                    "public replay requires a completed H1B candidate stage"
                )
            previous = h1b_receipts[-1]
            if subject_sha != previous.get("subject_sha"):
                raise Exp2CoordinatorError(
                    "public replay must use the frozen H1C subject"
                )
            if binding_digests[0] != previous.get("binding_digest"):
                raise Exp2CoordinatorError(
                    "public replay must use the frozen H1C binding"
                )
        receipt = Exp2StageReceipt(
            stage_id=f"stage-{len(ledger.get('receipt_digests') or []) + 1:02d}-{stage.lower()}",
            study_id=self.study_id,
            stage=stage,
            arm=arm,
            case_ids=observed_cases,
            projection_digests=tuple(projection_digest(item) for item in projections),
            report_digests=tuple(str(report["record_digest"]) for report in reports),
            subject_sha=subject_sha,
            frozen_input_digest=frozen_input_digests[0],
            binding_digest=binding_digests[0],
            execution_mode=execution_mode,
            direct_sdk_in_process=direct_sdk_in_process[0],
            outer_bubblewrap=outer_bubblewrap[0],
            workspace_only_preflight_digests=preflight_digests,
            attribution_digest=(
                str(ledger["attribution_digest"])
                if stage in {"H1B_PUBLIC", "PUBLIC_REPLAY"}
                else None
            ),
        )
        event_digest = self._append_event(
            "stage_completed",
            {
                "stage_receipt": receipt.to_dict(),
                "projections": [item.to_dict() for item in projections],
            },
        )
        updated = dict(ledger)
        updated.update(
            {
                "state": next_state,
                "awaiting_after": stage
                if next_state == "ATTRIBUTION_AWAITING"
                else None,
                "receipt_digests": [
                    *(ledger.get("receipt_digests") or []),
                    receipt.to_dict()["record_digest"],
                ],
                "event_sequence": int(ledger.get("event_sequence") or 0) + 1,
            }
        )
        self._save_ledger(updated)
        return {
            "status": "recorded",
            "stage": stage,
            "state": next_state,
            "stage_receipt": receipt.to_dict(),
            "event_digest": event_digest,
        }

    def _stage_receipts(self) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for event in self._load_events():
            payload = event.get("payload") or {}
            if event.get("kind") != "stage_completed":
                continue
            receipt = payload.get("stage_receipt")
            if not isinstance(receipt, Mapping):
                raise Exp2CoordinatorError("Exp2 stage receipt is missing")
            receipts.append(Exp2StageReceipt.from_dict(receipt).to_dict())
        return receipts

    def _stage_receipts_with_projections(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for event in self._load_events():
            payload = event.get("payload") or {}
            if event.get("kind") != "stage_completed":
                continue
            receipt = payload.get("stage_receipt")
            projections = payload.get("projections")
            if not isinstance(receipt, Mapping) or not isinstance(projections, list):
                raise Exp2CoordinatorError("Exp2 stage event is incomplete")
            typed_receipt = Exp2StageReceipt.from_dict(receipt)
            typed_projections = tuple(
                Exp2ResultProjection.from_dict(item)
                for item in projections
                if isinstance(item, Mapping)
            )
            values.append(
                {
                    "stage": typed_receipt.stage,
                    "projections": [item.to_dict() for item in typed_projections],
                }
            )
        return values

    def record_attribution(
        self,
        attribution: Exp2AttributionRecord | Mapping[str, Any],
    ) -> dict[str, Any]:
        record = (
            attribution
            if isinstance(attribution, Exp2AttributionRecord)
            else Exp2AttributionRecord.from_dict(attribution)
        )
        if record.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 attribution study_id differs from ledger")
        ledger = self._load_ledger()
        awaiting = str(ledger.get("awaiting_after") or "")
        if ledger.get("state") != "ATTRIBUTION_AWAITING":
            raise Exp2CoordinatorError("Exp2 coordinator is not awaiting attribution")
        if record.stage != awaiting or record.arm != "H1":
            raise Exp2CoordinatorError(
                "Exp2 attribution does not bind the awaiting stage"
            )
        expected_revision = 1 if awaiting == "H1A_PUBLIC" else 2
        if record.revision != expected_revision:
            raise Exp2CoordinatorError(
                f"Exp2 attribution revision must be {expected_revision} for {awaiting}"
            )
        receipts = self._stage_receipts()
        matching = [
            item
            for item in receipts
            if item.get("stage") == awaiting and item.get("study_id") == self.study_id
        ]
        if len(matching) != 1:
            raise Exp2CoordinatorError("Exp2 attribution source stage is not unique")
        if record.source_projection_digest not in set(
            matching[0].get("projection_digests", [])
        ):
            raise Exp2CoordinatorError("Exp2 attribution source projection is unknown")
        if record.parent_candidate_sha != matching[0].get("subject_sha"):
            raise Exp2CoordinatorError("Exp2 attribution parent candidate SHA differs")
        next_state = "H1B_LOCKED" if awaiting == "H1A_PUBLIC" else "H1C_LOCKED"
        event_digest = self._append_event("attribution_recorded", record.to_dict())
        updated = dict(ledger)
        updated.update(
            {
                "state": next_state,
                "awaiting_after": None,
                "attribution_digest": record.record_digest,
                "event_sequence": int(ledger.get("event_sequence") or 0) + 1,
            }
        )
        self._save_ledger(updated)
        return {
            "status": "attribution_recorded",
            "state": next_state,
            "attribution_digest": record.record_digest,
            "event_digest": event_digest,
        }

    def record_public_regression_gate(
        self,
        gate: Exp2PublicRegressionGate | Mapping[str, Any],
    ) -> dict[str, Any]:
        record = (
            gate
            if isinstance(gate, Exp2PublicRegressionGate)
            else Exp2PublicRegressionGate.from_dict(gate)
        )
        if record.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 public gate study_id differs from ledger")
        ledger = self._load_ledger()
        if ledger.get("state") != "PUBLIC_GATE_AWAITING":
            raise Exp2CoordinatorError(
                "Exp2 coordinator is not awaiting the public gate"
            )
        summary = self.paired_public_summary()
        if record.paired_public_digest != summary["record_digest"]:
            raise Exp2CoordinatorError("Exp2 public gate summary digest differs")
        receipts = self._stage_receipts()
        replay = [item for item in receipts if item.get("stage") == "PUBLIC_REPLAY"]
        if len(replay) != 1:
            raise Exp2CoordinatorError("Exp2 public replay receipt is not unique")
        replay_receipt = replay[0]
        if record.h1_subject_sha != replay_receipt.get("subject_sha"):
            raise Exp2CoordinatorError("Exp2 public gate subject differs from replay")
        if record.h1_binding_digest != replay_receipt.get("binding_digest"):
            raise Exp2CoordinatorError("Exp2 public gate binding differs from replay")
        if record.h1_regression_count != summary["regression_count"]:
            raise Exp2CoordinatorError("Exp2 public gate regression count differs")
        if record.h1_minus_h0_resolved != summary["h1_minus_h0_resolved"]:
            raise Exp2CoordinatorError("Exp2 public gate paired gain differs")
        if record.passed and summary["invalid_count"]:
            raise Exp2CoordinatorError(
                "Exp2 public gate cannot pass with an invalid arm"
            )
        event_digest = self._append_event(
            "public_gate_recorded", {"gate": record.to_dict()}
        )
        updated = dict(ledger)
        updated.update(
            {
                "state": "SEALED_UNLOCKED" if record.passed else "BLOCKED",
                "event_sequence": int(ledger.get("event_sequence") or 0) + 1,
                "public_gate_digest": record.record_digest,
            }
        )
        self._save_ledger(updated)
        return {
            "status": "public_gate_recorded",
            "state": updated["state"],
            "public_gate_digest": record.record_digest,
            "event_digest": event_digest,
        }

    def record_sealed_aggregate(
        self, aggregate: Exp2SealedAggregate | Mapping[str, Any]
    ) -> dict[str, Any]:
        record = (
            aggregate
            if isinstance(aggregate, Exp2SealedAggregate)
            else Exp2SealedAggregate.from_dict(aggregate)
        )
        if record.study_id != self.study_id:
            raise Exp2CoordinatorError(
                "Exp2 sealed aggregate study_id differs from ledger"
            )
        ledger = self._load_ledger()
        if ledger.get("state") != "SEALED_UNLOCKED":
            raise Exp2CoordinatorError("Exp2 sealed Holdout is not unlocked")
        if record.treatment_lock_digest != ledger.get("public_gate_digest"):
            raise Exp2CoordinatorError(
                "Exp2 sealed aggregate is not bound to the public treatment lock"
            )
        event_digest = self._append_event(
            "sealed_aggregate_recorded", {"aggregate": record.to_dict()}
        )
        updated = dict(ledger)
        updated.update(
            {
                "state": (
                    "BLOCKED"
                    if record.regression_count or record.invalid_count
                    else "HOLDOUT_COMPLETE"
                ),
                "sealed_aggregate_digest": record.record_digest,
                "event_sequence": int(ledger.get("event_sequence") or 0) + 1,
            }
        )
        self._save_ledger(updated)
        return {
            "status": "sealed_aggregate_recorded",
            "state": updated["state"],
            "sealed_aggregate_digest": record.record_digest,
            "event_digest": event_digest,
        }

    def record_holdout_burn(
        self, burn: Exp2HoldoutBurnRecord | Mapping[str, Any]
    ) -> dict[str, Any]:
        record = (
            burn
            if isinstance(burn, Exp2HoldoutBurnRecord)
            else Exp2HoldoutBurnRecord.from_dict(burn)
        )
        if record.study_id != self.study_id:
            raise Exp2CoordinatorError("Exp2 Holdout burn study_id differs from ledger")
        ledger = self._load_ledger()
        if ledger.get("state") in {"HOLDOUT_COMPLETE", "REPORTED"}:
            raise Exp2CoordinatorError("Exp2 Holdout burn is too late for this ledger")
        event_digest = self._append_event("holdout_burned", {"burn": record.to_dict()})
        updated = dict(ledger)
        updated.update(
            {
                "state": "BLOCKED",
                "holdout_burn_digest": record.record_digest,
                "event_sequence": int(ledger.get("event_sequence") or 0) + 1,
            }
        )
        self._save_ledger(updated)
        return {
            "status": "holdout_burned",
            "state": "BLOCKED",
            "holdout_burn_digest": record.record_digest,
            "event_digest": event_digest,
        }

    def paired_public_summary(self) -> dict[str, Any]:
        """Reduce the frozen H0/H1 public projections only."""

        events = self._stage_receipts_with_projections()
        h0 = next(
            (item["projections"] for item in events if item["stage"] == "H0_PUBLIC"),
            None,
        )
        h1 = next(
            (
                item["projections"]
                for item in events
                if item["stage"] == "PUBLIC_REPLAY"
            ),
            None,
        )
        if h0 is None or h1 is None:
            raise Exp2CoordinatorError(
                "paired public summary requires completed H0 and H1 public stages"
            )
        return reduce_paired_public(h0, h1).to_dict()

    def resume(self, executor: StageExecutor | None = None) -> dict[str, Any]:
        plan = self.load_plan()
        current = self.status()
        state = str(current["state"])
        if state in {"HOLDOUT_COMPLETE", "REPORTED", "ROLLED_BACK", "BLOCKED"}:
            return {"status": "terminal", **current}
        if state == "ATTRIBUTION_AWAITING":
            return {
                "status": "blocked",
                "reason": "operator attribution and scoped candidate transition are required",
                **current,
            }
        next_stage: Exp2StageName | None
        arm: Exp2Arm
        if state == "PREPARED":
            next_stage, arm = "H0_CALIBRATION", "H0"
        elif state == "H0_CALIBRATED":
            next_stage, arm = "H0_PUBLIC", "H0"
        elif state == "H0_COMPLETE":
            next_stage, arm = "H1A_PUBLIC", "H1"
        elif state == "H1B_LOCKED":
            next_stage, arm = "H1B_PUBLIC", "H1"
        elif state == "H1C_LOCKED":
            next_stage, arm = "PUBLIC_REPLAY", "H1"
        elif state == "SEALED_UNLOCKED":
            return {
                "status": "blocked",
                "reason": "sealed Holdout must be executed and signed by Guard",
                **current,
            }
        else:
            raise Exp2CoordinatorError(f"unsupported Exp2 ledger state: {state}")
        cases = self._stage_cases(plan, next_stage)
        binding = self._stage_binding(plan, arm)
        if executor is None:
            return {
                "status": "ready",
                "stage": next_stage,
                "arm": arm,
                "case_ids": list(cases),
                "binding_path": str(binding),
                "execution_mode": plan.execution_mode,
                "disposable_root": plan.disposable_root,
                **current,
            }
        reports = executor(next_stage, cases, binding, arm)
        result = self.record_stage(
            stage=next_stage,
            reports=reports,
            subject_sha=str(reports[0].get("executed_subject_sha") if reports else ""),
            execution_mode=plan.execution_mode,
        )
        return {"status": "advanced", **result}
