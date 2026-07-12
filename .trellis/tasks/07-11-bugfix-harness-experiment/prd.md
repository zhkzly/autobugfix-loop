# Measure the frozen bugfix harness

## Goal

Run a real, descriptive capability evaluation of the current
bugfix-specialized Autobugfix `H0` on 16 pre-registered Defects4J cases.

This is not an Operator optimization study. It answers: given a repository and
an issue, how often does the existing complete Execution loop produce a final
patch that passes the dataset's independent official evaluator?

## Requirements

- Depend on the completed Defects4J adapter and frozen H0 subject definition.
- Freeze H0 subject SHA, model, role configuration, skills, read-only Memory
  snapshot, case manifest, visible verifier policy, official scorer, and budget
  before the first formal case starts.
- Pre-register 16 unique Defects4J cases independently of model outcomes.
- Use only `gpt-5.4-mini`, the production Python Codex SDK, case concurrency
  one, and the same bounded Writer attempt/call/time budget for every case.
- For every case, run the complete existing Execution loop on the buggy Git
  snapshot using only issue/evidence and predeclared visible checks.
- Freeze the final patch and Execution trace before invoking the official
  Defects4J evaluator in a fresh evaluation checkout.
- Never expose fixed code, developer patch, private tests, official verdict,
  or scorer diagnosis to Writer, Execution evaluator, Memory, or Operator.
- Do not rerun, tune, or exclude a valid case because its official score fails.
- Classify infrastructure failures separately from valid unsuccessful repairs.
- Retain complete immutable artifacts and report both aggregate and case-level
  outcomes after the full wave completes.

## Acceptance Criteria

- [x] H0 and the 16-case manifest are immutable and digest-complete before the
      formal run begins.
- [x] All production role calls use `gpt-5.4-mini`; no fake or model fallback
      is present.
- [x] Each case has exactly one final frozen submission and one later official
      score, with bounded internal Execution attempts only.
- [x] Official evaluator output cannot change the patch, task state, trace, or
      attempt count and cannot trigger another Writer call.
- [x] Target main checkouts remain unchanged; all Writer edits occur in task
      worktrees and official scoring occurs in separate clean checkouts.
- [x] The report separates repaired, unrepaired, harness error, first-attempt,
      loop-rescued, runtime/cost, verifier/oracle agreement, and artifact
      completeness metrics.
- [x] Every launched case retains real SDK logs, Execution events, generated
      diff, visible verifier evidence, official evaluator output, and a
      noninterference receipt.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- Experiment 2 is a separate `H0 -> H_general` Operator evolution study. It
  does not descend from or consume Experiment 1 outcomes.
