from __future__ import annotations

import subprocess
from pathlib import Path

from autobugfix.models import VerifierResult, utc_now


class VerifierError(RuntimeError):
    pass


def run_verifier(worktree: Path, command: str, timeout_seconds: int | None = None) -> VerifierResult:
    started = utc_now()
    try:
        result = subprocess.run(
            command,
            cwd=worktree,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTimed out after {timeout_seconds} seconds."
    return VerifierResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        started_at=started,
        finished_at=utc_now(),
    )


def write_test_result(path: Path, result: VerifierResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# Verifier Result",
                f"command: `{result.command}`",
                f"exit_code: {result.exit_code}",
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
