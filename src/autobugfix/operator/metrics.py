from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.models import utc_now


class OperatorMetricsError(RuntimeError):
    pass


def parse_metric(values: list[str]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise OperatorMetricsError(f"metric must be key=value: {item}")
        key, value = item.split("=", 1)
        metrics[key.strip()] = float(value)
    return metrics


def baseline_path(project_root: Path, name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in name).strip("-") or "baseline"
    return project_root / ".autobugfix/operator/baselines" / f"{safe}.yaml"


def record_baseline(project_root: Path, name: str, metrics: dict[str, float], notes: str = "") -> Path:
    path = baseline_path(project_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"name": name, "metrics": metrics, "notes": notes, "created_at": utc_now()},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def compare_baseline(
    project_root: Path,
    name: str,
    current_metrics: dict[str, float],
    max_regression_percent: dict[str, float] | None = None,
    min_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    path = baseline_path(project_root, name)
    if not path.exists():
        raise OperatorMetricsError(f"missing baseline: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    baseline = data.get("metrics") or {}
    if not isinstance(baseline, dict):
        raise OperatorMetricsError(f"baseline metrics must be a mapping: {path}")
    max_regression_percent = max_regression_percent or {}
    min_metrics = min_metrics or {}
    failures: list[str] = []
    comparisons: dict[str, dict[str, float]] = {}
    for key, current in current_metrics.items():
        old = float(baseline.get(key, current))
        comparisons[key] = {"baseline": old, "current": current}
        if key in min_metrics and current < min_metrics[key]:
            failures.append(f"{key} current {current} is below minimum {min_metrics[key]}")
        if key in max_regression_percent and old != 0:
            regression = ((current - old) / abs(old)) * 100.0
            comparisons[key]["regression_percent"] = regression
            if regression > max_regression_percent[key]:
                failures.append(f"{key} regressed by {regression:.2f}% > {max_regression_percent[key]:.2f}%")
    return {
        "name": name,
        "ok": not failures,
        "failures": failures,
        "comparisons": comparisons,
    }
