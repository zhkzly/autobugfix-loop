from __future__ import annotations

from pathlib import Path

import yaml


def diagnose_run(run_dir: Path) -> Path:
    reports = sorted(run_dir.glob("*/report.yaml"))
    failures = []
    for report in reports:
        data = yaml.safe_load(report.read_text(encoding="utf-8")) or {}
        if data.get("decision") != "pass":
            failures.append(report.parent.name)
    path = run_dir / "diagnosis.md"
    if failures:
        path.write_text("# Eval Diagnosis\n\nFailures:\n" + "\n".join(f"- {item}" for item in failures) + "\n", encoding="utf-8")
    else:
        path.write_text("# Eval Diagnosis\n\nAll cases passed.\n", encoding="utf-8")
    return path
