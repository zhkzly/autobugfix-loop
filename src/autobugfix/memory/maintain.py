from __future__ import annotations

from autobugfix.models import utc_now


def proposal_id_for(task_id: str) -> str:
    return f"{utc_now()[:10].replace('-', '')}-{task_id}"


def render_patch(task_id: str, maintainer_text: str) -> str:
    if maintainer_text.strip().startswith("NO_CHANGE"):
        return f"# No Change\n\nTask `{task_id}` produced no stable memory update.\n\n{maintainer_text}\n"
    return f"# Memory Proposal From {task_id}\n\n{maintainer_text.strip()}\n"
