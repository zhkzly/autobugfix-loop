from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Protocol

from autobugfix.models import VerifierOutcome, VerifierResult, utc_now


class VerifierError(RuntimeError):
    pass


class ExecutionVerifierBackend(Protocol):
    command_id: str

    def run(
        self,
        worktree: Path,
        artifact_dir: Path,
        *,
        timeout_seconds: int | None,
    ) -> VerifierResult: ...


HARNESS_ERROR_MARKER = "AUTOBUGFIX_VERIFIER_HARNESS:"
POLICY_VIOLATION_MARKER = "AUTOBUGFIX_VERIFIER_POLICY:"


def classify_verifier_result(exit_code: int, stderr: str) -> VerifierOutcome:
    if exit_code == 0:
        return "passed"
    first_line = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    if first_line.startswith(POLICY_VIOLATION_MARKER):
        return "policy_violation"
    if first_line.startswith(HARNESS_ERROR_MARKER):
        return "harness_error"
    if exit_code == 1:
        return "repair_failure"
    return "harness_error"


def run_verifier(worktree: Path, command: str, timeout_seconds: int | None = None) -> VerifierResult:
    started = utc_now()
    process = subprocess.Popen(
        command,
        cwd=worktree,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        exit_code = int(process.returncode or 0)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        exit_code = 124
        stderr = (stderr or "") + f"\nTimed out after {timeout_seconds} seconds."
    return VerifierResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started,
        finished_at=utc_now(),
        outcome=classify_verifier_result(exit_code, stderr),
    )


def write_test_result(path: Path, result: VerifierResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# Verifier Result",
                f"command: `{result.command}`",
                f"exit_code: {result.exit_code}",
                f"outcome: {result.outcome}",
                f"started_at: {result.started_at}",
                f"finished_at: {result.finished_at}",
                "",
                "## stdout",
                "```",
                result.stdout,
                "```",
                "",
                "## stderr",
                "```",
                result.stderr,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
