from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from autobugfix.git_utils import GitError, run_git
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


_SANDBOX_ENV_ALLOWLIST = (
    "CI",
    "FORCE_COLOR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "TERM",
    "TZ",
    "USER",
)
_HOME_AUTHORITY_DIRS = (
    ".aws",
    ".azure",
    ".codex",
    ".config/containers",
    ".config/gcloud",
    ".config/gh",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
)


def _host_authority_roots(host_environment: Mapping[str, str]) -> tuple[Path, ...]:
    roots = [Path("/run")]
    home = Path(host_environment.get("HOME") or str(Path.home())).expanduser()
    roots.extend(home / relative for relative in _HOME_AUTHORITY_DIRS)
    runner_temp = host_environment.get("RUNNER_TEMP")
    if runner_temp:
        roots.append(Path(runner_temp))
    for key in ("GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_STEP_SUMMARY"):
        value = host_environment.get(key)
        if value:
            roots.append(Path(value).parent)
    return tuple(roots)


def _prepare_bind_destination(candidate_root: Path, destination: Path) -> Path:
    lexical = Path(os.path.abspath(destination))
    try:
        relative = lexical.relative_to(candidate_root)
    except ValueError as exc:
        raise OperatorValidationError(
            f"read-only sandbox destination is outside candidate: {lexical}"
        ) from exc
    cursor = candidate_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise OperatorValidationError(
                f"read-only sandbox destination traverses a symlink: {cursor}"
            )
    if lexical.exists() and not lexical.is_dir():
        raise OperatorValidationError(
            f"read-only sandbox destination is not a directory: {lexical}"
        )
    lexical.mkdir(parents=True, exist_ok=True)
    return lexical


