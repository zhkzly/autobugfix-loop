from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.evaluator import parse_evaluator_decision
from autobugfix.git_utils import current_branch, git_common_dir, rev_parse, run_git
from autobugfix.memory.context import render_memory_context
from autobugfix.memory.fs import MemoryFileError
from autobugfix.models import AutobugfixConfig, RepoProfile, TaskRecord, utc_now
from autobugfix.prompts import evaluator_prompt, writer_prompt
from autobugfix.role_config import resolve_role
from autobugfix.task_store import TaskStore
from autobugfix.verifier import (
    ExecutionVerifierBackend,
    run_verifier,
    write_test_result,
)
from autobugfix.worktree import (
    diff_for_task,
    remove_ignored_writer_outputs,
    validate_task_worktree,
    verification_worktree,
)


class RunnerError(RuntimeError):
    pass


class TaskRunner:
    def __init__(
        self,
        config: AutobugfixConfig,
        store: TaskStore,
        backend: CodexBackend,
        *,
        verifier_backend: ExecutionVerifierBackend | None = None,
        sdk_hidden_paths: tuple[Path, ...] = (),
    ) -> None:
        self.config = config
        self.store = store
        self.backend = backend
        self.verifier_backend = verifier_backend
        self.sdk_hidden_paths = sdk_hidden_paths

    def run(self, task_id: str) -> TaskRecord:
        try:
            return self._run(task_id)
        except BaseException as exc:
            current = self.store.load(task_id)
            if current.state in {
                "ready",
                "feedback_available",
                "writer_rework_required",
                "writing",
                "verifying",
                "evaluating",
            }:
                reason = f"unhandled execution failure: {type(exc).__name__}: {exc}"
                self._save_state(current, "blocked", reason)
                self.store.append_event(
                    task_id,
                    "execution_node_failed",
                    {
                        "node": "runner",
                        "classification": "harness_error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "iteration": current.iterations,
                    },
                )
            raise

    def _run(self, task_id: str) -> TaskRecord:
        task = self.store.load(task_id)
        if task.state not in {"ready", "feedback_available", "writer_rework_required"}:
            raise RunnerError(f"task {task_id} is not runnable from state {task.state}")
        repo = self.config.repo(task.repo_id)
        if not task.worktree_path:
            raise RunnerError(f"task {task_id} has no worktree")
        try:
            worktree = validate_task_worktree(
                repo,
                Path(task.worktree_path),
                task.branch,
            )
        except Exception as exc:
            self._block_and_raise(
                task,
                "policy_violation",
                f"invalid task worktree: {exc}",
            )
        task.iterations += 1
        task_dir = self.store.find_task_dir(task_id)
        attempt_dir = task_dir / "artifacts" / "attempts" / f"{task.iterations:04d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        try:
            main_baseline = self._main_snapshot(repo)
        except Exception as exc:
            self._block_and_raise(
                task,
                "harness_error",
                f"cannot snapshot configured main checkout: {exc}",
            )
        self._record_main_snapshot(attempt_dir, "before_writer", main_baseline, True)
        if main_baseline["status"]:
            self._block_and_raise(
                task,
                "policy_violation",
                "configured main checkout became dirty before the Writer started",
            )

        writer_role = resolve_role(self.config, "writer", repo_id=task.repo_id)
        evaluator_role = resolve_role(self.config, "evaluator", repo_id=task.repo_id)
        self._require_role_boundary(task, "writer", writer_role.sandbox, writer_role.approval_mode)
        self._require_role_boundary(
            task,
            "evaluator",
            evaluator_role.sandbox,
            evaluator_role.approval_mode,
        )

        self._save_state(task, "writing", "")
        context = self.store.read_text_tree(task_id, "context")
        feedback = self.store.read_text_tree(task_id, "feedback")
        try:
            memory_context = render_memory_context(
                self.config.project_root / ".autobugfix-memory",
                "writer",
            )
        except (MemoryFileError, UnicodeError) as exc:
            self._block_and_raise(
                task,
                "harness_error",
                f"approved Memory context is invalid: {exc}",
            )

        writer_request = build_codex_request(
            self.config.project_root,
            "writer",
            writer_prompt(
                task.body or task.title,
                context,
                feedback,
                memory_context,
            ),
            worktree,
            None,
            None,
            None,
            self._path_from_template(task_dir, writer_role.raw_log_template, task, "writer"),
            self._path_from_template(task_dir, writer_role.stderr_log_template, task, "writer"),
            repo_id=task.repo_id,
            resolved_role=writer_role,
            hidden_paths=self.sdk_hidden_paths,
            expected_git_common_dir=git_common_dir(repo.main_checkout),
        )
        try:
            writer_result = self.backend.run(writer_request)
        except BaseException as exc:
            self._capture_diff(task, repo, worktree, attempt_dir, task_dir)
            self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "writer_failed")
            self._handle_node_failure(task, "writer", exc)
        (task_dir / "runs" / f"writer-{task.iterations}.md").write_text(writer_result.text, encoding="utf-8")
        self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "after_writer")
        removed_ignored = remove_ignored_writer_outputs(
            worktree,
            tuple(str(item) for item in task.metadata.get("initial_ignored_paths") or ()),
        )
        (attempt_dir / "ignored-writer-outputs.json").write_text(
            json.dumps({"removed": list(removed_ignored)}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if removed_ignored:
            self.store.append_event(
                task_id,
                "ignored_writer_outputs_removed",
                {"iteration": task.iterations, "paths": list(removed_ignored)},
            )
        try:
            worktree = validate_task_worktree(repo, worktree, task.branch)
        except Exception as exc:
            self._block_and_raise(
                task,
                "policy_violation",
                f"Writer changed task worktree Git authority: {exc}",
            )
        diff_text = self._capture_diff(task, repo, worktree, attempt_dir, task_dir)
        task.metadata["candidate_patch_sha256"] = hashlib.sha256(
            diff_text.encode("utf-8", errors="surrogateescape")
        ).hexdigest()
        self.store.append_event(task_id, "writer_completed", {"iteration": task.iterations})

        self._save_state(task, "verifying", "")
        try:
            base_ref = str(
                task.metadata.get("base_commit")
                or f"{repo.remote}/{repo.main_branch}"
            )
            verifier_path = attempt_dir / "verification-worktree"
            with verification_worktree(
                repo,
                worktree,
                base_ref,
                diff_text,
                verifier_path,
            ) as verifier_worktree:
                if self.verifier_backend is None:
                    test_result = run_verifier(
                        verifier_worktree,
                        repo.test_commands.full,
                        timeout_seconds=self.config.scheduler.codex_timeout_seconds,
                    )
                else:
                    if repo.test_commands.full != self.verifier_backend.command_id:
                        self._block_and_raise(
                            task,
                            "policy_violation",
                            "managed verifier command ID does not match trusted backend",
                        )
                    test_result = self.verifier_backend.run(
                        verifier_worktree,
                        attempt_dir / "managed-verifier",
                        timeout_seconds=self.config.scheduler.codex_timeout_seconds,
                    )
        except RunnerError:
            raise
        except BaseException as exc:
            self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "verifier_failed")
            self._handle_node_failure(task, "verifier", exc)
        attempt_test_result = attempt_dir / "test-result.md"
        write_test_result(attempt_test_result, test_result)
        shutil.copy2(attempt_test_result, task_dir / "artifacts" / "test-result.md")
        self._check_candidate_unchanged(
            task,
            repo,
            worktree,
            diff_text,
            "the isolated verifier was running",
        )
        self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "after_verifier")
        self.store.append_event(
            task_id,
            "verifier_completed",
            {
                "command": test_result.command,
                "exit_code": test_result.exit_code,
                "outcome": test_result.outcome,
                "passed": test_result.passed,
                "attempt": task.iterations,
                "artifact": str(attempt_test_result.relative_to(task_dir)),
            },
        )
        if test_result.outcome == "repair_failure":
            self._save_state(task, "writer_rework_required", f"verifier failed with {test_result.exit_code}")
            return task
        if test_result.outcome in {"harness_error", "policy_violation"}:
            self._block_and_raise(
                task,
                test_result.outcome,
                f"verifier {test_result.outcome} with exit {test_result.exit_code}",
            )

        self._save_state(task, "evaluating", "")
        evaluator_request = build_codex_request(
            self.config.project_root,
            "evaluator",
            evaluator_prompt(task.body or task.title, diff_text, (task_dir / "artifacts" / "test-result.md").read_text(encoding="utf-8")),
            worktree,
            None,
            None,
            None,
            self._path_from_template(task_dir, evaluator_role.raw_log_template, task, "evaluator"),
            self._path_from_template(task_dir, evaluator_role.stderr_log_template, task, "evaluator"),
            repo_id=task.repo_id,
            resolved_role=evaluator_role,
            hidden_paths=self.sdk_hidden_paths,
            expected_git_common_dir=git_common_dir(repo.main_checkout),
        )
        try:
            evaluator_result = self.backend.run(evaluator_request)
        except BaseException as exc:
            self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "evaluator_failed")
            self._check_candidate_unchanged(
                task,
                repo,
                worktree,
                diff_text,
                "the evaluator failed",
            )
            self._handle_node_failure(task, "evaluator", exc)
        (task_dir / "runs" / f"evaluator-{task.iterations}.md").write_text(evaluator_result.text, encoding="utf-8")
        self._check_main_unchanged(task, repo, attempt_dir, main_baseline, "after_evaluator")
        self._check_candidate_unchanged(
            task,
            repo,
            worktree,
            diff_text,
            "the read-only evaluator was running",
        )
        decision = parse_evaluator_decision(evaluator_result.text)
        self.store.append_event(task_id, "evaluator_completed", {"decision": decision.decision, "reason": decision.reason})
        if decision.passed:
            self._write_ppe_brief(task_dir, task, repo, diff_text)
            self._save_state(task, "waiting_human_ppe_approval", "")
        elif decision.decision == "blocked":
            self._save_state(task, "blocked", decision.reason or "evaluator blocked")
        else:
            self._save_state(task, "writer_rework_required", decision.reason or "evaluator requested changes")
        return task

    @staticmethod
    def _main_snapshot(repo: RepoProfile) -> dict[str, str]:
        return {
            "head": rev_parse(repo.main_checkout, "HEAD"),
            "branch": current_branch(repo.main_checkout),
            "status": run_git(
                repo.main_checkout,
                ["status", "--porcelain=v1", "--untracked-files=all"],
            ).stdout,
        }

    @staticmethod
    def _record_main_snapshot(
        attempt_dir: Path,
        stage: str,
        snapshot: dict[str, str],
        matches_baseline: bool,
    ) -> None:
        path = attempt_dir / "main-checkout.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": utc_now(),
                        "stage": stage,
                        "matches_baseline": matches_baseline,
                        **snapshot,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    def _check_main_unchanged(
        self,
        task: TaskRecord,
        repo: RepoProfile,
        attempt_dir: Path,
        baseline: dict[str, str],
        stage: str,
    ) -> None:
        observed = self._main_snapshot(repo)
        matches = observed == baseline
        self._record_main_snapshot(attempt_dir, stage, observed, matches)
        if not matches:
            self._block_and_raise(
                task,
                "policy_violation",
                f"configured main checkout changed during {stage}",
            )

    def _require_role_boundary(
        self,
        task: TaskRecord,
        role: str,
        sandbox: str,
        approval_mode: str,
    ) -> None:
        expected = {
            "writer": ("workspace-write", "auto_review"),
            "evaluator": ("read-only", "deny_all"),
        }[role]
        if (sandbox, approval_mode) != expected:
            self._block_and_raise(
                task,
                "policy_violation",
                f"{role} role must use sandbox={expected[0]} and approval_mode={expected[1]}; "
                f"resolved sandbox={sandbox}, approval_mode={approval_mode} for task {task.task_id}",
            )

    def _capture_diff(
        self,
        task: TaskRecord,
        repo: RepoProfile,
        worktree: Path,
        attempt_dir: Path,
        task_dir: Path,
    ) -> str:
        base_ref = str(task.metadata.get("base_commit") or f"{repo.remote}/{repo.main_branch}")
        diff_text = diff_for_task(repo, worktree, base_ref)
        attempt_diff = attempt_dir / "diff.patch"
        attempt_diff.write_text(
            diff_text, encoding="utf-8", errors="surrogateescape"
        )
        shutil.copy2(attempt_diff, task_dir / "artifacts" / "diff.patch")
        return diff_text

    def _check_candidate_unchanged(
        self,
        task: TaskRecord,
        repo: RepoProfile,
        worktree: Path,
        expected_diff: str,
        stage: str,
    ) -> None:
        base_ref = str(task.metadata.get("base_commit") or f"{repo.remote}/{repo.main_branch}")
        observed = diff_for_task(repo, worktree, base_ref)
        if observed != expected_diff:
            self._block_and_raise(
                task,
                "policy_violation",
                f"task worktree changed while {stage}",
            )

    def _handle_node_failure(
        self,
        task: TaskRecord,
        node: str,
        exc: BaseException,
    ) -> None:
        reason = f"{node} runtime failed: {type(exc).__name__}: {exc}"
        self._save_state(task, "blocked", reason)
        self.store.append_event(
            task.task_id,
            "execution_node_failed",
            {
                "node": node,
                "classification": "harness_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "iteration": task.iterations,
            },
        )
        if not isinstance(exc, Exception):
            raise exc
        raise RunnerError(reason) from exc

    def _block_and_raise(
        self,
        task: TaskRecord,
        classification: str,
        reason: str,
    ) -> None:
        self._save_state(task, "blocked", reason)
        self.store.append_event(
            task.task_id,
            "execution_blocked",
            {
                "classification": classification,
                "reason": reason,
                "iteration": task.iterations,
            },
        )
        raise RunnerError(reason)

    def _path_from_template(self, task_dir: Path, template: str, task: TaskRecord, role: str) -> Path:
        value = template.format(role=role, iteration=task.iterations, task_id=task.task_id, repo_id=task.repo_id)
        path = Path(value)
        if not path.is_absolute():
            path = task_dir / path
        return path

    def _save_state(self, task: TaskRecord, state: str, block_reason: str) -> None:
        old = task.state
        task.state = state  # type: ignore[assignment]
        task.block_reason = block_reason
        task.updated_at = utc_now()
        self.store.save(task)
        self.store.append_event(task.task_id, "state_changed", {"from": old, "to": state, "block_reason": block_reason})

    def _write_ppe_brief(self, task_dir: Path, task: TaskRecord, repo: RepoProfile, diff_text: str) -> None:
        (task_dir / "artifacts" / "ppe-brief.md").write_text(
            "\n".join(
                [
                    f"# PPE Brief: {task.title}",
                    f"repo: {repo.repo_id}",
                    f"branch: {task.branch}",
                    f"worktree: {task.worktree_path}",
                    "",
                    "## Diff",
                    "```diff",
                    diff_text,
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
