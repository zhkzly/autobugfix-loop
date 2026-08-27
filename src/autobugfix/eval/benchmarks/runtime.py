from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from autobugfix.eval.benchmarks.models import CommandEvidence, canonical_json, digest_file
from autobugfix.models import utc_now


_SENSITIVE_ENV_FRAGMENTS = (
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)


_DETERMINISM_ENV_KEYS = frozenset(
    {
        # Literal pinned timestamps, not credentials: the fragment filter
        # would otherwise strip GIT_AUTHOR_DATE / GIT_COMMITTER_DATE
        # ("AUTH" matches "AUTHOR") and silently break snapshot determinism.
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_DATE",
    }
)


def _safe_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Remove host credentials from every benchmark subprocess environment."""

    safe: dict[str, str] = {}
    for key, value in source.items():
        normalized = str(key).upper()
        if normalized == "SSH_AUTH_SOCK" or (
            normalized not in _DETERMINISM_ENV_KEYS
            and any(
                fragment in normalized for fragment in _SENSITIVE_ENV_FRAGMENTS
            )
        ):
            continue
        safe[str(key)] = str(value)
    return safe


def _write_private_text(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    artifact_dir: Path,
    name: str,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
    inherit_env: bool = True,
) -> CommandEvidence:
    if not argv or any(not str(item) for item in argv):
        raise ValueError("benchmark command argv must not be empty")
    if timeout_seconds < 1:
        raise ValueError("benchmark command timeout must be positive")
    artifact_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    artifact_dir.chmod(0o700)
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    if stdout_path.exists() or stderr_path.exists():
        raise ValueError(f"benchmark command artifact directory is not fresh: {artifact_dir}")
    command_env = _safe_environment(os.environ) if inherit_env else {}
    if env:
        command_env.update(_safe_environment(env))
    captured_env = {
        key: command_env[key]
        for key in sorted(command_env)
        if key
        in {
            "HOME",
            "JAVA_HOME",
            "LD_LIBRARY_PATH",
            "PATH",
            "PERL5LIB",
            "PYTHONPATH",
            "TZ",
        }
    }
    environment_digest = hashlib.sha256(
        canonical_json(captured_env).encode("utf-8")
    ).hexdigest()
    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    exit_code: int | None
    stdout: str
    stderr: str
    process = subprocess.Popen(
        [str(item) for item in argv],
        cwd=cwd,
        env=command_env,
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
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = None
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
        stderr += f"\ncommand timed out after {timeout_seconds} seconds\n"
    _write_private_text(stdout_path, stdout)
    _write_private_text(stderr_path, stderr)
    return CommandEvidence(
        name=name,
        argv=tuple(str(item) for item in argv),
        cwd=str(cwd.resolve()),
        started_at=started_at,
        finished_at=utc_now(),
        duration_seconds=time.monotonic() - started_monotonic,
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_path=str(stdout_path.resolve()),
        stderr_path=str(stderr_path.resolve()),
        stdout_sha256=digest_file(stdout_path),
        stderr_sha256=digest_file(stderr_path),
        environment_digest=environment_digest,
    )
