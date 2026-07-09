from autobugfix.scheduler import tick
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, make_service_project


def test_scheduler_runs_runnable_task(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix", "Bug")
    assert tick(service, 1) == [task.task_id]
