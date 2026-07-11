from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from autobugfix.models import utc_now
from autobugfix.config import load_config
from autobugfix.operator.guard import effective_request
from autobugfix.operator.metrics import (
    OperatorMetricsError,
    baseline_for_request,
    compare_baseline,
    derive_metric_receipt,
)
from autobugfix.operator.models import digest_payload
from autobugfix.operator.policy import PolicyDecision, evaluate_policy
from autobugfix.operator.store import OperatorStore
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy


class OperatorValidationError(RuntimeError):
    pass


def _format_argv(argv: list[Any], values: Mapping[str, str]) -> list[str]:
    return [str(item).format_map(values) for item in argv]


def _safe_log_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "command"


def _run_command(
    candidate_root: Path,
    log_root: Path,
    name: str,
    argv: list[str],
    timeout_seconds: int | None,
    *,
    process_sandbox: str,
    require_process_sandbox: bool,
    network_access: bool,
    hidden_roots: tuple[Path, ...],
    writable_roots: tuple[Path, ...],
    read_only_binds: tuple[tuple[Path, Path], ...],
) -> dict[str, Any]:
    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY"))
    }
    environment["UV_NO_SYNC"] = "1"
    candidate_src = candidate_root / "src"
    python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{candidate_src}{os.pathsep}{python_path}" if python_path else str(candidate_src)
    )
    sandboxed = False
    executed_argv = list(argv)
    if process_sandbox not in {"auto", "bubblewrap", "none"}:
        raise OperatorValidationError(f"unsupported process sandbox: {process_sandbox}")
    bubblewrap = shutil.which("bwrap") if process_sandbox in {"auto", "bubblewrap"} else None
    if bubblewrap:
        sandboxed = True
        wrapper = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        if not network_access:
            wrapper.append("--unshare-net")
        for root in hidden_roots:
            resolved = root.resolve()
            if resolved.exists():
                try:
                    candidate_root.resolve().relative_to(resolved)
                except ValueError:
                    wrapper.extend(["--tmpfs", str(resolved)])
        for root in writable_roots:
            resolved = root.resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            wrapper.extend(["--bind", str(resolved), str(resolved)])
        for source, destination in read_only_binds:
            source = source.resolve()
            destination = destination.resolve()
            if not source.exists():
                raise OperatorValidationError(f"read-only sandbox bind does not exist: {source}")
            destination.mkdir(parents=True, exist_ok=True)
            wrapper.extend(["--ro-bind", str(source), str(destination)])
        wrapper.extend(
            ["--bind", str(candidate_root), str(candidate_root), "--chdir", str(candidate_root), "--"]
        )
        executed_argv = [*wrapper, *argv]
    elif require_process_sandbox:
        raise OperatorValidationError(
            "authoritative command execution requires Bubblewrap; install bwrap or configure a supported sandbox"
        )
    try:
        result = subprocess.run(
            executed_argv,
            cwd=candidate_root,
            text=True,
            shell=False,
            capture_output=True,
            env=environment,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr += f"\nvalidation command timed out after {timeout_seconds} seconds\n"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{name}.stdout.log"
    stderr_path = log_root / f"{name}.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "name": name,
        "argv": argv,
        "executed_argv": executed_argv,
        "sandbox": "bubblewrap" if sandboxed else "none",
        "cwd": str(candidate_root),
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": time.monotonic() - started_monotonic,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def run_validation_profiles(
    candidate_root: Path,
    record_root: Path,
    request_id: str,
    validation_id: str,
    decision: PolicyDecision,
    constitution: Mapping[str, Any],
    *,
    log_root_override: Path | None = None,
    process_sandbox: str = "auto",
    require_process_sandbox: bool = True,
    network_access: bool = False,
    hidden_roots: tuple[Path, ...] = (),
    writable_roots: tuple[Path, ...] = (),
    read_only_binds: tuple[tuple[Path, Path], ...] = (),
) -> list[dict[str, Any]]:
    profiles = constitution.get("validation_profiles") or {}
    values = {
        "base_sha": decision.base_sha,
        "head_sha": decision.head_sha,
        "request_id": request_id,
        "candidate_root": str(candidate_root),
    }
    results: list[dict[str, Any]] = []
    log_root = (
        log_root_override.resolve()
        if log_root_override is not None
        else record_root / ".autobugfix/operator/logs" / request_id / validation_id
    )
    for profile_name in decision.required_profiles:
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            raise OperatorValidationError(f"missing trusted validation profile: {profile_name}")
        default_timeout = int(profile.get("timeout_seconds", 300))
        commands = profile.get("commands") or []
        if not commands:
            raise OperatorValidationError(f"trusted validation profile has no commands: {profile_name}")
        results.extend(
            run_command_specs(
                candidate_root,
                log_root,
                commands,
                values=values,
                default_timeout_seconds=default_timeout,
                name_prefix=profile_name,
                process_sandbox=process_sandbox,
                require_process_sandbox=require_process_sandbox,
                network_access=network_access,
                hidden_roots=hidden_roots,
                writable_roots=writable_roots,
                read_only_binds=read_only_binds,
            )
        )
    return results


