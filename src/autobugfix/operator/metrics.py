from __future__ import annotations

import hashlib
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.models import utc_now
from autobugfix.git_utils import GitError, rev_parse, run_git
from autobugfix.operator.models import digest_payload


class OperatorMetricsError(RuntimeError):
    pass


BASELINE_ROOT = ".autobugfix-baselines"
SENSITIVE_VALUE_KEY_MARKERS = (
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def portable_profile_values(values: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip()
        value = str(raw_value)
        canonical_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        if not key or not canonical_key:
            raise OperatorMetricsError("experiment profile value key must not be empty")
        if any(marker in canonical_key for marker in SENSITIVE_VALUE_KEY_MARKERS):
            raise OperatorMetricsError(
                f"experiment profile value {key!r} may contain sensitive authority data"
            )
        if not value:
            raise OperatorMetricsError(
                f"experiment profile value {key!r} must not be empty"
            )
        if "\n" in value or "\r" in value:
            raise OperatorMetricsError(
                f"experiment profile value {key!r} must be a single line"
            )
        if (
            value.startswith(("~/", "~\\"))
            or Path(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
        ):
            raise OperatorMetricsError(
                f"experiment profile value {key!r} must be CI-portable, not an absolute local path"
            )
        normalized[key] = value
    return normalized


def baseline_path(project_root: Path, name: str) -> Path:
    if not name or any(
        ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in name
    ):
        raise OperatorMetricsError(f"invalid baseline name: {name!r}")
    return project_root / BASELINE_ROOT / f"{name}.yaml"


def _validated_baseline(data: object, source: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise OperatorMetricsError(f"invalid baseline: {source}")
    result = dict(data)
    stored = result.pop("baseline_digest", None)
    if stored != digest_payload(result):
        raise OperatorMetricsError(f"baseline digest mismatch: {source}")
    expected_input = experiment_input_digest(
        str(result.get("profile") or ""),
        {str(key): str(value) for key, value in (result.get("profile_values") or {}).items()},
    )
    if result.get("input_digest") != expected_input:
        raise OperatorMetricsError(f"baseline input digest mismatch: {source}")
    contract = result.get("profile_contract")
    if not isinstance(contract, dict) or not contract.get("commands"):
        raise OperatorMetricsError(f"baseline has no executable profile contract: {source}")
    if result.get("profile_digest") != digest_payload(contract):
        raise OperatorMetricsError(f"baseline profile digest mismatch: {source}")
    result["baseline_digest"] = stored
    return result


def experiment_input_digest(profile: str, values: Mapping[str, str]) -> str:
    portable = portable_profile_values(values)
    return digest_payload(
        {
            "profile": profile,
            "values": {key: value for key, value in sorted(portable.items())},
        }
    )


def _artifact_digest(path: object) -> str | None:
    if not path:
        return None
    candidate = Path(str(path))
    return hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else None


def derive_metric_receipt(
    *,
    source: str,
    profile: str,
    values: Mapping[str, str],
    base_sha: str,
    head_sha: str,
    patch_digest: str,
    command_results: Sequence[Mapping[str, Any]],
    profile_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not command_results:
        raise OperatorMetricsError("metric receipt requires at least one observed command")
    observations: list[dict[str, Any]] = []
    complete_logs = 0
    passed = 0
    runtime_seconds = 0.0
    for item in command_results:
        stdout_sha = _artifact_digest(item.get("stdout_path"))
        stderr_sha = _artifact_digest(item.get("stderr_path"))
        if stdout_sha is not None and stderr_sha is not None:
            complete_logs += 1
        if bool(item.get("passed")):
            passed += 1
        duration = float(item.get("duration_seconds") or 0.0)
        runtime_seconds += max(duration, 0.0)
        observations.append(
            {
                "name": str(item.get("name") or "command"),
                "exit_code": int(item.get("exit_code", 1)),
                "passed": bool(item.get("passed")),
                "timed_out": bool(item.get("timed_out")),
                "duration_seconds": duration,
                "stdout_sha256": stdout_sha,
                "stderr_sha256": stderr_sha,
            }
        )
    count = len(command_results)
    if not isinstance(profile_contract, Mapping) or not profile_contract.get("commands"):
        raise OperatorMetricsError("metric receipt requires an executable profile contract")
    contract = dict(profile_contract)
    payload = {
        "source": source,
        "profile": profile,
        "profile_contract": contract,
        "profile_digest": digest_payload(contract),
        "input_digest": experiment_input_digest(profile, values),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "patch_digest": patch_digest,
        "metrics": {
            "pass_rate": passed / count,
            "artifact_completeness": complete_logs / count,
            "runtime_seconds": runtime_seconds,
        },
        "commands": observations,
        "created_at": utc_now(),
    }
    return {**payload, "receipt_digest": digest_payload(payload)}


def verify_metric_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(receipt)
    stored = data.pop("receipt_digest", None)
    if not stored or stored != digest_payload(data):
        raise OperatorMetricsError("metric receipt digest mismatch")
    if not isinstance(data.get("metrics"), dict) or not data.get("profile"):
        raise OperatorMetricsError("invalid metric receipt")
    contract = data.get("profile_contract")
    if not isinstance(contract, dict) or data.get("profile_digest") != digest_payload(contract):
        raise OperatorMetricsError("metric receipt profile contract mismatch")
    data["receipt_digest"] = stored
    return data


def record_baseline(
    project_root: Path,
    name: str,
    receipt: Mapping[str, Any],
    *,
    profile_values: Mapping[str, str],
    notes: str = "",
) -> Path:
    observed = verify_metric_receipt(receipt)
    expected_input = experiment_input_digest(str(observed["profile"]), profile_values)
    if observed["input_digest"] != expected_input:
        raise OperatorMetricsError("baseline profile inputs do not match metric receipt")
    path = baseline_path(project_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "base_sha": str(observed["head_sha"]),
        "profile": str(observed["profile"]),
        "profile_contract": dict(observed["profile_contract"]),
        "profile_digest": str(observed["profile_digest"]),
        "profile_values": {str(key): str(value) for key, value in sorted(profile_values.items())},
        "input_digest": str(observed["input_digest"]),
        "metrics": {str(key): float(value) for key, value in sorted(observed["metrics"].items())},
        "receipt_digest": str(observed["receipt_digest"]),
        "commands": list(observed["commands"]),
        "notes": notes,
        "created_at": utc_now(),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(
                {**payload, "baseline_digest": digest_payload(payload)},
                handle,
                sort_keys=False,
            )
    except FileExistsError as exc:
        raise OperatorMetricsError(f"immutable baseline already exists: {path}") from exc
    return path


def read_baseline(project_root: Path, name: str) -> dict[str, Any]:
    path = baseline_path(project_root, name)
    if not path.exists():
        raise OperatorMetricsError(f"missing baseline: {path}")
    return _validated_baseline(
        yaml.safe_load(path.read_text(encoding="utf-8")) or {},
        str(path),
    )


def read_baseline_at_ref(project_root: Path, name: str, ref: str) -> dict[str, Any]:
    relative = baseline_path(Path("."), name).as_posix()
    try:
        resolved_ref = rev_parse(project_root, ref)
        result = run_git(project_root, ["show", f"{resolved_ref}:{relative}"], check=True)
    except GitError as exc:
        raise OperatorMetricsError(
            f"baseline {name!r} is not committed in trusted ref {ref!r}"
        ) from exc
    return _validated_baseline(yaml.safe_load(result.stdout) or {}, f"{resolved_ref}:{relative}")


def baseline_for_request(
    project_root: Path,
    name: str,
    request_base_sha: str,
) -> dict[str, Any]:
    """Load a committed baseline and prove its measured code still matches the request base."""
    data = read_baseline_at_ref(project_root, name, request_base_sha)
    measured_sha = str(data.get("base_sha") or "")
    try:
        measured = rev_parse(project_root, measured_sha)
        request_base = rev_parse(project_root, request_base_sha)
        ancestor = run_git(
            project_root,
            ["merge-base", "--is-ancestor", measured, request_base],
            check=False,
        ).returncode == 0
        changed = run_git(
            project_root,
            ["diff", "--name-only", measured, request_base, "--"],
            check=True,
        ).stdout.splitlines()
    except GitError as exc:
        raise OperatorMetricsError(f"cannot validate baseline Git binding: {exc}") from exc
    if not ancestor:
        raise OperatorMetricsError("performance baseline measured SHA is not an ancestor of request base")
    allowed_prefix = f"{BASELINE_ROOT}/"
    behavior_changes = sorted(
        path.strip() for path in changed if path.strip() and not path.strip().startswith(allowed_prefix)
    )
    if behavior_changes:
        raise OperatorMetricsError(
            "performance baseline is stale; behavior changed after measurement: "
            + ", ".join(behavior_changes)
        )
    return data


def compare_baseline(
    project_root: Path,
    name: str,
    current_receipt: Mapping[str, Any],
    metric_contract: Mapping[str, Any],
    *,
    request_base_sha: str | None = None,
) -> dict[str, Any]:
    data = (
        baseline_for_request(project_root, name, request_base_sha)
        if request_base_sha is not None
        else read_baseline(project_root, name)
    )
    observed = verify_metric_receipt(current_receipt)
    baseline = {str(key): float(value) for key, value in data["metrics"].items()}
    current = {str(key): float(value) for key, value in observed["metrics"].items()}
    failures: list[str] = []
    comparisons: dict[str, dict[str, Any]] = {}

    if observed["profile"] != data["profile"]:
        failures.append(
            f"metric profile {observed['profile']!r} does not match baseline {data['profile']!r}"
        )
    if observed["profile_digest"] != data.get("profile_digest"):
        failures.append("metric profile command contract does not match baseline")
    if observed["input_digest"] != data["input_digest"]:
        failures.append("metric profile inputs do not match baseline inputs")

    for key, raw_rule in metric_contract.items():
        if not isinstance(raw_rule, dict):
            failures.append(f"metric contract for {key} must be a mapping")
            continue
        required = bool(raw_rule.get("required", False))
        if required and key not in baseline:
            failures.append(f"required metric {key} is missing from baseline")
        if required and key not in current:
            failures.append(f"required metric {key} is missing from current result")
        if key not in baseline or key not in current:
            continue
        old = baseline[key]
        value = current[key]
        direction = str(raw_rule.get("direction") or "")
        if direction not in {"higher_is_better", "lower_is_better"}:
            failures.append(f"metric {key} has invalid direction {direction!r}")
            continue
        comparison: dict[str, Any] = {
            "baseline": old,
            "current": value,
            "direction": direction,
        }
        absolute_regression = old - value if direction == "higher_is_better" else value - old
        comparison["regression_absolute"] = absolute_regression
        absolute_threshold = raw_rule.get("max_regression_absolute")
        if absolute_threshold is not None and absolute_regression > float(absolute_threshold):
            failures.append(
                f"{key} regressed by {absolute_regression:.6f} > "
                f"{float(absolute_threshold):.6f} absolute"
            )
        if "minimum" in raw_rule and value < float(raw_rule["minimum"]):
            failures.append(f"{key} current {value} is below minimum {float(raw_rule['minimum'])}")
        if "maximum" in raw_rule and value > float(raw_rule["maximum"]):
            failures.append(f"{key} current {value} is above maximum {float(raw_rule['maximum'])}")
        minimum_for_percent = float(raw_rule.get("min_baseline_for_percent", 0.0))
        if old != 0 and abs(old) >= minimum_for_percent:
            if direction == "higher_is_better":
                regression = ((old - value) / abs(old)) * 100.0
            else:
                regression = ((value - old) / abs(old)) * 100.0
            comparison["regression_percent"] = regression
            threshold = raw_rule.get("max_regression_percent")
            if threshold is not None and regression > float(threshold):
                failures.append(
                    f"{key} regressed by {regression:.2f}% > {float(threshold):.2f}%"
                )
        comparisons[str(key)] = comparison

    unknown = sorted(set(current) - set(metric_contract))
    if unknown:
        failures.append(f"current result contains metrics without trusted contracts: {', '.join(unknown)}")
    return {
        "name": name,
        "baseline_digest": data["baseline_digest"],
        "receipt_digest": observed["receipt_digest"],
        "ok": not failures,
        "failures": failures,
        "comparisons": comparisons,
    }
