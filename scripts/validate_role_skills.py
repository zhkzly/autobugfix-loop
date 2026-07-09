from __future__ import annotations

from pathlib import Path


REQUIRED = [
    ".agents/role-skills/base/autobugfix-runtime-base/SKILL.md",
    ".agents/role-skills/execution/writer/autobugfix-writer/SKILL.md",
    ".agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md",
    ".agents/role-skills/memory/maintainer/autobugfix-memory-maintainer/SKILL.md",
    ".agents/role-skills/eval/judge/autobugfix-eval-judge/SKILL.md",
    ".agents/skills/oncall-bugfix/SKILL.md",
    ".agents/skills/autobugfix-eval-operator/SKILL.md",
]


def main() -> int:
    root = Path.cwd()
    missing = [rel for rel in REQUIRED if not (root / rel).exists()]
    invalid = []
    for rel in REQUIRED:
        path = root / rel
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---") or "# " not in text:
                invalid.append(rel)
    if missing or invalid:
        for rel in missing:
            print(f"missing: {rel}")
        for rel in invalid:
            print(f"invalid: {rel}")
        return 1
    print("role skills valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
