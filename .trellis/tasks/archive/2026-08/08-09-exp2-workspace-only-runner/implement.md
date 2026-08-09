# Implementation plan: Experiment 2 execution-only closed loop

## Preconditions

This document plans source implementation and later experiment execution. It
does not authorize a source edit from the dirty main checkout.

Before phase 1:

1. Obtain explicit approval of prd.md and design.md.
2. Create a fresh non-main implementation worktree from
   experiment/swe-runtime-freeze-v3 at 5946398. The historical branch remains
   the source of truth; governed candidate worktrees are ephemeral.
3. Record the implementation base SHA, dirty-worktree audit, and current
   protocol digest. Do not absorb unrelated main changes.
4. Provision a disposable direct-mode experiment environment. Stop if it
   cannot prove the no-credential/no-authority-root write boundary described in
   design.md.
5. Select and pin 2-3 external calibration cases, then make the external
   Guard exposure ledger exclude their repositories before formal sealing.
6. Define separate protocol-wave, public-case, sealed-case, and opaque
   Operator-budget-slot namespaces. Prove the mapping before any budget grant.

No model call, H0 result, or H1 candidate is valid before the shared apparatus
is frozen.

## Phase 1: Lock the cohort and study contracts

### Work

1. Add a versioned calibration manifest and a cohort-audit record. They must
   state public case IDs, repository counts, task-type distribution, formal
   denominators, calibration exclusion, the 2/5/10 public schedule, the
   0/0/6 sealed schedule, and the 3/8/16 opaque Operator slot schedule.
2. Add a study-level experimental policy record with:
   - max_h1_revisions = 2;
   - sealed_holdout_min_wave = 16;
   - final_holdout_requires_treatment_lock = true;
   - public_regression_limit = 0;
   - sealed_regression_limit = 0;
   - fixed empty Memory fixture identity;
   - protocol/public/holdout/Operator-slot namespace mapping;
   - candidate execution-scope allowlist;
   - prohibited-claim and Holdout-burn text.
3. Add a versioned, committed empty Memory fixture and pass it to the
   existing study snapshot mechanism. Do not change Memory service code,
   Memory configuration, the ordinary approved Memory root, or invoke a
   maintainer/collector. The copied study snapshot is input materialization,
   not Memory processing.
4. Make the study manifest record H0 source identity, frozen apparatus
   identity, external Guard identity, Operator role-skill digest, and
   direct-mode preflight receipt.

### Likely owners

- benchmarks/swe-experiment-2.yaml and a new experiment-local calibration
  manifest;
- src/autobugfix/eval/benchmarks/exp2_records.py;
- src/autobugfix/eval/benchmarks/exp2_coordinator.py;
- tests/test_swe_models.py, tests/test_operator_budget.py, and
  tests/test_operator_policy.py.

### Exit gate

- Parsing rejects altered cohort counts, missing calibration exclusion,
  revision count above two, pre-wave-16 Holdout, namespace collisions, and
  nonempty/redirected experiment Memory fixtures.
- The cohort audit reports ten public cases across six repository clusters and
  does not call that a random independent split.

## Phase 2: Build and freeze the shared measurement apparatus

### Work

1. Add a typed workspace-treatment binding and exact-subject selector. It must
   select exactly H0 or one resolved non-main H1 SHA/tree and reject refs,
   protected branches, working-tree subjects, duplicate roots, and drift.
2. Keep scorer, materializer, protocol parser, dataset revisions, subject
   runtime contract, and scoring authority independent of candidate code.
3. Add a frozen-apparatus receipt with the apparatus SHA/tree plus source
   digests of the dispatch, submission-freeze, scoring, projection, and
   reporting owners.
4. Expose a separate workspace-only runner entrypoint. Do not weaken the
   existing development endpoint by making it accept arbitrary H1 input.
5. Add an immutable run directory layout keyed by study, arm, protocol wave,
   public stage, public case token, opaque budget slot, and binding digest.
   Reuse must fail instead of overwriting evidence. Sealed identities are
   never used as directory keys outside the Guard.

### Likely owners

