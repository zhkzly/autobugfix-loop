from __future__ import annotations

from pathlib import Path


ROLE_SKILL_PATHS = {
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


class PromptError(RuntimeError):
    pass


def load_role_instructions(project_root: Path, role: str, strict: bool = True) -> str:
    paths = ROLE_SKILL_PATHS.get(role)
    if not paths:
        raise PromptError(f"unknown role: {role}")
    parts: list[str] = []
    for rel in paths:
        path = project_root / rel
        if not path.exists():
            if strict:
                raise PromptError(f"missing role skill for {role}: {rel}")
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def assert_skill_guard(project_root: Path, role: str, instructions: str) -> None:
    expected = set(ROLE_SKILL_PATHS[role])
    leaked = []
    for other_role, paths in ROLE_SKILL_PATHS.items():
        if other_role == role:
            continue
        for rel in paths:
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
