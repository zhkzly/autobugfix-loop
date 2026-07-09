from autobugfix.projection import inspect_projection, status_projection
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project


def test_projection_reads_task_store(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix", "Bug")
    assert status_projection(service.store)["tasks"]
    assert inspect_projection(service.store, task.task_id)["task"]["task_id"] == task.task_id
