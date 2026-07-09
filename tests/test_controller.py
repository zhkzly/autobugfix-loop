from __future__ import annotations

from autobugfix.controller import Controller
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project


def test_controller_reads_projection_and_ticks_service(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix", "Bug")
    controller = Controller(service)
    assert controller.status()["tasks"]
    assert controller.inspect(task.task_id)["task"]["task_id"] == task.task_id