- src/autobugfix/eval/benchmarks/swe_models.py;
- src/autobugfix/eval/benchmarks/service.py;
- src/autobugfix/eval/benchmarks/subject_broker.py;
- src/autobugfix/eval/benchmarks/swe_submission.py;
- new src/autobugfix/eval/benchmarks/swe_workspace_runner.py if separation is
  cleaner than growing the broker;
- tests/test_swe_models.py, tests/test_swe_adapters.py, and
  tests/test_subject_broker.py.

### Exit gate

- H0 and H1 dispatch tests show the same apparatus/evaluator/runtime/Memory
  inputs and different exact subject bindings.
- The apparatus exposes result/attribution records without changing the
  official scorer or score interpretation.
- A candidate cannot select main, a protected ref, a non-canonical SHA, or a
  subject outside the experiment line.
- The apparatus commit is reviewed and frozen before a formal H0 run.

## Phase 3: Implement direct workspace-only dispatch safely

### Work

1. Add an execution-mode enum whose default remains the existing protected
   mode. The new workspace-only mode must be explicit in every binding and
   cannot become a global default.
2. In workspace-only mode, invoke the copied subject worker directly from the
   disposable experiment environment rather than calling the broker
   Bubblewrap argv.
3. Configure the Codex SDK backend for in-process direct dispatch so it does
   not create the second Bubblewrap worker. Preserve model, reasoning,
   approval, timeout, raw-log, and role receipt checks.
4. Preserve the exact-subject checkout, task-worktree creation, target-main
   identity checks, visible verifier server, capability ledger, and frozen
   evidence tree.
5. Add direct-mode environment preflight and an execution-mode receipt:
   writable roots, absent credentials, absent/readonly authority roots, worker
   argv, SDK invocation mode, and each request cwd.
6. Reject a SDK request whose cwd is not the dedicated target task worktree.
   Reject a direct-mode run if an outer broker Bubblewrap or an SDK Bubblewrap
   worker is observed.

### Likely owners

- src/autobugfix/eval/benchmarks/subject_broker.py;
- src/autobugfix/eval/benchmarks/swe_codex.py;
- src/autobugfix/codex_sdk.py;
- harnesses/swebench/scripts/run_subject.py;
- src/autobugfix/eval/benchmarks/swe_runtime.py;
- tests/test_subject_broker.py, tests/test_swe_runtime.py, and
  harnesses/swebench/tests/test_runtime.py.

### Exit gate

- Unit/integration fixtures prove direct worker argv, SDK mode, worktree cwd,
  target-main protection, and retained raw evidence.
- A capability or authority-root preflight failure stops before any SDK call.
- The actual direct path has a real public calibration before it is used in a
  formal wave.

## Phase 4: Support governed H1 candidates without moving Memory or Eval

### Work

1. Define the candidate Execution allowlist before H1a. It contains only
   execution orchestration, task/worktree lifecycle, direct dispatch
   configuration, runtime/harness code, and explicitly named Execution
   role-skill files. Scorer/materializer/Guard/Memory, all Operator role-skill
   files, and all Operator policy/state paths remain outside it. Any expansion
   requires a new study or a separately approved ablation.
2. Bind the frozen Operator supervisor/writer/verifier role configuration,
   policy, and skill digest to the study. Record the H0 Execution role-skill
   digest and bind each H1 Execution role-skill change to the candidate scope.
3. Add a named exp2-execution-only experiment profile. It fixes the role
   runtime contract and contains no Memory-maintainer action.
4. Add a trusted transition that records candidate revision number, parent
   SHA/tree, diff/scope digest, public-evidence cutoff, attribution ID, and
   rationale. Enforce at most two revisions.
5. Keep the existing Operator state machine and candidate worktree isolation.
   Do not modify its constitution or create a second candidate state
   authority. The Exp2 coordinator may pass opaque budget slots through the
   existing API, but may not pass Holdout identities before final unlock.

### Likely owners

- src/autobugfix/eval/benchmarks/exp2_coordinator.py;
- src/autobugfix/eval/benchmarks/exp2_records.py;
- tests/test_config_task_store.py, tests/test_operator_budget.py, and
  tests/test_operator_policy.py.

### Exit gate

- The profile preflight proves frozen Operator skills and excludes Memory
  maintenance.
- A third revision, protected path, modified Memory root, scorer path, or
  any Operator role-skill/policy/state path change fails before Writer
  execution. An Execution role-skill change outside its allowlist also fails.
