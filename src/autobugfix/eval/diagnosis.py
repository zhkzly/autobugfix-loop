from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml


def diagnose_run(run_dir: Path) -> Path:
    reports = sorted(run_dir.glob("*/report.yaml"))
    grouped: dict[str, list[str]] = defaultdict(list)
    for report in reports:
        data = yaml.safe_load(report.read_text(encoding="utf-8")) or {}
        if data.get("decision") == "pass":
            continue
        stage = str(data.get("failure_stage") or "unknown")
        grouped[stage].append(report.parent.name)
    path = run_dir / "diagnosis.md"
    if not grouped:
        path.write_text("# Eval Diagnosis\n\nAll cases passed.\n", encoding="utf-8")
        return path
    lines = ["# Eval Diagnosis", ""]
    for stage, case_ids in sorted(grouped.items()):
        lines.extend([f"## {stage}", *[f"- {case_id}" for case_id in case_ids], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
