from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autobugfix.eval.models import EvalCase, FrozenSubmission, OracleResult
from autobugfix.eval.benchmarks.verify import (
    Defects4JOfficialOracle,
)
from autobugfix.git_utils import run_git
from autobugfix.models import utc_now
from autobugfix.verifier import run_verifier


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

    def score_submission(
        self,
        case: EvalCase,
        materialized: MaterializedCase,
        submission: FrozenSubmission,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        official_evaluator: object | None = None,
    ) -> OracleResult: ...


class SubmissionApplyError(EvalAdapterError):
    def __init__(self, stdout: str, stderr: str, exit_code: int):
        super().__init__(stderr.strip() or "frozen submission patch could not be applied")
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


def _submission_checkout(
    case: EvalCase,
    materialized: MaterializedCase,
    submission: FrozenSubmission,
    destination: Path,
) -> Path:
    if submission.case_id != case.case_id:
        raise EvalAdapterError("frozen submission is bound to a different case")
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--no-hardlinks",
            str(materialized.remote),
            str(destination),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if clone.returncode != 0:
        raise EvalAdapterError(
            clone.stderr.strip() or "cannot create isolated scoring checkout"
        )
    run_git(destination, ["checkout", "--detach", case.base_commit], check=True)
    run_git(destination, ["clean", "-fdx"], check=True)
    if submission.patch.strip():
        applied = subprocess.run(
            ["git", "-C", str(destination), "apply", "--binary", "-"],
            input=submission.patch,
            text=True,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            raise SubmissionApplyError(
                applied.stdout,
                applied.stderr,
                applied.returncode,
            )
    return destination


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

    def score_submission(
        self,
        case: EvalCase,
        materialized: MaterializedCase,
        submission: FrozenSubmission,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        official_evaluator: object | None = None,
    ) -> OracleResult:
        if official_evaluator is not None:
            raise EvalAdapterError(
                "local-git adapter does not accept a managed official evaluator"
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
        try:
            candidate = _submission_checkout(
                case,
                materialized,
                submission,
                artifact_dir / "candidate",
            )
        except SubmissionApplyError as exc:
            stdout_path.write_text(exc.stdout, encoding="utf-8")
            stderr_path.write_text(exc.stderr, encoding="utf-8")
            return OracleResult(
                status="failed",
                oracle_type="command",
                command="git apply --binary && " + command,
                exit_code=exc.exit_code,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started,
                finished_at=utc_now(),
                error="frozen submission could not be applied to the clean buggy revision",
            )
        result = run_verifier(
            candidate,
            command,
            timeout_seconds=case.oracle.timeout_seconds,
        )
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

    def score_submission(
        self,
        case: EvalCase,
        materialized: MaterializedCase,
        submission: FrozenSubmission,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
        official_evaluator: object | None = None,
    ) -> OracleResult:
        del command_override
        started = utc_now()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"
        if not isinstance(official_evaluator, Defects4JOfficialOracle):
            error = "Defects4J case has no independent official evaluator"
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
                or official_evaluator.contract.eligibility_receipt_digest
                != case.benchmark.eligibility_receipt_digest
            ):
                raise EvalAdapterError(
                    "Defects4J official evaluator is not bound to the case receipt"
                )
            try:
                candidate = _submission_checkout(
                    case,
                    materialized,
                    submission,
                    artifact_dir / "candidate",
                )
            except SubmissionApplyError as exc:
                stderr_path.write_text(exc.stderr, encoding="utf-8")
                stdout_path.write_text(exc.stdout, encoding="utf-8")
                return OracleResult(
                    status="failed",
                    oracle_type="defects4j",
                    command="git apply --binary && " + command,
                    exit_code=exc.exit_code,
                    stdout_path=str(stdout_path),
                    stderr_path=str(stderr_path),
                    started_at=started,
                    finished_at=utc_now(),
                    error="frozen submission could not be applied to the clean buggy revision",
                )
            result = official_evaluator.run(
                candidate,
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
                started_at=result.started_at,
                finished_at=result.finished_at,
                error=(
                    ""
                    if status != "error"
                    else f"official evaluator outcome: {result.outcome}"
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
