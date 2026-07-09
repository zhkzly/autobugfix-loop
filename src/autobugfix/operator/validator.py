from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

from autobugfix.models import utc_now
from autobugfix.operator.metrics import OperatorMetricsError, compare_baseline
from autobugfix.operator.models import digest_payload
from autobugfix.operator.policy import PolicyDecision, evaluate_policy
from autobugfix.operator.store import OperatorStore
from autobugfix.operator.trusted import TrustedPolicy, load_trusted_policy


class OperatorValidationError(RuntimeError):
    pass


def _format_argv(argv: list[Any], values: Mapping[str, str]) -> list[str]:
    return [str(item).format_map(values) for item in argv]


def _run_command(
    candidate_root: Path,
    log_root: Path,
    name: str,
    argv: list[str],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    started_at = utc_now()
    timed_out = False
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY"))
    }
    try:
        result = subprocess.run(
            argv,
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
        "cwd": str(candidate_root),
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "started_at": started_at,
        "finished_at": utc_now(),
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
) -> list[dict[str, Any]]:
    profiles = constitution.get("validation_profiles") or {}
    values = {
        "base_sha": decision.base_sha,
        "head_sha": decision.head_sha,
        "request_id": request_id,
        "candidate_root": str(candidate_root),
    }
    results: list[dict[str, Any]] = []
    log_root = record_root / ".autobugfix/operator/logs" / request_id / validation_id
    for profile_name in decision.required_profiles:
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            raise OperatorValidationError(f"missing trusted validation profile: {profile_name}")
        default_timeout = int(profile.get("timeout_seconds", 300))
        commands = profile.get("commands") or []
        if not commands:
            raise OperatorValidationError(f"trusted validation profile has no commands: {profile_name}")
        for index, raw in enumerate(commands, start=1):
            if not isinstance(raw, dict) or not isinstance(raw.get("argv"), list):
                raise OperatorValidationError(f"invalid command in validation profile {profile_name}")
            command_name = str(raw.get("name") or f"{profile_name}-{index}")
            argv = _format_argv(raw["argv"], values)
            results.append(
                _run_command(
                    candidate_root,
                    log_root,
                    command_name,
                    argv,
                    int(raw.get("timeout_seconds", default_timeout)),
                )
            )
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
    current_metrics: Mapping[str, float] | None = None,
    allowed_signers: Path | None = None,
    phase: str = "postflight",
    record: bool = True,
    trusted_policy: TrustedPolicy | None = None,
) -> dict[str, Any]:
    record_root = Path(project_root).resolve()
    candidate = Path(candidate_root or project_root).resolve()
    store = OperatorStore(record_root)
    request = store.read_request(request_id)
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
    )
    validation_id = f"validation-{uuid.uuid4().hex[:12]}"
    command_results: list[dict[str, Any]] = []
    regression: dict[str, Any] | None = None

    if not policy.trusted:
        decision.violations.append("bootstrap policy is local feedback only and cannot produce merge-ready authority")
        decision.allowed = False

    if decision.effective_risk in {"medium", "high", "constitutional"} and not request.performance_baseline:
        decision.violations.append("cross-layer or protected change requires a performance baseline")
        decision.allowed = False

    if decision.allowed and run_profiles:
        command_results = run_validation_profiles(
            candidate,
            record_root,
            request_id,
            validation_id,
            decision,
            policy.data,
        )
        if any(not item["passed"] for item in command_results):
            decision.violations.append("one or more trusted validation profile commands failed")
            decision.allowed = False

    if decision.allowed and request.performance_baseline:
        try:
            regression = compare_baseline(
                record_root,
                request.performance_baseline,
                current_metrics or {},
                policy.data.get("metrics") or {},
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
        "regression": regression,
        "current_metrics": {str(key): float(value) for key, value in (current_metrics or {}).items()},
        "created_at": utc_now(),
    }
    report = {**payload, "validation_digest": digest_payload(payload)}
    if record:
        report["record_path"] = str(store.write_validation(request_id, validation_id, report))
    return report
