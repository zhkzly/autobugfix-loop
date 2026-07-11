# Implement governed experiment integration lines

## Goal

Add real named experimental integration lines, immutable checkpoints, staged
budget authority, compare-and-swap integration receipts, and non-rewriting
rollback while keeping protected `main` as the only trust root.

## Requirements

- Preserve the four existing Operator Request phases. Studies, lines,
  integrations, checkpoints, grants, and usage are typed child records.
- `OperatorGovernanceService` and its external SQLite store own experiment
  authority; candidate worktrees contain no authoritative records.
- Support independent `experiment/bugfix-main` and
  `experiment/general-main` lines rooted at one frozen H0 cohort. Cohort peers
  must match Git, harness, policy, config, role, model, skills, and Memory
  digests.
- Freeze visible Optimization manifest and Memory as separate read-only H0
  snapshots. Sealed Holdout identities/results remain external Guard state.
- Requests name an experiment line and freeze its current SHA/generation
  instead of implicitly using the control checkout's `HEAD`.
- Only the trusted Guard may integrate a clean, currently VERIFIED candidate.
  Integration must fail on a stale line, patch/policy/scope drift, missing
  approval, missing experiment receipt, failed validation, or exhausted
  budget.
- Update line refs atomically with expected-old-SHA compare-and-swap and retain
  raw integration evidence.
- Freeze H0/H_bug/H_general checkpoints with subject, harness, policy, config,
  model, skill, memory, manifest, budget, and metric digests.
- Accept checkpoint metrics only through content-addressed, digest-protected
  `StudyMetricRecord` IDs registered by the benchmark Guard; reject arbitrary
  paths and case-level payloads.
- Require H_bug and H_general to independently name H0 as parent.
- Implement `3 -> 8 -> 16` grants over exact case IDs with one-case
  concurrency, call/attempt/revision/time limits, and host-observed usage.
- A provider/quota failure must stop without switching from `gpt-5.4-mini` to
  `gpt-5.3-codex-spark`.
- Rollback reactivates a known immutable release and restores its tree through
  a new history-preserving commit; no reset or force push.
- CLI and UI code use service/projection APIs and never write SQLite or Git
  refs directly.
- Budget CLI approval requires a real interactive terminal confirmation bound
  to the canonical request digest.

## Acceptance Criteria

- [x] Two lines can be initialized from the same H0 without checking either
      out over main or trusting their constitution.
- [x] A request branches from the exact line head and becomes stale after a
      competing integration.
- [x] Only a verified, clean, in-scope, approved, budget-authorized candidate
      can advance a line and produce a digest-bound IntegrationReceipt.
- [x] Concurrent/stale compare-and-swap integration leaves the line unchanged.
- [x] H_bug/H_general lineage enforcement rejects either checkpoint when its
      parent is not H0.
- [x] Budget usage is atomically reserved/finalized and exhaustion fails before
      another SDK call starts.
- [x] Rollback restores the checkpoint tree/release without history rewrite.
- [x] Existing Operator requests, experiments, promotion, canary, and rollback
      records migrate without loss.
- [x] Focused store/policy/service/CLI tests and the full project validation
      commands pass.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This child affects Operator and shared runtime. State owner is
  `OperatorGovernanceService` plus trusted Git facts.
- Benchmark adapters and real experiment runs are separate children.
