from __future__ import annotations

from dataclasses import dataclass
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
    if not isinstance(data, dict):
        lowered = text.lower()
        if "needs_changes" in lowered or "needs changes" in lowered:
            return EvaluatorDecision("needs_changes", text.strip())
        if "blocked" in lowered:
            return EvaluatorDecision("blocked", text.strip())
        if "pass" in lowered:
            return EvaluatorDecision("pass", text.strip())
        return EvaluatorDecision("needs_changes", "Evaluator output was not structured YAML.")
    decision = str(data.get("decision", "needs_changes")).strip()
    if decision not in {"pass", "needs_changes", "blocked"}:
        decision = "needs_changes"
    return EvaluatorDecision(decision=decision, reason=str(data.get("reason", "")).strip())
