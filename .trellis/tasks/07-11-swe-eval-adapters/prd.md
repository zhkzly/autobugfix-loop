# Implement SWE benchmark Eval adapters

## Goal

Implement production SWE-bench Verified and SWE-bench-Live adapters using
their official container evaluation contracts and seal the independent
16-case Experiment 2 manifest.

## Requirements

- Pin framework, dataset, image, repository, and case revisions. Pin
  SWE-bench-Live tag `v1.0-multi-language-multi-os-benchmarking` at commit
  `c5ea7e48b7b8bb0f4bcbbceb182a09dadfabfc2c` unless preflight identifies a
  documented incompatibility requiring an explicit revision decision.
- Add Docker/resource/cache doctor checks and fail before model calls when the
  official harness cannot run.
- Materialize each full base repository for the real Autobugfix Execution
  loop, with target edits only in task worktrees.
- Run existing visible verifier commands during Execution and submit the
  generated patch to the official hidden harness for authority.
- Preserve source issue text, attachments, screenshots, and reproduction
  evidence; never normalize richer cases down to text-only input.
- Keep gold patches, fail-to-pass/pass-to-pass tests, hidden images, and sealed
  case artifacts outside Writer and Operator views.
- Preflight every selected case using the official gold patch and reject image,
  setup, test, or flakiness failures before sealing.
- Curate 10 visible Verified Optimization cases spanning bugfix, feature, and
  maintenance plus 6 unseen-repository SWE-bench-Live Holdout cases.
- Produce nested 3/8/16 projections and retained official harness logs/results.

## Acceptance Criteria

- [ ] Doctor reports the current WSL Docker blocker without starting an SDK
      role or fabricating a local result.
- [ ] Once Docker is available, at least one official gold prediction passes
      its pinned harness and produces retained logs.
- [ ] Generated patches are scored by the official harness and normalized into
      existing Eval observations without exact-diff authority.
- [ ] Writer/Operator cannot read hidden tests, gold patches, or sealed
      case-level results.
- [ ] All final 16 cases pass gold eligibility and satisfy task-type and
      unseen-repository constraints.
- [ ] Harness errors are separate from unresolved issue results.
- [ ] Adapter, container, schema, CLI, sealing, and real-case tests pass.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This child affects Eval and shared runtime; Eval authority remains
  `EvalRunner` plus the official external harness.
- It does not depend on H_bug or Experiment 1 artifacts.
