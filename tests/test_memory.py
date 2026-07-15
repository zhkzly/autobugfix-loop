from __future__ import annotations

import hashlib
import os
import shutil

import pytest
import yaml

from autobugfix.memory.maintainer_backend import CodexMemoryMaintainerBackend
from autobugfix.memory.service import MemoryService, MemoryServiceError, _record_digest
from autobugfix.memory.store import MemoryStore, MemoryStoreError
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
    memory.approve(proposal_id, "ok", memory.review(proposal_id)["review_digest"])
    assert memory.search("verifier")
    assert "writer" in memory.context("writer")


def test_memory_human_can_activate_reviewed_proposal_as_approved_skill(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted skill evidence", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal = memory.maintain(task.task_id)
    reviewed = memory.review(proposal.name)

    skill = memory.approve_skill(
        proposal.name,
        "preserve-verifier-evidence",
        "Preserve verifier evidence when accepting a real repair.",
        "Reviewed as a reusable execution procedure.",
        reviewed["review_digest"],
    )

    assert skill == (
        memory.store.root
        / "skills/approved/preserve-verifier-evidence/SKILL.md"
    )
    content = skill.read_text(encoding="utf-8")
    assert content.startswith("---\nname: preserve-verifier-evidence\n")
    assert "Remember to preserve verifier evidence" in content
    assert "# Preserve Verifier Evidence" in memory.context("writer")
    assert memory.store.read_yaml(proposal / "proposal.yaml")["status"] == "approved_skill"
    assert memory.lint() == []
    with pytest.raises(MemoryStoreError, match="only pending"):
        memory.approve(
            proposal.name,
            "cannot activate twice",
            reviewed["review_digest"],
        )


def test_memory_skill_activation_recovers_after_skill_write(tmp_path, monkeypatch):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "recover skill evidence", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal = memory.maintain(task.task_id)
    reviewed = memory.review(proposal.name)
    real_write = memory.store.write_yaml

    def fail_final_status(path, data):
        if data.get("status") == "approved_skill":
            raise OSError("crash after skill write")
        return real_write(path, data)

    monkeypatch.setattr(memory.store, "write_yaml", fail_final_status)
    with pytest.raises(OSError, match="crash after skill write"):
        memory.approve_skill(
            proposal.name,
            "preserve-verifier-evidence",
            "Preserve verifier evidence when accepting a real repair.",
            "Reviewed as a reusable execution procedure.",
            reviewed["review_digest"],
        )
    monkeypatch.setattr(memory.store, "write_yaml", real_write)

    skill = memory.approve_skill(
        proposal.name,
        "preserve-verifier-evidence",
        "Preserve verifier evidence when accepting a real repair.",
        "Reviewed as a reusable execution procedure.",
        reviewed["review_digest"],
    )

    assert skill.is_file()
    assert memory.store.read_yaml(proposal / "proposal.yaml")["status"] == "approved_skill"


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


def test_memory_collect_rejects_unaccepted_execution_evidence(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "unaccepted task", "Bug")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())

    with pytest.raises(MemoryServiceError, match="only accepted execution evidence"):
        memory.collect(task.task_id)

    assert execution.store.load(task.task_id).state == "ready"
    assert not memory.store.raw_task_dir(task.task_id).exists()


def test_memory_rejects_eval_provenance_even_if_task_was_accepted(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task(
        "toy_repo",
        "eval-origin task",
        "Bug",
        metadata={"origin": "eval", "memory_eligible": False},
    )
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())

    with pytest.raises(MemoryServiceError, match="Eval or benchmark"):
        memory.collect(task.task_id)

    assert not memory.store.raw_task_dir(task.task_id).exists()


