from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


class MemoryStoreError(RuntimeError):
    pass


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for rel in (
            "active",
            "raw/tasks",
            "digests/tasks",
            "proposals",
            "rejected",
            "skills/approved",
            "maintainer-runs",
        ):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        files = {
            "README.md": "# Autobugfix Memory\n\nReviewed long-term memory for Autobugfix roles.\n",
            "schema.md": "# Memory Schema\n\nRaw packets, digests, proposals, and approved memory are separated.\n",
            "index.md": "# Memory Index\n\n",
            "log.md": "# Memory Log\n\n",
            "config.yaml": "maintainer:\n  backend: codex\n  model: null\n  timeout_seconds: 1800\n",
            "active/user-preferences.md": "# User Preferences\n\n",
        }
        for rel, content in files.items():
            path = self.root / rel
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def raw_task_dir(self, task_id: str) -> Path:
        return self.root / "raw/tasks" / task_id

    def digest_path(self, task_id: str) -> Path:
        return self.root / "digests/tasks" / f"{task_id}.md"

    def proposal_dir(self, proposal_id: str) -> Path:
        return self.root / "proposals" / proposal_id

    def rejected_dir(self, proposal_id: str) -> Path:
        return self.root / "rejected" / proposal_id

    def write_yaml(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise MemoryStoreError(f"missing file: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise MemoryStoreError(f"expected mapping in {path}")
        return data

    def list_proposals(self) -> list[dict[str, Any]]:
        proposals = []
        for path in sorted((self.root / "proposals").glob("*/proposal.yaml")):
            data = self.read_yaml(path)
            data["proposal_id"] = path.parent.name
            proposals.append(data)
        return proposals

    def approve(self, proposal_id: str, note: str) -> Path:
        proposal = self.proposal_dir(proposal_id)
        if not proposal.exists():
            raise MemoryStoreError(f"proposal not found: {proposal_id}")
        patch = proposal / "patch.md"
        active = self.root / "active/user-preferences.md"
        with active.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n## Approved Proposal {proposal_id}\n\nApproval note: {note}\n\n")
            handle.write(patch.read_text(encoding="utf-8") if patch.exists() else "")
        meta = self.read_yaml(proposal / "proposal.yaml")
        meta["status"] = "approved"
        meta["approval_note"] = note
        self.write_yaml(proposal / "proposal.yaml", meta)
        return active

    def reject(self, proposal_id: str, reason: str) -> Path:
        proposal = self.proposal_dir(proposal_id)
        if not proposal.exists():
            raise MemoryStoreError(f"proposal not found: {proposal_id}")
        dest = self.rejected_dir(proposal_id)
        if dest.exists():
            raise MemoryStoreError(f"rejected proposal already exists: {proposal_id}")
        meta = self.read_yaml(proposal / "proposal.yaml")
        meta["status"] = "rejected"
        meta["rejection_reason"] = reason
        self.write_yaml(proposal / "proposal.yaml", meta)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(proposal), str(dest))
        return dest
