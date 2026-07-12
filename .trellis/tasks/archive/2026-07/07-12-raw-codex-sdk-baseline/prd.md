# Build raw Codex SDK baseline

## Goal

Build and run an independent Raw Codex SDK comparator for the completed
Defects4J H0 measurement. The comparison must answer one system-level question:

> With the same model, repository revisions, visible issue evidence, and
> official scorer, how does one direct Codex SDK coding turn perform relative
> to the complete Autobugfix Execution/Memory/Verifier/Evaluator harness?

Autobugfix remains a local, repo-agnostic loop-engineering and
harness-engineering control system. This task adds an Eval-owned experimental
arm; it does not redefine or replace Execution, Memory, Eval, or Operator.

## Background

- The frozen Autobugfix H0 subject is
  `f529f09de53183d7ddbf9e05b31a9d3b3fbde008`.
- Its formal Defects4J run completed 16 cases with 14 official passes, two
  failures, zero harness errors, and no post-freeze mutation.
- `d4j-jsoup-2`, `d4j-gson-2`, and `d4j-jacksoncore-2` were used in development
  or pilot runs before the formal H0 run. They are development cases, not blind
  evidence. The other 13 cases form the primary paired comparison cohort.
- The new branch is `experiment/raw-codex-sdk-baseline`, stacked on the frozen
  Defects4J Eval infrastructure branch. The original H0 branch and artifacts
  remain unchanged.

## Requirements

### Experimental Treatment

- Run exactly one fresh Codex Python SDK thread and one turn per case.
- Pin `openai-codex==0.1.0b3`, `gpt-5.4-mini`, reasoning effort, service tier,
  wall-time limit, prompt template, and concurrency before the formal run.
- Give Raw Codex the same sanitized buggy repository, problem statement,
  visible reproduction evidence, triggering-test failure text, and attachments
  that were available to H0.
- Let the SDK agent inspect and edit only its isolated target worktree and run
  commands available there. Do not give it an Autobugfix-managed verifier,
  Writer retry, Evaluator turn, Memory, role skill, Operator action, gold patch,
  fixed revision, hidden test, official verdict, or case-specific prompt.
- A timeout freezes and scores the patch present at the deadline. There is no
  second SDK turn and no case-level retry.

### Generation Isolation

- Implement the Raw generator as a separate uv project that depends on the
  preview Codex Python SDK but does not import `autobugfix`.
- Invoke `openai_codex.Codex` directly. Invoking `codex exec`, using a fake
  production backend, or routing through `CodexSDKBackend` is forbidden.
- Run each SDK process with project hooks and multi-agent support disabled, a
  fresh ephemeral `CODEX_HOME`, and an outer process sandbox.
- The SDK process may see only its target worktree, visible case bundle,
  isolated SDK runtime, and raw-log output directory. It must not see the
  Autobugfix source checkout, active Memory, trusted receipts, Docker socket,
  official scorer, other case artifacts, or previous case outputs.
- The trusted Eval host, not the Raw process, computes the final Git diff,
  changed paths, hashes, timestamps, and frozen submission record.

### Shared Measurement Control

- Reuse the existing pinned Defects4J case receipts, sanitized repositories,
  immutable Docker image IDs, official evaluator, and score semantics.
- Derive a new prepared Raw-baseline manifest from the same 16 case identities;
  do not reuse the H0 subject/skills/Memory fingerprint as the Raw subject.
- Bind the prepared manifest to the Raw runner source digest, nested lockfile,
  SDK version, prompt digest, model, budget, case receipts, scorer runtime IDs,
  H0 report digest, and development/primary cohort assignment.
- After generation, validate that every changed path is within the receipt's
  production source roots. Policy-violating patches are retained and counted
  as unsuccessful submissions rather than silently filtered.
- Apply each frozen patch to a fresh clean buggy checkout and run the same
  independent official Defects4J evaluator used for H0.
- Recompute the worktree patch and submission artifacts after scoring. Any
  mutation is a harness error.
- Official score, hidden tests, fixed truth, and scorer diagnosis must never
  return to the Raw SDK process or trigger another generation attempt.

### Formal Run And Reporting

