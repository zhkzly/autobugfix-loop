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
- [ ] Re-run final post-hardening Docker E2E after Docker Desktop image inspect
      recovers; latest doctor fails closed because the materializer cannot be
      inspected and the verifier image is unavailable. Receipt:
      `.autobugfix/trusted-eval-cases/doctor/defects4j/425ace3ea754e152c2314a1bf12e2e8d1f1b3804653228e93cb4b574d7c60240.yaml`.

## Phase 5: project gates and review

- [x] Update README, example config, role skill, task design, and constitution.
- [x] Run full unit suite (`165 passed` after the final explicit control-root
      regression test).
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
- [ ] Commit and push the adapter branch after all available gates pass.

## Rollback Points

- Config/schema compatibility must pass before registering the adapter.
- Doctor must pass before any checkout/preflight/SDK mutation.
- One real official case must pass before attempting the 16-case seal.
- Any leaked Holdout identity or readable gold artifact invalidates the seal
  and requires deleting/recreating trusted runtime state.
