from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvalCase:
    case_id: str
    repo: str
    worktree_path: Path
    base_commit: str
    final_commit: str
    problem_statement: str
    agent_prompt: str
    expected_behavior: str = ""
    raw: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "EvalCase":
        case_id = str(row.get("raw_id") or row.get("case_id") or row.get("id"))
        return cls(
            case_id=case_id,
            repo=str(row["repo"]),
            worktree_path=Path(str(row["worktree_path"])).resolve(),
            base_commit=str(row["base_commit"]),
            final_commit=str(row["final_commit"]),
            problem_statement=str(row.get("problem_statement") or row.get("agent_prompt") or ""),
            agent_prompt=str(row.get("agent_prompt") or row.get("problem_statement") or ""),
            expected_behavior=str(row.get("expected_behavior") or ""),
            raw=row,
        )


@dataclass(slots=True)
class EvalScore:
    decision: str
    generated_equals_oracle: bool
    generated_non_empty: bool
