from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
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


def diff_against(path: Path, base_ref: str, extra_args: list[str] | None = None) -> str:
    args = ["diff", "--binary", base_ref]
    if extra_args:
        args.extend(extra_args)
    return run_git(path, args, check=True).stdout
