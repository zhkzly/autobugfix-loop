from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import sys
import uuid
from pathlib import Path

import yaml

from autobugfix.codex_runtime import build_codex_request
from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.config import load_config, write_default_config
from autobugfix.dataset import build_raw_dataset
from autobugfix.eval.baselines.raw_codex import RawCodexBaselineService
from autobugfix.eval.baselines.swe_raw_codex import SWERawCodexBaselineService
from autobugfix.eval.benchmarks.exp2_coordinator import (
    Exp2Coordinator,
    Exp2CoordinatorError,
)
from autobugfix.eval.benchmarks.exp2_resume import (
    EXP2_WRITER_SKILL_PATH,
    Exp2AttributionHypothesis,
    Exp2ResumeCoordinator,
    Exp2ResumeError,
    Exp2ResumeProtocol,
    Exp2ResumeStudyPlan,
)
from autobugfix.eval.benchmarks.exp2_runtime import (
    Exp2EvalAuthority,
    build_exp2_apparatus_receipt,
    build_exp2_resume_protocol,
    build_exp2_study_plan,
    run_exp2_source_check,
)
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.eval.diagnosis import diagnose_run
from autobugfix.eval.improvements import (
    list_improvements,
    show_improvement,
    update_improvement,
)
from autobugfix.eval.runner import run_eval, score_path
from autobugfix.eval.supervision import supervision_note
from autobugfix.eval.swe_holdout_guard import SWEHoldoutGuardService
from autobugfix.gradio_app import launch as launch_ui
from autobugfix.memory.service import MemoryService
from autobugfix.memory_gradio_app import launch as launch_memory_ui
from autobugfix.memory_worker import start_worker as start_memory_worker
from autobugfix.memory_worker import stop_worker as stop_memory_worker
from autobugfix.memory_worker import worker_status as memory_worker_status
from autobugfix.operator.metrics import read_baseline
from autobugfix.operator.models import (
    VALID_APPROVAL_DECISIONS,
    VALID_CONFIDENCE,
    VALID_LAYERS,
    VALID_RISKS,
)
from autobugfix.operator.service import OperatorGovernanceService
from autobugfix.projection import inspect_projection, render_inspect, status_projection
from autobugfix.role_config import resolve_role
from autobugfix.scheduler import tick
from autobugfix.service import AutobugfixService
from autobugfix.worker import start_worker, stop_worker, worker_status


def _stdin_or_file(args: argparse.Namespace) -> str:
    if getattr(args, "from_stdin", False):
        return sys.stdin.read()
    file_value = getattr(args, "file", None)
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    return ""


def _print_yaml(data: object) -> None:
    print(yaml.safe_dump(data, sort_keys=False).strip())


