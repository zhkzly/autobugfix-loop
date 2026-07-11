from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.config import default_config_dict, load_config
from autobugfix.eval.adapters import get_adapter
from autobugfix.eval.artifacts import copy_role_skills, read_jsonl, write_text, write_yaml
from autobugfix.eval.diagnosis import diagnose_run
from autobugfix.eval.models import EvalCase, EvalObservation, OracleResult
from autobugfix.eval.scorers import normalize_diff, score_case, score_observation
from autobugfix.models import AutobugfixConfig, RoleConfig, utc_now
from autobugfix.role_config import resolve_role
from autobugfix.service import AutobugfixService


class EvalRunnerError(RuntimeError):
    pass


HUMAN_GATE_STATES = {
    "waiting_human_review",
    "waiting_human_ppe_approval",
}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _role_to_raw(role: RoleConfig) -> dict[str, object]:
    raw = {key: value for key, value in asdict(role).items() if value is not None}
    if isinstance(raw.get("skill_paths"), tuple):
        raw["skill_paths"] = list(raw["skill_paths"])  # type: ignore[index]
    return raw


def _apply_role_overrides(cfg: dict[str, object], source_config: AutobugfixConfig) -> None:
    codex = cfg.setdefault("codex", {})
    if not isinstance(codex, dict):
        return
    codex["default_model"] = source_config.codex.default_model
    codex["default_timeout_seconds"] = source_config.codex.default_timeout_seconds
    runtime = source_config.codex.role_runtime
    codex["role_runtime"] = {
        "enabled": runtime.enabled,
        "runtime_root": str(runtime.runtime_root),
        "codex_bin": str(runtime.codex_bin) if runtime.codex_bin is not None else None,
        "bridge_auth": runtime.bridge_auth,
        "skill_guard": runtime.skill_guard,
        "strict_skill_guard": runtime.strict_skill_guard,
    }
    roles = codex.setdefault("roles", {})
    if not isinstance(roles, dict):
        return
    for role_name, role_config in source_config.codex.roles.items():
        current = roles.get(role_name)
        roles[role_name] = _deep_merge(
            current if isinstance(current, dict) else {}, _role_to_raw(role_config)
        )
    for role_name, role_config in source_config.eval.roles.items():
        current = roles.get(role_name)
        roles[role_name] = _deep_merge(
            current if isinstance(current, dict) else {}, _role_to_raw(role_config)
        )


def _config_for_case(
    repo_id: str,
    main_checkout: Path,
    test_command: str,
    source_config: AutobugfixConfig,
) -> dict[str, object]:
    cfg = default_config_dict()
    _apply_role_overrides(cfg, source_config)
    cfg["repos"] = {
        repo_id: {
            "main_checkout": str(main_checkout),
            "remote": "origin",
            "main_branch": "main",
            "branch_template": "fix/{date}_oncall_{slug}",
            "test_commands": {"targeted": test_command, "full": test_command},
            "ppe": {"enabled": False, "command_template": None},
        }
    }
    return cfg


def _last_execution_verifier_result(service: AutobugfixService, task_id: str) -> bool | None:
    results = [
        event.payload.get("passed")
        for event in service.store.events(task_id)
        if event.kind == "verifier_completed"
    ]
    return bool(results[-1]) if results else None


def _attachment_context(case: EvalCase) -> list[tuple[str, str]]:
    return [
        (
            f"attachment-{attachment.kind}",
            "\n".join(
                [
                    f"kind: {attachment.kind}",
                    f"uri: {attachment.uri}",
                    f"description: {attachment.description}",
                    f"media_type: {attachment.media_type or ''}",
                    f"sha256: {attachment.sha256 or ''}",
                ]
            ),
        )
        for attachment in case.task.attachments
    ]


def _write_case_report(
    case_dir: Path,
    case: EvalCase,
    observation: EvalObservation,
    *,
    task_id: str | None,
) -> dict[str, Any]:
    write_yaml(case_dir / "observation.yaml", observation.to_dict())
    score = score_observation(observation)
    report = {
        "case_id": case.case_id,
        "task_id": task_id,
        "decision": score.decision,
        "failure_stage": score.failure_stage,
        "task_type": case.task.task_type,
        "adapter": case.source.adapter,
        "benchmark": case.source.benchmark,
        "generated_equals_oracle": score.generated_equals_oracle,
        "generated_non_empty": score.generated_non_empty,
        "execution_verifier_passed": score.execution_verifier_passed,
        "execution_reached_human_gate": score.execution_reached_human_gate,
        "execution_state": observation.execution_state,
        "oracle_passed": score.oracle_passed,
        "oracle_status": observation.oracle_status,
        "harness_error": observation.harness_error,
    }
    write_yaml(case_dir / "report.yaml", report)
    return report


