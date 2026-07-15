from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import yaml


@dataclass(slots=True)
class EvaluatorDecision:
    decision: str
    reason: str

    @property
    def passed(self) -> bool:
        return self.decision == "pass"


def parse_evaluator_decision(text: str) -> EvaluatorDecision:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict) or set(data) != {"decision", "reason"}:
        data = _parse_exact_plain_contract(text)
    if not isinstance(data, dict) or set(data) != {"decision", "reason"}:
        return EvaluatorDecision("needs_changes", "Evaluator output was not structured YAML.")
    decision_value = data.get("decision")
    reason_value = data.get("reason")
    if not isinstance(decision_value, str) or not isinstance(reason_value, str):
        return EvaluatorDecision("needs_changes", "Evaluator output used invalid field types.")
    decision = decision_value.strip()
    if decision not in {"pass", "needs_changes", "blocked"}:
        return EvaluatorDecision("needs_changes", "Evaluator output used an invalid decision.")
    reason = reason_value.strip()
    if not reason:
        return EvaluatorDecision("needs_changes", "Evaluator output omitted its reason.")
    return EvaluatorDecision(decision=decision, reason=reason)


def _parse_exact_plain_contract(text: str) -> dict[str, str] | None:
    """Recover the exact two-field contract when a plain YAML reason contains `: `."""

    lines = text.strip().splitlines()
    if len(lines) != 2:
        return None
    decision_match = re.fullmatch(
        r"decision:\s*(pass|needs_changes|blocked)\s*",
        lines[0],
    )
    reason_match = re.fullmatch(r"reason:\s*(\S.*)", lines[1])
    if decision_match is None or reason_match is None:
        return None
    return {
        "decision": decision_match.group(1),
        "reason": reason_match.group(1),
    }
