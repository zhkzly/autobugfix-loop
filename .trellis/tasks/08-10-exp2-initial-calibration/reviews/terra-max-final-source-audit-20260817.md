# Terra Max final source audit — 2026-08-17

## Verdict

- Source freeze: `BLOCK`.
- Real two-case calibration: `BLOCK`; do not make model calls on this source.

## Hard blockers

1. The production broker copies execution ledger and SDK receipts under
   `frozen-execution-evidence/`, while the v2 authority expects them directly
   under `run/subject-run`. The fake service test models the wrong layout.
2. The declared workspace-only proof is not OS-enforced. Path topology and
   hidden-path metadata do not prove that authority roots and credentials are
   inaccessible to the direct host worker.
3. `--state-root` is not restricted to trusted Eval state; event append lacks a
   lock and `fsync`; a torn final JSONL line permanently rejects replay.
4. H0 metrics and candidate provenance still accept caller-provided facts.
   The H0 aggregate also leaks transfer/reserve outcome information before
   candidate lock.
5. CLI invokes Operator rollback before the coordinator proves it is in
   `ROLLBACK_AWAITING`, permitting a cross-plane split on an early call.
6. The plan binds apparatus/fixture digests but not authority paths, and init
   does not reopen and revalidate those records. Source-check artifacts prove
   only file existence/hash/size, not passing checks.
7. Report integrity ratios are tautological, while required loop-rescue and
   patch-shape metrics are emitted as null instead of being derived.

## Solid foundations

- New execution routes through v2 and legacy v1 execution is rejected.
- Per-case intent/retry sequencing and calibration terminal states are
  materially improved.
- OCI checking is content-addressed rather than tag-only.
- Source/transfer separation and `not_run` labels are appropriately bounded;
  no larger dataset, Holdout, Pro, statistical, or paper comparator is needed
  for this MVP.

## Required remediation order

1. Publish and consume one canonical trusted broker evidence tree; add a real
   broker-layout interruption test.
2. Do not permit formal workspace-only execution until an external isolation
   attestation proves authority roots and credentials inaccessible at the OS
   layer.
3. Constrain the state root to trusted Eval state and make the journal locked,
   durable, and torn-tail recoverable.
4. Replace caller-authored H0/candidate facts with Eval/Operator record lookup;
   release only permitted source evidence.
5. Require coordinator rollback authorization before Operator mutation.
6. Revalidate apparatus/policy/empty-Memory records at initialization.
7. Derive required report metrics from trusted artifacts, then commit a clean
   apparatus and rerun all source checks.

The reviewer used `gpt-5.6-terra` at reasoning effort `max`, read-only. It made
no source edits, image pulls, qualifications, model calls, approvals, commits,
or external mutations.