def _parse_values(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"value must use key=value: {item}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise ValueError("value key must not be empty")
        parsed[key.strip()] = value
    return parsed


def _read_yaml_mapping(path: Path | str, field: str) -> dict[str, object]:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{field} must contain a YAML mapping: {source}")
    return data


def _installed_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def command_doctor(args: argparse.Namespace) -> int:
    if args.init_config:
        write_default_config(Path.cwd())
    cfg = load_config(Path.cwd())
    print("Autobugfix doctor")
    print(f"project_root: {cfg.project_root}")
    print(f"task_root: {cfg.project_root / cfg.task_root}")
    print(f"codex_runtime_root: {cfg.codex.role_runtime.runtime_root}")
    print(f"codex_binary: {cfg.codex.role_runtime.codex_bin or 'sdk-bundled'}")
    print(f"codex_python_sdk_version: {_installed_version('openai-codex')}")
    print(f"codex_bundled_cli_version: {_installed_version('openai-codex-cli-bin')}")
    print("roles:")
    for role in sorted(cfg.codex.roles):
        resolved = resolve_role(cfg, role)
        print(f"  {role}:")
        print(f"    backend: {resolved.backend}")
        print(f"    model: {resolved.model if resolved.model is not None else 'runtime-default'}")
        print(f"    sandbox: {resolved.sandbox}")
        print(f"    approval_mode: {resolved.approval_mode}")
        print(f"    timeout_seconds: {resolved.timeout_seconds}")
        print("    skill_paths:")
        for path in resolved.to_dict(cfg.project_root)["skill_paths"]:
            print(f"      - {path}")
    if not cfg.repos:
        print("repos: (none configured)")
    for repo_id, repo in cfg.repos.items():
        print(f"repo {repo_id}:")
        print(f"  main_checkout: {repo.main_checkout}")
        print(f"  worktree_root: {repo.worktree_root}")
        print(f"  remote: {repo.remote}")
        print(f"  main_branch: {repo.main_branch}")
        print(f"  test_full: {repo.test_commands.full}")
        for role in sorted(cfg.codex.roles):
            resolved = resolve_role(cfg, role, repo_id=repo_id)
            if resolved.source.get("repo_override") != "none":
                print(f"  role_override {role}:")
                print(f"    model: {resolved.model}")
                print(f"    sandbox: {resolved.sandbox}")
                print(f"    timeout_seconds: {resolved.timeout_seconds}")
    return 0


def command_create(args: argparse.Namespace) -> int:
    body = _stdin_or_file(args)
    record = AutobugfixService(Path.cwd()).create_task(args.repo, args.title, body)
    print(record.task_id)
    return 0


def command_context_add(args: argparse.Namespace) -> int:
    content = _stdin_or_file(args)
    path = AutobugfixService(Path.cwd()).add_context(args.task_id, args.kind, content)
    print(path)
    return 0


def command_run(args: argparse.Namespace) -> int:
    record = AutobugfixService(Path.cwd()).run_task(args.task_id)
    _print_yaml(record.to_dict())
    return 0


def command_feedback(args: argparse.Namespace) -> int:
    content = _stdin_or_file(args)
    record = AutobugfixService(Path.cwd()).add_feedback(args.task_id, args.decision, content, args.queue_only)
    _print_yaml(record.to_dict())
    return 0


def command_gate(args: argparse.Namespace) -> int:
    record = AutobugfixService(Path.cwd()).apply_gate(args.task_id, args.action)
    _print_yaml(record.to_dict())
    return 0


def command_deploy_ppe(args: argparse.Namespace) -> int:
    record = AutobugfixService(Path.cwd()).deploy_ppe(args.task_id)
    _print_yaml(record.to_dict())
    return 0


def command_archive(args: argparse.Namespace) -> int:
    path = AutobugfixService(Path.cwd()).archive(args.task_id, args.result)
    print(path)
    return 0


def command_status(args: argparse.Namespace) -> int:
    _print_yaml(status_projection(AutobugfixService(Path.cwd()).store))
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    print(render_inspect(inspect_projection(AutobugfixService(Path.cwd()).store, args.task_id)))
    return 0


def command_watch(args: argparse.Namespace) -> int:
    data = inspect_projection(AutobugfixService(Path.cwd()).store, args.task_id)
    print(render_inspect(data))
    return 0


def command_tick(args: argparse.Namespace) -> int:
    ran = tick(AutobugfixService(Path.cwd()), args.max_concurrent)
    _print_yaml({"ran": ran})
    return 0


def command_daemon(args: argparse.Namespace) -> int:
    ran = tick(AutobugfixService(Path.cwd()), 1)
    _print_yaml({"ran": ran, "once": args.once})
    return 0


def command_worker(args: argparse.Namespace) -> int:
    root = Path.cwd()
    if args.worker_action in {"start", "ensure"}:
        print(start_worker(root))
    elif args.worker_action == "status":
        _print_yaml(worker_status(root))
    elif args.worker_action == "stop":
        stop_worker(root)
        print("stopped")
    return 0


def command_ui(args: argparse.Namespace) -> int:
    launch_ui(args.host, args.port, Path.cwd())
    return 0


def command_memory(args: argparse.Namespace) -> int:
    service = MemoryService(Path.cwd())
    action = args.memory_action
    if action == "init":
        service.init()
        print(service.config.root)
    elif action == "collect":
        print(service.collect(args.task_id))
    elif action == "digest":
        print(service.digest(args.task_id))
    elif action == "maintain":
        print(service.maintain(args.task_id))
    elif action == "tick":
        _print_yaml({"processed": service.tick(args.max_tasks)})
    elif action == "status":
        _print_yaml(service.status())
    elif action == "proposals":
        _print_yaml(service.proposals())
    elif action == "review":
        _print_yaml(service.review(args.proposal_id))
    elif action == "show":
        print(service.show(args.proposal_id))
    elif action == "approve":
        print(
            service.approve(
                args.proposal_id,
                args.note,
                args.confirm_review_digest,
            )
        )
    elif action == "approve-skill":
        print(
            service.approve_skill(
                args.proposal_id,
                args.skill_name,
                args.description,
                args.note,
                args.confirm_review_digest,
            )
        )
    elif action == "reject":
        print(service.reject(args.proposal_id, args.reason))
    elif action == "lint":
        errors = service.lint()
        _print_yaml({"ok": not errors, "errors": errors})
        return 1 if errors else 0
    elif action == "search":
        _print_yaml({"matches": service.search(args.query)})
    elif action == "context":
        print(service.context(args.audience))
    return 0


def command_memory_worker(args: argparse.Namespace) -> int:
    root = Path.cwd()
    if args.worker_action in {"start", "ensure"}:
        print(start_memory_worker(root))
    elif args.worker_action == "status":
        _print_yaml(memory_worker_status(root))
    elif args.worker_action == "stop":
        stop_memory_worker(root)
        print("stopped")
    return 0


def command_memory_ui(args: argparse.Namespace) -> int:
    launch_memory_ui(args.host, args.port, Path.cwd())
    return 0


def command_dataset(args: argparse.Namespace) -> int:
    if args.dataset_action == "build-raw":
        print(build_raw_dataset(Path.cwd(), args.repo, Path(args.out), args.base_ref))
    return 0


def command_eval(args: argparse.Namespace) -> int:
    action = args.eval_action
    if action == "exp2":
        config = load_config(Path.cwd())
        default_state_root = (
            config.eval.benchmarks.trusted_case_root / "exp2" / args.study_id
        ).resolve()
        state_root = (
            Path(args.state_root).resolve()
            if getattr(args, "state_root", None)
            else default_state_root
        )
        if state_root != default_state_root or (
            Path(args.state_root).is_symlink()
            if getattr(args, "state_root", None)
            else False
        ):
            raise Exp2ResumeError(
                "Exp2 state root must be the configured trusted Eval study root"
            )
        if args.exp2_action == "build-protocol-v2":
            artifact_root = (
                Path(args.artifact_root).resolve()
                if args.artifact_root
                else (
                    config.eval.benchmarks.trusted_case_root
                    / "exp2-protocol-builds"
                    / args.study_id
                ).resolve()
            )
            protocol = build_exp2_resume_protocol(
                Path.cwd(),
                protocol_id=args.protocol_id,
                swe_protocol_path=Path(args.swe_protocol),
                empty_memory_fixture_path=Path(args.empty_memory_fixture),
                execution_allowlist=tuple(args.execution_allowlist),
                artifact_root=artifact_root,
                evaluation_mode=args.evaluation_mode,
            )
            output = Path(args.out).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise Exp2ResumeError(
                    "Exp2 v2 protocol output already exists"
                )
            output.write_text(
                yaml.safe_dump(protocol.to_dict(), sort_keys=False),
                encoding="utf-8",
            )
            _print_yaml(
                {
                    "protocol_path": str(output),
                    "protocol_digest": protocol.record_digest,
                    "qualification_status": protocol.qualification_status,
                    "image_count": len(protocol.oci_images),
                }
            )
            return 0
        if args.exp2_action == "build-plan-v2":
            memory_root = Path(args.memory_root).expanduser()
            output = Path(args.out).resolve()
            resolved_memory_root = memory_root.resolve()
            if output == resolved_memory_root or output.is_relative_to(
                resolved_memory_root
            ):
                raise Exp2ResumeError(
                    "Exp2 plan output must not mutate the empty Memory root"
                )
            plan = build_exp2_study_plan(
                Path.cwd(),
                study_id=args.study_id,
                study_kind=args.study_kind,
                protocol_path=Path(args.protocol_v2),
                swe_protocol_path=Path(args.swe_protocol),
                apparatus_receipt_path=Path(args.apparatus_receipt),
                memory_fixture_spec_path=Path(args.empty_memory_fixture),
                memory_root=memory_root,
                disposable_root=Path(args.disposable_root),
                guard_root=Path(args.guard_root),
                public_manifest_path=(
                    Path(args.public_manifest)
                    if args.public_manifest
                    else None
                ),
                h0_binding_path=(
                    Path(args.h0_binding) if args.h0_binding else None
                ),
                calibration_terminal_receipt_path=(
                    Path(args.calibration_terminal_receipt)
                    if args.calibration_terminal_receipt
                    else None
                ),
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise Exp2ResumeError("Exp2 v2 plan output already exists")
            output.write_text(
                yaml.safe_dump(plan.to_dict(), sort_keys=False),
                encoding="utf-8",
            )
            _print_yaml(
                {
                    "plan_path": str(output),
                    "plan_digest": plan.record_digest,
                    "study_kind": plan.study_kind,
                }
            )
            return 0
        if args.exp2_action == "build-apparatus-receipt-v2":
            receipt = build_exp2_apparatus_receipt(
                Path.cwd(),
                protocol_path=Path(args.protocol_v2),
                swe_protocol_path=Path(args.swe_protocol),
                check_artifacts=tuple(Path(item) for item in args.check_artifact),
            )
            output = Path(args.out).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise Exp2ResumeError(
                    "Exp2 apparatus receipt output already exists"
                )
            output.write_text(
                yaml.safe_dump(receipt, sort_keys=False),
                encoding="utf-8",
            )
            _print_yaml(
                {
                    "apparatus_receipt_path": str(output),
                    "apparatus_receipt_digest": receipt["record_digest"],
                    "apparatus_sha": receipt["apparatus_sha"],
                }
            )
            return 0
        if args.exp2_action == "export-h0-binding":
            exported = OperatorGovernanceService(
                Path.cwd()
            ).export_exp2_h0_binding(args.operator_study_id)
            _print_yaml(exported)
            return 0
        if args.exp2_action == "source-check-v2":
            receipt = run_exp2_source_check(
                Path.cwd(),
                name=args.name,
                argv=tuple(args.command),
                artifact_root=Path(args.artifact_root),
                timeout_seconds=args.timeout_seconds,
            )
            output = Path(args.out).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise Exp2ResumeError(
                    "Exp2 source-check receipt output already exists"
                )
            output.write_text(
                yaml.safe_dump(receipt, sort_keys=False),
                encoding="utf-8",
            )
            _print_yaml(
                {
                    "receipt_path": str(output),
                    "receipt_digest": receipt["record_digest"],
                    "passed": receipt["passed"],
                }
            )
            return 0 if receipt["passed"] else 1
        if args.exp2_action == "prepare-manifest-v2":
            _print_yaml(
                EvalBenchmarkService(Path.cwd()).prepare_exp2_resume_manifest(
                    Path(args.swe_protocol)
                )
            )
            return 0
        if args.exp2_action == "init":
            if not args.study_kind or not args.plan_v2 or not args.protocol_v2:
                raise Exp2ResumeError(
                    "new Exp2 studies require --study-kind, --plan-v2, and --protocol-v2; v1 initialization is audit-only"
                )
            plan = Exp2ResumeStudyPlan.from_dict(
                _read_yaml_mapping(args.plan_v2, "Exp2 v2 study plan")
            )
            protocol = Exp2ResumeProtocol.from_dict(
                _read_yaml_mapping(args.protocol_v2, "Exp2 v2 protocol")
            )
            if plan.study_kind != args.study_kind:
                raise Exp2ResumeError(
                    "Exp2 --study-kind differs from the v2 plan"
                )
            coordinator = Exp2ResumeCoordinator(state_root, args.study_id)
            _print_yaml(coordinator.initialize(plan, protocol))
            return 0

        plan_source = state_root / "plan.yaml"
        raw_plan = _read_yaml_mapping(plan_source, "Exp2 persisted study plan")
        is_v2 = raw_plan.get("schema") == "autobugfix-exp2-resume-study-plan-v2"
        if is_v2:
            coordinator_v2 = Exp2ResumeCoordinator(state_root, args.study_id)
            if args.exp2_action == "resume":
                authority = Exp2EvalAuthority(Path.cwd(), coordinator_v2)
                _print_yaml(authority.resume(execute=args.execute))
                return 0
            if args.exp2_action == "record-attribution":
                draft = _read_yaml_mapping(
                    args.record,
                    "Exp2 attribution draft",
                )
                operator = OperatorGovernanceService(Path.cwd())
                exported = operator.export_exp2_attribution(
                    exp2_study_id=args.study_id,
                    operator_study_id=args.operator_study_id,
                    evidence_id=args.evidence_id,
                    expected_mechanism=str(
                        draft.get("expected_mechanism") or ""
                    ),
                    execution_scope=tuple(
                        str(item)
                        for item in draft.get("execution_scope") or ()
                    ),
                    validation_plan=tuple(
                        str(item)
                        for item in draft.get("validation_plan") or ()
                    ),
                    hypothesis=str(draft.get("hypothesis") or ""),
                )
                attribution = {
                    key: value
                    for key, value in exported.items()
                    if key != "artifact_path"
                }
                _print_yaml(
                    {
                        "operator_artifact_path": exported["artifact_path"],
                        "status": coordinator_v2.record_attribution(
                            Exp2AttributionHypothesis.from_dict(attribution),
                            operator_service=operator,
                        ),
                    }
                )
                return 0
            if args.exp2_action == "register-h0":
                operator = OperatorGovernanceService(Path.cwd())
                _print_yaml(
                    Exp2EvalAuthority(
                        Path.cwd(), coordinator_v2
                    ).register_operator_h0(
                        args.operator_study_id,
                        operator_service=operator,
                    )
                )
                return 0
            if args.exp2_action == "export-candidate":
                context = coordinator_v2.candidate_handoff_context()
                operator = OperatorGovernanceService(Path.cwd())
                exported = operator.export_exp2_candidate_transition(
                    operator_study_id=args.operator_study_id,
                    request_id=args.request_id,
                    attribution_digest=str(context["attribution_digest"]),
                )
                transition = {
                    key: value
                    for key, value in exported.items()
                    if key != "artifact_path"
                }
                _print_yaml(
                    {
                        "operator_artifact_path": exported["artifact_path"],
                        "status": coordinator_v2.record_candidate_transition(
                            transition,
                            operator_service=operator,
                        ),
                    }
                )
                return 0
            if args.exp2_action == "rollback":
                transition = coordinator_v2.load_candidate_transition()
                if transition is None:
                    raise Exp2ResumeError(
                        "Exp2 study has no locked candidate to roll back"
                    )
                authorization = coordinator_v2.rollback_authorization()
                operator = OperatorGovernanceService(Path.cwd())
                exported = operator.export_exp2_rollback_receipt(
                    transition.to_dict(),
                    rollback_authorization_path=Path(
                        authorization["authorization_path"]
                    ),
                    reason=args.reason,
                    push_remote=args.push_remote,
                )
                rollback = {
                    key: value
                    for key, value in exported.items()
                    if key != "artifact_path"
                }
                _print_yaml(
                    {
                        "operator_artifact_path": exported["artifact_path"],
                        "status": coordinator_v2.record_rollback(
                            rollback,
                            operator_service=operator,
                        ),
                    }
                )
                return 0
            if args.exp2_action == "report":
                _print_yaml(coordinator_v2.publish_report())
                return 0
            raise Exp2ResumeError(
                f"Exp2 v1 action {args.exp2_action!r} is unavailable for a v2 study"
            )

        coordinator = Exp2Coordinator(state_root, args.study_id)
        if args.exp2_action == "resume":
            if args.execute:
                raise Exp2CoordinatorError(
                    "Exp2 v1 execution is audit-only; initialize a v2 study"
                )
            if not args.execute:
                _print_yaml(coordinator.resume())
                return 0
        if args.exp2_action == "budget-plan":
            _print_yaml(coordinator.budget_allocation(args.wave))
            return 0
        if args.exp2_action == "record-attribution":
            _print_yaml(
                coordinator.record_attribution(
                    _read_yaml_mapping(args.record, "Exp2 attribution")
                )
            )
            return 0
        if args.exp2_action == "record-sealed-aggregate":
            _print_yaml(
                coordinator.record_sealed_aggregate(
                    _read_yaml_mapping(args.record, "Exp2 sealed aggregate")
                )
            )
            return 0
        if args.exp2_action == "record-public-gate":
            _print_yaml(
                coordinator.record_public_regression_gate(
                    _read_yaml_mapping(args.record, "Exp2 public regression gate")
                )
            )
            return 0
        if args.exp2_action == "record-burn":
            _print_yaml(
                coordinator.record_holdout_burn(
                    _read_yaml_mapping(args.record, "Exp2 Holdout burn")
                )
            )
            return 0
        if args.exp2_action == "report":
            result = {"status": coordinator.status()}
            try:
                result["paired_public"] = coordinator.paired_public_summary()
            except Exp2CoordinatorError as exc:
                result["paired_public_unavailable"] = str(exc)
            _print_yaml(result)
            return 0
        raise AssertionError(f"unhandled Exp2 action: {args.exp2_action}")
    if action == "baseline":
        if args.baseline_action == "prepare-swe-raw-codex":
            service = SWERawCodexBaselineService(Path.cwd())
            _print_yaml(
                service.prepare(
                    Path(args.source_protocol),
                    Path(args.treatment),
                )
            )
            return 0
        if args.baseline_action == "run-swe-raw-development":
            service = SWERawCodexBaselineService(Path.cwd())
            result = service.run_development(
                Path(args.source_protocol),
                Path(args.treatment),
                instance_id=args.instance,
                out_root=Path(args.out),
                run_id=args.run_id,
            )
            _print_yaml(result)
            return 0 if result["summary"]["status"] == "completed" else 1
        if args.baseline_action == "run-swe-raw-codex":
            service = SWERawCodexBaselineService(Path.cwd())
            result = service.run_formal(
                Path(args.manifest),
                out_root=Path(args.out),
                run_id=args.run_id,
            )
            _print_yaml(result)
            return 0 if result["summary"]["status"] == "completed" else 1
        service = RawCodexBaselineService(Path.cwd())
        if args.baseline_action == "prepare-raw-codex":
            _print_yaml(
                service.prepare(
                    Path(args.protocol),
                    Path(args.source_manifest),
                    Path(args.h0_report),
                )
            )
            return 0
        if args.baseline_action == "pilot-raw-codex":
            result = service.pilot(
                Path(args.protocol),
                Path(args.source_manifest),
                case_id=args.case,
                out_root=Path(args.out),
                run_id=args.run_id,
            )
            _print_yaml(result)
            return 0 if result["summary"]["harness_error_count"] == 0 else 1
        if args.baseline_action == "run-raw-codex":
            result = service.run_formal(
                Path(args.manifest),
                out_root=Path(args.out),
                run_id=args.run_id,
            )
            _print_yaml(result)
            return 0 if result["summary"]["status"] == "completed" else 1
        if args.baseline_action == "report-raw-codex":
            _print_yaml(
                service.report(Path(args.run_dir), Path(args.h0_report))
            )
            return 0
        raise AssertionError(f"unhandled Eval baseline action: {args.baseline_action}")
    if action == "benchmark":
        service = EvalBenchmarkService(Path.cwd())
        if args.benchmark_action == "doctor":
            report = service.doctor(args.adapter)
            _print_yaml(report)
            return 0 if report["passed"] else 1
        if args.benchmark_action == "preflight":
            report = service.preflight(Path(args.manifest), case_selector=args.case)
            _print_yaml(report)
            return 0 if report["failed_count"] == 0 else 1
        if args.benchmark_action == "inspect-swe":
            _print_yaml(service.inspect_swe(args.adapter, args.instance))
            return 0
        if args.benchmark_action == "qualify-swe":
            guard_secret = None
            guard_root = None
            if args.adapter == "swebench_live":
                if not args.guard_root:
                    raise RuntimeError(
                        "SWE Holdout qualification requires --guard-root"
                    )
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "SWE Holdout qualification requires an interactive Guard terminal"
                    )
                guard_root = Path(args.guard_root)
                guard_secret = getpass.getpass(
                    "SWE Guard secret (at least 16 bytes; never stored): "
                )
            report = service.qualify_swe(
                Path(args.protocol),
                args.adapter,
                args.instance,
                guard_root=guard_root,
                guard_secret=guard_secret,
            )
            _print_yaml(report)
            return 0 if report["eligible"] else 1
        if args.benchmark_action == "qualify-swe-holdout-cohort":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "SWE Holdout cohort qualification requires an interactive Guard terminal"
                )
            secret = getpass.getpass(
                "SWE Guard secret (at least 16 bytes; never stored): "
            )

            def show_progress(progress) -> None:
                aggregate = progress.aggregate()
                print(
                    "SWE Guard qualification progress: "
                    f"attempted={aggregate['attempted_count']} "
                    f"eligible={aggregate['eligible_count']}/6 "
                    f"repositories={aggregate['repository_count']}/6 "
                    f"languages={aggregate['language_count']}/4+",
                    flush=True,
                )

            report = SWEHoldoutGuardService(Path.cwd()).qualify_cohort(
                Path(args.protocol),
                guard_root=Path(args.guard_root),
                guard_secret=secret,
                max_candidates=args.max_candidates,
                progress=show_progress,
            )
            _print_yaml(report)
            return 0
        if args.benchmark_action == "run-swe-development":
            report = service.run_swe_development_case(
                Path(args.protocol),
                args.adapter,
                args.instance,
                run_id=args.run_id,
                subject_sha=args.subject_sha,
                model=args.model,
                max_attempts=args.max_attempts,
                timeout_seconds=args.timeout_seconds,
            )
            _print_yaml(report)
            return 0 if report["resolved"] and not report["harness_error"] else 1
        if args.benchmark_action == "prepare-swe":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "SWE cohort preparation requires an interactive Guard terminal"
                )
            secret = getpass.getpass(
                "SWE Guard secret (at least 16 bytes; never stored): "
            )
            _print_yaml(
                service.prepare_swe(
                    Path(args.protocol),
                    guard_root=Path(args.guard_root),
                    guard_secret=secret,
                )
            )
            return 0
        if args.benchmark_action == "seal-swe":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "SWE Holdout sealing requires an interactive trusted human terminal"
                )
            service.guard_authority()
            secret = getpass.getpass(
                "SWE Guard secret (at least 16 bytes; never stored): "
            )
            confirmation = getpass.getpass("Confirm SWE Guard secret: ")
            if secret != confirmation:
                raise RuntimeError("SWE Guard secret confirmation did not match")
            _print_yaml(
                service.seal_swe(
                    Path(args.prepared),
                    guard_root=Path(args.guard_root),
                    guard_secret=secret,
                )
            )
            return 0
        if args.benchmark_action == "run-swe-optimization":
            report = service.run_swe_optimization_case(
                Path(args.manifest),
                case_selector=args.case,
                study_binding_path=Path(args.study_binding),
                out_root=Path(args.out),
                run_id=args.run_id,
                execution_mode=args.execution_mode,
                disposable_root=(
                    Path(args.disposable_root) if args.disposable_root else None
                ),
            )
            _print_yaml(report)
            return 0 if not report["harness_error"] else 1
        if args.benchmark_action == "guard-run-swe":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "SWE Guard execution requires an interactive trusted terminal"
                )
            secret = getpass.getpass(
                "SWE Guard secret (at least 16 bytes; never stored): "
            )
            report = service.guard_run_swe(
                Path(args.manifest),
                guard_root=Path(args.guard_root),
                guard_secret=secret,
                wave_token=args.wave_token,
                study_binding_path=Path(args.study_binding),
                out_root=(
                    Path(args.out)
                    if args.out
                    else Path(args.guard_root) / "results/swe"
                ),
                run_id=args.run_id,
            )
            _print_yaml(report)
            return 0 if report["harness_error_count"] == 0 else 1
        if args.benchmark_action == "prepare-evaluation":
            _print_yaml(service.prepare_evaluation(Path(args.manifest)))
            return 0
        if args.benchmark_action == "run-evaluation":
            report = service.run_evaluation(
                Path(args.manifest),
                out_root=Path(args.out),
                run_id=args.run_id,
            )
            _print_yaml(report)
            return 0 if report["summary"].get("harness_error_count") == 0 else 1
        if args.benchmark_action == "report-evaluation":
            _print_yaml(service.report_evaluation(Path(args.run_dir)))
            return 0
        if args.benchmark_action == "seal":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Guard sealing requires an interactive trusted human terminal"
                )
            service.guard_authority()
            secret = getpass.getpass(
                "Guard secret (at least 16 bytes; never stored): "
            )
            confirmation = getpass.getpass("Confirm Guard secret: ")
            if secret != confirmation:
                raise RuntimeError("Guard secret confirmation did not match")
            projects = tuple(
                item.strip()
                for item in getpass.getpass(
                    "Private Holdout project pool (comma-separated; input is hidden): "
                ).split(",")
                if item.strip()
            )
            _print_yaml(
                service.seal(
                    Path(args.manifest),
                    guard_secret=secret,
                    holdout_projects=projects,
                )
            )
            return 0
        if args.benchmark_action == "guard-run":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Guard Holdout execution requires an interactive trusted human terminal"
                )
            service.guard_authority()
            secret = getpass.getpass("Guard secret: ")
            study_binding = (
                _read_yaml_mapping(args.study_binding, "Guard Study binding")
                if args.study_binding
                else None
            )
            report = service.guard_run(
                Path(args.manifest),
                wave_token=args.wave_token,
                out_root=Path(args.out),
                run_id=args.run_id,
                guard_secret=secret,
                model=args.model,
                max_attempts=args.max_attempts,
                study_binding=study_binding,
            )
            _print_yaml(report)
            return 0 if report["harness_error_count"] == 0 else 1
        if args.benchmark_action == "run-case":
            report = service.run_case(
                Path(args.manifest),
                case_selector=args.case,
                out_root=Path(args.out),
                run_id=args.run_id,
                model=args.model,
                max_attempts=args.max_attempts,
            )
            _print_yaml(report)
            return 0 if report["report"].get("decision") == "pass" else 1
    elif action == "run":
        run_dir = run_eval(
            Path.cwd(),
            Path(args.dataset),
            Path(args.out),
            case_selector=args.case,
            run_id=args.run_id,
            model_mode=args.model_mode,
            test_command=args.test_command,
            codex_timeout_seconds=args.codex_timeout_seconds,
            writer_timeout_seconds=args.writer_timeout_seconds,
            evaluator_timeout_seconds=args.evaluator_timeout_seconds,
            model=args.model,
            max_attempts=args.max_attempts,
        )
        print(run_dir)
        summary = yaml.safe_load((run_dir / "summary.yaml").read_text(encoding="utf-8")) or {}
        if summary.get("failed_count") or summary.get("harness_error_count"):
            return 1
    elif action == "score":
        print(score_path(Path(args.path)))
    elif action == "diagnose":
        print(diagnose_run(Path(args.run_dir)))
    elif action == "improvements":
        if args.improvement_action == "list":
            _print_yaml({"improvements": list_improvements(Path.cwd())})
        elif args.improvement_action == "show":
            print(show_improvement(Path.cwd(), args.name))
        elif args.improvement_action == "update":
            print(update_improvement(Path.cwd(), args.name, sys.stdin.read()))
    elif action == "iterate":
        print(supervision_note(Path.cwd()))
    elif action == "supervise":
        print(supervision_note(Path.cwd()))
    return 0