def test_memory_revalidates_packet_provenance_before_digest_and_maintain(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    packet_path = memory.collect(task.task_id)
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    packet["task"]["metadata"] = {"origin": "eval", "memory_eligible": False}
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    with pytest.raises(MemoryServiceError, match="digest mismatch"):
        memory.digest(task.task_id)
    with pytest.raises(MemoryServiceError, match="digest mismatch"):
        memory.maintain(task.task_id)


def test_memory_packet_cannot_forge_authoritative_execution_task(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    packet_path = memory.collect(task.task_id)
    packet = yaml.safe_load(packet_path.read_text(encoding="utf-8"))
    packet["task"]["title"] = "forged task"
    packet["record_digest"] = _record_digest(packet)
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")

    with pytest.raises(MemoryServiceError, match="authoritative Execution task"):
        memory.digest(task.task_id)


def test_memory_approval_binds_patch_and_is_not_replayable(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal = memory.maintain(task.task_id)
    review_digest = memory.review(proposal.name)["review_digest"]
    patch = proposal / "patch.md"
    original = patch.read_text(encoding="utf-8")
    patch.write_text(original + "\nforged\n", encoding="utf-8")

    with pytest.raises(MemoryServiceError, match="changed after review"):
        memory.approve(proposal.name, "reviewed", review_digest)

    patch.write_text(original, encoding="utf-8")
    memory.approve(proposal.name, "reviewed", review_digest)
    with pytest.raises(MemoryStoreError, match="only pending"):
        memory.approve(proposal.name, "replay", review_digest)


def test_memory_approval_recovers_after_active_replace_without_duplicate(
    tmp_path, monkeypatch
):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal = memory.maintain(task.task_id)
    review_digest = memory.review(proposal.name)["review_digest"]
    real_write = memory.store._atomic_write

    def fail_final_status(path, content):
        if path.name == "proposal.yaml" and b"status: approved" in content:
            raise OSError("crash after active replace")
        return real_write(path, content)

    monkeypatch.setattr(memory.store, "_atomic_write", fail_final_status)
    with pytest.raises(OSError, match="crash after active replace"):
        memory.approve(proposal.name, "reviewed", review_digest)

    active = memory.store.root / "active/user-preferences.md"
    assert active.read_text(encoding="utf-8").count(
        f"## Approved Proposal {proposal.name}"
    ) == 1
    monkeypatch.setattr(memory.store, "_atomic_write", real_write)
    memory.approve(proposal.name, "reviewed", review_digest)
    assert active.read_text(encoding="utf-8").count(
        f"## Approved Proposal {proposal.name}"
    ) == 1


def test_memory_store_rejects_proposal_id_path_escape(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")

    for proposal_id in ("../escape", "/absolute", "nested/value", ".."):
        with pytest.raises(MemoryStoreError, match="unsupported path syntax"):
            store.proposal_dir(proposal_id)


def test_memory_activation_hashes_and_activates_one_patch_read(
    tmp_path,
    monkeypatch,
) -> None:
    store = MemoryStore(tmp_path / "memory")
    store.init()
    proposal = store.proposal_dir("proposal-1")
    proposal.mkdir()
    patch = proposal / "patch.md"
    patch.write_text("Trusted memory addition\n", encoding="utf-8")
    store.write_yaml(
        proposal / "proposal.yaml",
        {
            "status": "pending",
            "proposal_id": "proposal-1",
            "review_digest": "c" * 64,
        },
    )
    expected = hashlib.sha256(patch.read_bytes()).hexdigest()
    real_read = store.read_regular_file
    patch_reads = 0

    def observed_read(path, *, label):
        nonlocal patch_reads
        if path == patch:
            patch_reads += 1
        return real_read(path, label=label)

    monkeypatch.setattr(store, "read_regular_file", observed_read)
    active = store.approve(
        "proposal-1",
        "reviewed",
        expected_patch_sha256=expected,
        expected_review_digest="c" * 64,
    )

    assert patch_reads == 1
    assert "Trusted memory addition" in active.read_text(encoding="utf-8")


def test_memory_digest_must_be_deterministically_derived_from_packet(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    digest = memory.digest(task.task_id)
    digest.write_text("# Forged non-Execution evidence\n", encoding="utf-8")

    with pytest.raises(MemoryServiceError, match="deterministic Execution evidence"):
        memory.maintain(task.task_id)


def test_memory_human_confirmation_binds_reviewed_content(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal_dir = memory.maintain(task.task_id)
    reviewed = memory.review(proposal_dir.name)
    patch_path = proposal_dir / "patch.md"
    patch_path.write_text(reviewed["patch"] + "\nforged after review\n", encoding="utf-8")
    proposal = memory.store.read_yaml(proposal_dir / "proposal.yaml")
    proposal["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    proposal["review_digest"] = _record_digest(
        {
            "schema": proposal["schema"],
            "proposal_id": proposal["proposal_id"],
            "task_id": proposal["task_id"],
            "packet_sha256": proposal["packet_sha256"],
            "digest_sha256": proposal["digest_sha256"],
            "patch_sha256": proposal["patch_sha256"],
        }
    )
    memory.store.write_yaml(proposal_dir / "proposal.yaml", proposal)

    with pytest.raises(MemoryServiceError, match="human approval digest"):
        memory.approve(proposal_dir.name, "reviewed", reviewed["review_digest"])


def test_memory_context_and_lint_reject_active_symlink(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.init()
    outside = tmp_path / "secret.txt"
    outside.write_text("host secret", encoding="utf-8")
    active = store.root / "active/user-preferences.md"
    active.unlink()
    active.symlink_to(outside)
    memory = MemoryService(tmp_path, config=type("Config", (), {"root": store.root})())

    with pytest.raises(RuntimeError, match="redirected"):
        memory.context("writer")
    assert any("redirected" in error for error in memory.lint())


def test_memory_trusted_write_rejects_symlinked_parent(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.init()
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(store.root / "digests")
    (store.root / "digests").symlink_to(outside, target_is_directory=True)

    with pytest.raises(MemoryStoreError, match="redirected"):
        store._atomic_write(store.root / "digests/tasks/escaped.md", b"escaped")

    assert not (outside / "tasks/escaped.md").exists()


def test_memory_approval_lock_rejects_symlink(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.init()
    outside = tmp_path / "outside.lock"
    outside.write_text("unchanged", encoding="utf-8")
    (store.root / ".approval.lock").symlink_to(outside)

    with pytest.raises(MemoryStoreError, match="redirected"):
        with store._approval_lock():
            pass

    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_memory_rejection_rejects_symlinked_destination_parent(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    store.init()
    proposal = store.proposal_dir("proposal-1")
    store.ensure_directory(proposal, exist_ok=False)
    store.write_yaml(
        proposal / "proposal.yaml",
        {"proposal_id": "proposal-1", "status": "pending"},
    )
    outside = tmp_path / "outside-rejected"
    outside.mkdir()
    shutil.rmtree(store.root / "rejected")
    (store.root / "rejected").symlink_to(outside, target_is_directory=True)

    with pytest.raises(MemoryStoreError, match="redirected"):
        store.reject("proposal-1", "not stable")

    assert proposal.is_dir()
    assert list(outside.iterdir()) == []


def test_memory_maintainer_cannot_redirect_trusted_output(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    outside = tmp_path / "outside.md"
    outside.write_text("unchanged", encoding="utf-8")

    class RedirectingMaintainer:
        def maintain(self, _project_root, run_dir, *_args):
            (run_dir / "maintainer.md").symlink_to(outside)
            return "stable proposal"

    memory = MemoryService(project_root, backend=RedirectingMaintainer())
    memory.collect(task.task_id)
    memory.digest(task.task_id)

    with pytest.raises(MemoryStoreError, match="not a regular file"):
        memory.maintain(task.task_id)

    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_memory_proposal_identity_cannot_be_cloned(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    execution = AutobugfixService(project_root, backend=FakeCodexBackend())
    task = execution.create_task("toy_repo", "accepted task", "Bug")
    execution.run_task(task.task_id)
    execution.apply_gate(task.task_id, "accepted")
    memory = MemoryService(project_root, backend=FakeMaintainerBackend())
    memory.collect(task.task_id)
    memory.digest(task.task_id)
    proposal = memory.maintain(task.task_id)
    clone = memory.store.proposal_dir("clone-proposal")
    shutil.copytree(proposal, clone)

    with pytest.raises(MemoryServiceError, match="identity"):
        memory.review(clone.name)


def test_memory_reject_recovers_after_atomic_move(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "memory")
    store.init()
    proposal = store.proposal_dir("proposal-1")
    proposal.mkdir()
    store.write_yaml(
        proposal / "proposal.yaml",
        {"proposal_id": "proposal-1", "status": "pending"},
    )
    real_write = store.write_yaml

    def fail_final_status(path, data):
        if path.parent == store.rejected_dir("proposal-1") and data.get("status") == "rejected":
            raise OSError("crash after move")
        return real_write(path, data)

    monkeypatch.setattr(store, "write_yaml", fail_final_status)
    with pytest.raises(OSError, match="crash after move"):
        store.reject("proposal-1", "not stable")
    assert store.rejected_dir("proposal-1").is_dir()
    monkeypatch.setattr(store, "write_yaml", real_write)
    assert store.reject("proposal-1", "not stable") == store.rejected_dir("proposal-1")
    assert store.read_yaml(store.rejected_dir("proposal-1") / "proposal.yaml")["status"] == "rejected"