- Candidate code can be verified and rolled back through the existing service.

## Phase 5: Add the trusted Exp2 coordinator and attribution contract

### Work

1. Add immutable `Exp2StageRecord`, `Exp2ResultProjection`, and
   `Exp2AttributionRecord` models. Every record binds study, arm, stage,
   protocol wave, public case token, opaque Operator slot, candidate digest,
   source artifact digests, and predecessor receipt.
2. Implement the coordinator state machine from design.md. Its `resume`
   operation is idempotent, crash-safe, and receipt-driven; it never rewrites
   terminal runs or retries an officially scored case with its own result.
3. Define the result projection explicitly: terminal official result and
   failure-stage classification are available to the Operator only after
   submission freeze, while gold patches, hidden tests, raw oracle material,
   scorer diagnosis, and private Guard data remain unavailable.
4. Require each attribution to name one evidence-backed hypothesis, one
   expected mechanism, one allowlisted scope, and one validation plan. Treat
   it as a hypothesis rather than a causal label.
5. Map public case manifests to opaque Operator budget slots. Validate that
   wave 3/8/16 grants have exactly 3/8/16 slots while public execution is
   2/5/10 and sealed execution is 0/0/6.
6. Expose the trusted-host command:

~~~text
uv run --cache-dir /tmp/uv-cache autobugfix eval exp2 resume --study-id <id>
~~~

The command advances one legal stage, pauses for required human approval, and
can be rerun after interruption without changing the evidence ledger.

### Likely owners

- src/autobugfix/eval/benchmarks/exp2_coordinator.py;
- src/autobugfix/eval/benchmarks/exp2_records.py;
- src/autobugfix/eval/reporting.py only for the frozen adapter/reducer;
- tests/test_exp2_coordinator.py;
- tests/test_exp2_records.py.

### Exit gate

- A resumed coordinator produces the same next action after an interrupted
  non-terminal step and never duplicates a terminal run.
- A same-case official retry, missing predecessor receipt, wrong slot
  namespace, or attribution outside the allowlist fails closed.
- Operator state remains owned by OperatorGovernanceService and Eval/Guard
  state remains owned by their existing authorities.

## Phase 6: Add public handoff, paired comparison, and sealed unlock gates

### Work

1. Implement the single-owner public Optimization feedback projection defined
   in design.md. It must redact oracle and private fields by construction.
2. Implement a paired H0/H1 comparison reducer and immutable reports for
   public and sealed domains. Invalid runs remain explicit rows.
3. Add public regression gate enforcement: zero observed H1c regressions and
   non-negative net paired gain before sealed measurement.
4. Add a treatment-lock receipt. It binds H1c SHA/tree, scope digest,
   revision count, full-check digest, final public comparison digest, and
   Holdout exposure audit.
5. Make the external Guard accept only that receipt for this study and only at
   wave 16. It must release aggregate paired data without sealed identities,
   per-case results, scorer diagnosis, or private artifact locations.
6. Add an irreversible Holdout-exposure/burn record. A post-exposure change
   must be rejected even if a new candidate branch exists.

### Likely owners

- src/autobugfix/eval/benchmarks/exp2_coordinator.py;
- src/autobugfix/eval/benchmarks/exp2_records.py;
- src/autobugfix/eval/reporting.py only for the frozen Exp2 adapter/reducer;
- src/autobugfix/eval/swe_holdout_guard.py only for the pre-registered
  wave-16 unlock adapter;
- tests/test_swe_adapters.py, tests/test_swe_guard.py,
  tests/test_swe_holdout_guard.py, tests/test_eval_benchmarks.py, and
  tests/test_exp2_coordinator.py.

### Exit gate

- Raw official score material cannot appear in the public projection.
- Guard calls at waves 3 and 8 fail for this adaptive study.
- Official scorer/dataset semantics and Operator constitution remain unchanged;
  any compatibility adapter is frozen before H0.
- H1 cannot be changed after treatment lock or any Holdout exposure.
- Paired reports preserve every formal denominator and distinguish invalid
  infrastructure from a failed repair.

## Phase 7: Contract tests and full source validation

Run focused tests after each phase, then the complete required ladder from the
candidate worktree:

