# Implement Defects4J Eval adapter

## Goal

Implement a production Defects4J adapter that materializes real Java
repositories for the existing Execution loop, runs official tests as hidden
authority, and seals an eligible 16-case Experiment 1 manifest.

## Requirements

- Pin Defects4J tag `v3.0.1` and commit
  `6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09` plus every selected project
  revision and runtime dependency.
- Add benchmark doctor output for Java 11, Git, Subversion, Perl, `cpanm`,
  timezone, cache space, and framework initialization.
- Materialize the complete buggy repository as a real Git target compatible
  with task worktrees without exposing fixed code or modified-class hints.
- Generate target `.autobugfix/config.yaml` through the existing Eval/service
  boundary and run real Defects4J triggering/full tests.
- Preserve issue text, failure output, stack trace, reproduction command, and
  available attachments as visible evidence.
- Keep fixed revision, gold patch, hidden metadata, and Holdout case artifacts
  in the trusted Eval root.
- Preflight every selected case without an LLM: buggy must fail as expected and
  fixed/gold must pass; unstable cases are rejected/replaced.
- Curate 16 cases: 10 Optimization plus 6 Holdout cases whose repositories are
  absent from Optimization, with nested 3/8/16 wave projections.
- Official tests determine correctness; diff equality remains diagnostic.
- Retain setup, framework, compile, test, oracle, diff, timing, and eligibility
  artifacts.

## Acceptance Criteria

- [ ] Doctor fails before mutation/SDK use when any required runtime is absent.
- [ ] At least one pinned case completes buggy-fail and gold-pass preflight
      using official Defects4J commands.
- [ ] Materialized target main remains unchanged while Execution edits only its
      task worktree.
- [ ] Writer/Operator views cannot read fixed revision, gold patch,
      modified-class hints, or sealed case artifacts.
- [ ] All final 16 cases have immutable eligibility receipts and valid
      repository-group splits.
- [ ] A generated alternative patch passes when official tests pass even when
      it differs from the gold diff.
- [ ] Harness/setup failures are distinct from model repair failures.
- [ ] Adapter, schema, CLI, isolation, and real-case acceptance tests pass.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This child affects Eval and benchmark-facing Execution configuration. Eval
  state owner remains `EvalRunner`; target task state owner remains
  `AutobugfixService`.
- No model smoke is allowed until the experiment-line child supplies the
  initial three-case budget grant.
