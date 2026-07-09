from __future__ import annotations

from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project


def test_runner_rework_when_evaluator_rejects(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend(evaluator_text="decision: needs_changes\nreason: too broad\n"))
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    result = service.run_task(task.task_id)
    assert result.state == "writer_rework_required"
    assert result.block_reason == "too broad"