def _sandbox_directory_args(masked_root: Path, destination: Path) -> list[str]:
    """Recreate directory mountpoints hidden by a parent tmpfs."""
    try:
        relative = destination.relative_to(masked_root)
    except ValueError:
        return []
    args: list[str] = []
    cursor = masked_root
    for part in relative.parts:
        cursor /= part
        args.extend(["--dir", str(cursor)])
    return args


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
    candidate_root = candidate_root.resolve()
    started_at = utc_now()
    started_monotonic = time.monotonic()
    timed_out = False
    host_environment = dict(os.environ)
    environment = {
        key: host_environment[key]
        for key in _SANDBOX_ENV_ALLOWLIST
        if key in host_environment
    }
    environment["HOME"] = "/tmp/autobugfix-home"
    environment["TMPDIR"] = "/tmp"
    environment["XDG_CACHE_HOME"] = "/tmp/autobugfix-home/.cache"
    environment["XDG_CONFIG_HOME"] = "/tmp/autobugfix-home/.config"
    environment["XDG_DATA_HOME"] = "/tmp/autobugfix-home/.local/share"
    environment["UV_NO_SYNC"] = "1"
    candidate_src = candidate_root / "src"
    python_path = environment.get("PYTHONPATH")
    project_python_path = os.pathsep.join((str(candidate_src), str(candidate_root)))
    environment["PYTHONPATH"] = (
        f"{project_python_path}{os.pathsep}{python_path}"
        if python_path
        else project_python_path
    )
    sandboxed = False
    executed_argv = list(argv)
    if process_sandbox not in {"auto", "bubblewrap", "none"}:
        raise OperatorValidationError(f"unsupported process sandbox: {process_sandbox}")
    inherited_sandbox = host_environment.get("AUTOBUGFIX_PROCESS_SANDBOX") == "bubblewrap"
    bubblewrap = shutil.which("bwrap") if process_sandbox in {"auto", "bubblewrap"} else None
    if inherited_sandbox:
        sandboxed = True
        environment["AUTOBUGFIX_PROCESS_SANDBOX"] = "bubblewrap"
    elif bubblewrap:
        sandboxed = True
        candidate_venv = candidate_root / ".venv"
        host_home = Path(host_environment.get("HOME") or str(Path.home())).expanduser().resolve()
        wrapper = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            str(host_home),
            "--dir",
            "/tmp/autobugfix-home",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        if not network_access:
            wrapper.append("--unshare-net")
        # Mount the broad candidate first. Authority masks and exact runtime
        # grants must remain visible as more-specific overlays.
        wrapper.extend(_sandbox_directory_args(host_home, candidate_root))
        wrapper.extend(["--bind", str(candidate_root), str(candidate_root)])
        masked_roots: set[Path] = set()
        for root in (*_host_authority_roots(host_environment), *hidden_roots):
            resolved = root.resolve()
            if resolved.exists() and resolved not in masked_roots:
                try:
                    resolved.relative_to(host_home)
                    continue
                except ValueError:
                    pass
                try:
                    candidate_root.relative_to(resolved)
                except ValueError:
                    wrapper.extend(["--tmpfs", str(resolved)])
                    masked_roots.add(resolved)
        for root in writable_roots:
            resolved = root.resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            wrapper.extend(_sandbox_directory_args(host_home, resolved))
            wrapper.extend(["--bind", str(resolved), str(resolved)])
        for source, destination in read_only_binds:
            source = source.resolve()
            if not source.exists():
                raise OperatorValidationError(f"read-only sandbox bind does not exist: {source}")
            destination = _prepare_bind_destination(candidate_root, destination)
            wrapper.extend(["--ro-bind", str(source), str(destination)])
            if destination == candidate_venv:
                # Console scripts retain their trusted-venv shebang. Re-expose
                # only that exact source path after masking HOME or /tmp.
                for masked_root in (host_home, Path("/tmp")):
                    try:
                        relative_source = source.relative_to(masked_root)
                    except ValueError:
                        continue
                    if not relative_source.parts:
                        raise OperatorValidationError(
                            "trusted runtime source cannot equal a masked authority root: "
                            f"{source}"
                        )
                    wrapper.extend(_sandbox_directory_args(masked_root, source))
                    wrapper.extend(["--ro-bind", str(source), str(source)])
                    break
                environment["VIRTUAL_ENV"] = str(destination)
                environment["UV_PROJECT_ENVIRONMENT"] = str(destination)
                runtime_python = source / "bin/python"
                runtime_link = runtime_python
                visited_links: set[Path] = set()
                while runtime_link.is_symlink() and runtime_link not in visited_links:
                    visited_links.add(runtime_link)
                    link_target = Path(os.readlink(runtime_link))
                    if not link_target.is_absolute():
                        link_target = runtime_link.parent / link_target
                    link_target = Path(os.path.abspath(link_target))
                    try:
                        link_target.relative_to(source)
                    except ValueError:
                        pass
                    else:
                        runtime_link = link_target
                        continue
                    runtime_source_prefix = link_target.resolve().parent.parent
                    runtime_destination_prefix = link_target.parent.parent
                    for masked_root in (host_home, Path("/tmp")):
                        directory_args = _sandbox_directory_args(
                            masked_root, runtime_destination_prefix
                        )
                        if not directory_args:
                            continue
                        wrapper.extend(
                            directory_args
                        )
                        wrapper.extend(
                            [
                                "--ro-bind",
                                str(runtime_source_prefix),
                                str(runtime_destination_prefix),
                            ]
                        )
                        break
                    break
        command_argv = list(argv)
        executable = shutil.which(command_argv[0], path=host_environment.get("PATH"))
        if executable:
            resolved_executable = Path(executable).resolve()
            try:
                resolved_executable.relative_to(host_home)
            except ValueError:
                pass
            else:
                executable_prefix = resolved_executable.parent.parent
                if (
                    resolved_executable.name.startswith("python")
                    and (executable_prefix / "lib").is_dir()
                ):
                    wrapper.extend(_sandbox_directory_args(host_home, executable_prefix))
                    wrapper.extend(
                        ["--ro-bind", str(executable_prefix), str(executable_prefix)]
                    )
                    command_argv[0] = str(resolved_executable)
                else:
                    sandbox_executable = (
                        Path("/tmp/autobugfix-bin") / resolved_executable.name
                    )
                    wrapper.extend(
                        [
                            "--dir",
                            str(sandbox_executable.parent),
                            "--ro-bind",
                            str(resolved_executable),
                            str(sandbox_executable),
                        ]
                    )
                    sandbox_bin = str(sandbox_executable.parent)
                    inherited_path = environment.get("PATH")
                    environment["PATH"] = (
                        f"{sandbox_bin}{os.pathsep}{inherited_path}"
                        if inherited_path
                        else sandbox_bin
                    )
                    command_argv[0] = str(sandbox_executable)
        if network_access:
            # On systemd-resolved hosts /etc/resolv.conf symlinks into /run,
            # which sandbox authority masking replaces with a tmpfs; recreate
            # the resolved target so network-enabled commands keep DNS.
            resolv = Path("/etc/resolv.conf")
            try:
                resolv_real = resolv.resolve(strict=False)
            except OSError:
                resolv_real = resolv
            if resolv_real != resolv and resolv_real.is_file():
                wrapper.extend(
                    [
                        "--dir",
                        str(resolv_real.parent),
                        "--ro-bind",
                        str(resolv_real),
                        str(resolv_real),
                    ]
                )
        git_meta_roots: list[Path] = []
        for git_query in (
            ["rev-parse", "--absolute-git-dir"],
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ):
            try:
                meta = Path(
                    run_git(candidate_root, git_query, check=True).stdout.strip()
                ).resolve()
            except (GitError, OSError):
                continue
            if meta.is_dir() and meta not in git_meta_roots:
                git_meta_roots.append(meta)
        for meta_root in git_meta_roots:
            try:
                meta_root.relative_to(candidate_root)
            except ValueError:
                pass
            else:
                continue
            wrapper.extend(_sandbox_directory_args(host_home, meta_root))
            wrapper.extend(["--ro-bind", str(meta_root), str(meta_root)])
        environment["AUTOBUGFIX_PROCESS_SANDBOX"] = "bubblewrap"
        wrapper.extend(["--chdir", str(candidate_root), "--"])
        executed_argv = [*wrapper, *command_argv]
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
        "sandbox_inherited": inherited_sandbox,
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
        production_root=record_root if request.experiment_line_id else None,
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
