from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from autobugfix.codex_runtime import build_codex_request
from autobugfix.config import load_config, write_default_config
from autobugfix.dataset import build_raw_dataset
from autobugfix.eval.diagnosis import diagnose_run
from autobugfix.eval.improvements import list_improvements, show_improvement, update_improvement
from autobugfix.eval.runner import run_eval, score_path
from autobugfix.eval.supervision import supervision_note
from autobugfix.gradio_app import launch as launch_ui
from autobugfix.memory.service import MemoryService
from autobugfix.memory_gradio_app import launch as launch_memory_ui
from autobugfix.projection import inspect_projection, render_inspect, status_projection
from autobugfix.role_config import resolve_role
from autobugfix.scheduler import tick
from autobugfix.service import AutobugfixService
from autobugfix.worker import start_worker, stop_worker, worker_status
from autobugfix.memory_worker import start_worker as start_memory_worker
from autobugfix.memory_worker import stop_worker as stop_memory_worker
from autobugfix.memory_worker import worker_status as memory_worker_status
from autobugfix.operator.metrics import compare_baseline, parse_metric, record_baseline
from autobugfix.operator.models import (
    VALID_CONFIDENCE,
    VALID_LAYERS,
    VALID_REVIEW_DECISIONS,
    VALID_REVIEW_KINDS,
    VALID_RISKS,
    OperatorRequest,
    OperatorReview,
    OperatorTriage,
)
from autobugfix.operator.store import OperatorStore
from autobugfix.operator.validator import validate_operator_request


def _stdin_or_file(args: argparse.Namespace) -> str:
    if getattr(args, "from_stdin", False):
        return sys.stdin.read()
    file_value = getattr(args, "file", None)
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    return ""


def _print_yaml(data: object) -> None:
    print(yaml.safe_dump(data, sort_keys=False).strip())


def command_doctor(args: argparse.Namespace) -> int:
    if args.init_config:
        write_default_config(Path.cwd())
    cfg = load_config(Path.cwd())
    print("Autobugfix doctor")
    print(f"project_root: {cfg.project_root}")
    print(f"task_root: {cfg.project_root / cfg.task_root}")
    print(f"codex_runtime_root: {cfg.codex.role_runtime.runtime_root}")
    print("roles:")
    for role in sorted(cfg.codex.roles):
        resolved = resolve_role(cfg, role)
        print(f"  {role}:")
        print(f"    backend: {resolved.backend}")
        print(f"    model: {resolved.model}")
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
    elif action in {"review", "show"}:
        print(service.show(args.proposal_id))
    elif action == "approve":
        print(service.approve(args.proposal_id, args.note))
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
    if action == "run":
        print(
            run_eval(
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
            )
        )
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
    request = build_codex_request(
        Path.cwd(),
        args.role,
        "probe",
        Path.cwd(),
        None,
        None,
        None,
        Path.cwd() / ".autobugfix/controller" / f"{args.role}.probe.raw.jsonl",
        Path.cwd() / ".autobugfix/controller" / f"{args.role}.probe.stderr.log",
        repo_id=args.repo,
        resolved_role=resolved,
    )
    _print_yaml(
        {
            "role": request.role,
            "repo": args.repo,
            "cwd": str(request.cwd),
            "sandbox": request.sandbox,
            "approval_mode": request.approval_mode,
            "model": request.model,
            "timeout_seconds": request.timeout_seconds,
            "instructions_bytes": len(request.developer_instructions),
            "resolved": resolved.to_dict(cfg.project_root),
        }
    )
    return 0


