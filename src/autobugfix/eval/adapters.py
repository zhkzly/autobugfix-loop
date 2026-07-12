from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autobugfix.eval.models import EvalCase, OracleResult
from autobugfix.eval.benchmarks.verify import (
    Defects4JManagedVerifier,
)
from autobugfix.git_utils import run_git
from autobugfix.models import utc_now
from autobugfix.verifier import ExecutionVerifierBackend, run_verifier


class EvalAdapterError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class MaterializedCase:
    remote: Path
    main_checkout: Path


class EvalCaseAdapter(Protocol):
    name: str

    def materialize(self, case: EvalCase, setup_dir: Path) -> MaterializedCase: ...

    def oracle_diff(self, case: EvalCase) -> str | None: ...

    def verify(
        self,
        case: EvalCase,
        worktree: Path,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        verifier_backend: ExecutionVerifierBackend | None = None,
    ) -> OracleResult: ...


class LocalGitAdapter:
    name = "local-git"

    def materialize(self, case: EvalCase, setup_dir: Path) -> MaterializedCase:
        if (
            case.environment.image
            or case.environment.platform
            or case.environment.setup_commands
        ):
            raise EvalAdapterError(
                "local-git adapter cannot satisfy a declared container environment"
            )
        return _materialize_git(case, setup_dir)

    def oracle_diff(self, case: EvalCase) -> str | None:
        if not case.final_commit:
            return None
        return run_git(
            case.worktree_path,
            ["diff", "--binary", case.base_commit, case.final_commit],
            check=True,
        ).stdout

    def verify(
        self,
        case: EvalCase,
        worktree: Path,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        verifier_backend: ExecutionVerifierBackend | None = None,
    ) -> OracleResult:
        if verifier_backend is not None:
            raise EvalAdapterError(
                "local-git adapter does not accept a managed verifier backend"
            )
        started = utc_now()
        command = case.resolved_oracle_command(command_override)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"
        if case.oracle.oracle_type != "command":
            error = f"local-git adapter does not support oracle type {case.oracle.oracle_type!r}"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(error + "\n", encoding="utf-8")
            return OracleResult(
                status="error",
                oracle_type=case.oracle.oracle_type,
                command=command,
                exit_code=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error=error,
            )
        if not command:
            error = "command oracle requires a real test command"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(error + "\n", encoding="utf-8")
            return OracleResult(
                status="error",
                oracle_type="command",
                command=None,
                exit_code=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error=error,
            )
        result = run_verifier(worktree, command, timeout_seconds=case.oracle.timeout_seconds)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        return OracleResult(
            status="passed" if result.passed else "failed",
            oracle_type="command",
            command=result.command,
            exit_code=result.exit_code,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            started_at=result.started_at,
            finished_at=result.finished_at,
        )


def _materialize_git(case: EvalCase, setup_dir: Path) -> MaterializedCase:
    remote = setup_dir / "remote.git"
    main = setup_dir / "main"
    if remote.exists():
        shutil.rmtree(remote)
    if main.exists():
        shutil.rmtree(main)
    run_git(case.worktree_path, ["rev-parse", "--git-dir"], check=True)
    subprocess.run(
        ["git", "clone", "--bare", str(case.worktree_path), str(remote)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(remote), str(main)],
        check=True,
        text=True,
        capture_output=True,
    )
    run_git(main, ["checkout", "-B", "main", case.base_commit], check=True)
    run_git(main, ["push", "origin", "main", "--force"], check=True)
    return MaterializedCase(remote=remote, main_checkout=main)


class Defects4JAdapter:
    name = "defects4j"

    def materialize(self, case: EvalCase, setup_dir: Path) -> MaterializedCase:
        if not case.environment.image or not case.environment.platform:
            raise EvalAdapterError("Defects4J case requires a pinned container environment")
        return _materialize_git(case, setup_dir)

    def oracle_diff(self, case: EvalCase) -> str | None:
        return None

    def verify(
        self,
        case: EvalCase,
        worktree: Path,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        verifier_backend: ExecutionVerifierBackend | None = None,
    ) -> OracleResult:
        del command_override
        started = utc_now()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"
        if not isinstance(verifier_backend, Defects4JManagedVerifier):
            error = "Defects4J case has no trusted managed verifier authority"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(error + "\n", encoding="utf-8")
            return OracleResult(
                status="error",
                oracle_type="defects4j",
                command=None,
                exit_code=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error=error,
            )
        command = f"defects4j test ({case.environment.image})"
        try:
            if (
                case.benchmark is None
                or verifier_backend.contract.eligibility_receipt_digest
                != case.benchmark.eligibility_receipt_digest
            ):
                raise EvalAdapterError(
                    "Defects4J verifier contract is not bound to the case eligibility receipt"
                )
            result = verifier_backend.run(
                worktree,
                artifact_dir / "managed",
                timeout_seconds=case.oracle.timeout_seconds,
            )
            stdout_path.write_text(result.stdout, encoding="utf-8")
            stderr_path.write_text(result.stderr, encoding="utf-8")
            status = (
                "passed"
                if result.outcome == "passed"
                else "failed"
                if result.outcome == "repair_failure"
                else "error"
            )
            return OracleResult(
                status=status,
                oracle_type="defects4j",
                command=command,
                exit_code=result.exit_code,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error=(
                    ""
                    if status != "error"
                    else f"managed verifier outcome: {result.outcome}"
                ),
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(error + "\n", encoding="utf-8")
            return OracleResult(
                status="error",
                oracle_type="defects4j",
                command=command,
                exit_code=None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error=error,
            )


_ADAPTERS: dict[str, EvalCaseAdapter] = {
    LocalGitAdapter.name: LocalGitAdapter(),
    Defects4JAdapter.name: Defects4JAdapter(),
}


def get_adapter(name: str) -> EvalCaseAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise EvalAdapterError(f"unknown Eval case adapter: {name}") from exc
