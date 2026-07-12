from __future__ import annotations

from pathlib import Path

import pytest

from autobugfix.codex_runtime import build_codex_request
from autobugfix.git_utils import git_common_dir, git_dir
from tests.helpers import make_service_project, run


def test_codex_runtime_passes_role_parameters(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    request = build_codex_request(
        project_root,
        "writer",
        "prompt",
        project_root,
        "workspace-write",
        "model-x",
        10,
        project_root / "raw.jsonl",
        project_root / "stderr.log",
    )
    assert request.cwd == project_root
    assert request.control_root == project_root.resolve()
    assert request.sandbox == "workspace-write"
    assert request.model == "model-x"
    assert request.approval_mode == "auto_review"


def test_codex_runtime_mounts_only_service_owned_linked_worktree_metadata(tmp_path):
    project_root, main = make_service_project(tmp_path)
    worktree = tmp_path / "task-worktree"
    run(
        [
            "git",
            "-C",
            str(main),
            "worktree",
            "add",
            "-b",
            "fix/runtime-metadata",
            str(worktree),
            "HEAD",
        ]
    )

    writer = build_codex_request(
        project_root,
        "writer",
        "prompt",
        worktree,
        "workspace-write",
        "model-x",
        10,
        project_root / "writer.raw.jsonl",
        project_root / "writer.stderr.log",
        expected_git_common_dir=git_common_dir(main),
    )
    evaluator = build_codex_request(
        project_root,
        "evaluator",
        "prompt",
        worktree,
        "read-only",
        "model-x",
        10,
        project_root / "evaluator.raw.jsonl",
        project_root / "evaluator.stderr.log",
        expected_git_common_dir=git_common_dir(main),
    )

    assert writer.readable_paths == (git_common_dir(main),)
    assert writer.writable_paths == (git_dir(worktree),)
    assert evaluator.readable_paths == (git_common_dir(main),)
    assert evaluator.writable_paths == ()

    unrelated = tmp_path / "unrelated"
    run(["git", "init", "-b", "main", str(unrelated)])
    with pytest.raises(RuntimeError, match="differs from service-owned repository"):
        build_codex_request(
            project_root,
            "writer",
            "prompt",
            worktree,
            "workspace-write",
            "model-x",
            10,
            project_root / "invalid.raw.jsonl",
            project_root / "invalid.stderr.log",
            expected_git_common_dir=git_common_dir(unrelated),
        )
