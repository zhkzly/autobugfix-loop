from __future__ import annotations

from pathlib import Path

from autobugfix.codex_backend import CodexBackend
from autobugfix.codex_runtime import build_codex_request
from autobugfix.evaluator import parse_evaluator_decision
from autobugfix.models import AutobugfixConfig, RepoProfile, TaskRecord, utc_now
from autobugfix.prompts import evaluator_prompt, writer_prompt
from autobugfix.role_config import resolve_role
from autobugfix.task_store import TaskStore
from autobugfix.verifier import run_verifier, write_test_result
from autobugfix.worktree import diff_for_task


class RunnerError(RuntimeError):
    pass


class TaskRunner:
    def __init__(self, config: AutobugfixConfig, store: TaskStore, backend: CodexBackend) -> None:
        self.config = config
        self.store = store
        self.backend = backend

    def run(self, task_id: str) -> TaskRecord:
        task = self.store.load(task_id)
        if task.state not in {"ready", "feedback_available", "writer_rework_required"}:
            raise RunnerError(f"task {task_id} is not runnable from state {task.state}")
        repo = self.config.repo(task.repo_id)
        if not task.worktree_path:
            raise RunnerError(f"task {task_id} has no worktree")
        worktree = Path(task.worktree_path)
        task.iterations += 1
        self._save_state(task, "writing", "")
        task_dir = self.store.find_task_dir(task_id)
        context = self.store.read_text_tree(task_id, "context")
        feedback = self.store.read_text_tree(task_id, "feedback")

        writer_role = resolve_role(self.config, "writer", repo_id=task.repo_id)
        writer_request = build_codex_request(
            self.config.project_root,
            "writer",
            writer_prompt(task.body or task.title, context, feedback),
            worktree,
            None,
            None,
            None,
            self._path_from_template(task_dir, writer_role.raw_log_template, task, "writer"),
            self._path_from_template(task_dir, writer_role.stderr_log_template, task, "writer"),
            repo_id=task.repo_id,
            resolved_role=writer_role,
        )
        writer_result = self.backend.run(writer_request)
        (task_dir / "runs" / f"writer-{task.iterations}.md").write_text(writer_result.text, encoding="utf-8")
        self.store.append_event(task_id, "writer_completed", {"iteration": task.iterations})

        self._save_state(task, "verifying", "")
        test_result = run_verifier(
            worktree,
            repo.test_commands.full,
            timeout_seconds=self.config.scheduler.codex_timeout_seconds,
        )
        write_test_result(task_dir / "artifacts" / "test-result.md", test_result)
        diff_text = diff_for_task(repo, worktree)
        (task_dir / "artifacts" / "diff.patch").write_text(diff_text, encoding="utf-8")
        self.store.append_event(
            task_id,
            "verifier_completed",
            {"command": test_result.command, "exit_code": test_result.exit_code, "passed": test_result.passed},
        )
        if not test_result.passed:
            self._save_state(task, "writer_rework_required", f"verifier failed with {test_result.exit_code}")
            return task

        self._save_state(task, "evaluating", "")
        evaluator_role = resolve_role(self.config, "evaluator", repo_id=task.repo_id)
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
        )
        evaluator_result = self.backend.run(evaluator_request)
        (task_dir / "runs" / f"evaluator-{task.iterations}.md").write_text(evaluator_result.text, encoding="utf-8")
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