def command_codex(args: argparse.Namespace) -> int:
    cfg = load_config(Path.cwd())
    resolved = resolve_role(cfg, args.role, repo_id=args.repo)
    # Keep the authority directory hidden while mounting one private leaf for logs.
    probe_root = (
        Path.cwd()
        / ".autobugfix/controller/probes"
        / f"{args.role}-{uuid.uuid4().hex}"
    )
    request = build_codex_request(
        Path.cwd(),
        args.role,
        "probe",
        Path.cwd(),
        None,
        None,
        None,
        probe_root / "raw.jsonl",
        probe_root / "stderr.log",
        repo_id=args.repo,
        resolved_role=resolved,
    )
    report = {
        "role": request.role,
        "repo": args.repo,
        "cwd": str(request.cwd),
        "sandbox": request.sandbox,
        "approval_mode": request.approval_mode,
        "model": request.model,
        "timeout_seconds": request.timeout_seconds,
        "instructions_bytes": len(request.developer_instructions),
        "resolved": resolved.to_dict(cfg.project_root),
        "python_sdk_version": _installed_version("openai-codex"),
        "bundled_cli_version": _installed_version("openai-codex-cli-bin"),
    }
    if args.execute:
        result = CodexSDKBackend().run(request)
        report["executed"] = True
        report["response"] = result.text
        report["backend"] = result.raw.get("module")
    else:
        report["executed"] = False
    _print_yaml(report)
    return 0


