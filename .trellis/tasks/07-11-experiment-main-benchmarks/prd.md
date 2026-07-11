# Build experiment integration line and benchmark program

## Goal

Build a real, governed experimental integration line for Autobugfix and use it
to run two reproducible benchmark experiments without allowing Operator or
candidate code to mutate the trusted `main` control plane. The program must
measure whether Operator-guided loop/harness changes improve repository-level
bug fixing and whether the resulting harness can generalize to broader
repository issue resolution.

## Background

Autobugfix is a local, repo-agnostic, Git-controlled loop-engineering and
harness-engineering control system. Execution repairs a configured target
repository in an isolated task worktree. Memory compiles accepted Execution
evidence into reviewed wiki knowledge and skills. Eval reproducibly invokes the
real Execution loop against datasets, hidden oracles, and scorers. Operator
diagnoses and improves Autobugfix itself through governed non-main experiments.
LLM roles are bounded nodes; services, Git facts, deterministic checks,
external approval, and trusted CI own truth.

Current Operator governance creates one candidate branch/worktree per request
and promotes a verified candidate toward `main`, but it has no long-lived
experimental integration branch on which multiple verified Operator changes can
accumulate before final promotion. Current Eval has a canonical case schema and
a real local-Git execution path, but no Defects4J, SWE-bench, or
SWE-bench-Live adapter.

## Requirements

### Project Boundaries

- R1. Preserve the four-loop constitution. This work may extend Eval,
  Operator, shared configuration/runtime, and benchmark-facing Execution
  integration, but must not redefine the purpose or state ownership of any
  loop.
- R2. Production benchmark runs must use the local preview Codex Python SDK.
  They must not use `codex exec`, fake CLI output, or a fake production
  backend.
- R3. Target repository edits must occur only in real task worktrees. Neither
  target repository main checkouts nor the Autobugfix `main` checkout may be
  modified by Writer roles.
- R4. Every run must retain resolved configuration, Git revisions, raw role
  logs, verifier output, events, generated diffs, oracle results, scores,
  diagnosis, timing, and budget usage.

### Experimental Integration Line

- R5. Add named long-lived experimental integration lines. The initial program
  uses independent `experiment/bugfix-main` and `experiment/general-main`
  branches rooted at the same frozen `H0` subject SHA. Experimental lines are
  untrusted accumulation branches, not additional trust roots.
- R6. The trusted Operator host must continue loading the machine constitution
  and transition policy from protected `origin/main`, never from
  an experiment line or a candidate worktree.
- R7. Every Operator request must use a frozen explicit base revision. A
  request targeting the experiment line must branch from its current verified
  head and become stale when that head advances.
- R8. Operator and Writer roles must not directly update experiment-line refs.
  Only a trusted Guard may integrate a patch-bound, currently verified
  candidate after deterministic checks and required experiment/regression
  profiles pass.
- R9. Keep the existing request phases `REQUESTED`, `ACTIVE`, `VERIFIED`, and
  `CLOSED`. Represent experiment accumulation with immutable integration
  receipts and checkpoints instead of creating another large state machine.
- R10. Freeze named checkpoints `H0`, `H_bug`, and `H_general`, including code
  SHA, parent subject SHA, trusted-policy digest, resolved role configuration,
  model, skills, memory snapshot, benchmark manifest digest, budget, and metric
  summary. Both `H_bug` and `H_general` must name `H0` as their experimental
  parent; neither may name the other treatment checkpoint as its parent.
- R11. Rollback must immediately reactivate the last-known-good immutable
  experiment release and preserve Git history through a revert/integration
  receipt. It must not require force-pushing or rewriting `main` history.
- R12. Final promotion must use a frozen checkpoint commit, a fresh full
  validation, a pull request to `main`, existing merge authority, canary, and
  active-release rollback support.

### Model And Cost Policy

- R13. Use `gpt-5.4-mini` for all primary Writer, Evaluator,
  operator-supervisor, operator-writer, and operator-verifier runs so H0/H1
  comparisons do not mix models.
- R14. Do not automatically fall back to `gpt-5.3-codex-spark`. A Spark run is
  a separately authorized cross-model robustness experiment with a separate
  report and must not be merged into the primary score.
- R15. Enforce hard experiment budgets for unique cases, case executions,
  Writer iterations, Operator revisions, Codex calls, wall time, and retries.
  Exhaustion must stop at a durable state with an explicit reason rather than
  silently dropping cases or changing models.
