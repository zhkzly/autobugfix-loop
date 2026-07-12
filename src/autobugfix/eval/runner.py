from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.codex_backend import CodexBackend
from autobugfix.config import default_config_dict, load_config
from autobugfix.eval.adapters import get_adapter
from autobugfix.eval.artifacts import copy_role_skills, read_jsonl, write_text, write_yaml
from autobugfix.eval.benchmarks.models import digest_file, record_with_digest
from autobugfix.eval.diagnosis import diagnose_run
from autobugfix.eval.models import (
    EvalCase,
    EvalObservation,
    FrozenSubmission,
    OracleResult,
)
from autobugfix.eval.scorers import normalize_diff, score_case, score_observation
from autobugfix.models import AutobugfixConfig, RoleConfig, utc_now
from autobugfix.role_config import resolve_role
from autobugfix.service import AutobugfixService
from autobugfix.verifier import ExecutionVerifierBackend
from autobugfix.git_utils import rev_parse
from autobugfix.worktree import diff_for_task


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


def _subject_sha(project_root: Path) -> str:
    try:
        return rev_parse(project_root, "HEAD")
    except Exception:
        return "unavailable"


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
    codex["reasoning_effort"] = source_config.codex.reasoning_effort
    codex["service_tier"] = source_config.codex.service_tier
    codex["disable_response_storage"] = source_config.codex.disable_response_storage
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


def _apply_model_override(cfg: dict[str, object], model: str | None) -> None:
    if model is None:
        return
    codex = cfg.get("codex")
    if not isinstance(codex, dict):
        return
    roles = codex.get("roles")
    if not isinstance(roles, dict):
        return
    for role_name in ("writer", "evaluator"):
        role = roles.get(role_name)
        if isinstance(role, dict):
            role["model"] = model


