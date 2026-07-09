from __future__ import annotations

from typing import Any


def render_digest(packet: dict[str, Any]) -> str:
    task = packet["task"]
    events = packet.get("events", [])
    artifacts = packet.get("artifacts", {})
    lines = [
        f"# Task Digest: {task['task_id']}",
        "",
        f"- repo: {task['repo_id']}",
        f"- title: {task['title']}",
        f"- final_state: {task['state']}",
        f"- branch: {task.get('branch') or ''}",
        f"- worktree: {task.get('worktree_path') or ''}",
        "",
        "## Evidence",
        f"- events: {len(events)}",
        f"- artifacts: {', '.join(sorted(artifacts)) or 'none'}",
        f"- block_reason: {task.get('block_reason') or ''}",
        "",
        "## Context",
        packet.get("context") or "(none)",
        "",
        "## Test Result Excerpt",
        (artifacts.get("test-result.md") or "(missing)")[:4000],
        "",
        "## Diff Excerpt",
        (artifacts.get("diff.patch") or "(missing)")[:4000],
        "",
    ]
    return "\n".join(lines)