- R16. Use staged release of cost independently in each experiment: no-model
  oracle preflight, then manually approved 3-, 8-, and 16-case Mini waves. The
  first wave authorizes exactly 2 Optimization and 1 sealed Holdout case, at
  most 2 Writer attempts per case, and at most 30 primary Mini calls. Later
  call/time/revision caps are derived from the previous trusted usage receipt.
  A failed stage blocks automatic expansion.
- R16a. Execute the pilot serially with model/case concurrency set to one.
  Finish the trusted experiment-line implementation and deterministic adapter
  preflight before any model run. Process one Operator candidate at a time and
  integrate it before starting a dependent candidate so each measured change
  has an attributable base revision.
- R16b. The shared control-plane, adapters, budget ledger, and reporting code
  for both experiments may be implemented in one development program. The
  experiments themselves are independent studies with the same frozen `H0`
  subject baseline. They may run sequentially to control quota, and Experiment
  2 requires its own explicit human start gate, but Experiment 2 must not use
  `H_bug`, Experiment 1 artifacts, or Experiment 1 Operator changes as its code,
  skill, memory, or configuration baseline.
- R16c. Separate neutral experiment infrastructure from the subject harness
  under study. Infrastructure required to materialize, execute, seal, or score
  cases must not be counted as an Operator capability improvement. Record both
  the trusted evaluation-harness SHA and the subject Autobugfix SHA. If common
  plumbing must change subject-visible behavior, run and report a compatibility
  baseline before freezing `H0`.

### Benchmark Case Contract

- R17. Keep benchmark source, semantic task type, and experiment role as
  independent dimensions: `source`, `task_type`, and `experiment_role`.
- R18. Experiment roles are `optimization`, dynamic `regression`, and
  `sealed_holdout`. A case exposed to Operator can never be reported as sealed
  holdout afterward.
- R19. Split sealed holdout by repository, not only by random case, and prevent
  repository-specific memory or artifacts from crossing into unseen-repository
  evaluation.
- R20. Preserve all source-provided evidence, including issue text, failure
  logs, stack traces, reproduction commands, screenshots, and attachments.
  Benchmarks with less input must not remove richer evidence support from the
  common schema.
- R21. Gold patches, modified-file hints, hidden tests, and holdout case-level
  results belong to the trusted Eval side and must not be copied into Writer or
  Operator views.
- R22. Official tests or benchmark harness results are the primary correctness
  oracle. Gold-diff equality is diagnostic only.

### Experiment 1: Bugfix Harness

- R23. Pin Defects4J 3.0.1 and its framework revision, selected project
  revisions, issue evidence, environment, test commands, and case manifest.
- R24. Reproduce buggy failure and fixed/gold success without an LLM before a
  Defects4J case becomes eligible for a model run.
- R24a. Every selected Experiment 1 case, not only Holdout cases, must pass the
  deterministic buggy-fail/gold-pass eligibility gate before the manifest is
  frozen.
- R25. Record `H0`, expose only Optimization cases to Operator, accumulate
  solved cases into Regression, freeze `H_bug`, and compare `H0` with `H_bug`
  on the same sealed unseen-repository holdout under identical model and budget
  settings.
- R25a. Experiment 1's final manifest contains 16 unique Defects4J cases: 10
  visible Optimization cases and 6 sealed Holdout cases. Holdout repositories
  must be absent from the Optimization set.
- R26. Measure repair success, first-attempt success, loop success, rescue,
  regression, runtime, model calls, Writer iterations, verifier/oracle
  agreement, artifact completeness, and governance violations.
- R26a. H_bug is promotion-eligible only with positive visible net improvement,
  zero Regression and sealed Holdout regression, zero governance violations,
  and budget compliance. Holdout rescue is reported as stronger evidence; an
  unchanged Holdout requires explicit human promotion review.

### Experiment 2: General Issue-Resolution Harness

- R27. Start independently from frozen `H0`, the bugfix-specialized main
  subject before either experiment's Operator optimization. Use visible
  SWE-bench Verified optimization cases to test whether Operator can evolve
  that bugfix-specialized harness into a broader issue-resolution agent and
  produce `H_general`.
- R28. Use pinned, unseen-repository SWE-bench-Live cases as the final sealed
  generalization comparison. SWE-bench Verified results are compatibility
  evidence, not the final scientific holdout claim.
- R28a. Experiment 2's final manifest contains 16 unique SWE cases: 10 visible
  SWE-bench Verified Optimization cases and 6 SWE-bench-Live sealed Holdout
  cases. The manifest must deliberately cover bugfix, feature, and maintenance
  issue types, and Holdout repositories must be absent from the Optimization
  set.
