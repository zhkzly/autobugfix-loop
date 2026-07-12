# Implement Defects4J Eval adapter

## Goal

Implement a production Defects4J adapter for the common Autobugfix benchmark
contract:

```text
buggy repository + issue/evidence
-> complete existing Execution loop
-> frozen final patch and trace
-> independent official Defects4J evaluator
-> immutable score and diagnosis
```

Experiment 1 measures the frozen bugfix-specialized `H0`; it does not optimize
Autobugfix, create `H_bug`, or expose official evaluation results to Writer.

## Requirements

- Pin Defects4J tag `v3.0.1` and commit
  `6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09`, selected project revisions,
  Docker image identities, Java version, platform, and timezone.
- Treat Defects4J as an adapter over the common Case contract. The adapter may
  materialize a buggy repository, construct visible issue/evidence, configure
  visible Execution checks, and invoke the dataset's official evaluator. It
  must not replace or bypass the existing Execution service.
- Materialize each buggy revision as a history-free Git repository compatible
  with real task worktrees. Do not expose the fixed revision, developer patch,
  modified-class hints, hidden tests, or official verdict to Execution.
- Preserve all source-provided visible evidence: issue text when available,
  triggering tests, failure output, stack traces, reproduction commands, and
  attachments. A sparse benchmark input must not narrow the common evidence
  schema used by normal on-call tasks.
- Separate the two feedback domains:
  - Execution verifier: only predeclared, deployment-visible commands and
    triggering tests; its failures may drive bounded Writer retries.
  - Official evaluator: full Defects4J scoring after final submission freeze;
    its result is Eval-owned and may never drive another Writer attempt.
- Freeze the final generated patch, task/events digests, subject SHA, attempt
  count, model, configuration, skills, Memory snapshot, and timestamp before
  invoking the official evaluator.
- Recompute all frozen values after scoring and fail the harness if the oracle
  changed task state, patch contents, trace, or attempt count.
- Preflight selected cases without an LLM. Qualification may inspect buggy and
  fixed revisions privately to establish that the official framework is
  runnable, but private qualification output must not enter Execution input.
- Pre-register 16 `evaluation` cases before the formal run. Case selection must
  not depend on model outcomes. All cases use the same frozen `H0`, production
  Codex Python SDK, `gpt-5.4-mini`, and fixed attempt/time/call budgets.
- Official tests determine correctness; gold-diff equality is diagnostic only.
- Retain setup, checkout, SDK request/result/stdout/stderr, Execution events,
  per-attempt visible verifier output, frozen submission, official evaluator
  output, noninterference receipt, score, timing, and failure classification.
- Distinguish harness/setup failure from a valid unsuccessful repair. A valid
  failing official score remains part of Experiment 1 and must not be rerun
  merely because it failed.
- Use one pinned Dockerfile with separate materializer and verifier roles.
  Host Java, Defects4J, Perl, SVN, cpanm, and library paths are forbidden
  configuration surfaces.
- Expose benchmark operations through CLI adapters over the Eval benchmark
  service. CLI code must not write receipts, case state, or task state directly.

## Acceptance Criteria

- [x] Doctor fails before SDK use when a required Docker runtime is absent.
- [x] At least one pinned case completes private no-model qualification using
      official Defects4J commands.
- [x] Materialized target main remains unchanged while Execution edits only its
      task worktree.
- [x] Writer input cannot read fixed revision, gold patch, modified-class
      hints, private baseline, or official-evaluator artifacts.
- [x] A final submission is frozen before the official evaluator starts, and
      an immutable noninterference receipt proves no post-score mutation.
- [x] A real production `gpt-5.4-mini` case completes the full protocol. Its
      official failure is retained as a valid H0 result, not fed back to Writer.
- [x] The final 16-case manifest is pre-registered and every eligible case is
      run exactly once under the frozen Experiment 1 protocol.
- [x] The report includes repair success, first-attempt success, bounded-loop
      rescue, iterations, model calls, runtime, verifier/oracle agreement,
      harness errors, and artifact completeness.
- [x] Existing `local-git` Eval datasets remain compatible.

## Out Of Scope

- Experiment 1 does not optimize code, prompts, skills, Memory, configuration,
  or model settings based on case outcomes.
- Existing `H_bug`, sealed-Holdout, experiment-line, and Operator governance
  capabilities remain available for other studies but are not part of this
  descriptive H0 measurement.
- Experiment 2 (`H0 -> H_general` on SWE tasks) is independent and starts from
  the same original frozen H0 rather than from Experiment 1 artifacts.
- Building the pinned images and providing Docker are operator prerequisites.
  The adapter never invokes `sudo` or installs host packages.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This child affects Eval and benchmark-facing Execution configuration. Eval
  owns Case, submission, oracle, score, and report state; `AutobugfixService`
  owns the inner Execution task/worktree state.
- Memory may be frozen as part of H0 input, but Experiment 1 does not maintain
  or approve Memory from benchmark outcomes. Operator does not participate in
  case execution or inspect a result to alter H0.
