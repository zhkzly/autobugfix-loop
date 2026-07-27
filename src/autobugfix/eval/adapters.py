from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autobugfix.eval.models import EvalCase, OracleResult
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

    def verify(
        self,
        case: EvalCase,
        worktree: Path,
        artifact_dir: Path,
        *,
        command_override: str | None = None,
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
        remote = setup_dir / "remote.git"
        main = setup_dir / "main"
        if remote.exists():
            shutil.rmtree(remote)
        if main.exists():
            shutil.rmtree(main)
        run_git(case.worktree_path, ["rev-parse", "--git-dir"], check=True)
        subprocess.run(
            ["git", "init", "--bare", str(remote)],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(remote),
                "fetch",
                "--no-tags",
                str(case.worktree_path),
                f"{case.base_commit}:refs/heads/main",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        run_git(remote, ["symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        refs = run_git(
            remote,
            ["for-each-ref", "--format=%(refname)"],
            check=True,
        ).stdout.splitlines()
        if refs != ["refs/heads/main"]:
            raise EvalAdapterError(
                f"isolated Execution remote contains unexpected refs: {refs}"
            )
        if case.final_commit and case.final_commit != case.base_commit:
            leaked = run_git(
                remote,
                ["cat-file", "-e", f"{case.final_commit}^{{commit}}"],
                check=False,
            )
            if leaked.returncode == 0:
                raise EvalAdapterError(
                    "isolated Execution remote contains the hidden reference commit"
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
    ) -> OracleResult:
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


_ADAPTERS: dict[str, EvalCaseAdapter] = {LocalGitAdapter.name: LocalGitAdapter()}


def get_adapter(name: str) -> EvalCaseAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise EvalAdapterError(f"unknown Eval case adapter: {name}") from exc
