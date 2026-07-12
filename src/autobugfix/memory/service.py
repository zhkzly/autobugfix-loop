from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.config import load_config
from autobugfix.memory.collect import collect_task_packet
from autobugfix.memory.config import MemoryConfig, load_memory_config
from autobugfix.memory.context import render_memory_context
from autobugfix.memory.digest import render_digest
from autobugfix.memory.lint import lint_memory_root
from autobugfix.memory.maintain import proposal_id_for, render_patch
from autobugfix.memory.maintainer_backend import CodexMemoryMaintainerBackend, MemoryMaintainerBackend
from autobugfix.memory.projection import memory_status
from autobugfix.memory.search import search_memory
from autobugfix.memory.store import MemoryStore
from autobugfix.task_store import TaskStore


class MemoryServiceError(RuntimeError):
    pass


def _require_memory_eligible_task(task: Mapping[str, Any], task_id: str) -> None:
    metadata = task.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise MemoryServiceError(f"task {task_id} has invalid provenance metadata")
    if metadata.get("memory_eligible") is False or metadata.get("origin") == "eval":
        raise MemoryServiceError(
            f"memory may not collect Eval or benchmark task evidence: {task_id}"
        )
    state = str(task.get("state") or "")
    archived_result = task.get("archived_result")
    accepted = state == "accepted" or (
        state == "archived" and archived_result == "accepted"
    )
    if not accepted:
        raise MemoryServiceError(
            f"memory may collect only accepted execution evidence: "
            f"task {task_id} is {state} with result {archived_result or 'none'}"
        )


def _require_memory_eligible_packet(packet: Mapping[str, Any], task_id: str) -> None:
    task = packet.get("task")
    if not isinstance(task, Mapping):
        raise MemoryServiceError(f"memory packet has no valid task provenance: {task_id}")
    _require_memory_eligible_task(task, task_id)


class MemoryService:
    def __init__(
        self,
        project_root: Path | str = ".",
        config: MemoryConfig | None = None,
        backend: MemoryMaintainerBackend | None = None,
        codex_backend: CodexBackend | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config or load_memory_config(self.project_root)
        self.store = MemoryStore(self.config.root)
        self.backend = backend or CodexMemoryMaintainerBackend(codex_backend or CodexSDKBackend())

    def init(self) -> None:
        self.store.init()

    def collect(self, task_id: str) -> Path:
        self.store.init()
        execution_config = load_config(self.project_root)
        task_store = TaskStore(self.project_root, execution_config.task_root)
        task = task_store.load(task_id)
        _require_memory_eligible_task(task.to_dict(), task_id)
        packet = collect_task_packet(task_store, task_id)
        raw_dir = self.store.raw_task_dir(task_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        self.store.write_yaml(raw_dir / "packet.yaml", packet)
        return raw_dir / "packet.yaml"

    def digest(self, task_id: str) -> Path:
        self.store.init()
        packet = self.store.read_yaml(self.store.raw_task_dir(task_id) / "packet.yaml")
        _require_memory_eligible_packet(packet, task_id)
        digest = render_digest(packet)
        path = self.store.digest_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest, encoding="utf-8")
        self.store.write_yaml(self.store.root / "digests/index.yaml", {"last_digest": task_id})
        return path

    def maintain(self, task_id: str) -> Path:
        self.store.init()
        packet = self.store.read_yaml(self.store.raw_task_dir(task_id) / "packet.yaml")
        _require_memory_eligible_packet(packet, task_id)
        digest_path = self.store.digest_path(task_id)
        if not digest_path.exists():
            raise MemoryServiceError(f"missing digest for task {task_id}")
        proposal_id = proposal_id_for(task_id)
        run_dir = self.store.root / "maintainer-runs" / proposal_id
        digest_text = digest_path.read_text(encoding="utf-8")
        maintainer_text = self.backend.maintain(
            self.project_root,
            run_dir,
            digest_text,
            self.config.maintainer.model,
            self.config.maintainer.timeout_seconds,
            self.config.maintainer.role,
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "maintainer.md").write_text(maintainer_text, encoding="utf-8")
        proposal_dir = self.store.proposal_dir(proposal_id)
        proposal_dir.mkdir(parents=True, exist_ok=True)
        self.store.write_yaml(
            proposal_dir / "proposal.yaml",
            {"proposal_id": proposal_id, "task_id": task_id, "status": "pending", "run_dir": str(run_dir)},
        )
        (proposal_dir / "patch.md").write_text(render_patch(task_id, maintainer_text), encoding="utf-8")
        (proposal_dir / "evidence.md").write_text(f"Digest: {digest_path}\nRaw: {self.store.raw_task_dir(task_id)}\n", encoding="utf-8")
        return proposal_dir

    def tick(self, max_tasks: int) -> list[str]:
        processed: list[str] = []
        for raw_dir in sorted((self.store.root / "raw/tasks").glob("*")):
            if len(processed) >= max_tasks:
                break
            task_id = raw_dir.name
            if not self.store.digest_path(task_id).exists():
                self.digest(task_id)
                processed.append(task_id)
        return processed

    def status(self) -> dict[str, object]:
        self.store.init()
        return memory_status(self.store)

    def proposals(self) -> list[dict[str, object]]:
        self.store.init()
        return self.store.list_proposals()

    def show(self, proposal_id: str) -> str:
        proposal = self.store.proposal_dir(proposal_id)
        return (proposal / "patch.md").read_text(encoding="utf-8")

    def approve(self, proposal_id: str, note: str) -> Path:
        return self.store.approve(proposal_id, note)

    def reject(self, proposal_id: str, reason: str) -> Path:
        return self.store.reject(proposal_id, reason)

    def lint(self) -> list[str]:
        self.store.init()
        return lint_memory_root(self.store.root)

    def search(self, query: str) -> list[str]:
        self.store.init()
        return search_memory(self.store.root, query)

    def context(self, audience: str) -> str:
        self.store.init()
        return render_memory_context(self.store.root, audience)