def command_operator(args: argparse.Namespace) -> int:
    root = Path.cwd()
    store = OperatorStore(root)
    action = args.operator_action
    if action == "triage":
        triage_id = args.triage_id or store.next_id("triage")
        triage = OperatorTriage(
            triage_id=triage_id,
            summary=args.summary,
            suspected_layers=args.suspected_layer,
            confidence=args.confidence,
            evidence=args.evidence or [],
            next_actions=args.next_action or [],
        )
        path = store.write_triage(triage)
        _print_yaml({"triage_id": triage_id, "path": str(path)})
    elif action == "request":
        request_id = args.request_id or store.next_id("request")
        request = OperatorRequest(
            request_id=request_id,
            summary=args.summary,
            primary_layer=args.primary_layer,
            secondary_layers=args.secondary_layer or [],
            risk=args.risk,
            triage_id=args.triage_id,
            evidence=args.evidence or [],
            validation_commands=args.validation_command or [],
            performance_baseline=args.performance_baseline,
        )
        path = store.write_request(request)
        _print_yaml({"request_id": request_id, "path": str(path)})
    elif action == "review":
        review = OperatorReview(
            request_id=args.request_id,
            reviewer=args.reviewer,
            reviewer_kind=args.kind,
            decision=args.decision,
            reason=args.reason,
            approved_paths=args.approved_path or [],
            required_validation=args.required_validation or [],
        )
        path = store.write_review(review)
        _print_yaml({"request_id": args.request_id, "path": str(path), "decision": args.decision})
    elif action in {"validate", "preflight"}:
        report = validate_operator_request(
            root,
            args.request_id,
            base_ref=args.base_ref,
            run_validation_commands=args.run_validation_commands,
            validation_timeout_seconds=args.validation_timeout_seconds,
            record=not args.no_record,
        )
        _print_yaml(report)
        return 0 if report["policy"]["allowed"] else 1
    elif action == "baseline":
        if args.baseline_action == "record":
            path = record_baseline(root, args.name, parse_metric(args.metric or []), notes=args.notes or "")
            print(path)
        elif args.baseline_action == "compare":
            report = compare_baseline(
                root,
                args.name,
                parse_metric(args.metric or []),
                max_regression_percent=parse_metric(args.max_regression_percent or []),
                min_metrics=parse_metric(args.min_metric or []),
            )
            _print_yaml(report)
            return 0 if report["ok"] else 1
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
    approve.set_defaults(func=command_memory)
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
    erun = eval_sub.add_parser("run")
    erun.add_argument("--dataset", required=True)
    erun.add_argument("--case")
    erun.add_argument("--out", required=True)
    erun.add_argument("--run-id", default="run")
    erun.add_argument("--model-mode", default="codex", choices=["codex", "fake"])
    erun.add_argument("--test-command")
    erun.add_argument("--codex-timeout-seconds", type=int)
    erun.add_argument("--writer-timeout-seconds", type=int)
    erun.add_argument("--evaluator-timeout-seconds", type=int)
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
    probe.add_argument("--role", required=True, choices=["writer", "evaluator", "controller", "memory_maintainer", "eval_judge"])
    probe.add_argument("--repo")
    probe.set_defaults(func=command_codex)

    operator = sub.add_parser("operator")
    operator_sub = operator.add_subparsers(dest="operator_action", required=True)
    triage = operator_sub.add_parser("triage")
    triage.add_argument("--triage-id")
    triage.add_argument("--summary", required=True)
    triage.add_argument("--suspected-layer", action="append", choices=VALID_LAYERS, required=True)
    triage.add_argument("--confidence", choices=VALID_CONFIDENCE, default="low")
    triage.add_argument("--evidence", action="append")
    triage.add_argument("--next-action", action="append")
    triage.set_defaults(func=command_operator)
    request = operator_sub.add_parser("request")
    request.add_argument("--request-id")
    request.add_argument("--summary", required=True)
    request.add_argument("--primary-layer", choices=VALID_LAYERS, required=True)
    request.add_argument("--secondary-layer", action="append", choices=VALID_LAYERS)
    request.add_argument("--risk", choices=VALID_RISKS, default="low")
    request.add_argument("--triage-id")
    request.add_argument("--evidence", action="append")
    request.add_argument("--validation-command", action="append")
    request.add_argument("--performance-baseline")
    request.set_defaults(func=command_operator)
    review = operator_sub.add_parser("review")
    review.add_argument("request_id")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--kind", choices=VALID_REVIEW_KINDS, required=True)
    review.add_argument("--decision", choices=VALID_REVIEW_DECISIONS, required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--approved-path", action="append")
    review.add_argument("--required-validation", action="append")
    review.set_defaults(func=command_operator)
    for name in ("validate", "preflight"):
        validate = operator_sub.add_parser(name)
        validate.add_argument("--request-id", required=True)
        validate.add_argument("--base-ref", default="HEAD")
        validate.add_argument("--run-validation-commands", action="store_true")
        validate.add_argument("--validation-timeout-seconds", type=int)
        validate.add_argument("--no-record", action="store_true")
        validate.set_defaults(func=command_operator)
    baseline = operator_sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_action", required=True)
    baseline_record = baseline_sub.add_parser("record")
    baseline_record.add_argument("--name", required=True)
    baseline_record.add_argument("--metric", action="append", help="Numeric metric as key=value")
    baseline_record.add_argument("--notes")
    baseline_record.set_defaults(func=command_operator)
    baseline_compare = baseline_sub.add_parser("compare")
    baseline_compare.add_argument("--name", required=True)
    baseline_compare.add_argument("--metric", action="append", help="Current numeric metric as key=value")
    baseline_compare.add_argument("--max-regression-percent", action="append", help="Maximum allowed increase as key=value")
    baseline_compare.add_argument("--min-metric", action="append", help="Minimum allowed value as key=value")
    baseline_compare.set_defaults(func=command_operator)
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
