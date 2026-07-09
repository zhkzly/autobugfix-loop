from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.models import utc_now
from autobugfix.operator.models import digest_payload


class OperatorMetricsError(RuntimeError):
    pass


def parse_metric(values: list[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise OperatorMetricsError(f"metric must be key=value: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise OperatorMetricsError("metric key must not be empty")
        metrics[key] = float(value)
    return metrics


def baseline_path(project_root: Path, name: str) -> Path:
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in name):
        raise OperatorMetricsError(f"invalid baseline name: {name!r}")
    return project_root / ".autobugfix-baselines" / f"{name}.yaml"


def record_baseline(
    project_root: Path,
    name: str,
    metrics: Mapping[str, float],
    *,
    base_sha: str,
    artifacts: list[str] | None = None,
    notes: str = "",
) -> Path:
    if not metrics:
        raise OperatorMetricsError("baseline metrics must not be empty")
    path = baseline_path(project_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "base_sha": base_sha,
        "metrics": {str(key): float(value) for key, value in sorted(metrics.items())},
        "artifacts": list(artifacts or []),
        "notes": notes,
        "created_at": utc_now(),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            yaml.safe_dump({**payload, "baseline_digest": digest_payload(payload)}, handle, sort_keys=False)
    except FileExistsError as exc:
        raise OperatorMetricsError(f"immutable baseline already exists: {path}") from exc
    return path


def read_baseline(project_root: Path, name: str) -> dict[str, Any]:
    path = baseline_path(project_root, name)
    if not path.exists():
        raise OperatorMetricsError(f"missing baseline: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("metrics"), dict):
        raise OperatorMetricsError(f"invalid baseline: {path}")
    stored = data.pop("baseline_digest", None)
    if stored != digest_payload(data):
        raise OperatorMetricsError(f"baseline digest mismatch: {path}")
    data["baseline_digest"] = stored
    return data


def compare_baseline(
    project_root: Path,
    name: str,
    current_metrics: Mapping[str, float],
    metric_contract: Mapping[str, Any],
) -> dict[str, Any]:
    data = read_baseline(project_root, name)
    baseline = {str(key): float(value) for key, value in data["metrics"].items()}
    current = {str(key): float(value) for key, value in current_metrics.items()}
    failures: list[str] = []
    comparisons: dict[str, dict[str, Any]] = {}

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
        comparison: dict[str, Any] = {"baseline": old, "current": value, "direction": direction}
        if "minimum" in raw_rule and value < float(raw_rule["minimum"]):
            failures.append(f"{key} current {value} is below minimum {float(raw_rule['minimum'])}")
        if "maximum" in raw_rule and value > float(raw_rule["maximum"]):
            failures.append(f"{key} current {value} is above maximum {float(raw_rule['maximum'])}")
        if old != 0:
            if direction == "higher_is_better":
                regression = ((old - value) / abs(old)) * 100.0
            else:
                regression = ((value - old) / abs(old)) * 100.0
            comparison["regression_percent"] = regression
            threshold = raw_rule.get("max_regression_percent")
            if threshold is not None and regression > float(threshold):
                failures.append(f"{key} regressed by {regression:.2f}% > {float(threshold):.2f}%")
        comparisons[str(key)] = comparison

    unknown = sorted(set(current) - set(metric_contract))
    if unknown:
        failures.append(f"current result contains metrics without trusted contracts: {', '.join(unknown)}")
    return {
        "name": name,
        "baseline_digest": data["baseline_digest"],
        "ok": not failures,
        "failures": failures,
        "comparisons": comparisons,
    }