def run_command_specs(
    candidate_root: Path,
    log_root: Path,
    commands: list[Any],
    *,
    values: Mapping[str, str],
    default_timeout_seconds: int = 300,
    name_prefix: str = "command",
    process_sandbox: str = "auto",
    require_process_sandbox: bool = True,
    network_access: bool = False,
    hidden_roots: tuple[Path, ...] = (),
    writable_roots: tuple[Path, ...] = (),
    read_only_binds: tuple[tuple[Path, Path], ...] = (),
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(commands, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("argv"), list):
            raise OperatorValidationError(f"invalid configured command {name_prefix}[{index}]")
        display_name = str(raw.get("name") or f"{name_prefix}-{index}")
        log_name = f"{index:02d}-{_safe_log_name(display_name)}"
        argv = _format_argv(raw["argv"], values)
        result = _run_command(
            candidate_root,
            log_root,
            log_name,
            argv,
            int(raw.get("timeout_seconds", default_timeout_seconds)),
            process_sandbox=process_sandbox,
            require_process_sandbox=require_process_sandbox,
            network_access=network_access,
            hidden_roots=hidden_roots,
            writable_roots=writable_roots,
            read_only_binds=read_only_binds,
        )
        result["name"] = display_name
        results.append(result)
    return results


def validate_operator_request(
    project_root: Path | str,
    request_id: str,
    *,
    candidate_root: Path | str | None = None,
    trusted_ref: str | None = "origin/main",
    trusted_file: Path | None = None,
    bootstrap_policy: bool = False,
    run_profiles: bool = True,
    allowed_signers: Path | None = None,
    phase: str = "postflight",
    record: bool = True,
    trusted_policy: TrustedPolicy | None = None,
) -> dict[str, Any]:
    record_root = Path(project_root).resolve()
    candidate = Path(candidate_root or project_root).resolve()
    config = load_config(record_root)
    store = OperatorStore(
        record_root,
        state_root=config.operator.state.root,
        artifact_root=config.operator.artifacts.root,
        database_name=config.operator.state.database_name,
        lease_timeout_seconds=config.operator.state.lease_timeout_seconds,
    )
    request, scope_version = effective_request(
        store.read_request(request_id), store.read_scope_revisions(request_id)
    )
    approvals = store.read_approvals(request_id)
    policy = trusted_policy or load_trusted_policy(
        record_root,
        trusted_ref=trusted_ref,
        trusted_file=trusted_file,
        bootstrap=bootstrap_policy,
    )
    decision = evaluate_policy(
        candidate,
        request,
        approvals,
        constitution=policy.data,
        trusted_policy_source=policy.source,
        trusted_policy=policy.trusted,
        phase=phase,
        allowed_signers=allowed_signers,
        scope_version=scope_version,
    )
    validation_id = f"validation-{uuid.uuid4().hex[:12]}"
    command_results: list[dict[str, Any]] = []
    experiment_results: list[dict[str, Any]] = []
    regression: dict[str, Any] | None = None

    if not policy.trusted:
        decision.violations.append("bootstrap policy is local feedback only and cannot produce merge-ready authority")
        decision.allowed = False

    baseline_layers = {str(item) for item in policy.data.get("baseline_required_layers") or []}
    behavior_change = bool((set(decision.changed_layers) or request.declared_layers) & baseline_layers)
    if behavior_change and not request.performance_baseline:
        decision.violations.append("behavior-affecting change requires a trusted performance baseline")
        decision.allowed = False

    if decision.allowed and run_profiles:
        runtime_binds = (
            ((config.operator.verification.runtime_venv, candidate / ".venv"),)
            if config.operator.verification.runtime_venv
            and config.operator.verification.runtime_venv.is_dir()
            else ()
        )
        command_results = run_validation_profiles(
            candidate,
            record_root,
            request_id,
            validation_id,
            decision,
            policy.data,
            log_root_override=store.artifact_root / request_id / "standalone" / validation_id,
            process_sandbox=config.operator.verification.process_sandbox,
            require_process_sandbox=config.operator.verification.require_process_sandbox,
            network_access=config.operator.verification.network_access,
            hidden_roots=(store.root, store.artifact_root),
            read_only_binds=runtime_binds,
        )
        if any(not item["passed"] for item in command_results):
            decision.violations.append("one or more trusted validation profile commands failed")
            decision.allowed = False

    current_receipt: dict[str, Any] | None = None
    if decision.allowed and request.performance_baseline and run_profiles:
        try:
            baseline = baseline_for_request(
                record_root,
                request.performance_baseline,
                request.base_sha,
            )
            profile = baseline["profile_contract"]
            profile_values = {
                str(key): str(value)
                for key, value in (baseline.get("profile_values") or {}).items()
            }
            shadow_root = store.root / "standalone" / validation_id / "shadow"
            shadow_root.mkdir(parents=True, exist_ok=True)
            experiment_results = run_command_specs(
                candidate,
                store.artifact_root / request_id / "standalone" / validation_id / "experiment",
                list(profile.get("commands") or []),
                values={
                    "request_id": request_id,
                    "base_sha": decision.base_sha,
                    "head_sha": decision.head_sha,
                    "candidate_root": str(candidate),
                    "shadow_state_root": str(shadow_root),
                    **profile_values,
                },
                default_timeout_seconds=int(profile.get("timeout_seconds", 1800)),
                name_prefix=str(baseline["profile"]),
                process_sandbox=config.operator.verification.process_sandbox,
                require_process_sandbox=config.operator.verification.require_process_sandbox,
                network_access=bool(profile.get("network_access", False)),
                hidden_roots=(store.root, store.artifact_root),
                writable_roots=(shadow_root,),
                read_only_binds=runtime_binds,
            )
            current_receipt = derive_metric_receipt(
                source="trusted_admission_experiment",
                profile=str(baseline["profile"]),
                values=profile_values,
                base_sha=decision.base_sha,
                head_sha=decision.head_sha,
                patch_digest=decision.patch_digest,
                command_results=experiment_results,
                profile_contract=profile,
            )
            regression = compare_baseline(
                record_root,
                request.performance_baseline,
                current_receipt,
                policy.data.get("metrics") or {},
                request_base_sha=request.base_sha,
            )
        except OperatorMetricsError as exc:
            regression = {"ok": False, "failures": [str(exc)], "comparisons": {}}
        if not regression["ok"]:
            decision.violations.extend(str(item) for item in regression["failures"])
            decision.allowed = False

    payload = {
        "validation_id": validation_id,
        "request_id": request.request_id,
        "request_digest": request.request_digest,
        "candidate_root": str(candidate),
        "policy": decision.to_dict(),
        "command_results": command_results,
        "experiment_results": experiment_results,
        "regression": regression,
        "metric_receipt": current_receipt,
        "created_at": utc_now(),
    }
    report = {**payload, "validation_digest": digest_payload(payload)}
    if record:
        report["record_path"] = str(store.write_validation(request_id, validation_id, report))
    return report
