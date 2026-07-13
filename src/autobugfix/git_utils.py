from __future__ import annotations

import subprocess
import os
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(
    repo: Path,
    args: list[str],
    check: bool = True,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-C",
                str(repo),
                *args,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git -C {repo} {' '.join(args)} timed out after {timeout_seconds} seconds"
        ) from exc
    if check and result.returncode != 0:
        raise GitError(
            f"git -C {repo} {' '.join(args)} failed with {result.returncode}: {result.stderr.strip()}"
        )
    return result


def ensure_git_repo(path: Path) -> None:
    if not path.exists():
        raise GitError(f"target repo checkout does not exist: {path}")
    result = run_git(path, ["rev-parse", "--show-toplevel"], check=True)
    top = Path(result.stdout.strip()).resolve()
    if top != path.resolve():
        raise GitError(f"configured main_checkout must be a git repository root: {path}")


def current_branch(path: Path) -> str:
    return run_git(path, ["branch", "--show-current"]).stdout.strip()


def is_clean(path: Path) -> bool:
    return run_git(path, ["status", "--porcelain"]).stdout.strip() == ""


def fetch_remote(path: Path, remote: str, branch: str) -> None:
    run_git(path, ["fetch", remote, branch], check=True)


def rev_parse(path: Path, ref: str) -> str:
    return run_git(path, ["rev-parse", ref], check=True).stdout.strip()


def _resolved_git_path(path: Path, argument: str) -> Path:
    value = run_git(path, ["rev-parse", argument], check=True).stdout.strip()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = path / candidate
    return candidate.resolve()


def git_common_dir(path: Path) -> Path:
    return _resolved_git_path(path, "--git-common-dir")


def git_dir(path: Path) -> Path:
    return _resolved_git_path(path, "--git-dir")


def diff_against(path: Path, base_ref: str, extra_args: list[str] | None = None) -> str:
    args = ["diff", "--no-ext-diff", "--no-textconv", "--binary", base_ref]
    if extra_args:
        args.extend(extra_args)
    return run_git(path, args, check=True).stdout
