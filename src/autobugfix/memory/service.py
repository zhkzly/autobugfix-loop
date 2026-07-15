from __future__ import annotations

import hashlib
import json
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
from autobugfix.memory.store import MemoryStore, MemoryStoreError
from autobugfix.task_store import TaskStore


class MemoryServiceError(RuntimeError):
    pass


def _require_memory_eligible_task(task: Mapping[str, Any], task_id: str) -> None:
    metadata = task.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise MemoryServiceError(f"task {task_id} has invalid provenance metadata")
    if metadata.get("origin") != "execution" or metadata.get("memory_eligible") is not True:
        raise MemoryServiceError(
            "memory may not collect Eval or benchmark evidence, or untrusted task evidence; "
            f"explicit accepted Execution provenance is required: {task_id}"
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


def _record_digest(record: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_digest"}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _task_evidence_view(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in task.items()
        if key not in {"state", "updated_at", "archived_result", "archived_path"}
    }


def _proposal_review_digest(proposal: Mapping[str, Any]) -> str:
    return _record_digest(
        {
            "schema": proposal.get("schema"),
            "proposal_id": proposal.get("proposal_id"),
            "task_id": proposal.get("task_id"),
            "packet_sha256": proposal.get("packet_sha256"),
            "digest_sha256": proposal.get("digest_sha256"),
            "patch_sha256": proposal.get("patch_sha256"),
        }
    )


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

    def _execution_store(self) -> TaskStore:
        execution_config = load_config(self.project_root)
        return TaskStore(self.project_root, execution_config.task_root)

    def _validated_packet(self, task_id: str) -> dict[str, Any]:
        packet = self.store.read_yaml(
            self.store.raw_task_dir(task_id) / "packet.yaml"
        )
        if packet.get("schema") != "autobugfix-memory-packet-v1":
            raise MemoryServiceError(f"unsupported memory packet schema: {task_id}")
        if packet.get("record_digest") != _record_digest(packet):
            raise MemoryServiceError(f"memory packet digest mismatch: {task_id}")
        _require_memory_eligible_packet(packet, task_id)
        execution_store = self._execution_store()
        authoritative = collect_task_packet(execution_store, task_id)
        authoritative_task = authoritative.get("task")
        packet_task = packet.get("task")
        if not isinstance(authoritative_task, Mapping) or not isinstance(packet_task, Mapping):
            raise MemoryServiceError(f"invalid authoritative task evidence: {task_id}")
        _require_memory_eligible_task(authoritative_task, task_id)
        if _task_evidence_view(packet_task) != _task_evidence_view(authoritative_task):
            raise MemoryServiceError(
                f"memory packet differs from authoritative Execution task: {task_id}"
            )
        packet_events = packet.get("events")
        authoritative_events = authoritative.get("events")
        if not isinstance(packet_events, list) or not isinstance(authoritative_events, list):
            raise MemoryServiceError(f"invalid Execution event evidence: {task_id}")
        if authoritative_events[: len(packet_events)] != packet_events:
            raise MemoryServiceError(
                f"memory packet event history differs from Execution: {task_id}"
            )
        extra_events = authoritative_events[len(packet_events) :]
        if any(
            not isinstance(event, Mapping) or event.get("kind") != "task_archived"
            for event in extra_events
        ):
            raise MemoryServiceError(
                f"Execution evidence changed after memory collection: {task_id}"
            )
        for field in ("context", "feedback", "artifacts", "logs"):
            if packet.get(field) != authoritative.get(field):
                raise MemoryServiceError(
                    f"memory packet {field} differs from Execution: {task_id}"
                )
        return packet

    def init(self) -> None:
        self.store.init()

    def collect(self, task_id: str) -> Path:
        self.store.init()
        task_store = self._execution_store()
        task = task_store.load(task_id)
        _require_memory_eligible_task(task.to_dict(), task_id)
        packet = collect_task_packet(task_store, task_id)
        packet["schema"] = "autobugfix-memory-packet-v1"
        packet["record_digest"] = _record_digest(packet)
        raw_dir = self.store.raw_task_dir(task_id)
        self.store.ensure_directory(raw_dir, exist_ok=False)
        self.store.write_yaml(raw_dir / "packet.yaml", packet)
        return raw_dir / "packet.yaml"

    def digest(self, task_id: str) -> Path:
        self.store.init()
        packet = self._validated_packet(task_id)
        digest = render_digest(packet)
        path = self.store.digest_path(task_id)
        self.store._atomic_write(path, digest.encode("utf-8"))
        self.store.write_yaml(self.store.root / "digests/index.yaml", {"last_digest": task_id})
        return path

    def maintain(self, task_id: str) -> Path:
        self.store.init()
        packet = self._validated_packet(task_id)
        digest_path = self.store.digest_path(task_id)
        digest_text = self._validated_digest(task_id, packet)
        proposal_id = proposal_id_for(task_id)
        run_dir = self.store.root / "maintainer-runs" / proposal_id
        self.store.ensure_directory(run_dir)
        maintainer_text = self.backend.maintain(
            self.project_root,
            run_dir,
            digest_text,
            self.config.maintainer.model,
            self.config.maintainer.timeout_seconds,
            self.config.maintainer.role,
        )
        self.store._atomic_write(
            run_dir / "maintainer.md",
            maintainer_text.encode("utf-8"),
        )
        proposal_dir = self.store.proposal_dir(proposal_id)
        self.store.ensure_directory(proposal_dir, exist_ok=False)
        patch_text = render_patch(task_id, maintainer_text)
        patch_sha256 = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        proposal = {
                "schema": "autobugfix-memory-proposal-v1",
                "proposal_id": proposal_id,
                "task_id": task_id,
                "status": "pending",
                "run_dir": str(run_dir),
                "packet_sha256": str(packet["record_digest"]),
                "digest_sha256": hashlib.sha256(
                    digest_text.encode("utf-8")
                ).hexdigest(),
                "patch_sha256": patch_sha256,
            }
        proposal["review_digest"] = _proposal_review_digest(proposal)
        self.store.write_yaml(proposal_dir / "proposal.yaml", proposal)
        self.store._atomic_write(proposal_dir / "patch.md", patch_text.encode("utf-8"))
        self.store._atomic_write(
            proposal_dir / "evidence.md",
            f"Digest: {digest_path}\nRaw: {self.store.raw_task_dir(task_id)}\n".encode("utf-8"),
        )
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
        _, patch_text = self._validated_proposal(proposal_id)
        return patch_text

    def review(self, proposal_id: str) -> dict[str, Any]:
        proposal, patch_text = self._validated_proposal(proposal_id)
        return {
            "proposal_id": proposal_id,
            "task_id": proposal["task_id"],
            "review_digest": proposal["review_digest"],
            "packet_sha256": proposal["packet_sha256"],
            "digest_sha256": proposal["digest_sha256"],
            "patch_sha256": proposal["patch_sha256"],
            "patch": patch_text,
        }

    def approve(self, proposal_id: str, note: str, confirm_review_digest: str) -> Path:
        proposal, _ = self._validated_proposal(proposal_id)
        if confirm_review_digest != proposal["review_digest"]:
            raise MemoryServiceError(
                "human approval digest does not match the reviewed proposal"
            )
        return self.store.approve(
            proposal_id,
            note,
            expected_patch_sha256=str(proposal["patch_sha256"]),
            expected_review_digest=confirm_review_digest,
        )

    def approve_skill(
        self,
        proposal_id: str,
        skill_name: str,
        description: str,
        note: str,
        confirm_review_digest: str,
    ) -> Path:
        proposal, _ = self._validated_proposal(proposal_id)
        if confirm_review_digest != proposal["review_digest"]:
            raise MemoryServiceError(
                "human approval digest does not match the reviewed proposal"
            )
        return self.store.approve_skill(
            proposal_id,
            skill_name,
            description,
            note,
            expected_patch_sha256=str(proposal["patch_sha256"]),
            expected_review_digest=confirm_review_digest,
        )

    def _validated_digest(self, task_id: str, packet: Mapping[str, Any]) -> str:
        path = self.store.digest_path(task_id)
        try:
            content = self.store.read_regular_file(path, label="memory digest")
            text = content.decode("utf-8")
        except (MemoryStoreError, UnicodeError) as exc:
            raise MemoryServiceError(f"missing or invalid digest for task {task_id}") from exc
        if text != render_digest(dict(packet)):
            raise MemoryServiceError(
                f"memory digest differs from deterministic Execution evidence: {task_id}"
            )
        return text

    def _validated_proposal(self, proposal_id: str) -> tuple[dict[str, Any], str]:
        proposal_dir = self.store.proposal_dir(proposal_id)
        proposal = self.store.read_yaml(proposal_dir / "proposal.yaml")
        if proposal.get("schema") != "autobugfix-memory-proposal-v1":
            raise MemoryServiceError("unsupported memory proposal schema")
        if proposal.get("proposal_id") != proposal_id:
            raise MemoryServiceError("memory proposal identity does not match its directory")
        task_id = str(proposal.get("task_id") or "")
        packet = self._validated_packet(task_id)
        digest_text = self._validated_digest(task_id, packet)
        patch_path = proposal_dir / "patch.md"
        try:
            patch_content = self.store.read_regular_file(
                patch_path,
                label="memory proposal patch",
            )
            patch_text = patch_content.decode("utf-8")
        except (MemoryStoreError, UnicodeError) as exc:
            raise MemoryServiceError("memory proposal evidence is incomplete") from exc
        expected = {
            "packet_sha256": str(packet["record_digest"]),
            "digest_sha256": hashlib.sha256(digest_text.encode("utf-8")).hexdigest(),
            "patch_sha256": hashlib.sha256(patch_content).hexdigest(),
        }
        if any(proposal.get(key) != value for key, value in expected.items()):
            raise MemoryServiceError("memory proposal evidence changed after review")
        if proposal.get("review_digest") != _proposal_review_digest(proposal):
            raise MemoryServiceError("memory proposal review digest mismatch")
        return proposal, patch_text

    def reject(self, proposal_id: str, reason: str) -> Path:
        return self.store.reject(proposal_id, reason)

    def lint(self) -> list[str]:
        if not self.store.root.exists():
            self.store.init()
        return lint_memory_root(self.store.root)

    def search(self, query: str) -> list[str]:
        self.store.init()
        return search_memory(self.store.root, query)

    def context(self, audience: str) -> str:
        self.store.init()
        return render_memory_context(self.store.root, audience)
