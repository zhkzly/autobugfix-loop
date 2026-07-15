from __future__ import annotations

import hashlib
from pathlib import Path

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.config import load_config
from autobugfix.git_utils import ensure_git_repo, rev_parse
from autobugfix.models import RUNNABLE_STATES, AutobugfixConfig, TaskRecord
from autobugfix.ppe import deploy_ppe as run_ppe_deploy
from autobugfix.runner import TaskRunner
from autobugfix.task_store import TaskStore
from autobugfix.verifier import ExecutionVerifierBackend
from autobugfix.worktree import create_task_worktree, ignored_paths
from autobugfix.worktree import diff_for_task, validate_task_worktree


class ServiceError(RuntimeError):
    pass


class AutobugfixService:
    def __init__(
        self,
        project_root: Path | str = ".",
        config: AutobugfixConfig | None = None,
        backend: CodexBackend | None = None,
        verifier_backend: ExecutionVerifierBackend | None = None,
        sdk_hidden_paths: tuple[Path, ...] = (),
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config or load_config(self.project_root)
        self.store = TaskStore(self.project_root, self.config.task_root)
        self.backend = backend or CodexSDKBackend()
        self.verifier_backend = verifier_backend
        self.sdk_hidden_paths = tuple(path.resolve() for path in sdk_hidden_paths)

    def create_task(
        self,
        repo_id: str,
        title: str,
        body: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> TaskRecord:
        repo = self.config.repo(repo_id)
        ensure_git_repo(repo.main_checkout)
        task_id = self.store.new_task_id(title)
        branch, worktree_path = create_task_worktree(repo, task_id, title)
        task_metadata = dict(metadata or {})
        task_metadata.setdefault("origin", "execution")
        task_metadata.setdefault(
            "memory_eligible",
            task_metadata["origin"] == "execution",
        )
        if task_metadata["origin"] == "eval":
            task_metadata["memory_eligible"] = False
        task_metadata["base_commit"] = rev_parse(worktree_path, "HEAD")
        task_metadata["initial_ignored_paths"] = list(ignored_paths(worktree_path))
        record = TaskRecord(
            task_id=task_id,
            repo_id=repo_id,
            title=title,
            body=body,
            state="ready",
            branch=branch,
            worktree_path=str(worktree_path),
            main_checkout=str(repo.main_checkout),
            metadata=task_metadata,
        )
        self.store.create(record)
        self.store.append_event(task_id, "worktree_created", {"branch": branch, "path": str(worktree_path)})
        return record

    def add_context(self, task_id: str, kind: str, content: str) -> Path:
        return self.store.add_context(task_id, kind, content)

    def add_feedback(self, task_id: str, decision: str, content: str, queue_only: bool = False) -> TaskRecord:
        record = self.store.load(task_id)
        allowed = {
            "writer_rework_required",
            "waiting_human_review",
            "waiting_human_ppe_approval",
            "ppe_approved",
            "ppe_deployed",
            "waiting_human_acceptance",
            "blocked",
            "paused",
        }
        if record.state not in allowed:
            raise ServiceError(f"cannot add feedback from state {record.state}")
        self.store.add_feedback(task_id, decision, content, queue_only)
        if not queue_only:
            record.state = "feedback_available"
            record.block_reason = ""
            self.store.save(record)
            self.store.append_event(task_id, "state_changed", {"to": "feedback_available", "decision": decision})
        return record

    def run_task(self, task_id: str) -> TaskRecord:
        record = self.store.load(task_id)
        if record.state not in RUNNABLE_STATES:
            raise ServiceError(f"task {task_id} is not runnable from state {record.state}")
        return TaskRunner(
            self.config,
            self.store,
            self.backend,
            verifier_backend=self.verifier_backend,
            sdk_hidden_paths=self.sdk_hidden_paths,
        ).run(task_id)

    def apply_gate(self, task_id: str, action: str) -> TaskRecord:
        record = self.store.load(task_id)
        old = record.state
        if action == "approve-ppe":
            if record.state != "waiting_human_ppe_approval":
                raise ServiceError(f"cannot approve PPE from {record.state}")
            self._require_frozen_candidate(record)
            record.state = "ppe_approved"
        elif action == "accepted":
            if record.state not in {"waiting_human_ppe_approval", "ppe_approved", "ppe_deployed", "waiting_human_acceptance"}:
                raise ServiceError(f"cannot accept from {record.state}")
            self._require_frozen_candidate(record)
            record.state = "accepted"
        elif action == "abandoned":
            record.state = "abandoned"
        elif action == "pause":
            record.state = "paused"
        elif action == "resume":
            if record.state != "paused":
                raise ServiceError(f"cannot resume from {record.state}")
            record.state = "ready"
        else:
            raise ServiceError(f"unknown gate action: {action}")
        record.block_reason = ""
        self.store.save(record)
        self.store.append_event(task_id, "human_gate_applied", {"action": action, "from": old, "to": record.state})
        return record

    def deploy_ppe(self, task_id: str) -> TaskRecord:
        record = self.store.load(task_id)
        if record.state != "ppe_approved":
            raise ServiceError(f"cannot deploy PPE from {record.state}")
        repo = self.config.repo(record.repo_id)
        if not record.worktree_path:
            raise ServiceError("task has no worktree")
        self._require_frozen_candidate(record)
        result = run_ppe_deploy(repo, Path(record.worktree_path), task_id)
        task_dir = self.store.find_task_dir(task_id)
        (task_dir / "artifacts" / "ppe-deploy.log").write_text(
            f"exit_code: {result.returncode}\n\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n",
            encoding="utf-8",
        )
        if result.returncode != 0:
            record.state = "blocked"
            record.block_reason = f"PPE deploy failed with {result.returncode}"
        else:
            record.state = "ppe_deployed"
            record.block_reason = ""
        self.store.save(record)
        self.store.append_event(task_id, "ppe_deployed", {"exit_code": result.returncode})
        return record

    def _require_frozen_candidate(self, record: TaskRecord) -> None:
        if not record.worktree_path:
            raise ServiceError("task has no worktree")
        repo = self.config.repo(record.repo_id)
        try:
            worktree = validate_task_worktree(
                repo,
                Path(record.worktree_path),
                record.branch,
            )
            base_ref = str(
                record.metadata.get("base_commit")
                or f"{repo.remote}/{repo.main_branch}"
            )
            patch = diff_for_task(repo, worktree, base_ref)
        except Exception as exc:
            raise ServiceError(f"cannot validate frozen task candidate: {exc}") from exc
        observed = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        expected = str(record.metadata.get("candidate_patch_sha256") or "")
        task_dir = self.store.find_task_dir(record.task_id)
        artifact = task_dir / "artifacts/diff.patch"
        if (
            len(expected) != 64
            or observed != expected
            or not artifact.is_file()
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected
        ):
            raise ServiceError("task candidate changed after verification/evaluation")

    def archive(self, task_id: str, result: str) -> Path:
        record = self.store.load(task_id)
        if record.state not in {"accepted", "abandoned", "blocked", "paused"}:
            raise ServiceError(f"cannot archive task from {record.state}")
        return self.store.archive(task_id, result)
