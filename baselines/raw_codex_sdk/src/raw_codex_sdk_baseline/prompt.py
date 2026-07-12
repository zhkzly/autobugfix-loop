from __future__ import annotations

import hashlib

from raw_codex_sdk_baseline.models import CaseBundle


DEVELOPER_INSTRUCTIONS = """You are a coding agent operating directly in the current Git repository.
Resolve the supplied issue by inspecting and editing implementation files in this repository.
Work only inside the current repository. Do not request human input.
Do not modify tests, benchmark metadata, build metadata, or Git configuration to evade evaluation.
Run relevant commands that are genuinely available in the repository when useful.
Finish after making the best complete repair possible in this single turn.
"""

PROMPT_TEMPLATE = """Fix the following repository issue.

Issue:
{problem_statement}

Expected behavior:
{expected_behavior}

Visible evidence:
{visible_evidence}

Visible attachments:
{attachments}

Inspect the repository, implement the repair, and leave all code changes in the current worktree.
"""


def _template_digest() -> str:
    payload = DEVELOPER_INSTRUCTIONS + "\n---PROMPT---\n" + PROMPT_TEMPLATE
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


PROMPT_TEMPLATE_DIGEST = _template_digest()


def _section(values: tuple[str, ...]) -> str:
    if not values:
        return "(none provided)"
    return "\n".join(f"- {value}" for value in values)


def render_prompt(case: CaseBundle) -> str:
    return PROMPT_TEMPLATE.format(
        problem_statement=case.problem_statement,
        expected_behavior=case.expected_behavior or "Resolve the reported behavior.",
        visible_evidence=_section(case.visible_evidence),
        attachments=_section(case.attachments),
    )
