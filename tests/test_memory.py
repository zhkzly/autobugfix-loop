from __future__ import annotations

import yaml

from autobugfix.memory.maintainer_backend import CodexMemoryMaintainerBackend
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


def test_codex_memory_maintainer_uses_resolved_role(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["codex"]["roles"] = {
        "memory_maintainer": {
            "model": "memory-model",
            "timeout_seconds": 66,
            "raw_log_template": "logs/memory.raw.jsonl",
            "stderr_log_template": "logs/memory.stderr.log",
        }
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    codex = FakeCodexBackend(edit=False)
    backend = CodexMemoryMaintainerBackend(codex)

    backend.maintain(project_root, tmp_path / "memory-run", "digest", None, None)

    request = codex.calls[0]
    assert request.role == "memory_maintainer"
    assert request.model == "memory-model"
    assert request.sandbox == "workspace-write"
    assert request.approval_mode == "auto_review"
    assert request.timeout_seconds == 66
    assert request.raw_log_path.name == "memory.raw.jsonl"
