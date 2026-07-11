#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from typing import Any


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def _command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("command") or "")


def _direct_authority_mutation(command: str) -> bool:
    mutating_command = re.search(
        r"(?:^|[;&|]\s*)(?:\S*/)?(?:rm|mv|cp|install|touch|truncate|chmod|chown|tee|dd|sqlite3)\b",
        command,
    )
    mutating_git = re.search(
        r"(?:^|[;&|]\s*)(?:\S*/)?git\s+(?:clean|checkout|restore)\b",
        command,
    )
    in_place_edit = re.search(r"(?:^|[;&|]\s*)(?:\S*/)?sed\b[^\n]*(?:\s-i|--in-place)", command)
    redirect = re.search(r"(?:^|[^<])>>?", command)
    return bool(mutating_command or mutating_git or in_place_edit or redirect)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        return 0
    command = _command(payload)
    if not command:
        return 0
    direct_merge = re.search(r"(?:^|[;&|]\s*)(?:\S*/)?git\s+merge\b", command)
    gh_merge = re.search(r"(?:^|[;&|]\s*)gh\s+pr\s+merge\b", command)
    if direct_merge or gh_merge:
        _deny("Operator candidates must use promotion and a reviewed pull request; direct merge is blocked.")
        return 0
    push = re.search(r"(?:^|[;&|]\s*)(?:\S*/)?git\s+push\b([^\n]*)", command)
    if push:
        arguments = push.group(1)
        if re.search(r"(?:^|\s)(?:-f|--force(?:-with-lease)?)(?:\s|$)", arguments):
            _deny("Force-push is outside the Autobugfix promotion and rollback contract.")
            return 0
        if re.search(r"(?:^|[\s:])(main|master)(?:$|[\s:])", arguments):
            _deny("Direct pushes to protected branches are blocked; push the candidate branch and open a PR.")
            return 0
    authority_markers = (
        ".autobugfix/operator-v3",
        ".autobugfix/operator-artifacts",
        "governance.sqlite3",
        ".autobugfix/active-release",
    )
    if (
        any(marker in command for marker in authority_markers)
        and _direct_authority_mutation(command)
        and not re.search(
        r"(?:uv\s+run\s+)?autobugfix\s+operator\b", command
        )
    ):
        _deny("Operator authority state is service-owned; use the typed Autobugfix Operator CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
