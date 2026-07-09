from __future__ import annotations

from autobugfix.memory.service import MemoryService
from autobugfix.service import AutobugfixService
from tests.helpers import FakeCodexBackend, FakeMaintainerBackend, make_service_project


def test_memory_collect_digest_maintain_approve_reject_lint_search_context(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    service = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = service.create_task("toy_repo", "fix toy add", "Bug")
    service.run_task(task.task_id)
    service.apply_gate(task.task_id, "accepted")
    service.archive(task.task_id, "accepted")

    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.init()
    assert memory.collect(task.task_id).exists()
    assert memory.digest(task.task_id).exists()
    proposal_dir = memory.maintain(task.task_id)
    proposal_id = proposal_dir.name
    assert memory.lint() == []
    assert "verifier" in memory.show(proposal_id).lower()
    memory.approve(proposal_id, "ok")
    assert memory.search("verifier")
    assert "writer" in memory.context("writer")