def _run_case(
    project_root: Path,
    source_config: AutobugfixConfig,
    case: EvalCase,
    case_dir: Path,
    *,
    test_command_override: str | None,
    codex_timeout_seconds: int | None,
    writer_timeout_seconds: int | None,
    evaluator_timeout_seconds: int | None,
    backend: CodexBackend | None,
) -> dict[str, Any]:
    write_yaml(case_dir / "case.yaml", case.to_dict())
    task_id: str | None = None
    generated = ""
    execution_state = "not_started"
    execution_verifier_passed: bool | None = None
    oracle_diff: str | None = None
    oracle_result: OracleResult | None = None
    try:
        adapter = get_adapter(case.source.adapter)
        execution_command = case.resolved_test_command(test_command_override)
        if not execution_command:
            raise EvalRunnerError("Eval case requires a real Execution verifier command")
        materialized = adapter.materialize(case, case_dir / "setup")
        control_root = case_dir / "control"
        copied_skills = copy_role_skills(project_root, control_root)
        cfg = _config_for_case(
            case.repo,
            materialized.main_checkout,
            execution_command,
            source_config,
        )
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
        (control_root / ".autobugfix/config.yaml").write_text(
            yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
        )
        isolated_config = load_config(control_root)
        write_yaml(
            case_dir / "resolved-roles.yaml",
            {
                role: resolve_role(isolated_config, role, repo_id=case.repo).to_dict(control_root)
                for role in ("writer", "evaluator")
            },
        )
        write_yaml(
            case_dir / "setup.yaml",
            {
                "repo": case.repo,
                "adapter": case.source.adapter,
                "isolated_remote": str(materialized.remote),
                "main_checkout": str(materialized.main_checkout),
                "base_commit": case.base_commit,
            },
        )

        service = AutobugfixService(control_root, backend=backend)
        task = service.create_task(
            case.repo,
            f"eval {case.case_id}",
            case.agent_prompt or case.problem_statement,
        )
        task_id = task.task_id
        for kind, content in _attachment_context(case):
            service.add_context(task_id, kind, content)
        service.run_task(task_id)
        current = service.store.load(task_id)
        execution_state = current.state
        execution_verifier_passed = _last_execution_verifier_result(service, task_id)
        task_dir = service.store.find_task_dir(task_id)
        generated_path = task_dir / "artifacts/diff.patch"
        generated = generated_path.read_text(encoding="utf-8") if generated_path.exists() else ""
        write_text(case_dir / "generated.diff", generated)

        try:
            oracle_diff = adapter.oracle_diff(case)
        except Exception as exc:
            write_text(case_dir / "oracle-diff-error.txt", str(exc) + "\n")
        if oracle_diff is not None:
            write_text(case_dir / "oracle.diff", oracle_diff)

        if not current.worktree_path:
            raise EvalRunnerError("Execution task has no generated worktree for oracle verification")
        oracle_result = adapter.verify(
            case,
            Path(current.worktree_path),
            case_dir / "oracle",
            command_override=test_command_override,
        )
        write_yaml(case_dir / "oracle-result.yaml", oracle_result.to_dict())
        equals = (
            normalize_diff(generated) == normalize_diff(oracle_diff)
            if oracle_diff is not None
            else None
        )
        observation = EvalObservation(
            case_id=case.case_id,
            patch_required=case.oracle.require_patch,
            generated_non_empty=bool(generated.strip()),
            execution_verifier_passed=execution_verifier_passed,
            execution_state=execution_state,
            execution_reached_human_gate=execution_state in HUMAN_GATE_STATES,
            oracle_status=oracle_result.status,
            oracle_exit_code=oracle_result.exit_code,
            generated_equals_oracle=equals,
            harness_error=oracle_result.error if oracle_result.status == "error" else "",
        )
        return _write_case_report(case_dir, case, observation, task_id=task_id)
    except Exception as exc:
        write_text(case_dir / "harness-error.txt", f"{type(exc).__name__}: {exc}\n")
        if not (case_dir / "generated.diff").exists():
            write_text(case_dir / "generated.diff", generated)
        if oracle_result is not None and not (case_dir / "oracle-result.yaml").exists():
            write_yaml(case_dir / "oracle-result.yaml", oracle_result.to_dict())
        observation = EvalObservation(
            case_id=case.case_id,
            patch_required=case.oracle.require_patch,
            generated_non_empty=bool(generated.strip()),
            execution_verifier_passed=execution_verifier_passed,
            execution_state=execution_state,
            execution_reached_human_gate=execution_state in HUMAN_GATE_STATES,
            oracle_status="error",
            oracle_exit_code=oracle_result.exit_code if oracle_result else None,
            generated_equals_oracle=(
                normalize_diff(generated) == normalize_diff(oracle_diff)
                if oracle_diff is not None
                else None
            ),
            harness_error=f"{type(exc).__name__}: {exc}",
        )
        return _write_case_report(case_dir, case, observation, task_id=task_id)


