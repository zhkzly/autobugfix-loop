from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

import yaml

from autobugfix.codex_runtime import build_codex_request
from autobugfix.codex_sdk import CodexSDKBackend
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
from autobugfix.operator.metrics import read_baseline
from autobugfix.operator.models import (
    VALID_APPROVAL_DECISIONS,
    VALID_CONFIDENCE,
    VALID_LAYERS,
    VALID_RISKS,
)
from autobugfix.operator.service import OperatorGovernanceService


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
    if action == "triage":
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
    erun.add_argument("--model-mode", default="codex", choices=["codex"])
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
