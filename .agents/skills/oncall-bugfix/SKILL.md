---
name: oncall-bugfix
description: Operator-facing workflow for running Autobugfix on an on-call bug report.
---

# On-Call Bugfix

Use Autobugfix as a control system:

1. Create a task from the human bug report.
2. Add logs, screenshots, API responses, and feedback as context.
3. Run the execution loop in a task worktree.
4. Inspect diff, verifier result, evaluator output, and raw logs.
5. Apply human gates explicitly.
6. Archive accepted or abandoned tasks.
7. Collect accepted evidence into memory proposals.

Do not bypass Autobugfix services by editing task files directly.
