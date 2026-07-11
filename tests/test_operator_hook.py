from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


HOOK = Path(__file__).parents[1] / ".codex/hooks/operator-governance.py"


def invoke(command: str) -> dict[str, object] | None:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout else None


def test_hook_blocks_direct_merge_and_protected_push():
    assert invoke("git merge operator/candidate") is not None
    assert invoke("git push origin main") is not None
    assert invoke("git push --force-with-lease origin feature") is not None


def test_hook_allows_candidate_push_and_typed_cli():
    assert invoke("git push origin operator/candidate") is None
    assert invoke("uv run autobugfix operator status --request-id op-1") is None


def test_hook_allows_read_only_artifact_access_but_blocks_direct_state_mutation():
    artifact = ".autobugfix/operator-artifacts/op-1/checks/raw.jsonl"
    state = ".autobugfix/operator-v3/governance.sqlite3"
    assert invoke(f"tail -80 {artifact}") is None
    assert invoke(f"cat {state}") is None
    assert invoke(f"rm {state}") is not None
    assert invoke(f"printf forged > {artifact}") is not None
    assert invoke(f"sqlite3 {state} 'delete from events'") is not None