def _config_for_case(
    repo_id: str,
    main_checkout: Path,
    test_command: str,
    source_config: AutobugfixConfig,
    model: str | None,
) -> dict[str, object]:
    cfg = default_config_dict()
    _apply_role_overrides(cfg, source_config)
    _apply_model_override(cfg, model)
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
    model: str | None,
    max_attempts: int,
    backend: CodexBackend | None,
    verifier_backend: ExecutionVerifierBackend | None,
    official_evaluator: object | None,
    sdk_hidden_paths: tuple[Path, ...],
) -> dict[str, Any]:
    write_yaml(case_dir / "case.yaml", case.to_dict())
    task_id: str | None = None
    generated = ""
    execution_state = "not_started"
    execution_verifier_passed: bool | None = None
    oracle_diff: str | None = None
    oracle_result: OracleResult | None = None
    service: AutobugfixService | None = None
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
            model,
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

        service = AutobugfixService(
            control_root,
            backend=backend,
            verifier_backend=verifier_backend,
            sdk_hidden_paths=sdk_hidden_paths,
        )
        task = service.create_task(
            case.repo,
            f"eval {case.case_id}",
            case.agent_prompt or case.problem_statement,
            metadata={
                "origin": "eval",
                "memory_eligible": False,
                "eval_case_id": case.case_id,
                "eval_adapter": case.source.adapter,
                "experiment_role": (
                    case.experiment.role if case.experiment is not None else "unassigned"
                ),
            },
        )
        task_id = task.task_id
        for kind, content in _attachment_context(case):
            service.add_context(task_id, kind, content)
        while True:
            service.run_task(task_id)
            current = service.store.load(task_id)
            if current.state != "writer_rework_required":
                break
            if current.iterations >= max_attempts:
                break
            task_dir = service.store.find_task_dir(task_id)
            test_result = task_dir / "artifacts/test-result.md"
            feedback = [
                f"Attempt {current.iterations} did not satisfy the repair contract.",
                f"State reason: {current.block_reason}",
            ]
            if test_result.is_file():
                feedback.extend(("", test_result.read_text(encoding="utf-8")))
            service.add_feedback(
                task_id,
                "retry",
                "\n".join(feedback),
            )
        current = service.store.load(task_id)
        execution_state = current.state
        execution_verifier_passed = _last_execution_verifier_result(service, task_id)
        task_dir = service.store.find_task_dir(task_id)
        generated_path = task_dir / "artifacts/diff.patch"
        generated = generated_path.read_text(encoding="utf-8") if generated_path.exists() else ""
        write_text(case_dir / "generated.diff", generated)

        if not current.worktree_path:
            raise EvalRunnerError("Execution task has no generated worktree to freeze")
        execution_worktree = Path(current.worktree_path)
        execution_repo = isolated_config.repo(case.repo)
        base_ref = str(
            current.metadata.get("base_commit")
            or f"{execution_repo.remote}/{execution_repo.main_branch}"
        )
        live_patch = diff_for_task(execution_repo, execution_worktree, base_ref)
        if live_patch != generated:
            raise EvalRunnerError(
                "Execution diff artifact differs from the live task worktree"
            )

        events_path = task_dir / "events.jsonl"
        task_path = task_dir / "task.yaml"
        frozen_patch_path = case_dir / "generated.diff"
        submission = record_with_digest(
            {
                "schema": "autobugfix-eval-submission-v1",
                "case_id": case.case_id,
                "task_id": task_id,
                "subject_sha": _subject_sha(project_root),
                "execution_state": execution_state,
                "iterations": current.iterations,
                "patch_path": str(frozen_patch_path),
                "patch_sha256": digest_file(frozen_patch_path),
                "worktree_patch_sha256": hashlib.sha256(
                    live_patch.encode("utf-8")
                ).hexdigest(),
                "events_sha256": digest_file(events_path),
                "task_sha256": digest_file(task_path),
                "frozen_at": utc_now(),
            }
        )
        write_yaml(case_dir / "submission.yaml", submission)
        frozen_submission = FrozenSubmission(
            case_id=case.case_id,
            patch=generated,
            patch_sha256=str(submission["patch_sha256"]),
            record_digest=str(submission["record_digest"]),
        )

        try:
            oracle_diff = adapter.oracle_diff(case)
        except Exception as exc:
            write_text(case_dir / "oracle-diff-error.txt", str(exc) + "\n")
        if oracle_diff is not None:
            write_text(case_dir / "oracle.diff", oracle_diff)

        oracle_result = adapter.score_submission(
            case,
            materialized,
            frozen_submission,
            case_dir / "oracle",
            command_override=test_command_override,
            official_evaluator=official_evaluator,
        )
        post_oracle_live_patch = diff_for_task(
            execution_repo,
            execution_worktree,
            base_ref,
        )
        post_oracle = {
            "patch_sha256": digest_file(frozen_patch_path),
            "worktree_patch_sha256": hashlib.sha256(
                post_oracle_live_patch.encode("utf-8")
            ).hexdigest(),
            "events_sha256": digest_file(events_path),
            "task_sha256": digest_file(task_path),
            "iterations": service.store.load(task_id).iterations,
        }
        expected_post_oracle = {
            "patch_sha256": submission["patch_sha256"],
            "worktree_patch_sha256": submission["worktree_patch_sha256"],
            "events_sha256": submission["events_sha256"],
            "task_sha256": submission["task_sha256"],
            "iterations": submission["iterations"],
        }
        unchanged = post_oracle == expected_post_oracle
        write_yaml(
            case_dir / "oracle-noninterference.yaml",
            record_with_digest(
                {
                    "schema": "autobugfix-oracle-noninterference-v1",
                    "case_id": case.case_id,
                    "submission_digest": submission["record_digest"],
                    "unchanged": unchanged,
                    "expected": expected_post_oracle,
                    "observed": post_oracle,
                    "checked_at": utc_now(),
                }
            ),
        )
        if not unchanged:
            raise EvalRunnerError(
                "official evaluator changed the frozen Execution submission"
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
        if task_id is not None and service is not None:
            try:
                current = service.store.load(task_id)
                execution_state = current.state
                execution_verifier_passed = _last_execution_verifier_result(
                    service,
                    task_id,
                )
                task_dir = service.store.find_task_dir(task_id)
                generated_path = task_dir / "artifacts/diff.patch"
                if generated_path.is_file():
                    generated = generated_path.read_text(encoding="utf-8")
            except Exception as state_exc:
                write_text(
                    case_dir / "execution-state-error.txt",
                    f"{type(state_exc).__name__}: {state_exc}\n",
                )
        write_text(case_dir / "harness-error.txt", f"{type(exc).__name__}: {exc}\n")
        if not (case_dir / "generated.diff").exists():
            write_text(case_dir / "generated.diff", generated)
        if oracle_result is not None and not (case_dir / "oracle-result.yaml").exists():
            write_yaml(case_dir / "oracle-result.yaml", oracle_result.to_dict())
        if oracle_result is None:
            oracle_dir = case_dir / "oracle"
            write_text(oracle_dir / "stdout.log", "")
            write_text(
                oracle_dir / "stderr.log",
                f"Oracle not run because the harness failed: {type(exc).__name__}: {exc}\n",
            )
            oracle_result = OracleResult(
                status="error",
                oracle_type=case.oracle.oracle_type,
                command=None,
                exit_code=None,
                stdout_path=str(oracle_dir / "stdout.log"),
                stderr_path=str(oracle_dir / "stderr.log"),
                started_at=utc_now(),
                finished_at=utc_now(),
                error=f"{type(exc).__name__}: {exc}",
            )
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
    model: str | None = None,
    max_attempts: int = 1,
    backend: CodexBackend | None = None,
    verifier_backends: Mapping[str, ExecutionVerifierBackend] | None = None,
    official_evaluators: Mapping[str, object] | None = None,
    sdk_hidden_paths: tuple[Path, ...] = (),
) -> Path:
    if max_attempts < 1:
        raise EvalRunnerError("max_attempts must be positive")
    if run_id in {"", ".", ".."} or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
        raise EvalRunnerError("run_id must be a safe path component")
    source_config = load_config(project_root)
    requested_mode = model_mode or source_config.eval.model_mode
    if backend is None and requested_mode != "codex":
        raise EvalRunnerError("production Eval supports only the real Codex backend")
    observed_mode = "injected-test-backend" if backend is not None else "codex"
    output_root = out.resolve()
    run_dir = (output_root / run_id).resolve()
    if not run_dir.is_relative_to(output_root):
        raise EvalRunnerError("Eval run directory escapes configured output root")
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
    resolved_verifiers = dict(verifier_backends or {})
    resolved_evaluators = dict(official_evaluators or {})
    for case in cases:
        if case.source.adapter != "defects4j" or (
            case.case_id in resolved_verifiers
            and case.case_id in resolved_evaluators
        ):
            continue
        if case.benchmark is None:
            raise EvalRunnerError(
                f"Defects4J case {case.case_id} has no benchmark receipt binding"
            )
        from autobugfix.eval.benchmarks.store import BenchmarkStore
        from autobugfix.eval.benchmarks.verify import (
            managed_verifier_for_receipt,
            official_oracle_for_receipt,
        )

        benchmark_config = source_config.eval.benchmarks
        benchmark_store = BenchmarkStore(
            benchmark_config.trusted_case_root,
            benchmark_config.visible_manifest_root,
            benchmark_config.cache_root,
        )
        try:
            receipt = benchmark_store.read_receipt(
                benchmark_store.receipt_path(
                    case.case_id,
                    case.benchmark.eligibility_receipt_digest,
                )
            )
        except Exception as exc:
            raise EvalRunnerError(
                f"Defects4J case {case.case_id} has no valid trusted receipt: {exc}"
            ) from exc
        managed = managed_verifier_for_receipt(receipt, benchmark_config)
        if (
            receipt.role not in {"evaluation", "optimization"}
            or case.execution.test_command != managed.command_id
            or case.environment.image != receipt.verifier_runtime_id
            or case.repository.base_commit != receipt.sanitized_base_sha
        ):
            raise EvalRunnerError(
                f"Defects4J case {case.case_id} disagrees with its trusted receipt"
            )
        resolved_verifiers[case.case_id] = managed
        resolved_evaluators[case.case_id] = official_oracle_for_receipt(
            receipt,
            benchmark_config,
        )
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
            "model": model,
            "max_attempts": max_attempts,
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
            model=model,
            max_attempts=max_attempts,
            backend=backend,
            verifier_backend=resolved_verifiers.get(case.case_id),
            official_evaluator=resolved_evaluators.get(case.case_id),
            sdk_hidden_paths=sdk_hidden_paths,
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
        "submission.yaml",
        "oracle-noninterference.yaml",
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
