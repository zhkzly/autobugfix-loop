from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from autobugfix.models import utc_now
from autobugfix.operator.policy import PolicyDecision, evaluate_policy
from autobugfix.operator.store import OperatorStore


def _run_validation_command(project_root: Path, command: str, timeout_seconds: int | None) -> dict[str, Any]:
    started_at = utc_now()
    result = subprocess.run(
        command,
        cwd=project_root,
        text=True,
        shell=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "started_at": started_at,
        "finished_at": utc_now(),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def validate_operator_request(
    project_root: Path | str,
    request_id: str,
    base_ref: str = "HEAD",
    run_validation_commands: bool = False,
    validation_timeout_seconds: int | None = None,
    record: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    store = OperatorStore(root)
    request = store.read_request(request_id)
    reviews = store.read_reviews(request_id)
    decision: PolicyDecision = evaluate_policy(root, request, reviews, base_ref=base_ref)
    command_results: list[dict[str, Any]] = []
    if run_validation_commands and decision.allowed:
        for command in request.validation_commands:
            command_results.append(_run_validation_command(root, command, validation_timeout_seconds))
    if any(not item["passed"] for item in command_results):
        decision.violations.append("one or more validation commands failed")
        decision.allowed = False
    report = {
        "request": request.to_dict(),
        "policy": decision.to_dict(),
        "command_results": command_results,
        "created_at": utc_now(),
    }
    if record:
        path = store.write_validation(request_id, report)
        report["record_path"] = str(path)
    return report
