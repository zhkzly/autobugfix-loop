Active task: .trellis/tasks/08-10-exp2-initial-calibration

You are an independent runtime/operability reviewer. This is a READ-ONLY audit:
do not edit files, run real benchmark cases, start the task, or create trusted
study state.

Determine whether the current plan can actually be executed from the committed
Exp2 implementation, rather than merely described. Inspect the isolated
worktree at `.worktrees/exp2-execution-only`, commit `ebb994f`, the current
PRD, archived design/implement documents, CLI help, coordinator, service,
subject broker, configs, and tests.

Verify:

1. Exact commands and prerequisites from study init through calibration
   resume/report.
2. Which inputs/manifests/receipts do not yet exist.
3. Whether a disposable workspace-only environment can be supplied on this
   host without weakening the fail-closed contract.
4. What can run automatically and where human/budget/approval stops occur.
5. Whether the current H0-only adapter can validate the promised full loop.
6. Recovery/idempotency after SDK, Docker, scorer, or process failures.
7. The smallest smoke/calibration sequence that produces decisive evidence
   without consuming formal cases.

Give severity-ranked findings and an executable preflight checklist. End with
ALLOW, REVISE, or BLOCK and the exact blockers to a real first case.
