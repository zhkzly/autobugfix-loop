from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autobugfix.config import load_config
from autobugfix.git_utils import diff_against, rev_parse, run_git


def _worktree_entries(main_checkout: Path) -> list[dict[str, str]]:
    output = run_git(main_checkout, ["worktree", "list", "--porcelain"]).stdout
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def build_raw_dataset(project_root: Path, repo_id: str, out: Path, base_ref: str | None = None) -> Path:
    cfg = load_config(project_root)
    repo = cfg.repo(repo_id)
    base = base_ref or f"{repo.remote}/{repo.main_branch}"
    base_commit = rev_parse(repo.main_checkout, base)
    rows: list[dict[str, Any]] = []
    worktree_root = repo.worktree_root.resolve() if repo.worktree_root else None
    for entry in _worktree_entries(repo.main_checkout):
        path_text = entry.get("worktree")
        if not path_text:
            continue
        path = Path(path_text).resolve()
        if path == repo.main_checkout.resolve():
            continue
        if worktree_root and worktree_root not in path.parents:
            continue
        final_commit = rev_parse(path, "HEAD")
        if final_commit == base_commit:
            continue
        branch = entry.get("branch", "").replace("refs/heads/", "")
        rows.append(
            {
                "raw_id": path.name,
                "repo": repo_id,
                "branch": branch,
                "worktree_path": str(path),
                "base_commit": base_commit,
                "final_commit": final_commit,
                "diff": diff_against(path, base_commit),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return out
