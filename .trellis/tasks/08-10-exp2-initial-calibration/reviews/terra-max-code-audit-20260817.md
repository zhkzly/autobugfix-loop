# Terra Max code audit — 2026-08-17

## Decision

`BLOCK`

The Phase 1–2 ledger hardening is materially better: strict schemas,
calibration-to-pilot binding, retry submission lineage, replay ordering,
one-case scheduling, H0 terminalization, and eight focused unit tests are
present. The MVP is still not executable as required.

## Blocking findings

1. The v2 coordinator is not connected to the real Eval path. It accepts
   arbitrary executor/reconciler callbacks, while the CLI still constructs the
   v1 coordinator and batches stages in memory. Existing official Eval methods
   are never called by v2.
2. Official truth remains caller-supplied. A callback can return a terminal
   receipt directly; self-consistent digests do not prove that Eval issued the
   result, froze the submission, or verified noninterference.
3. Open-intent reconciliation is delegated to a callback rather than derived
   from the trusted run root. The focused test uses a synthetic lambda report,
   not an Eval artifact lookup.
4. Image qualification and workspace isolation remain assertions rather than
   trusted runtime gates. Execution readiness trusts a caller-authored status,
   terminal image identity is optional and not compared to the frozen case,
   and most protected roots are not consumed during execution.
5. The full MVP cannot lock or roll back a candidate because
   `OperatorGovernanceService` does not yet implement the verifier/exporter
   methods required by the v2 coordinator.

## Minimal remediation order

1. Wire a typed v2 CLI/service adapter to `EvalBenchmarkService`; make v1
   explicitly audit-only for new studies.
2. Generate and reconcile receipts only from trusted Eval artifacts keyed by
   intent/run ID; do not permit arbitrary production callbacks.
3. Bind actual image, preflight, worktree, and protected-root evidence to every
   terminal receipt.
4. Add service-bound fake-backend integration tests for interruption,
   reconciliation, and scorer-only retry.
5. Implement Operator transition and rollback exporters before calling the full
   MVP executable.

The reviewer used `gpt-5.6-terra` with reasoning effort `max` in a read-only
Trellis worker. It did not run real cases, pull images, call benchmark models,
approve budgets, or edit source.
