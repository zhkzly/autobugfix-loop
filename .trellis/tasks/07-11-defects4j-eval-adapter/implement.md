# Implementation plan: production Defects4J Eval adapter

## Phase 1: Runtime And Case Contracts

- [x] Add Docker-only typed configuration and reject legacy host-runtime keys.
- [x] Pin materializer/verifier images, framework revision, platform, Java, and
      timezone; add a fail-closed doctor with raw evidence.
- [x] Materialize history-free buggy Git snapshots and retain private
      qualification evidence outside Writer-visible roots.
- [x] Keep the canonical Eval case extensible for issue text, failures,
      reproduction commands, screenshots, and attachments.

## Phase 2: Feedback-Domain Separation

- [x] Implement a visible verifier contract containing triggering tests and
      production source roots but no fixed baseline, gold data, or final score.
- [x] Run visible triggering-test verification for bounded Execution feedback.
- [x] Implement a separate trusted official evaluator using a fresh buggy
      checkout and the full Defects4J scoring semantics.
- [x] Keep official evaluator output outside Execution task state and feedback.

## Phase 3: Freeze And Noninterference

- [x] Freeze generated patch, task/events digests, subject SHA, task state,
      iteration count, and timestamp before official evaluation.
- [x] Recompute frozen facts after evaluation and emit a noninterference
      receipt; fail the harness on any mutation.
- [x] Require submission, official output, score, and noninterference artifacts
      in Eval artifact-completeness checks.
- [x] Distinguish valid unsuccessful repair from harness failure.

## Phase 4: Real Protocol Acceptance

- [x] Run Docker doctor successfully against both immutable Defects4J images.
- [x] Qualify a real pinned case with official no-model framework commands.
- [x] Run `d4j-jacksoncore-2` through production Codex Python SDK with
      `gpt-5.4-mini` and two bounded Writer attempts.
- [x] Confirm attempt one received only visible verifier feedback.
- [x] Freeze the final patch before official scoring and confirm no third
      attempt or post-score task mutation occurred.
- [x] Retain the official compile failure as a valid H0 result with
      `harness_error_count: 0` and artifact completeness `1.0`.

This one-case result validates the measurement protocol. It is not a claim
about aggregate repair capability and must not be used to tune H0 inside
Experiment 1.

## Phase 5: Formal Experiment 1

- [x] Freeze one source-controlled schema-v3 seed containing 16 pre-registered `evaluation`
      cases selected independently of model outcomes.
- [ ] Freeze H0 code/config/model/skills/Memory/budget identities.
- [ ] Run every case exactly once at concurrency one; internal retries are
      limited to the common frozen Execution budget.
- [ ] Publish aggregate and case-level descriptive results only after all
      launched submissions have been independently scored.

## Phase 6: Project Gates And Review

- [x] Run `uv run pytest -q` (`179 passed`).
- [x] Run `uv run python -m compileall -q src tests scripts`.
- [x] Run `git diff --check` and role-skill validation.
- [x] Complete sequential Execution, Memory, Eval, Codex runtime,
      portability/privacy, and acceptance reviewer passes. This environment
      has no independent subagent tool, so do not represent them as subagent
      reviews. The acceptance pass recognizes the real one-case protocol pilot
      but does not treat it as the formal 16-case capability result.
- [ ] Commit and push source changes without runtime benchmark artifacts.

## Rollback Points

- Doctor and private qualification must pass before any SDK call.
- A visible-verifier contract containing private oracle facts invalidates the
  run and must be corrected before another model case.
- Oracle invocation before submission freeze invalidates the result.
- Any post-oracle patch/task/trace mutation invalidates the result.
- A valid official failure remains evidence and is not a rollback condition.
