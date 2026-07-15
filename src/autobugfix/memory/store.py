from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from autobugfix.memory.fs import (
    MemoryFileError,
    atomic_write,
    ensure_directory,
    open_lock_descriptor,
    read_regular_file,
    replace_directory,
)

try:  # pragma: no cover - platform-specific imports
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform-specific imports
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class MemoryStoreError(RuntimeError):
    pass


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    def init(self) -> None:
        if self.root.is_symlink():
            raise MemoryStoreError(f"Memory authority root is redirected: {self.root}")
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
            self.ensure_directory(self.root / rel)
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
            if path.exists() or path.is_symlink():
                self.read_regular_file(path, label=f"Memory bootstrap file {rel}")
            else:
                self._atomic_write(path, content.encode("utf-8"))

    def ensure_directory(self, path: Path, *, exist_ok: bool = True) -> None:
        try:
            relative = path.relative_to(self.root)
            ensure_directory(self.root, relative, exist_ok=exist_ok)
        except (ValueError, MemoryFileError) as exc:
            raise MemoryStoreError(str(exc)) from exc

    def raw_task_dir(self, task_id: str) -> Path:
        return self.root / "raw/tasks" / task_id

    def digest_path(self, task_id: str) -> Path:
        return self.root / "digests/tasks" / f"{task_id}.md"

    def proposal_dir(self, proposal_id: str) -> Path:
        return self.root / "proposals" / self._proposal_id(proposal_id)

    def rejected_dir(self, proposal_id: str) -> Path:
        return self.root / "rejected" / self._proposal_id(proposal_id)

    @staticmethod
    def _proposal_id(proposal_id: str) -> str:
        value = str(proposal_id)
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or Path(value).is_absolute()
        ):
            raise MemoryStoreError(
                f"memory proposal id contains unsupported path syntax: {proposal_id!r}"
            )
        return value

    @staticmethod
    def _skill_name(skill_name: str) -> str:
        value = str(skill_name).strip()
        if (
            len(value) > 63
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
        ):
            raise MemoryStoreError(
                "memory skill name must contain 1-63 lowercase letters, digits, or hyphens"
            )
        return value

    @staticmethod
    def _skill_content(skill_name: str, description: str, patch: bytes) -> bytes:
        normalized_description = str(description).strip()
        if (
            not normalized_description
            or len(normalized_description) > 512
            or "\n" in normalized_description
            or "\r" in normalized_description
        ):
            raise MemoryStoreError(
                "memory skill description must be one non-empty line up to 512 characters"
            )
        frontmatter = yaml.safe_dump(
            {"name": skill_name, "description": normalized_description},
            sort_keys=False,
        ).encode("utf-8")
        title = skill_name.replace("-", " ").title().encode("utf-8")
        return b"---\n" + frontmatter + b"---\n\n# " + title + b"\n\n" + patch

    @staticmethod
    def _read_regular_file_once(path: Path, *, label: str) -> bytes:
        try:
            return read_regular_file(path.parent, path.name, label=label)
        except MemoryFileError as exc:
            raise MemoryStoreError(str(exc)) from exc

    def read_regular_file(self, path: Path, *, label: str) -> bytes:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise MemoryStoreError(f"{label} is outside Memory authority") from exc
        try:
            return read_regular_file(self.root, relative, label=label)
        except MemoryFileError as exc:
            raise MemoryStoreError(str(exc)) from exc

    def write_yaml(self, path: Path, data: dict[str, Any]) -> None:
        self._atomic_write(path, yaml.safe_dump(data, sort_keys=False).encode("utf-8"))

    def _atomic_write(self, path: Path, content: bytes) -> None:
        try:
            relative = path.relative_to(self.root)
            atomic_write(self.root, relative, content)
        except (ValueError, MemoryFileError) as exc:
            raise MemoryStoreError(str(exc)) from exc

    @contextmanager
    def _approval_lock(self):
        try:
            descriptor = open_lock_descriptor(self.root, ".approval.lock")
        except MemoryFileError as exc:
            raise MemoryStoreError(str(exc)) from exc
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - unsupported Python platform
                raise MemoryStoreError("no advisory file-lock implementation available")
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            os.close(descriptor)

    def read_yaml(self, path: Path) -> dict[str, Any]:
        content = self.read_regular_file(path, label=f"memory YAML {path.name}")
        try:
            data = yaml.safe_load(content.decode("utf-8")) or {}
        except (UnicodeError, yaml.YAMLError) as exc:
            raise MemoryStoreError(f"invalid YAML in {path}") from exc
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

    def approve(
        self,
        proposal_id: str,
        note: str,
        *,
        expected_patch_sha256: str,
        expected_review_digest: str,
    ) -> Path:
        with self._approval_lock():
            proposal = self.proposal_dir(proposal_id)
            if not proposal.exists() or proposal.is_symlink():
                raise MemoryStoreError(f"proposal not found: {proposal_id}")
            patch = proposal / "patch.md"
            meta_path = proposal / "proposal.yaml"
            meta = self.read_yaml(meta_path)
            if meta.get("proposal_id") != proposal_id:
                raise MemoryStoreError("memory proposal identity does not match its directory")
            if meta.get("review_digest") != expected_review_digest:
                raise MemoryStoreError("memory proposal review authority changed before activation")
            status = meta.get("status")
            if status not in {"pending", "activating"}:
                raise MemoryStoreError(
                    f"only pending proposals can be approved: {proposal_id}"
                )
            patch_content = self.read_regular_file(
                patch,
                label="memory proposal patch",
            )
            if hashlib.sha256(patch_content).hexdigest() != expected_patch_sha256:
                raise MemoryStoreError("memory proposal patch changed before activation")
            active = self.root / "active/user-preferences.md"
            current = self.read_regular_file(
                active,
                label="active memory authority",
            )
            current_sha = hashlib.sha256(current).hexdigest()
            if status == "pending":
                addition = (
                    f"\n\n## Approved Proposal {proposal_id}\n\n"
                    f"Approval note: {note}\n\n"
                ).encode("utf-8") + patch_content
                activated = current + addition
                meta["status"] = "activating"
                meta["approval_note"] = note
                meta["approved_review_digest"] = expected_review_digest
                meta["active_before_sha256"] = current_sha
                meta["active_after_sha256"] = hashlib.sha256(activated).hexdigest()
                self.write_yaml(meta_path, meta)
            else:
                before_sha = str(meta.get("active_before_sha256") or "")
                after_sha = str(meta.get("active_after_sha256") or "")
                if len(before_sha) != 64 or len(after_sha) != 64:
                    raise MemoryStoreError("memory activation journal is invalid")
                addition = (
                    f"\n\n## Approved Proposal {proposal_id}\n\n"
                    f"Approval note: {meta.get('approval_note') or ''}\n\n"
                ).encode("utf-8") + patch_content
                activated = current + addition
                if current_sha == after_sha:
                    activated = current
                elif current_sha != before_sha:
                    raise MemoryStoreError(
                        "active memory changed during proposal activation"
                    )
                elif hashlib.sha256(activated).hexdigest() != after_sha:
                    raise MemoryStoreError("memory activation journal digest mismatch")
            if hashlib.sha256(activated).hexdigest() != str(
                meta["active_after_sha256"]
            ):
                raise MemoryStoreError("memory activation output digest mismatch")
            if current_sha != str(meta["active_after_sha256"]):
                self._atomic_write(active, activated)
            meta["status"] = "approved"
            self.write_yaml(meta_path, meta)
            return active

    def approve_skill(
        self,
        proposal_id: str,
        skill_name: str,
        description: str,
        note: str,
        *,
        expected_patch_sha256: str,
        expected_review_digest: str,
    ) -> Path:
        with self._approval_lock():
            proposal = self.proposal_dir(proposal_id)
            if not proposal.exists() or proposal.is_symlink():
                raise MemoryStoreError(f"proposal not found: {proposal_id}")
            name = self._skill_name(skill_name)
            patch_path = proposal / "patch.md"
            meta_path = proposal / "proposal.yaml"
            meta = self.read_yaml(meta_path)
            if meta.get("proposal_id") != proposal_id:
                raise MemoryStoreError(
                    "memory proposal identity does not match its directory"
                )
            if meta.get("review_digest") != expected_review_digest:
                raise MemoryStoreError(
                    "memory proposal review authority changed before skill activation"
                )
            status = meta.get("status")
            if status not in {"pending", "activating_skill"}:
                raise MemoryStoreError(
                    f"only pending proposals can be approved as skills: {proposal_id}"
                )
            patch = self.read_regular_file(
                patch_path,
                label="memory proposal patch",
            )
            if hashlib.sha256(patch).hexdigest() != expected_patch_sha256:
                raise MemoryStoreError("memory proposal patch changed before skill activation")
            content = self._skill_content(name, description, patch)
            content_sha = hashlib.sha256(content).hexdigest()
            skill_dir = self.root / "skills/approved" / name
            skill_path = skill_dir / "SKILL.md"
            if status == "pending":
                if skill_dir.exists() or skill_dir.is_symlink():
                    raise MemoryStoreError(f"approved memory skill already exists: {name}")
                meta["status"] = "activating_skill"
                meta["approval_note"] = note
                meta["approved_review_digest"] = expected_review_digest
                meta["skill_name"] = name
                meta["skill_description"] = str(description).strip()
                meta["skill_sha256"] = content_sha
                self.write_yaml(meta_path, meta)
            else:
                if (
                    meta.get("skill_name") != name
                    or meta.get("skill_description") != str(description).strip()
                    or meta.get("skill_sha256") != content_sha
                    or meta.get("approval_note") != note
                ):
                    raise MemoryStoreError(
                        "memory skill activation arguments changed during recovery"
                    )
            if skill_dir.exists() or skill_dir.is_symlink():
                self.ensure_directory(skill_dir)
                existing = self.read_regular_file(
                    skill_path,
                    label=f"approved memory skill {name}",
                )
                if existing != content:
                    raise MemoryStoreError(
                        f"approved memory skill conflicts during activation: {name}"
                    )
            else:
                self.ensure_directory(skill_dir, exist_ok=False)
                self._atomic_write(skill_path, content)
            meta["status"] = "approved_skill"
            self.write_yaml(meta_path, meta)
            return skill_path

    def reject(self, proposal_id: str, reason: str) -> Path:
        with self._approval_lock():
            proposal = self.proposal_dir(proposal_id)
            dest = self.rejected_dir(proposal_id)
            if proposal.exists() and dest.exists():
                raise MemoryStoreError(f"ambiguous rejected proposal state: {proposal_id}")
            if not proposal.exists() and not dest.exists():
                raise MemoryStoreError(f"proposal not found: {proposal_id}")
            current = proposal if proposal.exists() else dest
            if current.is_symlink():
                raise MemoryStoreError(f"proposal is redirected: {proposal_id}")
            meta_path = current / "proposal.yaml"
            meta = self.read_yaml(meta_path)
            if meta.get("proposal_id") != proposal_id:
                raise MemoryStoreError("memory proposal identity does not match its directory")
            status = meta.get("status")
            if status == "rejected":
                if current != dest or meta.get("rejection_reason") != reason:
                    raise MemoryStoreError(f"proposal rejection conflicts: {proposal_id}")
                return dest
            if status not in {"pending", "rejecting"}:
                raise MemoryStoreError(
                    f"only pending proposals can be rejected: {proposal_id}"
                )
            if status == "pending":
                meta["status"] = "rejecting"
                meta["rejection_reason"] = reason
                self.write_yaml(meta_path, meta)
            elif meta.get("rejection_reason") != reason:
                raise MemoryStoreError(f"proposal rejection reason changed: {proposal_id}")
            if current == proposal:
                try:
                    replace_directory(
                        self.root,
                        proposal.relative_to(self.root),
                        dest.relative_to(self.root),
                    )
                except MemoryFileError as exc:
                    raise MemoryStoreError(str(exc)) from exc
                current = dest
                meta_path = current / "proposal.yaml"
            meta["status"] = "rejected"
            self.write_yaml(meta_path, meta)
            return dest
