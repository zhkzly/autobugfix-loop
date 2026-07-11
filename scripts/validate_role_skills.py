from __future__ import annotations

from pathlib import Path


REQUIRED = [
    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
    ".agents/role-skills/execution/writer/autobugfix-writer/SKILL.md",
    ".agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md",
    ".agents/role-skills/memory/maintainer/autobugfix-memory-maintainer/SKILL.md",
    ".agents/role-skills/eval/judge/autobugfix-eval-judge/SKILL.md",
    ".agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md",
    ".agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md",
    ".agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md",
    ".agents/skills/oncall-bugfix/SKILL.md",
    ".agents/skills/autobugfix-eval-operator/SKILL.md",
    ".agents/skills/autobugfix-operator-governance/SKILL.md",
]

REQUIRED_MARKERS = {
    ".agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md": (
        "H_bug",
        "H_general",
        "3 -> 8 -> 16",
        "sealed Holdout",
    ),
    ".agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md": (
        "authority SQLite",
        "H_bug",
        "H_general",
        "gpt-5.4-mini",
    ),
    ".agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md": (
        "H0",
        "budget",
        "sealed Holdout",
        "Guard-owned facts",
    ),
    ".agents/skills/autobugfix-operator-governance/SKILL.md": (
        "governed benchmark study",
        "3 -> 8 -> 16",
        "Defects4J",
        "SWE-bench Verified",
        "SWE-bench-Live",
    ),
    ".agents/skills/autobugfix-eval-operator/SKILL.md": (
        "Governance v4",
        "Defects4J",
        "SWE-bench Verified",
        "SWE-bench-Live",
        "10 Optimization",
        "6 unseen-repository",
    ),
}


def main() -> int:
    root = Path.cwd()
    missing = [rel for rel in REQUIRED if not (root / rel).exists()]
    invalid = []
    missing_markers: list[tuple[str, str]] = []
    for rel in REQUIRED:
        path = root / rel
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---") or "# " not in text:
                invalid.append(rel)
            for marker in REQUIRED_MARKERS.get(rel, ()):
                if marker not in text:
                    missing_markers.append((rel, marker))
    if missing or invalid or missing_markers:
        for rel in missing:
            print(f"missing: {rel}")
        for rel in invalid:
            print(f"invalid: {rel}")
        for rel, marker in missing_markers:
            print(f"missing marker: {rel}: {marker}")
        return 1
    print("role skills valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