- Use an already exposed development case for one real production pilot before
  freezing the formal runner. Pilot output is never included in the primary
  comparison.
- Pre-register all 16 formal case identities and run them serially with one
  fresh thread each after the runner, prompt, SDK, model, and budgets are
  frozen.
- If a runner, sandbox, materialization, transport, or scorer harness defect is
  found after the formal run starts, retain the partial run as invalid, fix the
  defect on a new code digest, prepare a new manifest, and restart the complete
  formal suite. Never rerun only a failed or unfavorable case.
- Treat an SDK deadline, empty patch, invalid source-path modification, or
  official rejection as a valid unsuccessful baseline outcome. Treat missing
  auth, unavailable API, corrupt case materialization, patch-apply failure, or
  scorer infrastructure failure as a harness error.
- Produce a digest-bound comparison report with:
  - primary paired result over the 13 non-pilot cases;
  - secondary all-16 and three-development-case results;
  - Raw and H0 success rates;
  - paired rescue/regression counts and exact McNemar result where defined;
  - wall time, SDK calls, token usage, timeouts, invalid patches, harness errors,
    artifact completeness, and per-case submission/score digests.
- Do not describe the 16-case aggregate as a sealed blind result.

### State And Interfaces

- Eval owns prepared baseline manifests, visible case bundles, frozen Raw
  submissions, official scores, noninterference receipts, and comparison
  reports.
- The Raw runner owns no durable authority. Its SDK logs and process result are
  untrusted observations consumed by Eval.
- CLI handlers may request Eval service operations but may not author state or
  score records directly.
- Runtime worktrees, authentication bridges, logs, submissions, and reports
  stay under gitignored runtime roots. Source code, schemas, tests, and the
  generic prompt template are version controlled.

## Acceptance Criteria

- [x] The baseline generator is a separately locked uv project and an automated
      import check proves it does not import `autobugfix`.
- [x] Production generation calls `openai_codex.Codex` directly with SDK
      version `0.1.0b3`; no `codex exec` or fake fallback exists.
- [x] The model, prompt, SDK, runner, environment, budget, case, H0 report, and
      official-scorer identities are frozen in a digest-verified prepared
      manifest before formal generation.
- [x] A process-isolation test proves Raw Codex cannot read Autobugfix source,
      Memory, trusted receipts, official artifacts, Docker authority, previous
      case output, or host Codex hooks/skills.
- [x] A real exposed pilot case completes direct SDK generation, trusted patch
      freeze, clean-checkout application, official Defects4J scoring, and
      noninterference verification.
- [x] Target repository main snapshots remain unchanged; all model edits occur
      in per-case baseline worktrees.
- [x] All 16 formal Raw cases run once from one frozen manifest, or the entire
      run is explicitly marked invalid and restarted under a new manifest after
      a harness defect.
- [x] The official evaluator never appends Raw SDK input, starts another turn,
      or changes a frozen submission.
- [x] Raw SDK requests, streamed events, stderr, timing, Git diff, changed
      paths, submission, oracle output, and receipts are retained for every
      case. Final response and usage are retained when produced; a hard timeout
      records their explicit absence without synthesizing model output.
- [x] A deterministic report compares Raw SDK with the existing H0 report and
      separates the 13-case primary cohort from the three exposed cases.
- [x] `uv run --cache-dir /tmp/uv-cache pytest -q` passes.
- [x] The standalone baseline project's tests and compile checks pass.
- [x] `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`
      and `git diff --check` pass.
- [x] Role-skill validation passes even though the Raw treatment itself loads no
      role skills.

## Out Of Scope

- Optimizing H0, creating H_bug, or using Raw/H0 results as Operator feedback.
- Updating or approving Memory from benchmark outcomes.
- Changing Experiment 2 or H_general.
- Claiming model-only causality: this is a system-level comparison between a
  direct SDK coding turn and the complete Autobugfix harness, with compute and
  runtime reported explicitly.

## Notes

- Base branch: `agent/defects4j-eval-adapter`.
- Implementation branch: `experiment/raw-codex-sdk-baseline`.
- The existing H0 result remains immutable and is consumed only as a comparison
  artifact.
