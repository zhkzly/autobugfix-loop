from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from autobugfix.git_utils import GitError, ensure_git_repo, fetch_remote, is_clean, run_git
from autobugfix.models import RepoProfile
from autobugfix.task_store import slugify


class WorktreeError(RuntimeError):
    pass


def _ensure_contained(root: Path, child: Path) -> None:
    root_resolved = root.resolve()
    child_resolved = child.resolve()
    if root_resolved != child_resolved and root_resolved not in child_resolved.parents:
        raise WorktreeError(f"worktree path {child_resolved} is outside configured root {root_resolved}")


def branch_for_task(repo: RepoProfile, task_id: str, title: str) -> str:
    date = datetime.now(UTC).strftime("%Y%m%d")
    slug = f"{slugify(title, 40)}-{task_id[-8:]}"
    return repo.branch_template.format(date=date, slug=slug, task_id=task_id, repo_id=repo.repo_id)


def create_task_worktree(repo: RepoProfile, task_id: str, title: str) -> tuple[str, Path]:
    ensure_git_repo(repo.main_checkout)
    if not is_clean(repo.main_checkout):
        raise WorktreeError(f"main checkout is not clean: {repo.main_checkout}")
    if repo.worktree_root is None:
        raise WorktreeError("repo worktree_root was not resolved")
    worktree_root = repo.worktree_root.resolve()
    worktree_path = worktree_root / task_id
    _ensure_contained(worktree_root, worktree_path)
    if worktree_path.exists():
        raise WorktreeError(f"worktree already exists: {worktree_path}")
    worktree_root.mkdir(parents=True, exist_ok=True)
    fetch_remote(repo.main_checkout, repo.remote, repo.main_branch)
    base_ref = f"{repo.remote}/{repo.main_branch}"
    branch = branch_for_task(repo, task_id, title)
    try:
        run_git(repo.main_checkout, ["worktree", "add", "-b", branch, str(worktree_path), base_ref])
    except GitError:
        run_git(repo.main_checkout, ["worktree", "add", str(worktree_path), base_ref])
        run_git(worktree_path, ["checkout", "-B", branch])
    return branch, worktree_path.resolve()


def diff_for_task(repo: RepoProfile, worktree_path: Path) -> str:
    base_ref = f"{repo.remote}/{repo.main_branch}"
    return run_git(worktree_path, ["diff", "--binary", base_ref], check=True).stdout
