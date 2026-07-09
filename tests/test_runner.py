from __future__ import annotations

import yaml

from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project


def test_runner_rework_when_evaluator_rejects(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend(evaluator_text="decision: needs_changes\nreason: too broad\n"))
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    result = service.run_task(task.task_id)
    assert result.state == "writer_rework_required"
    assert result.block_reason == "too broad"


def test_runner_uses_resolved_writer_and_evaluator_roles(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["codex"]["roles"] = {
        "writer": {"model": "writer-model", "timeout_seconds": 41},
        "evaluator": {"model": "evaluator-model", "timeout_seconds": 42},
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    backend = FakeCodexBackend(evaluator_text="decision: needs_changes\nreason: inspect request\n")
    service = AutobugfixService(project_root, backend=backend)
    task = service.create_task("toy_repo", "fix toy add", "Bug")

    service.run_task(task.task_id)

    writer_request = backend.calls[0]
    evaluator_request = backend.calls[1]
    assert writer_request.role == "writer"
    assert writer_request.model == "writer-model"
    assert writer_request.sandbox == "workspace-write"
    assert writer_request.approval_mode == "auto_review"
    assert writer_request.timeout_seconds == 41
    assert evaluator_request.role == "evaluator"
    assert evaluator_request.model == "evaluator-model"
    assert evaluator_request.sandbox == "read-only"
    assert evaluator_request.approval_mode == "deny_all"
    assert evaluator_request.timeout_seconds == 42