~~~text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark doctor --adapter swebench_verified
~~~

Also run the focused suites while implementing:

~~~text
uv run --cache-dir /tmp/uv-cache pytest -q \
  tests/test_swe_models.py tests/test_swe_runtime.py \
  tests/test_subject_broker.py tests/test_swe_adapters.py

uv run --cache-dir /tmp/uv-cache pytest -q \
  tests/test_swe_guard.py tests/test_swe_holdout_guard.py \
  tests/test_operator_budget.py tests/test_operator_policy.py \
  tests/test_exp2_records.py tests/test_exp2_coordinator.py

uv run --cache-dir /tmp/uv-cache autobugfix eval exp2 resume --study-id <id>
~~~

Inspect the full diff for a changed model/budget, direct host paths,
credential exposure, target-main writes, scorer-to-writer feedback,
Memory-maintainer use, accidental Holdout identities, fake production paths,
and untracked apparatus drift.

## Phase 8: Calibration and study initialization

1. Run H0 and an explicit non-main candidate selection smoke on the 2-3
   calibration cases, in fresh task worktrees, through direct workspace-only
   dispatch and official Docker scoring.
2. Verify evidence, direct-mode receipt, no Bubblewrap record, subject
   identity, target-main integrity, submission freeze, scorer ordering, and
   noninterference.
3. Treat any calibration outcome only as apparatus evidence. Do not add it to
   a formal report denominator or use it as an H1 effect estimate.
4. After calibration, create the governed study and record the H0 baseline
   receipt, frozen apparatus receipt, fixed empty Memory fixture, namespace
   mapping, and sealed manifest. Do not access a Holdout result.

Stop on a Docker/runtime qualification failure, a direct-mode safety failure,
or a missing receipt; repair infrastructure and repeat only calibration with a
new run ID.

## Phase 9: Execute the bounded public Optimization loop

1. Run H0 on all ten named public cases once and create the immutable paired
   reference ledger.
2. Create H1a through the governed request lifecycle. Obtain a wave-3 grant
   containing three opaque slots and run only the first two public cases.
3. Emit terminal result projections and one attribution hypothesis per public
   handoff. The frozen-skill Operator may request at most one scoped H1b
   change; it may not retry those two scored cases.
4. Obtain the cumulative wave-8 grant containing eight opaque slots. Verify
   H1b, then run the next three previously unseen public cases. Repeat the
   projection/attribution process once.
5. Create H1c under the second revision allowance, obtain the wave-16 grant
   with opaque slots only, verify it, and lock its SHA/tree/diff. Run H1c
   across all ten public cases and calculate the public paired regression
   report. The wave-16 grant does not unlock Guard identities.
6. If public regression or integrity gates fail, use the governed rollback,
   retain artifacts, and stop before the Holdout. Do not retry a case from its
   official score.

The public wave history is evidence that the process learned from public
results. It is not an independent final score.

## Phase 10: Final sealed measurement and handoff

1. Verify the treatment-lock receipt, no-Holdout-exposure audit, external
   Guard identity, and six-case cohort invariant.
2. Run H0 and H1c through the Guard at wave 16 only. Use fresh execution
   worktrees and the same frozen protocol/model/budget/Memory/Eval apparatus.
3. Import only the aggregate paired receipt. If it reports an observed H1
   regression, roll back the experiment line; never issue another candidate
   change based on it.
4. Produce an immutable final report with:
   - apparatus/H0/H1/protocol/runtime/Memory/Operator/coordinator digests;
   - public chronology and selection-bias statement;
   - public and sealed fixed-denominator paired tables;
   - artifacts, noninterference, failure-stage, runtime, and usage evidence;
   - direct-mode environment attestation;
   - explicit small-cohort/no-generalization limitations.
5. Retain or roll back only the experiment-line candidate. No main promotion,
   release activation, or resume/leaderboard claim is part of this task.

## Completion definition

The implementation is complete only when all source checks pass and the
coordinator/run ledger can enforce every boundary above. The experiment is
complete only when a real calibration, public bounded loop, final locked
paired Holdout run, and truthful report exist. A blocked direct-mode safety
precondition or a burned Holdout is a valid stopped outcome, not something to
work around by weakening the harness.