def _write_schema_error(run_dir: Path, index: int, exc: Exception) -> dict[str, Any]:
    case_id = f"schema-error-{index:04d}"
    case_dir = run_dir / case_id
    message = f"{type(exc).__name__}: {exc}"
    write_text(case_dir / "harness-error.txt", message + "\n")
    report = {
        "case_id": case_id,
        "task_id": None,
        "decision": "error",
        "failure_stage": "case_schema",
        "task_type": "unknown",
        "adapter": None,
        "benchmark": None,
        "generated_equals_oracle": None,
        "generated_non_empty": False,
        "execution_verifier_passed": None,
        "execution_reached_human_gate": False,
        "execution_state": "not_started",
        "oracle_passed": False,
        "oracle_status": "error",
        "harness_error": message,
        "dataset_row": index,
    }
    write_yaml(case_dir / "report.yaml", report)
    return report


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
    source_config = load_config(project_root)
    requested_mode = model_mode or source_config.eval.model_mode
    if backend is None and requested_mode != "codex":
        raise EvalRunnerError("production Eval supports only the real Codex backend")
    observed_mode = "injected-test-backend" if backend is not None else "codex"
    run_dir = out / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise EvalRunnerError(f"Eval run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    reports: list[dict[str, Any]] = []
    try:
        rows = read_jsonl(dataset)
    except Exception as exc:
        rows = []
        reports.append(_write_schema_error(run_dir, 0, exc))
    cases: list[EvalCase] = []
    seen_case_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        try:
            case = EvalCase.from_row(row)
            if case.case_id in seen_case_ids:
                raise EvalRunnerError(f"duplicate Eval case_id: {case.case_id}")
            seen_case_ids.add(case.case_id)
            cases.append(case)
        except Exception as exc:
            reports.append(_write_schema_error(run_dir, index, exc))
    if case_selector:
        cases = [
            case
            for case in cases
            if case.case_id == case_selector
            or case.source.instance_id == case_selector
            or (case.raw and case.raw.get("case") == case_selector)
        ]
    if not cases and not reports:
        raise EvalRunnerError("no eval cases selected")
    write_yaml(
        run_dir / "resolved-config.yaml",
        {
            "dataset": str(dataset),
            "case_selector": case_selector,
            "model_mode": observed_mode,
            "test_command_override": test_command,
            "codex_timeout_seconds": codex_timeout_seconds,
            "writer_timeout_seconds": writer_timeout_seconds,
            "evaluator_timeout_seconds": evaluator_timeout_seconds,
            "roles": {
                role: resolve_role(source_config, role).to_dict(source_config.project_root)
                for role in source_config.codex.roles
            },
        },
    )
    reports.extend(
        _run_case(
            project_root,
            source_config,
            case,
            run_dir / case.case_id,
            test_command_override=test_command,
            codex_timeout_seconds=codex_timeout_seconds,
            writer_timeout_seconds=writer_timeout_seconds,
            evaluator_timeout_seconds=evaluator_timeout_seconds,
            backend=backend,
        )
        for case in cases
    )
    failures = [str(item["case_id"]) for item in reports if item["decision"] == "fail"]
    harness_errors = [str(item["case_id"]) for item in reports if item["decision"] == "error"]
    passed = sum(1 for item in reports if item["decision"] == "pass")
    required_artifacts = (
        "case.yaml",
        "observation.yaml",
        "report.yaml",
        "generated.diff",
        "oracle-result.yaml",
        "oracle/stdout.log",
        "oracle/stderr.log",
    )
    report_case_ids = [str(item["case_id"]) for item in reports]
    present = sum(
        1
        for case_id in report_case_ids
        for name in required_artifacts
        if (run_dir / case_id / name).is_file()
    )
    case_count = len(reports)
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": utc_now(),
        "runtime_seconds": time.monotonic() - started_monotonic,
        "case_count": case_count,
        "passed_count": passed,
        "failed_count": len(failures),
        "harness_error_count": len(harness_errors),
        "pass_rate": passed / case_count,
        "artifact_completeness": present / (case_count * len(required_artifacts)),
        "failures": failures,
        "harness_errors": harness_errors,
    }
    write_yaml(run_dir / "summary.yaml", summary)
    diagnose_run(run_dir)
    return run_dir


def score_path(path: Path) -> Path:
    if (path / "observation.yaml").exists():
        score = score_case(path)
        write_yaml(
            path / "score.yaml",
            {
                "decision": score.decision,
                "failure_stage": score.failure_stage,
                "generated_equals_oracle": score.generated_equals_oracle,
                "generated_non_empty": score.generated_non_empty,
                "execution_verifier_passed": score.execution_verifier_passed,
                "execution_reached_human_gate": score.execution_reached_human_gate,
                "oracle_passed": score.oracle_passed,
            },
        )
        return path / "score.yaml"
    for case_dir in path.iterdir():
        if case_dir.is_dir() and (case_dir / "observation.yaml").exists():
            score_path(case_dir)
    return path
