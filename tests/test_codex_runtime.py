from __future__ import annotations

from pathlib import Path

from autobugfix.codex_runtime import build_codex_request
from tests.helpers import make_service_project


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
    assert request.sandbox == "workspace-write"
    assert request.model == "model-x"
