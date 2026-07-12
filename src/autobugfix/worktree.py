from __future__ import annotations

import subprocess
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from autobugfix.git_utils import (
    GitError,
    current_branch,
    ensure_git_repo,
    fetch_remote,
    git_common_dir,
    is_clean,
    run_git,
)
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


def validate_task_worktree(
    repo: RepoProfile,
    worktree_path: Path,
    expected_branch: str | None,
) -> Path:
    ensure_git_repo(worktree_path)
    resolved = worktree_path.resolve()
    if repo.worktree_root is None:
        raise WorktreeError("repo worktree_root was not resolved")
    _ensure_contained(repo.worktree_root, resolved)
    if resolved == repo.main_checkout.resolve():
        raise WorktreeError("task worktree resolves to the configured main checkout")

    if git_common_dir(resolved) != git_common_dir(repo.main_checkout):
        raise WorktreeError("task worktree does not belong to the configured main repository")
    if expected_branch and current_branch(resolved) != expected_branch:
        raise WorktreeError(
            f"task worktree branch is {current_branch(resolved)!r}, expected {expected_branch!r}"
        )
    return resolved


def diff_for_task(
    repo: RepoProfile,
    worktree_path: Path,
    base_ref: str | None = None,
) -> str:
    base_ref = base_ref or f"{repo.remote}/{repo.main_branch}"
    tracked = run_git(
        worktree_path,
        ["diff", "--binary", base_ref],
        check=True,
    ).stdout
    untracked_raw = run_git(
        worktree_path,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        check=True,
    ).stdout
    parts = [tracked]
    for relative_path in (item for item in untracked_raw.split("\0") if item):
        result = run_git(
            worktree_path,
            ["diff", "--binary", "--no-index", "--", "/dev/null", relative_path],
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise WorktreeError(
                f"failed to render untracked file in task diff: {relative_path}"
            )
        parts.append(result.stdout)
    return "".join(parts)


def ignored_paths(worktree_path: Path) -> tuple[str, ...]:
    raw = run_git(
        worktree_path,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        check=True,
    ).stdout
    return tuple(item for item in raw.split("\0") if item)


def remove_ignored_writer_outputs(
    worktree_path: Path,
    preserved: tuple[str, ...] = (),
) -> tuple[str, ...]:
    root = worktree_path.resolve()
    preserved_set = set(preserved)
    removed = tuple(path for path in ignored_paths(root) if path not in preserved_set)
    candidates = sorted((Path(item) for item in removed), key=lambda item: (len(item.parts), str(item)))
    removal_roots: list[Path] = []
    for candidate in candidates:
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorktreeError(f"unsafe ignored path reported by Git: {candidate}")
        if any(candidate == parent or candidate.is_relative_to(parent) for parent in removal_roots):
            continue
        removal_roots.append(candidate)
    for relative in removal_roots:
        path = (root / relative).resolve()
        if path == root or not path.is_relative_to(root):
            raise WorktreeError(f"ignored path escapes task worktree: {relative}")
        if path.is_symlink() or path.is_file():
            path.unlink()
            parent = path.parent
            while parent != root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        elif path.is_dir():
            shutil.rmtree(path)
    return removed


@contextmanager
def verification_worktree(
    repo: RepoProfile,
    candidate_worktree: Path,
    base_ref: str,
    patch_text: str,
    path: Path,
) -> Iterator[Path]:
    candidate = candidate_worktree.resolve()
    destination = path.resolve()
    if destination == candidate or destination.is_relative_to(candidate):
        raise WorktreeError("verification worktree must be outside the Writer worktree")
    if destination == repo.main_checkout.resolve():
        raise WorktreeError("verification worktree must not be the main checkout")
    if destination.exists():
        raise WorktreeError(f"verification worktree already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_git(
        repo.main_checkout,
        ["worktree", "add", "--detach", str(destination), base_ref],
        check=True,
    )
    try:
        if patch_text:
            applied = subprocess.run(
                [
                    "git",
                    "-C",
                    str(destination),
                    "apply",
                    "--binary",
                    "--whitespace=nowarn",
                    "-",
                ],
                input=patch_text,
                text=True,
                capture_output=True,
                check=False,
            )
            if applied.returncode != 0:
                raise WorktreeError(
                    "failed to apply Writer patch to verification worktree: "
                    + (applied.stderr.strip() or applied.stdout.strip())
                )
        yield destination
    finally:
        removed = run_git(
            repo.main_checkout,
            ["worktree", "remove", "--force", str(destination)],
            check=False,
        )
        if removed.returncode != 0:
            raise WorktreeError(
                "failed to remove verification worktree: "
                + (removed.stderr.strip() or removed.stdout.strip())
            )
