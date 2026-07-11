from __future__ import annotations

from pathlib import Path

import yaml

from autobugfix.eval.models import EvalObservation, EvalScore


def normalize_diff(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def score_observation(observation: EvalObservation) -> EvalScore:
    if observation.harness_error or observation.oracle_status == "error":
        return EvalScore(
            decision="error",
            failure_stage="harness",
            generated_equals_oracle=observation.generated_equals_oracle,
            generated_non_empty=observation.generated_non_empty,
            execution_verifier_passed=observation.execution_verifier_passed,
            execution_reached_human_gate=observation.execution_reached_human_gate,
            oracle_passed=False,
        )
    if observation.patch_required and not observation.generated_non_empty:
        failure_stage = "writer"
    elif observation.execution_verifier_passed is not True:
        failure_stage = "execution_verifier"
    elif not observation.execution_reached_human_gate:
        failure_stage = "execution_evaluator"
    elif observation.oracle_status != "passed":
        failure_stage = "oracle"
    else:
        failure_stage = None
    return EvalScore(
        decision="pass" if failure_stage is None else "fail",
        failure_stage=failure_stage,
        generated_equals_oracle=observation.generated_equals_oracle,
        generated_non_empty=observation.generated_non_empty,
        execution_verifier_passed=observation.execution_verifier_passed,
        execution_reached_human_gate=observation.execution_reached_human_gate,
        oracle_passed=observation.oracle_status == "passed",
    )


def score_case(case_dir: Path) -> EvalScore:
    path = case_dir / "observation.yaml"
    if not path.exists():
        raise ValueError(f"Eval case has no normalized observation: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"invalid Eval observation: {path}")
    return score_observation(EvalObservation.from_dict(data))