def command_operator(args: argparse.Namespace) -> int:
    root = Path.cwd()
    service = OperatorGovernanceService(
        root,
        trusted_ref=args.trusted_ref,
        trusted_file=Path(args.trusted_file) if args.trusted_file else None,
        bootstrap_policy=args.bootstrap_policy,
        allowed_signers=Path(args.allowed_signers) if args.allowed_signers else None,
    )
    action = args.operator_action
    if action == "study":
        if args.study_action == "create":
            study = service.create_study(
                study_id=args.study_id,
                purpose=args.purpose,
                manifest_path=args.manifest,
                success_contract=_read_yaml_mapping(
                    args.success_contract,
                    "success contract",
                ),
                base_ref=args.base_ref,
                harness_ref=args.harness_ref,
                line_id=args.line_id,
                cohort_id=args.cohort_id,
                primary_model=args.model,
                target_checkpoint_name=args.target_checkpoint,
                memory_root=args.memory_root,
                empty_memory_fixture=args.empty_memory_fixture,
                guard_root=args.guard_root,
            )
            _print_yaml(service.study_status(study.study_id)["study"])
        elif args.study_action == "show":
            _print_yaml(service.study_status(args.study_id))
        elif args.study_action == "list":
            _print_yaml({"studies": service.list_studies()})
        elif args.study_action == "guard-binding":
            terminalize = False
            if args.kind == "CANDIDATE":
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "candidate Guard binding requires an interactive trusted human terminal"
                    )
                print(
                    "This permanently closes the Study line before Holdout scoring. "
                    f"Type the Study ID {args.study_id!r} to continue: ",
                    file=sys.stderr,
                    end="",
                    flush=True,
                )
                confirmation = input()
                if confirmation != args.study_id:
                    raise RuntimeError("candidate Guard binding confirmation did not match")
                terminalize = True
            _print_yaml(
                service.guard_study_binding(
                    args.study_id,
                    kind=args.kind,
                    terminalize=terminalize,
                )
            )
        elif args.study_action == "evidence-register":
            evidence = service.register_study_evidence(
                args.study_id,
                binding_path=args.binding,
                artifact_path=args.artifact,
            )
            _print_yaml(
                {
                    "reference": service.study_evidence_reference(evidence),
                    "evidence": evidence.to_dict(),
                }
            )
        elif args.study_action == "import-guard-metric":
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Guard metric import requires an interactive trusted human terminal"
                )
            secret = getpass.getpass("Guard secret: ")
            metric = service.register_signed_guard_metric(
                args.study_id,
                metric_path=args.metric,
                kind=args.kind,
                guard_secret=secret,
            )
            _print_yaml(service.study_metric_projection(metric))
    elif action == "line":
        if args.line_action == "init":
            _print_yaml(
                service.initialize_experiment_line(
                    args.study_id,
                    metric_receipt_id=args.metric_receipt_id,
                )
            )
        elif args.line_action == "show":
            _print_yaml(service.experiment_line_status(args.line_id))
        elif args.line_action == "list":
            _print_yaml(
                {"lines": service.list_experiment_lines(study_id=args.study_id)}
            )
        elif args.line_action == "rollback":
            _print_yaml(
                service.rollback_experiment_line(
                    args.line_id,
                    args.checkpoint_id,
                    reason=args.reason,
                    push_remote=args.push_remote,
                    actor=args.actor,
                )
            )
    elif action == "checkpoint":
        if args.checkpoint_action == "create":
            _print_yaml(
                service.create_checkpoint(
                    args.line_id,
                    metric_receipt_id=args.metric_receipt_id,
                    checkpoint_name=args.name,
                )
            )
        elif args.checkpoint_action == "show":
            _print_yaml(service.experiment_line_status(args.line_id))
    elif action == "budget":
        if args.budget_action == "request":
            request = service.create_budget_request(
                args.study_id,
                wave=args.wave,
                case_ids=args.case,
                reason=args.reason,
                requester=args.requester,
                model=args.model,
                max_calls=args.max_calls,
                max_writer_attempts=args.max_writer_attempts,
                max_operator_revisions=args.max_operator_revisions,
                wall_time_seconds=args.wall_time_seconds,
                case_concurrency=args.case_concurrency,
            )
            _print_yaml(request.to_dict())
        elif args.budget_action == "approve":
            if args.approval_kind == "delegated_agent":
                if not args.delegation_note:
                    raise RuntimeError(
                        "delegated budget approval requires --delegation-note recording the standing user instruction"
                    )
            elif not sys.stdin.isatty():
                raise RuntimeError(
                    "budget approval requires an interactive human terminal"
                )
            if args.approval_kind != "delegated_agent":
                expected = f"APPROVE {args.confirm_request_digest}"
                entered = input(
                    "Type the exact budget approval phrase "
                    f"'{expected}' to authorize model spend: "
                )
                if entered.strip() != expected:
                    raise RuntimeError("budget approval confirmation did not match")
            grant = service.approve_budget_grant(
                args.budget_request_id,
                approver=args.approver,
                confirm_request_digest=args.confirm_request_digest,
                approval_kind=args.approval_kind,
                delegation_note=args.delegation_note,
            )
            _print_yaml(grant.to_dict())
        elif args.budget_action == "show":
            _print_yaml(service.budget_status(args.study_id))
    elif action == "triage":
        triage = service.create_triage(
            triage_id=args.triage_id,
            summary=args.summary,
            suspected_layers=args.suspected_layer,
            confidence=args.confidence,
            evidence=args.evidence,
            next_actions=args.next_action or (),
            creator=args.creator,
        )
        _print_yaml(triage.to_dict())
    elif action == "request":
        request = service.create_request(
            request_id=args.request_id,
            triage_id=args.triage_id,
            summary=args.summary,
            primary_layer=args.primary_layer,
            secondary_layers=args.secondary_layer or (),
            planned_paths=args.planned_path or (),
            requested_risk=args.risk,
            validation_profiles=args.validation_profile or (),
            performance_baseline=args.performance_baseline,
            creator=args.creator,
            branch=args.branch,
            experiment_line_id=args.experiment_line,
            budget_grant_id=args.budget_grant,
            expires_at=args.expires_at,
        )
        _print_yaml(request.to_dict())
    elif action == "review":
        approval = service.add_reviewer_decision(
            args.request_id,
            reviewer=args.reviewer,
            decision=args.decision,
            reason=args.reason,
            allowed_layers=args.allowed_layer or None,
            allowed_paths=args.allowed_path or (),
            expires_at=args.expires_at,
            scope_revision_id=args.scope_revision_id,
        )
        _print_yaml(approval.to_dict())
    elif action == "approval-payload":
        print(
            service.create_approval_payload(
                args.request_id,
                Path(args.out),
                approver=args.approver,
                stage=args.stage,
                reason=args.reason,
                allowed_layers=args.allowed_layer or None,
                allowed_paths=args.allowed_path or (),
                expires_at=args.expires_at,
                scope_revision_id=args.scope_revision_id,
            )
        )
    elif action == "approve-signed":
        approval = service.import_signed_approval(
            args.request_id,
            payload_path=Path(args.payload),
            signature_path=Path(args.signature),
            scope_revision_id=args.scope_revision_id,
        )
        _print_yaml(approval.to_dict())
    elif action == "approve-github":
        approval = service.import_github_approval(
            args.request_id,
            repository=args.repository,
            pull_request=args.pull_request,
            review_id=args.review_id,
            reason=args.reason,
            stage=args.stage,
        )
        _print_yaml(approval.to_dict())
    elif action == "preflight":
        report = service.preflight(args.request_id, actor=args.actor)
        _print_yaml(report)
        return 0 if report["allowed"] else 1
    elif action in {"workspace-create", "start"}:
        _print_yaml(service.start(args.request_id, actor=args.actor))
    elif action == "guide":
        _print_yaml(service.governance_context())
    elif action == "advance":
        _print_yaml(service.advance(args.request_id, actor=args.actor))
    elif action == "supervise":
        _print_yaml(service.run_supervisor(args.request_id, actor=args.actor))
    elif action == "writer-start":
        _print_yaml(service.start_writer(args.request_id, actor=args.actor))
    elif action == "writer-retry":
        _print_yaml(service.retry_writer(args.request_id, actor=args.actor))
    elif action == "writer-cancel":
        _print_yaml(service.cancel_writer(args.request_id, reason=args.reason, actor=args.actor))
    elif action == "candidate-commit":
        _print_yaml(
            service.commit_candidate(
                args.request_id,
                message=args.message,
                include_manifest=not args.no_manifest,
                actor=args.actor,
            )
        )
    elif action == "postflight":
        report = service.postflight(args.request_id, actor=args.actor)
        _print_yaml(report)
        return 0 if report["check_run"]["status"] == "PASSED" else 1
    elif action in {"validate", "verify"}:
        report = service.verify(
            args.request_id,
            mode=getattr(args, "mode", "full"),
            actor=args.actor,
        )
        _print_yaml(report)
        return 0 if report["check_run"]["status"] == "PASSED" else 1
    elif action == "scope-change":
        _print_yaml(
            service.request_scope_change(
                args.request_id,
                add_layers=args.add_layer or (),
                add_paths=args.add_path or (),
                requested_risk=args.risk,
                reason=args.reason,
                actor=args.actor,
            )
        )
    elif action == "scope-activate":
        _print_yaml(service.activate_scope_revision(args.request_id, args.revision_id, actor=args.actor))
    elif action == "experiment-run":
        report = service.run_experiment(
            args.request_id,
            profile=args.profile,
            values=_parse_values(args.value or []),
            actor=args.actor,
        )
        _print_yaml(report)
        return 0 if report["status"] == "COMPLETED" else 1
    elif action == "integrate":
        _print_yaml(
            service.integrate_candidate(
                args.request_id,
                grant_id=args.grant_id,
                push_remote=args.push_remote,
                actor=args.actor,
            )
        )
    elif action == "reopen":
        _print_yaml(service.reopen(args.request_id, reason=args.reason, actor=args.actor))
    elif action == "close":
        _print_yaml(service.close(args.request_id, outcome=args.outcome, actor=args.actor))
    elif action == "promotion-prepare":
        _print_yaml(service.prepare_promotion(args.request_id, actor=args.actor))
    elif action == "promotion-open-pr":
        _print_yaml(
            service.open_pull_request(
                args.promotion_id,
                title=args.title,
                body=args.body,
                base=args.base,
                push=not args.no_push,
            )
        )
    elif action == "promotion-observe-merge":
        _print_yaml(service.observe_merge(args.promotion_id, repository=args.repository))
    elif action == "promotion-canary":
        report = service.run_canary(args.promotion_id)
        _print_yaml(report)
        return 0 if report["promotion"]["status"] == "ACTIVE" else 1
    elif action == "promotion-rollback":
        _print_yaml(service.rollback(args.promotion_id, reason=args.reason, actor=args.actor))
    elif action == "promotion-revert-pr":
        _print_yaml(
            service.open_revert_pull_request(
                args.promotion_id,
                title=args.title,
                body=args.body,
                base=args.base,
                push=not args.no_push,
            )
        )
    elif action == "finalize":
        report = service.finalize(args.request_id, actor=args.actor)
        _print_yaml(report)
        return 0
    elif action == "status":
        _print_yaml(service.status(args.request_id))
    elif action == "audit":
        report = service.audit(args.request_id)
        _print_yaml(report)
        return 0 if report["allowed"] else 1
    elif action == "export-bundle":
        print(service.export_bundle(args.request_id, output_root=Path(args.output_root) if args.output_root else None))
    elif action == "revoke":
        _print_yaml(service.revoke(args.request_id, actor=args.actor, reason=args.reason))
    elif action == "baseline":
        if args.baseline_action == "record":
            report = service.capture_baseline(
                args.name,
                profile=args.profile,
                values=_parse_values(args.value or []),
                notes=args.notes or "",
                base_ref=args.base,
            )
            _print_yaml(report)
        elif args.baseline_action == "compare":
            report = service.compare_experiment_baseline(args.request_id, args.name)
            _print_yaml(report)
            return 0 if report["ok"] else 1
        elif args.baseline_action == "show":
            _print_yaml(read_baseline(root, args.name))
    return 0


