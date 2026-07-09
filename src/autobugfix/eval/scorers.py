from __future__ import annotations

from pathlib import Path

from autobugfix.eval.models import EvalScore


def normalize_diff(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def score_case(case_dir: Path) -> EvalScore:
    generated = (case_dir / "generated.diff").read_text(encoding="utf-8") if (case_dir / "generated.diff").exists() else ""
    oracle = (case_dir / "oracle.diff").read_text(encoding="utf-8") if (case_dir / "oracle.diff").exists() else ""
    generated_non_empty = bool(generated.strip())
    equals = normalize_diff(generated) == normalize_diff(oracle)
    return EvalScore(
        decision="pass" if generated_non_empty and equals else "fail",
        generated_equals_oracle=equals,
        generated_non_empty=generated_non_empty,
    )