- R29. Compare `H_general` directly with `H0`, not `H_bug`, on identical SWE
  evaluation cases, model, role configuration, memory baseline, call budget,
  and scorer. A Defects4J non-regression check may additionally determine
  whether broader issue-resolution capability preserved the original bugfix
  specialization, but Experiment 1 treatment artifacts must remain excluded.
- R30. Report Experiment 1 and Experiment 2 separately; do not combine visible
  optimization, regression, and sealed holdout into one headline score.
- R30a. Every selected Experiment 2 case must pass its official gold-patch
  harness before the manifest is frozen. Experiment 2 has its own budget,
  checkpoints, artifacts, and report; it must not consume Experiment 1's
  remaining budget implicitly.
- R30b. Experiment 2 succeeds only if `H_general` has positive net improvement
  over `H0` on visible SWE cases, rescues at least one sealed SWE Holdout case,
  produces no sealed Holdout regression, and preserves coverage of bugfix,
  feature, and maintenance task types. Defects4J performance is a secondary
  non-regression report, not Experiment 2 training input.

## Acceptance Criteria

- [ ] A trusted `main` checkout can create and inspect independent
      `experiment/bugfix-main` and `experiment/general-main` lines without
      checking either out over `main` or treating either as trusted policy.
- [ ] A verified per-request candidate can be integrated only by the Guard,
      producing a digest-bound integration receipt and a reproducible
      checkpoint.
- [ ] A stale, dirty, out-of-scope, policy-changing, unapproved, or
      regression-failing candidate is rejected without advancing the
      experiment line.
- [ ] Rollback restores the previous immutable experiment release and records
      the required Git revert intent/history.
- [ ] Defects4J buggy and fixed/gold revisions pass the no-model reproducibility
      gate using real framework commands.
- [ ] A real `gpt-5.4-mini` Execution run edits only an isolated target
      worktree and produces complete logs, events, diffs, verifier output, and
      oracle artifacts.
- [ ] Experiment 1 produces frozen `H0` and `H_bug` reports with separated
      Optimization, Regression, sealed Holdout, and unseen-repository metrics.
- [ ] Experiment 2 produces a frozen `H_general` report comparing it directly
      with H0 on the independent SWE manifest; any Defects4J non-regression
      result is clearly labeled secondary.
- [ ] Experiment 2 requires a separate start/budget record and proves its base
      is the frozen `H0` SHA; no `H_bug` commit, skill, memory, configuration, or
      case artifact is present in its baseline.
- [ ] Budget exhaustion is deterministic, observable, and cannot trigger a
      model fallback or unrecorded retry.
- [ ] The pilot scheduler runs one benchmark case and one Operator candidate at
      a time; run records prove the base/checkpoint used by every invocation.
- [ ] Production CLI rejects fake model mode; test-only injected backends
      remain limited to unit/integration tests.
- [ ] `uv run pytest -q` passes.
- [ ] `uv run python -m compileall -q src tests scripts` passes.
- [ ] `git diff --check` passes.
- [ ] `uv run python scripts/validate_role_skills.py` passes when the validator
      is available.
- [ ] At least one pinned public real-repository benchmark case completes the
      full production Codex SDK Execution/Eval path before the implementation
      is accepted.

## Out Of Scope

- Full Defects4J, SWE-bench Verified, or SWE-bench-Live leaderboard runs.
- Training or fine-tuning either available model.
- Treating any experiment line as a production or constitutional authority.
- Automatically publishing claims from a statistically small pilot as a full
  benchmark result.
- Mixing `gpt-5.3-codex-spark` and `gpt-5.4-mini` in the primary paired
  comparison.

## Task Map

- `07-11-experiment-integration-lines`: trusted named lines, checkpoints,
  integration receipts, budget/usage authority, rollback, and CLI.
- `07-11-defects4j-eval-adapter`: pinned Defects4J adapter, eligibility
  preflight, sealing, and the 16-case Experiment 1 manifest.
- `07-11-swe-eval-adapters`: official container adapters, eligibility
  preflight, sealing, and the 16-case Experiment 2 manifest.
- `07-11-bugfix-harness-experiment`: independent H0-to-H_bug 3/8/16 production
  run and report.
- `07-11-general-agent-experiment`: independent H0-to-H_general 3/8/16
  production run and report.

## Notes

- User accepts broad refactoring and external benchmark integration when
  required, provided the result is real and executable rather than a mock.
- This experiment protocol is mutable project research planning. It is not the
  immutable four-loop project constitution.