def command_writer_view(args: argparse.Namespace) -> int:
    root = Path(args.control_root).resolve()
    service = OperatorGovernanceService(
        root,
        trusted_ref=args.trusted_ref,
        trusted_file=Path(args.trusted_file) if args.trusted_file else None,
        bootstrap_policy=args.bootstrap_policy,
    )
    view = service.writer_view(args.request_id)
    action = args.writer_view_action
    if action == "task":
        payload = {"request_id": view["request_id"], "phase": view["phase"], "task": view["task"]}
    elif action == "context":
        payload = {"request_id": view["request_id"], "evidence": view["evidence"]}
    elif action == "scope":
        payload = {"request_id": view["request_id"], "scope": view["scope"]}
    elif action == "feedback":
        payload = {"request_id": view["request_id"], "feedback": view["feedback"]}
    elif action == "check-result":
        payload = {"request_id": view["request_id"], "latest_check": view["latest_check"]}
    else:
        raise RuntimeError(f"unsupported writer view action: {action}")
    _print_yaml(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autobugfix")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--init-config", action="store_true")
    doctor.set_defaults(func=command_doctor)

    create = sub.add_parser("create")
    create.add_argument("--repo", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--from-stdin", action="store_true")
    create.set_defaults(func=command_create)

    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_action", required=True)
    context_add = context_sub.add_parser("add")
    context_add.add_argument("task_id")
    context_add.add_argument("--kind", required=True)
    context_add.add_argument("--from-stdin", action="store_true")
    context_add.add_argument("--file")
    context_add.set_defaults(func=command_context_add)

    run = sub.add_parser("run")
    run.add_argument("task_id")
    run.set_defaults(func=command_run)

    feedback = sub.add_parser("feedback")
    feedback.add_argument("task_id")
    feedback.add_argument("--decision", required=True, choices=["needs_changes"])
    feedback.add_argument("--from-stdin", action="store_true")
    feedback.add_argument("--queue-only", action="store_true")
    feedback.set_defaults(func=command_feedback)

    gate = sub.add_parser("gate")
    gate.add_argument("task_id")
    gate.add_argument("action", choices=["approve-ppe", "accepted", "abandoned", "pause", "resume"])
    gate.set_defaults(func=command_gate)

    deploy = sub.add_parser("deploy-ppe")
    deploy.add_argument("task_id")
    deploy.set_defaults(func=command_deploy_ppe)

    archive = sub.add_parser("archive")
    archive.add_argument("task_id")
    archive.add_argument("--result", required=True)
    archive.set_defaults(func=command_archive)

    sub.add_parser("status").set_defaults(func=command_status)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("task_id")
    inspect.set_defaults(func=command_inspect)
    watch = sub.add_parser("watch")
    watch.add_argument("task_id")
    watch.add_argument("--once", action="store_true")
    watch.set_defaults(func=command_watch)
    tick_parser = sub.add_parser("tick")
    tick_parser.add_argument("--max-concurrent", type=int, default=None)
    tick_parser.set_defaults(func=command_tick)
    daemon = sub.add_parser("daemon")
    daemon.add_argument("--once", action="store_true")
    daemon.set_defaults(func=command_daemon)
    worker = sub.add_parser("worker")
    worker.add_argument("worker_action", choices=["start", "ensure", "status", "stop"])
    worker.set_defaults(func=command_worker)
    ui = sub.add_parser("ui")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=7860)
    ui.set_defaults(func=command_ui)

    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_action", required=True)
    for name in ("init", "status", "proposals", "lint"):
        memory_sub.add_parser(name).set_defaults(func=command_memory)
    for name in ("collect", "digest", "maintain"):
        p = memory_sub.add_parser(name)
        p.add_argument("task_id")
        p.set_defaults(func=command_memory)
    mtick = memory_sub.add_parser("tick")
    mtick.add_argument("--max-tasks", type=int, default=1)
    mtick.set_defaults(func=command_memory)
    for name in ("review", "show"):
        p = memory_sub.add_parser(name)
        p.add_argument("proposal_id")
        p.set_defaults(func=command_memory)
    approve = memory_sub.add_parser("approve")
    approve.add_argument("proposal_id")
    approve.add_argument("--note", required=True)
    approve.add_argument("--confirm-review-digest", required=True)
    approve.set_defaults(func=command_memory)
    approve_skill = memory_sub.add_parser("approve-skill")
    approve_skill.add_argument("proposal_id")
    approve_skill.add_argument("--skill-name", required=True)
    approve_skill.add_argument("--description", required=True)
    approve_skill.add_argument("--note", required=True)
    approve_skill.add_argument("--confirm-review-digest", required=True)
    approve_skill.set_defaults(func=command_memory)
    reject = memory_sub.add_parser("reject")
    reject.add_argument("proposal_id")
    reject.add_argument("--reason", required=True)
    reject.set_defaults(func=command_memory)
    search = memory_sub.add_parser("search")
    search.add_argument("query")
    search.set_defaults(func=command_memory)
    mcontext = memory_sub.add_parser("context")
    mcontext.add_argument("--audience", required=True, choices=["writer", "evaluator", "controller"])
    mcontext.set_defaults(func=command_memory)

    memory_worker = sub.add_parser("memory-worker")
    memory_worker.add_argument("worker_action", choices=["start", "ensure", "status", "stop"])
    memory_worker.set_defaults(func=command_memory_worker)
    memory_ui = sub.add_parser("memory-ui")
    memory_ui.add_argument("--host", default="127.0.0.1")
    memory_ui.add_argument("--port", type=int, default=7861)
    memory_ui.set_defaults(func=command_memory_ui)

    dataset = sub.add_parser("dataset")
    dataset_sub = dataset.add_subparsers(dest="dataset_action", required=True)
    raw = dataset_sub.add_parser("build-raw")
    raw.add_argument("--repo", required=True)
    raw.add_argument("--out", required=True)
    raw.add_argument("--base-ref")
    raw.set_defaults(func=command_dataset)

    eval_parser = sub.add_parser("eval")
    eval_sub = eval_parser.add_subparsers(dest="eval_action", required=True)
    exp2 = eval_sub.add_parser("exp2")
    exp2_sub = exp2.add_subparsers(dest="exp2_action", required=True)
    exp2_init = exp2_sub.add_parser("init")
    exp2_init.add_argument("--study-id", required=True)
    exp2_init.add_argument(
        "--study-kind", choices=["calibration", "resume_pilot"]
    )
    exp2_init.add_argument("--plan-v2")
    exp2_init.add_argument("--protocol-v2")
    exp2_init.add_argument("--calibration-protocol")
    exp2_init.add_argument("--manifest")
    exp2_init.add_argument("--h0-binding")
    exp2_init.add_argument("--candidate-binding")
    exp2_init.add_argument("--calibration-case", action="append")
    exp2_init.add_argument("--public-case", action="append")
    exp2_init.add_argument(
        "--execution-mode", choices=["protected", "workspace_only"], default="protected"
    )
    exp2_init.add_argument("--disposable-root")
    exp2_init.add_argument("--cohort-audit")
    exp2_init.add_argument("--policy")
    exp2_init.add_argument("--apparatus-receipt")
    exp2_init.add_argument("--empty-memory-fixture")
    exp2_init.add_argument("--state-root")
    exp2_init.set_defaults(func=command_eval)
    exp2_build_protocol = exp2_sub.add_parser("build-protocol-v2")
    exp2_build_protocol.add_argument("--study-id", required=True)
    exp2_build_protocol.add_argument(
        "--protocol-id", default="exp2-resume-mvp-v2"
    )
    exp2_build_protocol.add_argument("--swe-protocol", required=True)
    exp2_build_protocol.add_argument(
        "--empty-memory-fixture", required=True
    )
    exp2_build_protocol.add_argument(
        "--execution-allowlist",
        action="append",
        choices=[EXP2_WRITER_SKILL_PATH],
        required=True,
    )
    exp2_build_protocol.add_argument(
        "--evaluation-mode",
        choices=["legacy_pilot", "iterative_full"],
        default="iterative_full",
    )
    exp2_build_protocol.add_argument("--artifact-root")
    exp2_build_protocol.add_argument("--out", required=True)
    exp2_build_protocol.add_argument("--state-root")
    exp2_build_protocol.set_defaults(func=command_eval)
    exp2_build_plan = exp2_sub.add_parser("build-plan-v2")
    exp2_build_plan.add_argument("--study-id", required=True)
    exp2_build_plan.add_argument(
        "--study-kind", choices=["calibration", "resume_pilot"], required=True
    )
    exp2_build_plan.add_argument("--protocol-v2", required=True)
    exp2_build_plan.add_argument("--swe-protocol", required=True)
    exp2_build_plan.add_argument("--apparatus-receipt", required=True)
    exp2_build_plan.add_argument("--empty-memory-fixture", required=True)
    exp2_build_plan.add_argument("--memory-root", required=True)
    exp2_build_plan.add_argument("--disposable-root", required=True)
    exp2_build_plan.add_argument("--guard-root", required=True)
    exp2_build_plan.add_argument("--public-manifest")
    exp2_build_plan.add_argument("--h0-binding")
    exp2_build_plan.add_argument("--calibration-terminal-receipt")
    exp2_build_plan.add_argument("--out", required=True)
    exp2_build_plan.add_argument("--state-root")
    exp2_build_plan.set_defaults(func=command_eval)
    exp2_build_apparatus = exp2_sub.add_parser(
        "build-apparatus-receipt-v2"
    )
    exp2_build_apparatus.add_argument("--study-id", required=True)
    exp2_build_apparatus.add_argument("--protocol-v2", required=True)
    exp2_build_apparatus.add_argument("--swe-protocol", required=True)
    exp2_build_apparatus.add_argument(
        "--check-artifact", action="append", required=True
    )
    exp2_build_apparatus.add_argument("--out", required=True)
    exp2_build_apparatus.add_argument("--state-root")
    exp2_build_apparatus.set_defaults(func=command_eval)
    exp2_export_h0 = exp2_sub.add_parser("export-h0-binding")
    exp2_export_h0.add_argument("--study-id", required=True)
    exp2_export_h0.add_argument("--operator-study-id", required=True)
    exp2_export_h0.add_argument("--state-root")
    exp2_export_h0.set_defaults(func=command_eval)
    exp2_source_check = exp2_sub.add_parser("source-check-v2")
    exp2_source_check.add_argument("--study-id", required=True)
    exp2_source_check.add_argument("--name", required=True)
    exp2_source_check.add_argument("--artifact-root", required=True)
    exp2_source_check.add_argument("--out", required=True)
    exp2_source_check.add_argument("--timeout-seconds", type=int, default=7200)
    exp2_source_check.add_argument("command", nargs=argparse.REMAINDER)
    exp2_source_check.add_argument("--state-root")
    exp2_source_check.set_defaults(func=command_eval)
    exp2_prepare_manifest = exp2_sub.add_parser("prepare-manifest-v2")
    exp2_prepare_manifest.add_argument("--study-id", required=True)
    exp2_prepare_manifest.add_argument("--swe-protocol", required=True)
    exp2_prepare_manifest.add_argument("--state-root")
    exp2_prepare_manifest.set_defaults(func=command_eval)
    exp2_resume = exp2_sub.add_parser("resume")
    exp2_resume.add_argument("--study-id", required=True)
    exp2_resume.add_argument("--state-root")
    exp2_resume.add_argument(
        "--execute",
        action="store_true",
        help="execute the next safe public/calibration stage; otherwise inspect readiness",
    )
    exp2_resume.set_defaults(func=command_eval)
    exp2_budget = exp2_sub.add_parser("budget-plan")
    exp2_budget.add_argument("--study-id", required=True)
    exp2_budget.add_argument("--wave", type=int, choices=[3, 8, 16], required=True)
    exp2_budget.add_argument("--state-root")
    exp2_budget.set_defaults(func=command_eval)
    exp2_attribution = exp2_sub.add_parser("record-attribution")
    exp2_attribution.add_argument("--study-id", required=True)
    exp2_attribution.add_argument("--operator-study-id", required=True)
    exp2_attribution.add_argument("--evidence-id", required=True)
    exp2_attribution.add_argument("--record", required=True)
    exp2_attribution.add_argument("--state-root")
    exp2_attribution.set_defaults(func=command_eval)
    exp2_register_h0 = exp2_sub.add_parser("register-h0")
    exp2_register_h0.add_argument("--study-id", required=True)
    exp2_register_h0.add_argument("--operator-study-id", required=True)
    exp2_register_h0.add_argument("--state-root")
    exp2_register_h0.set_defaults(func=command_eval)
    exp2_export_candidate = exp2_sub.add_parser("export-candidate")
    exp2_export_candidate.add_argument("--study-id", required=True)
    exp2_export_candidate.add_argument("--operator-study-id", required=True)
    exp2_export_candidate.add_argument("--request-id", required=True)
    exp2_export_candidate.add_argument("--state-root")
    exp2_export_candidate.set_defaults(func=command_eval)
    exp2_rollback = exp2_sub.add_parser("rollback")
    exp2_rollback.add_argument("--study-id", required=True)
    exp2_rollback.add_argument("--reason", required=True)
    exp2_rollback.add_argument("--push-remote", action="store_true")
    exp2_rollback.add_argument("--state-root")
    exp2_rollback.set_defaults(func=command_eval)
    exp2_sealed_aggregate = exp2_sub.add_parser("record-sealed-aggregate")
    exp2_sealed_aggregate.add_argument("--study-id", required=True)
    exp2_sealed_aggregate.add_argument("--record", required=True)
    exp2_sealed_aggregate.add_argument("--state-root")
    exp2_sealed_aggregate.set_defaults(func=command_eval)
    exp2_gate = exp2_sub.add_parser("record-public-gate")
    exp2_gate.add_argument("--study-id", required=True)
    exp2_gate.add_argument("--record", required=True)
    exp2_gate.add_argument("--state-root")
    exp2_gate.set_defaults(func=command_eval)
    exp2_burn = exp2_sub.add_parser("record-burn")
    exp2_burn.add_argument("--study-id", required=True)
    exp2_burn.add_argument("--record", required=True)
    exp2_burn.add_argument("--state-root")
    exp2_burn.set_defaults(func=command_eval)
    exp2_report = exp2_sub.add_parser("report")
    exp2_report.add_argument("--study-id", required=True)
    exp2_report.add_argument("--state-root")
    exp2_report.set_defaults(func=command_eval)
    baseline = eval_sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(
        dest="baseline_action", required=True
    )
    baseline_prepare = baseline_sub.add_parser("prepare-raw-codex")
    baseline_prepare.add_argument("--protocol", required=True)
    baseline_prepare.add_argument("--source-manifest", required=True)
    baseline_prepare.add_argument("--h0-report", required=True)
    baseline_prepare.set_defaults(func=command_eval)
    baseline_pilot = baseline_sub.add_parser("pilot-raw-codex")
    baseline_pilot.add_argument("--protocol", required=True)
    baseline_pilot.add_argument("--source-manifest", required=True)
    baseline_pilot.add_argument("--case", required=True)
    baseline_pilot.add_argument(
        "--out", default=".autobugfix/raw-codex-baseline/pilot-runs"
    )
    baseline_pilot.add_argument("--run-id", required=True)
    baseline_pilot.set_defaults(func=command_eval)
    baseline_run = baseline_sub.add_parser("run-raw-codex")
    baseline_run.add_argument("--manifest", required=True)
    baseline_run.add_argument(
        "--out", default=".autobugfix/raw-codex-baseline/formal-runs"
    )
    baseline_run.add_argument("--run-id", required=True)
    baseline_run.set_defaults(func=command_eval)
    baseline_report = baseline_sub.add_parser("report-raw-codex")
    baseline_report.add_argument("--run-dir", required=True)
    baseline_report.add_argument("--h0-report", required=True)
    baseline_report.set_defaults(func=command_eval)
    baseline_prepare_swe = baseline_sub.add_parser("prepare-swe-raw-codex")
    baseline_prepare_swe.add_argument("--source-protocol", required=True)
    baseline_prepare_swe.add_argument("--treatment", required=True)
    baseline_prepare_swe.set_defaults(func=command_eval)
    baseline_development_swe = baseline_sub.add_parser(
        "run-swe-raw-development"
    )
    baseline_development_swe.add_argument("--source-protocol", required=True)
    baseline_development_swe.add_argument("--treatment", required=True)
    baseline_development_swe.add_argument("--instance", required=True)
    baseline_development_swe.add_argument(
        "--out",
        default=".autobugfix/raw-codex-baseline/swe/development-runs",
    )
    baseline_development_swe.add_argument("--run-id", required=True)
    baseline_development_swe.set_defaults(func=command_eval)
    baseline_run_swe = baseline_sub.add_parser("run-swe-raw-codex")
    baseline_run_swe.add_argument("--manifest", required=True)
    baseline_run_swe.add_argument(
        "--out",
        default=".autobugfix/raw-codex-baseline/swe/formal-runs",
    )
    baseline_run_swe.add_argument("--run-id", required=True)
    baseline_run_swe.set_defaults(func=command_eval)
    benchmark = eval_sub.add_parser("benchmark")
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_action", required=True)
    benchmark_doctor = benchmark_sub.add_parser("doctor")
    benchmark_doctor.add_argument(
        "--adapter",
        default="defects4j",
        choices=["defects4j", "swebench_verified", "swebench_live"],
    )
    benchmark_doctor.set_defaults(func=command_eval)
    benchmark_preflight = benchmark_sub.add_parser("preflight")
    benchmark_preflight.add_argument("--manifest", required=True)
    benchmark_preflight.add_argument("--case")
    benchmark_preflight.set_defaults(func=command_eval)
    benchmark_inspect_swe = benchmark_sub.add_parser("inspect-swe")
    benchmark_inspect_swe.add_argument(
        "--adapter",
        required=True,
        choices=["swebench_verified", "swebench_live"],
    )
    benchmark_inspect_swe.add_argument("--instance", required=True)
    benchmark_inspect_swe.set_defaults(func=command_eval)
    benchmark_qualify_swe = benchmark_sub.add_parser("qualify-swe")
    benchmark_qualify_swe.add_argument("--protocol", required=True)
    benchmark_qualify_swe.add_argument(
        "--adapter",
        required=True,
        choices=["swebench_verified", "swebench_live"],
    )
    benchmark_qualify_swe.add_argument("--instance", required=True)
    benchmark_qualify_swe.add_argument("--guard-root")
    benchmark_qualify_swe.set_defaults(func=command_eval)
    benchmark_qualify_swe_holdout = benchmark_sub.add_parser(
        "qualify-swe-holdout-cohort"
    )
    benchmark_qualify_swe_holdout.add_argument("--protocol", required=True)
    benchmark_qualify_swe_holdout.add_argument("--guard-root", required=True)
    benchmark_qualify_swe_holdout.add_argument(
        "--max-candidates", type=int, default=24
    )
    benchmark_qualify_swe_holdout.set_defaults(func=command_eval)
    benchmark_run_swe_development = benchmark_sub.add_parser(
        "run-swe-development"
    )
    benchmark_run_swe_development.add_argument("--protocol", required=True)
    benchmark_run_swe_development.add_argument(
        "--adapter",
        required=True,
        choices=["swebench_verified", "swebench_live"],
    )
    benchmark_run_swe_development.add_argument("--instance", required=True)
    benchmark_run_swe_development.add_argument("--run-id", required=True)
    benchmark_run_swe_development.add_argument("--subject-sha")
    benchmark_run_swe_development.add_argument(
        "--model", default="gpt-5.4-mini"
    )
    benchmark_run_swe_development.add_argument(
        "--max-attempts", type=int, default=2
    )
    benchmark_run_swe_development.add_argument(
        "--timeout-seconds", type=int, default=900
    )
    benchmark_run_swe_development.set_defaults(func=command_eval)
    benchmark_prepare_swe = benchmark_sub.add_parser("prepare-swe")
    benchmark_prepare_swe.add_argument("--protocol", required=True)
    benchmark_prepare_swe.add_argument("--guard-root", required=True)
    benchmark_prepare_swe.set_defaults(func=command_eval)
    benchmark_seal_swe = benchmark_sub.add_parser("seal-swe")
    benchmark_seal_swe.add_argument("--prepared", required=True)
    benchmark_seal_swe.add_argument("--guard-root", required=True)
    benchmark_seal_swe.set_defaults(func=command_eval)
    benchmark_run_swe_optimization = benchmark_sub.add_parser(
        "run-swe-optimization"
    )
    benchmark_run_swe_optimization.add_argument("--manifest", required=True)
    benchmark_run_swe_optimization.add_argument("--case", required=True)
    benchmark_run_swe_optimization.add_argument("--study-binding", required=True)
    benchmark_run_swe_optimization.add_argument("--run-id", required=True)
    benchmark_run_swe_optimization.add_argument(
        "--execution-mode", choices=["protected", "workspace_only"], default="protected"
    )
    benchmark_run_swe_optimization.add_argument("--disposable-root")
    benchmark_run_swe_optimization.add_argument(
        "--out",
        default=".autobugfix/trusted-eval-cases/swe/formal-optimization",
    )
    benchmark_run_swe_optimization.set_defaults(func=command_eval)
    benchmark_guard_run_swe = benchmark_sub.add_parser("guard-run-swe")
    benchmark_guard_run_swe.add_argument("--manifest", required=True)
    benchmark_guard_run_swe.add_argument("--guard-root", required=True)
    benchmark_guard_run_swe.add_argument("--wave-token", required=True)
    benchmark_guard_run_swe.add_argument("--study-binding", required=True)
    benchmark_guard_run_swe.add_argument("--run-id", required=True)
    benchmark_guard_run_swe.add_argument("--out")
    benchmark_guard_run_swe.set_defaults(func=command_eval)
    benchmark_prepare_evaluation = benchmark_sub.add_parser("prepare-evaluation")
    benchmark_prepare_evaluation.add_argument("--manifest", required=True)
    benchmark_prepare_evaluation.set_defaults(func=command_eval)
    benchmark_run_evaluation = benchmark_sub.add_parser("run-evaluation")
    benchmark_run_evaluation.add_argument("--manifest", required=True)
    benchmark_run_evaluation.add_argument(
        "--out", default=".autobugfix/eval-runs"
    )
    benchmark_run_evaluation.add_argument("--run-id", required=True)
    benchmark_run_evaluation.set_defaults(func=command_eval)
    benchmark_report_evaluation = benchmark_sub.add_parser("report-evaluation")
    benchmark_report_evaluation.add_argument("--run-dir", required=True)
    benchmark_report_evaluation.set_defaults(func=command_eval)
    benchmark_seal = benchmark_sub.add_parser("seal")
    benchmark_seal.add_argument("--manifest", required=True)
    benchmark_seal.set_defaults(func=command_eval)
    benchmark_guard_run = benchmark_sub.add_parser("guard-run")
    benchmark_guard_run.add_argument("--manifest", required=True)
    benchmark_guard_run.add_argument("--wave-token", required=True)
    benchmark_guard_run.add_argument("--out", default=".autobugfix/guard-results")
    benchmark_guard_run.add_argument("--run-id", required=True)
    benchmark_guard_run.add_argument("--model", default="gpt-5.4-mini")
    benchmark_guard_run.add_argument("--max-attempts", type=int, default=2)
    benchmark_guard_run.add_argument(
        "--study-binding",
        help="Operator-derived Study binding YAML to include in the signed aggregate",
    )
    benchmark_guard_run.set_defaults(func=command_eval)
    benchmark_run_case = benchmark_sub.add_parser("run-case")
    benchmark_run_case.add_argument("--manifest", required=True)
    benchmark_run_case.add_argument("--case", required=True)
    benchmark_run_case.add_argument("--out", default=".autobugfix/eval-runs")
    benchmark_run_case.add_argument("--run-id", required=True)
    benchmark_run_case.add_argument("--model", default="gpt-5.4-mini")
    benchmark_run_case.add_argument("--max-attempts", type=int, default=2)
    benchmark_run_case.set_defaults(func=command_eval)
    erun = eval_sub.add_parser("run")
    erun.add_argument("--dataset", required=True)
    erun.add_argument("--case")
    erun.add_argument("--out", required=True)
    erun.add_argument("--run-id", default="run")
    erun.add_argument("--model-mode", default="codex", choices=["codex"])
    erun.add_argument("--test-command")
    erun.add_argument("--codex-timeout-seconds", type=int)
    erun.add_argument("--writer-timeout-seconds", type=int)
    erun.add_argument("--evaluator-timeout-seconds", type=int)
    erun.add_argument("--model")
    erun.add_argument("--max-attempts", type=int, default=1)
    erun.set_defaults(func=command_eval)
    escore = eval_sub.add_parser("score")
    escore.add_argument("path")
    escore.set_defaults(func=command_eval)
    ediag = eval_sub.add_parser("diagnose")
    ediag.add_argument("run_dir")
    ediag.set_defaults(func=command_eval)
    improvements = eval_sub.add_parser("improvements")
    imp_sub = improvements.add_subparsers(dest="improvement_action", required=True)
    imp_sub.add_parser("list").set_defaults(func=command_eval)
    ishow = imp_sub.add_parser("show")
    ishow.add_argument("name")
    ishow.set_defaults(func=command_eval)
    iupdate = imp_sub.add_parser("update")
    iupdate.add_argument("name")
    iupdate.set_defaults(func=command_eval)
    eval_sub.add_parser("iterate").set_defaults(func=command_eval)
    eval_sub.add_parser("supervise").set_defaults(func=command_eval)

    codex = sub.add_parser("codex")
    codex_sub = codex.add_subparsers(dest="codex_action", required=True)
    probe = codex_sub.add_parser("probe-role")
    probe.add_argument(
        "--role",
        required=True,
        choices=[
            "writer",
            "evaluator",
            "controller",
            "memory_maintainer",
            "eval_judge",
            "operator_supervisor",
            "operator_writer",
            "operator_verifier",
        ],
    )
    probe.add_argument("--repo")
    probe.add_argument("--execute", action="store_true", help="Run a real read-only Codex SDK compatibility probe")
    probe.set_defaults(func=command_codex)

    operator = sub.add_parser("operator")
    operator_sub = operator.add_subparsers(dest="operator_action", required=True)

    def governance_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--trusted-ref", default="origin/main")
        command.add_argument("--trusted-file")
        command.add_argument("--bootstrap-policy", action="store_true")
        command.add_argument("--allowed-signers")

    study = operator_sub.add_parser("study")
    study_sub = study.add_subparsers(dest="study_action", required=True)
    study_create = study_sub.add_parser("create")
    governance_options(study_create)
    study_create.add_argument("--study-id", required=True)
    study_create.add_argument("--purpose", required=True)
    study_create.add_argument("--manifest", required=True)
    study_create.add_argument("--success-contract", required=True)
    study_create.add_argument("--base-ref")
    study_create.add_argument("--harness-ref")
    study_create.add_argument("--line-id")
    study_create.add_argument("--cohort-id")
    study_create.add_argument("--model", default="gpt-5.4-mini")
    study_create.add_argument(
        "--target-checkpoint",
        choices=["H_bug", "H_general"],
        default="H_bug",
    )
    study_create.add_argument("--memory-root")
    study_create.add_argument(
        "--empty-memory-fixture", action="store_true"
    )
    study_create.add_argument("--guard-root")
    study_create.set_defaults(func=command_operator)
    study_show = study_sub.add_parser("show")
    governance_options(study_show)
    study_show.add_argument("--study-id", required=True)
    study_show.set_defaults(func=command_operator)
    study_list = study_sub.add_parser("list")
    governance_options(study_list)
    study_list.set_defaults(func=command_operator)
    study_guard_binding = study_sub.add_parser("guard-binding")
    governance_options(study_guard_binding)
    study_guard_binding.add_argument("--study-id", required=True)
    study_guard_binding.add_argument(
        "--kind",
        choices=["BASELINE", "OPTIMIZATION", "CANDIDATE"],
        required=True,
    )
    study_guard_binding.set_defaults(func=command_operator)
    study_evidence_register = study_sub.add_parser("evidence-register")
    governance_options(study_evidence_register)
    study_evidence_register.add_argument("--study-id", required=True)
    study_evidence_register.add_argument("--binding", required=True)
    study_evidence_register.add_argument("--artifact", required=True)
    study_evidence_register.set_defaults(func=command_operator)
    study_import_guard = study_sub.add_parser("import-guard-metric")
    governance_options(study_import_guard)
    study_import_guard.add_argument("--study-id", required=True)
    study_import_guard.add_argument("--metric", required=True)
    study_import_guard.add_argument(
        "--kind", choices=["BASELINE", "CANDIDATE"], required=True
    )
    study_import_guard.set_defaults(func=command_operator)

    line = operator_sub.add_parser("line")
    line_sub = line.add_subparsers(dest="line_action", required=True)
    line_init = line_sub.add_parser("init")
    governance_options(line_init)
    line_init.add_argument("--study-id", required=True)
    line_init.add_argument("--metric-receipt-id", required=True)
    line_init.set_defaults(func=command_operator)
    line_show = line_sub.add_parser("show")
    governance_options(line_show)
    line_show.add_argument("--line-id", required=True)
    line_show.set_defaults(func=command_operator)
    line_list = line_sub.add_parser("list")
    governance_options(line_list)
    line_list.add_argument("--study-id")
    line_list.set_defaults(func=command_operator)
    line_rollback = line_sub.add_parser("rollback")
    governance_options(line_rollback)
    line_rollback.add_argument("--line-id", required=True)
    line_rollback.add_argument("--checkpoint-id", required=True)
    line_rollback.add_argument("--reason", required=True)
    line_rollback.add_argument("--push-remote", action="store_true")
    line_rollback.add_argument("--actor")
    line_rollback.set_defaults(func=command_operator)

    checkpoint = operator_sub.add_parser("checkpoint")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_action", required=True)
    checkpoint_create = checkpoint_sub.add_parser("create")
    governance_options(checkpoint_create)
    checkpoint_create.add_argument("--line-id", required=True)
    checkpoint_create.add_argument("--metric-receipt-id", required=True)
    checkpoint_create.add_argument("--name", choices=["H_bug", "H_general"])
    checkpoint_create.set_defaults(func=command_operator)
    checkpoint_show = checkpoint_sub.add_parser("show")
    governance_options(checkpoint_show)
    checkpoint_show.add_argument("--line-id", required=True)
    checkpoint_show.set_defaults(func=command_operator)

    budget = operator_sub.add_parser("budget")
    budget_sub = budget.add_subparsers(dest="budget_action", required=True)
    budget_request = budget_sub.add_parser("request")
    governance_options(budget_request)
    budget_request.add_argument("--study-id", required=True)
    budget_request.add_argument("--wave", type=int, choices=[3, 8, 16], required=True)
    budget_request.add_argument("--case", action="append", required=True)
    budget_request.add_argument("--reason", required=True)
    budget_request.add_argument("--requester")
    budget_request.add_argument("--model")
    budget_request.add_argument("--max-calls", type=int)
    budget_request.add_argument("--max-writer-attempts", type=int)
    budget_request.add_argument("--max-operator-revisions", type=int)
    budget_request.add_argument("--wall-time-seconds", type=int)
    budget_request.add_argument("--case-concurrency", type=int)
    budget_request.set_defaults(func=command_operator)
    budget_approve = budget_sub.add_parser("approve")
    governance_options(budget_approve)
    budget_approve.add_argument("--budget-request-id", required=True)
    budget_approve.add_argument("--approver", required=True)
    budget_approve.add_argument("--confirm-request-digest", required=True)
    budget_approve.add_argument(
        "--approval-kind",
        choices=["interactive", "delegated_agent"],
        default="interactive",
    )
    budget_approve.add_argument(
        "--delegation-note",
        help="records the standing user instruction behind a delegated approval",
    )
    budget_approve.set_defaults(func=command_operator)
    budget_show = budget_sub.add_parser("show")
    governance_options(budget_show)
    budget_show.add_argument("--study-id", required=True)
    budget_show.set_defaults(func=command_operator)

    triage = operator_sub.add_parser("triage")
    governance_options(triage)
    triage.add_argument("--triage-id")
    triage.add_argument("--summary", required=True)
    triage.add_argument("--suspected-layer", action="append", choices=VALID_LAYERS, required=True)
    triage.add_argument("--confidence", choices=VALID_CONFIDENCE, default="low")
    triage.add_argument("--evidence", action="append", required=True)
    triage.add_argument("--next-action", action="append")
    triage.add_argument("--creator")
    triage.set_defaults(func=command_operator)

    request = operator_sub.add_parser("request")
    governance_options(request)
    request.add_argument("--request-id")
    request.add_argument("--triage-id", required=True)
    request.add_argument("--summary", required=True)
    request.add_argument("--primary-layer", choices=VALID_LAYERS, required=True)
    request.add_argument("--secondary-layer", action="append", choices=VALID_LAYERS)
    request.add_argument("--planned-path", action="append", required=True)
    request.add_argument("--risk", choices=VALID_RISKS, default="low")
    request.add_argument("--validation-profile", action="append")
    request.add_argument("--performance-baseline")
    request.add_argument("--creator")
    request.add_argument("--branch")
    request.add_argument("--experiment-line")
    request.add_argument("--budget-grant")
    request.add_argument("--expires-at")
    request.set_defaults(func=command_operator)

    review = operator_sub.add_parser("review")
    governance_options(review)
    review.add_argument("request_id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=VALID_APPROVAL_DECISIONS, required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--allowed-layer", action="append", choices=VALID_LAYERS)
    review.add_argument("--allowed-path", action="append")
    review.add_argument("--expires-at")
    review.add_argument("--scope-revision-id")
    review.set_defaults(func=command_operator)

    payload = operator_sub.add_parser("approval-payload")
    governance_options(payload)
    payload.add_argument("request_id")
    payload.add_argument("--out", required=True)
    payload.add_argument("--approver", required=True)
    payload.add_argument("--stage", choices=["scope", "merge"], required=True)
    payload.add_argument("--reason", required=True)
    payload.add_argument("--allowed-layer", action="append", choices=VALID_LAYERS)
    payload.add_argument("--allowed-path", action="append")
    payload.add_argument("--expires-at")
    payload.add_argument("--scope-revision-id")
    payload.set_defaults(func=command_operator)

    signed = operator_sub.add_parser("approve-signed")
    governance_options(signed)
    signed.add_argument("request_id")
    signed.add_argument("--payload", required=True)
    signed.add_argument("--signature", required=True)
    signed.add_argument("--scope-revision-id")
    signed.set_defaults(func=command_operator)

    github = operator_sub.add_parser("approve-github")
    governance_options(github)
    github.add_argument("request_id")
    github.add_argument("--repository", required=True)
    github.add_argument("--pull-request", required=True, type=int)
    github.add_argument("--review-id", required=True, type=int)
    github.add_argument("--reason", required=True)
    github.add_argument("--stage", choices=["scope", "merge"], default="merge")
    github.set_defaults(func=command_operator)

    guide = operator_sub.add_parser("guide")
    governance_options(guide)
    guide.set_defaults(func=command_operator)

    for name in ("preflight", "start", "workspace-create", "postflight", "finalize", "status", "audit", "advance", "supervise"):
        command = operator_sub.add_parser(name)
        governance_options(command)
        command.add_argument("--request-id", required=True)
        command.add_argument("--actor")
        command.set_defaults(func=command_operator)

    for name in ("writer-start", "writer-retry"):
        command = operator_sub.add_parser(name)
        governance_options(command)
        command.add_argument("--request-id", required=True)
        command.add_argument("--actor")
        command.set_defaults(func=command_operator)

    writer_cancel = operator_sub.add_parser("writer-cancel")
    governance_options(writer_cancel)
    writer_cancel.add_argument("--request-id", required=True)
    writer_cancel.add_argument("--reason", required=True)
    writer_cancel.add_argument("--actor")
    writer_cancel.set_defaults(func=command_operator)

    candidate_commit = operator_sub.add_parser("candidate-commit")
    governance_options(candidate_commit)
    candidate_commit.add_argument("--request-id", required=True)
    candidate_commit.add_argument("--message", required=True)
    candidate_commit.add_argument("--no-manifest", action="store_true")
    candidate_commit.add_argument("--actor")
    candidate_commit.set_defaults(func=command_operator)

    export_bundle = operator_sub.add_parser("export-bundle")
    governance_options(export_bundle)
    export_bundle.add_argument("--request-id", required=True)
    export_bundle.add_argument("--output-root")
    export_bundle.set_defaults(func=command_operator)

    validate = operator_sub.add_parser("validate")
    governance_options(validate)
    validate.add_argument("--request-id", required=True)
    validate.add_argument("--actor")
    validate.set_defaults(func=command_operator)

    verify = operator_sub.add_parser("verify")
    governance_options(verify)
    verify.add_argument("--request-id", required=True)
    verify.add_argument("--mode", choices=["fast", "full"], default="full")
    verify.add_argument("--actor")
    verify.set_defaults(func=command_operator)

    scope_change = operator_sub.add_parser("scope-change")
    governance_options(scope_change)
    scope_change.add_argument("--request-id", required=True)
    scope_change.add_argument("--add-layer", action="append", choices=VALID_LAYERS)
    scope_change.add_argument("--add-path", action="append")
    scope_change.add_argument("--risk", choices=VALID_RISKS, default="low")
    scope_change.add_argument("--reason", required=True)
    scope_change.add_argument("--actor")
    scope_change.set_defaults(func=command_operator)

    scope_activate = operator_sub.add_parser("scope-activate")
    governance_options(scope_activate)
    scope_activate.add_argument("--request-id", required=True)
    scope_activate.add_argument("--revision-id", required=True)
    scope_activate.add_argument("--actor")
    scope_activate.set_defaults(func=command_operator)

    experiment_run = operator_sub.add_parser("experiment-run")
    governance_options(experiment_run)
    experiment_run.add_argument("--request-id", required=True)
    experiment_run.add_argument("--profile")
    experiment_run.add_argument("--value", action="append", help="Experiment input as key=value")
    experiment_run.add_argument("--actor")
    experiment_run.set_defaults(func=command_operator)

    integrate = operator_sub.add_parser("integrate")
    governance_options(integrate)
    integrate.add_argument("--request-id", required=True)
    integrate.add_argument("--grant-id", required=True)
    integrate.add_argument("--push-remote", action="store_true")
    integrate.add_argument("--actor")
    integrate.set_defaults(func=command_operator)

    reopen = operator_sub.add_parser("reopen")
    governance_options(reopen)
    reopen.add_argument("--request-id", required=True)
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--actor")
    reopen.set_defaults(func=command_operator)

    close = operator_sub.add_parser("close")
    governance_options(close)
    close.add_argument("--request-id", required=True)
    close.add_argument("--outcome", required=True, choices=["merged", "abandoned", "rejected", "superseded", "rolled_back"])
    close.add_argument("--actor")
    close.set_defaults(func=command_operator)

    promotion_prepare = operator_sub.add_parser("promotion-prepare")
    governance_options(promotion_prepare)
    promotion_prepare.add_argument("--request-id", required=True)
    promotion_prepare.add_argument("--actor")
    promotion_prepare.set_defaults(func=command_operator)

    promotion_pr = operator_sub.add_parser("promotion-open-pr")
    governance_options(promotion_pr)
    promotion_pr.add_argument("--promotion-id", required=True)
    promotion_pr.add_argument("--title", required=True)
    promotion_pr.add_argument("--body", required=True)
    promotion_pr.add_argument("--base", default="main")
    promotion_pr.add_argument("--no-push", action="store_true")
    promotion_pr.set_defaults(func=command_operator)

    promotion_merge = operator_sub.add_parser("promotion-observe-merge")
    governance_options(promotion_merge)
    promotion_merge.add_argument("--promotion-id", required=True)
    promotion_merge.add_argument("--repository", required=True)
    promotion_merge.set_defaults(func=command_operator)

    promotion_canary = operator_sub.add_parser("promotion-canary")
    governance_options(promotion_canary)
    promotion_canary.add_argument("--promotion-id", required=True)
    promotion_canary.set_defaults(func=command_operator)

    promotion_rollback = operator_sub.add_parser("promotion-rollback")
    governance_options(promotion_rollback)
    promotion_rollback.add_argument("--promotion-id", required=True)
    promotion_rollback.add_argument("--reason", required=True)
    promotion_rollback.add_argument("--actor")
    promotion_rollback.set_defaults(func=command_operator)

    promotion_revert = operator_sub.add_parser("promotion-revert-pr")
    governance_options(promotion_revert)
    promotion_revert.add_argument("--promotion-id", required=True)
    promotion_revert.add_argument("--title", required=True)
    promotion_revert.add_argument("--body", required=True)
    promotion_revert.add_argument("--base", default="main")
    promotion_revert.add_argument("--no-push", action="store_true")
    promotion_revert.set_defaults(func=command_operator)

    revoke = operator_sub.add_parser("revoke")
    governance_options(revoke)
    revoke.add_argument("--request-id", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--actor")
    revoke.set_defaults(func=command_operator)

    baseline = operator_sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_action", required=True)
    baseline_record = baseline_sub.add_parser("record")
    governance_options(baseline_record)
    baseline_record.add_argument("--name", required=True)
    baseline_record.add_argument("--profile")
    baseline_record.add_argument("--value", action="append", help="Experiment input as key=value")
    baseline_record.add_argument("--notes")
    baseline_record.add_argument(
        "--base",
        help="explicit measurement base ref (defaults to the configured trusted ref)",
    )
    baseline_record.set_defaults(func=command_operator)
    baseline_compare = baseline_sub.add_parser("compare")
    governance_options(baseline_compare)
    baseline_compare.add_argument("--name", required=True)
    baseline_compare.add_argument("--request-id", required=True)
    baseline_compare.set_defaults(func=command_operator)
    baseline_show = baseline_sub.add_parser("show")
    governance_options(baseline_show)
    baseline_show.add_argument("--name", required=True)
    baseline_show.set_defaults(func=command_operator)

    writer_view = sub.add_parser("writer")
    writer_view_sub = writer_view.add_subparsers(dest="writer_view_action", required=True)
    for name in ("task", "context", "scope", "feedback", "check-result"):
        command = writer_view_sub.add_parser(name)
        command.add_argument("--request-id", required=True)
        command.add_argument("--control-root", default=".")
        command.add_argument("--trusted-ref", default="origin/main")
        command.add_argument("--trusted-file")
        command.add_argument("--bootstrap-policy", action="store_true")
        command.set_defaults(func=command_writer_view)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
