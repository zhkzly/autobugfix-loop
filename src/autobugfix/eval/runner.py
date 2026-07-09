from __future__ import annotations

import json
from pathlib import Path

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.config import default_config_dict
from autobugfix.eval.artifacts import copy_role_skills, prepare_isolated_repo, read_jsonl, write_text, write_yaml
from autobugfix.eval.diagnosis import diagnose_run
from autobugfix.eval.models import EvalCase
from autobugfix.eval.scorers import score_case
from autobugfix.git_utils import run_git
from autobugfix.service import AutobugfixService


class EvalRunnerError(RuntimeError):
    pass


def _config_for_case(repo_id: str, main_checkout: Path, test_command: str | None) -> dict[str, object]:
    cfg = default_config_dict()
    cfg["repos"] = {
        repo_id: {
            "main_checkout": str(main_checkout),
            "remote": "origin",
            "main_branch": "main",
            "branch_template": "fix/{date}_oncall_{slug}",
            "test_commands": {
                "targeted": test_command or "uv run pytest",
                "full": test_command or "uv run pytest",
            },
            "ppe": {"enabled": False, "command_template": None},
        }
    }
    return cfg


def run_eval(
    project_root: Path,
    dataset: Path,
    out: Path,
    case_selector: str | None = None,
    run_id: str = "run",
    model_mode: str = "codex",
    test_command: str | None = None,
    codex_timeout_seconds: int | None = None,
    writer_timeout_seconds: int | None = None,
    evaluator_timeout_seconds: int | None = None,
    backend: CodexBackend | None = None,
) -> Path:
    rows = read_jsonl(dataset)
    cases = [EvalCase.from_row(row) for row in rows]
    if case_selector:
        cases = [case for case in cases if case.case_id == case_selector or case.raw and case.raw.get("case") == case_selector]
    if not cases:
        raise EvalRunnerError("no eval cases selected")
    run_dir = out / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(
        run_dir / "resolved-config.yaml",
        {
            "dataset": str(dataset),
            "case_selector": case_selector,
            "model_mode": model_mode,
            "test_command": test_command,
            "codex_timeout_seconds": codex_timeout_seconds,
            "writer_timeout_seconds": writer_timeout_seconds,
            "evaluator_timeout_seconds": evaluator_timeout_seconds,
        },
    )
    failures: list[str] = []
    for case in cases:
        case_dir = run_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        remote, main_checkout = prepare_isolated_repo(case, case_dir / "setup")
        control_root = case_dir / "control"
        copied_skills = copy_role_skills(project_root, control_root)
        cfg = _config_for_case(case.repo, main_checkout, test_command)
        if not copied_skills:
            codex_cfg = cfg.get("codex")
            if isinstance(codex_cfg, dict):
                role_runtime = codex_cfg.get("role_runtime")
                if isinstance(role_runtime, dict):
                    role_runtime["strict_skill_guard"] = False
        scheduler = cfg["scheduler"]  # type: ignore[index]
        if isinstance(scheduler, dict):
            if codex_timeout_seconds is not None:
                scheduler["codex_timeout_seconds"] = codex_timeout_seconds
            if writer_timeout_seconds is not None:
                scheduler["writer_timeout_seconds"] = writer_timeout_seconds
            if evaluator_timeout_seconds is not None:
                scheduler["evaluator_timeout_seconds"] = evaluator_timeout_seconds
        (control_root / ".autobugfix").mkdir(parents=True, exist_ok=True)
        (control_root / ".autobugfix/config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        write_yaml(case_dir / "setup.yaml", {"repo": case.repo, "isolated_remote": str(remote), "main_checkout": str(main_checkout)})

        service = AutobugfixService(control_root, backend=backend)
        prompt = case.agent_prompt or case.problem_statement
        task = service.create_task(case.repo, f"eval {case.case_id}", prompt)
        service.run_task(task.task_id)
        task_dir = service.store.find_task_dir(task.task_id)
        generated = (task_dir / "artifacts/diff.patch").read_text(encoding="utf-8")
        oracle = run_git(case.worktree_path, ["diff", "--binary", case.base_commit, case.final_commit], check=True).stdout
        write_text(case_dir / "generated.diff", generated)
        write_text(case_dir / "oracle.diff", oracle)
        score = score_case(case_dir)
        write_yaml(
            case_dir / "report.yaml",
            {
                "case_id": case.case_id,
                "task_id": task.task_id,
                "decision": score.decision,
                "generated_equals_oracle": score.generated_equals_oracle,
                "generated_non_empty": score.generated_non_empty,
                "execution_state": service.store.load(task.task_id).state,
            },
        )
        if score.decision != "pass":
            failures.append(case.case_id)
    write_yaml(run_dir / "summary.yaml", {"run_id": run_id, "case_count": len(cases), "failures": failures})
    diagnose_run(run_dir)
    return run_dir


def score_path(path: Path) -> Path:
    if (path / "generated.diff").exists():
        score = score_case(path)
        write_yaml(path / "score.yaml", {"decision": score.decision, "generated_equals_oracle": score.generated_equals_oracle})
        return path / "score.yaml"
    for case_dir in path.iterdir():
        if case_dir.is_dir() and (case_dir / "generated.diff").exists():
            score_path(case_dir)
    return path
