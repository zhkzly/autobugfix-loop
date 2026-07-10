from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from autobugfix.git_utils import GitError, rev_parse, run_git
from autobugfix.operator.models import OperatorRequest


class OperatorWorkspaceError(RuntimeError):
    pass


def workspace_path(control_root: Path, request_id: str, worktree_root: Path | None = None) -> Path:
    root = worktree_root.resolve() if worktree_root else control_root.resolve() / ".autobugfix/operator-worktrees"
    return root / request_id


def create_operator_workspace(
    control_root: Path,
    request: OperatorRequest,
    constitution: Mapping[str, Any],
    *,
    worktree_root: Path | None = None,
) -> dict[str, Any]:
    root = control_root.resolve()
    protected = {str(item) for item in constitution.get("protected_branches") or []}
    if request.branch in protected:
        raise OperatorWorkspaceError(f"operator workspace branch is protected: {request.branch}")
    try:
        resolved_base = rev_parse(root, request.base_sha)
    except GitError as exc:
        raise OperatorWorkspaceError(str(exc)) from exc
    if resolved_base != request.base_sha:
        raise OperatorWorkspaceError("request base SHA is not canonical")
    path = workspace_path(root, request.request_id, worktree_root)
    if path.exists():
        raise OperatorWorkspaceError(f"operator workspace already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = run_git(root, ["show-ref", "--verify", f"refs/heads/{request.branch}"], check=False)
    if existing.returncode == 0:
        raise OperatorWorkspaceError(f"operator workspace branch already exists: {request.branch}")
    try:
        run_git(root, ["worktree", "add", "-b", request.branch, str(path), request.base_sha], check=True)
    except GitError as exc:
        raise OperatorWorkspaceError(str(exc)) from exc
    actual = rev_parse(path, "HEAD")
    if actual != request.base_sha:
        raise OperatorWorkspaceError(f"workspace HEAD {actual} does not match request base {request.base_sha}")
    return {
        "request_id": request.request_id,
        "path": str(path.resolve()),
        "branch": request.branch,
        "base_sha": request.base_sha,
        "control_root": str(root),
    }


def recover_operator_workspace(
    control_root: Path,
    request: OperatorRequest,
    *,
    worktree_root: Path | None = None,
) -> dict[str, Any] | None:
    root = control_root.resolve()
    path = workspace_path(root, request.request_id, worktree_root)
    if not path.exists():
        return None
    inside = run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise OperatorWorkspaceError(f"existing Operator path is not a Git worktree: {path}")
    actual_head = rev_parse(path, "HEAD")
    actual_branch = run_git(path, ["branch", "--show-current"], check=True).stdout.strip()
    if actual_head != request.base_sha or actual_branch != request.branch:
        raise OperatorWorkspaceError(
            "existing Operator worktree does not match the frozen request base/branch"
        )
    return {
        "request_id": request.request_id,
        "path": str(path.resolve()),
        "branch": request.branch,
        "base_sha": request.base_sha,
        "control_root": str(root),
        "recovered": True,
    }
