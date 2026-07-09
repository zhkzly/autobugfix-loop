from __future__ import annotations

import subprocess
from pathlib import Path

from autobugfix.models import RepoProfile


class PpeError(RuntimeError):
    pass


def deploy_ppe(repo: RepoProfile, worktree: Path, task_id: str) -> subprocess.CompletedProcess[str]:
    if not repo.ppe.enabled:
        raise PpeError(f"PPE is disabled for repo {repo.repo_id}")
    if not repo.ppe.command_template:
        raise PpeError(f"PPE command is not configured for repo {repo.repo_id}")
    command = repo.ppe.command_template.format(task_id=task_id, worktree=str(worktree), repo_id=repo.repo_id)
    return subprocess.run(command, cwd=worktree, shell=True, text=True, capture_output=True, check=False)
