# Implementation plan: production Defects4J Eval adapter

## Phase 1: contracts and Docker runtime

- [x] Add Docker-only typed config and reject legacy host runtime keys.
- [x] Add canonical Eval case, digest-bound doctor/command/receipt/verifier,
      seed, sealed manifest, and tamper tests.
- [x] Add UTF-8, immutable image/platform/framework, raw-log, and disk doctor.

## Phase 2: materialization and scoring

- [x] Checkout real buggy/fixed revisions through official Defects4J Docker.
- [x] Build deterministic history-free buggy Git repositories.
- [x] Enforce production-source-only diffs and independent official scoring.
- [x] Implement stable fixed baseline plus triggering-test delta semantics.
- [x] Preserve per-attempt verifier evidence outside task worktrees.

## Phase 3: execution and sealing

- [x] Register Defects4J with the existing real Execution loop.
- [x] Add bounded Writer retries with real verifier feedback.
- [x] Run production Python SDK with `gpt-5.4-mini`; never approve/archive.
- [x] Implement encrypted 10 Optimization + 6 repository-disjoint Holdout
      sealing with cumulative 3/8/16 waves and no public identity leak.
- [x] Preserve historical pre-hardening Jsoup-2/Gson-2 production evidence;
      do not count it as final post-hardening acceptance.

## Phase 4: runtime hardening

- [x] Move SDK calls into timeout-enforced Python worker processes.
- [x] Keep SDK request/result/stdout/stderr and Docker command evidence.
- [x] Replace per-file ignored-output cleanup with safe minimal roots.
- [x] Split materializer/verifier images and remove Writer-visible gold and
      localization metadata without deleting official test dependencies.
- [x] Bind Guard bundle/metric authority to trusted Git, constitution, and
      harness identity; add signature-verifying Study metric import.
- [x] Hide the host home and authority roots at the OS mount layer; expose only
      role cwd, exact log roots, and service-verified linked-worktree Git
      metadata. Reject workspace-write at the trusted control root.
- [x] Re-run the post-hardening Docker doctor with native WSL Docker. Both
      immutable roles, Java 11, verifier sanitization, framework initialization,
      and disk checks passed; doctor receipt digest:
      `1bab311007163b648206ef0769ffb526577ab6c02ebd7123dd7cfdeea425fbc6`.
- [x] Fix native bind-mount checkout to run as the host UID/GID with ephemeral
      in-container Git safety configuration; no privileged ownership repair.
- [x] Retain digest-bound checkout metadata in the trusted Eval root and inject
      it only into the isolated verifier copy. Reject candidate-authored or
      changed verifier metadata and clean the injected copy after every check.
- [x] Run final real `d4j-jsoup-2` E2E with the production Python Codex SDK and
      `gpt-5.4-mini`. Three bounded Writer attempts consumed real official-test
      feedback; the final official suite and independent oracle passed, the
      read-only evaluator passed, target main stayed clean, the task stopped at
      `waiting_human_ppe_approval`, `gold_diff_equal` was false, and the run
      reported `harness_error_count: 0` plus artifact completeness `1.0`.

## Phase 5: project gates and review

- [x] Update README, example config, role skill, task design, and constitution.
- [x] Run full unit suite (`167 passed` after native-checkout and trusted
      verifier-metadata regressions were added).
- [x] Run compileall, diff check, and role validator. Operator policy CLI still
      requires a real request/bundle rather than a context-free invocation.
- [x] Run the production `gpt-5.4-mini` public-repository E2E: isolated
      Execution and independent Eval both passed real pytest, target main
      stayed clean, and Memory produced a pending proposal without self-approval.
- [x] Run the production `gpt-5.4-mini` Operator E2E through request, budget,
      isolated Writer, verification, trusted integration, `H_bug` checkpoint,
      and `CLOSED` state.
- [x] Complete independent Execution, Memory, Eval, Codex runtime,
      portability/privacy, and acceptance reviewer passes using six real
      subagents; address their Guard/runtime findings in the main session.
- [x] Re-run all static gates after runtime and authority edits.
- [ ] Seal and qualify the final 10 Optimization + 6 Holdout case suite; the
      successful single-case E2E proves the direct path but is not the complete
      Experiment 1 dataset.
- [ ] Commit and push the adapter branch after all available gates pass.

## Rollback Points

- Config/schema compatibility must pass before registering the adapter.
- Doctor must pass before any checkout/preflight/SDK mutation.
- One real official case must pass before attempting the 16-case seal.
- Any leaked Holdout identity or readable gold artifact invalidates the seal
  and requires deleting/recreating trusted runtime state.
