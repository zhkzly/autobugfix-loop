from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_ROLE_SKILL_PATHS = {
    "writer": [
        ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
        ".agents/role-skills/execution/writer/autobugfix-writer/SKILL.md",
    ],
    "evaluator": [
        ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
        ".agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md",
    ],
    "memory_maintainer": [
        ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
        ".agents/role-skills/memory/maintainer/autobugfix-memory-maintainer/SKILL.md",
    ],
    "eval_judge": [
        ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
        ".agents/role-skills/eval/judge/autobugfix-eval-judge/SKILL.md",
    ],
}

ROLE_SKILL_PATHS = DEFAULT_ROLE_SKILL_PATHS


class PromptError(RuntimeError):
    pass


def _display_path(project_root: Path, path: Path | str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def _resolve_paths(project_root: Path, paths: Iterable[Path | str]) -> list[Path]:
    resolved = []
    for value in paths:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = project_root / path
        resolved.append(path.resolve())
    return resolved


def load_role_instructions(
    project_root: Path,
    role: str,
    strict: bool = True,
    skill_paths: Iterable[Path | str] | None = None,
) -> str:
    paths = list(skill_paths) if skill_paths is not None else ROLE_SKILL_PATHS.get(role)
    if not paths:
        raise PromptError(f"unknown role: {role}")
    parts: list[str] = []
    for path in _resolve_paths(project_root, paths):
        if not path.exists():
            if strict:
                raise PromptError(f"missing role skill for {role}: {_display_path(project_root, path)}")
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def assert_skill_guard(
    project_root: Path,
    role: str,
    instructions: str,
    expected_paths: Iterable[Path | str] | None = None,
    role_catalog: Mapping[str, Iterable[Path | str]] | None = None,
) -> None:
    expected_values = expected_paths if expected_paths is not None else ROLE_SKILL_PATHS[role]
    expected = {_display_path(project_root, path) for path in _resolve_paths(project_root, expected_values)}
    catalog = role_catalog if role_catalog is not None else ROLE_SKILL_PATHS
    leaked = []
    for other_role, paths in catalog.items():
        if other_role == role:
            continue
        for path_value in paths:
            rel = _display_path(project_root, path_value)
            if rel in expected:
                continue
            marker = Path(rel).parent.name
            if marker in instructions:
                leaked.append(rel)
    if leaked:
        raise PromptError(f"role {role} instructions include unexpected role material: {leaked}")


def writer_prompt(task_text: str, context: str, feedback: str, memory_context: str = "") -> str:
    return "\n\n".join(
        [
            "You are the Autobugfix writer. Fix the smallest code path in this worktree.",
            "Task:",
            task_text,
            "Context:",
            context or "(none)",
            "Feedback:",
            feedback or "(none)",
            "Approved memory context:",
            memory_context or "(none)",
            "After editing, summarize files changed and tests you expect to pass.",
        ]
    )


def evaluator_prompt(task_text: str, diff_text: str, test_result: str) -> str:
    return "\n\n".join(
        [
            "You are the Autobugfix evaluator. Review only the provided worktree diff and verifier result.",
            "Return YAML with keys: decision: pass|needs_changes|blocked, reason: string.",
            "Task:",
            task_text,
            "Diff:",
            diff_text or "(empty)",
            "Verifier:",
            test_result,
        ]
    )
