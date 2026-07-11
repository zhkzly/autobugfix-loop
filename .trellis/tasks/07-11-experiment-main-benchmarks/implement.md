# Implementation plan: experiment lines and independent benchmark studies

The parent task owns the shared requirements, architecture, task map, and final
integration review. Implementation occurs in child tasks; the parent itself is
not the source-editing task.

## Child 1: governed experiment integration lines

Task: `07-11-experiment-integration-lines`

- [ ] Add additive SQLite records for studies, lines, integrations,
      checkpoints, budget grants, and usage entries with digest/tamper checks.
- [ ] Add explicit experiment-line base resolution to Operator requests.
- [ ] Implement stale-generation detection and compare-and-swap Guard
      integration in a separate trusted worktree.
- [ ] Implement immutable releases/checkpoints and non-rewriting rollback.
- [ ] Add atomic SDK usage reservation/finalization and `3 -> 8 -> 16` grants.
- [ ] Add service/projection-backed CLI without direct store/ref writes.
- [ ] Update constitution layer/path/runtime policies and Operator role skills.
- [ ] Test forged state, stale line, concurrent integration, budget exhaustion,
      dirty candidate, policy drift, rollback, and H0 parent lineage.
- [ ] Run focused Operator/config/CLI tests and full static validation.

Rollback point: no benchmark adapter work starts until the line/store migration
and rollback tests pass.

## Child 2: Defects4J Eval adapter

Task: `07-11-defects4j-eval-adapter`

- [ ] Add benchmark doctor/preflight/manifest services and CLI projections.
- [ ] Pin Defects4J 3.0.1 and model framework/project/runtime identity.
- [ ] Implement isolated buggy-repo materialization compatible with real Git
      task worktrees and Defects4J test metadata.
- [ ] Implement visible evidence capture without gold/fault-location leakage.
- [ ] Implement official triggering/full-test Execution and oracle contracts.
- [ ] Persist eligibility receipts for buggy-fail and fixed/gold-pass.
- [ ] Curate 16 eligible cases with 10 Optimization and 6 unseen-repository
      sealed Holdout cases; include nested 3/8/16 wave projections.
- [ ] Run all 16 no-model eligibility checks before sealing the manifest.
- [ ] Run one real Mini production SDK smoke only after Child 1 budget control
      is available and the three-case grant is approved.

Rollback point: an unstable or non-reproducible case is replaced before
manifest sealing; model output is never used to repair benchmark setup.

## Child 3: SWE benchmark Eval adapters

Task: `07-11-swe-eval-adapters`

- [ ] Add a trusted container-harness runner with structured argv, resource
      limits, cache roots, timeouts, and retained logs.
- [ ] Pin SWE-bench framework/dataset/image revisions and implement Verified
      materialization plus official generated-patch scoring.
- [ ] Pin SWE-bench-Live framework/dataset/image revisions and implement its
      official generated-patch scoring.
- [ ] Preserve issue text, attachments, screenshots, and task-type labels while
      sealing gold patches and hidden tests.
- [ ] Distinguish image/setup/harness errors from repair failures.
- [ ] Curate 10 Verified Optimization cases covering bugfix, feature, and
      maintenance plus 6 unseen-repository SWE-bench-Live Holdout cases.
- [ ] Produce nested 3/8/16 wave projections and run all 16 official gold
      preflights before sealing.
- [ ] Verify Docker/resource doctor behavior on the local WSL host before any
      Mini call.

Rollback point: missing Docker/runtime support leaves this child blocked with
raw doctor evidence; no local-git substitute may claim official SWE results.

## Child 4: Experiment 1, bugfix harness

Task: `07-11-bugfix-harness-experiment`

- [ ] Freeze common H0 subject/harness/config/model/skills/memory identities.
- [ ] Initialize `experiment/bugfix-main` from H0.
- [ ] Run the three-case H0 wave under the initial 30-call Mini grant.
- [ ] Report cost/harness evidence and obtain explicit wave-8 approval.
- [ ] Run wave 8, then obtain explicit wave-16 approval.
- [ ] Complete the 10 visible Optimization baseline and keep six Holdout cases
      sealed.
- [ ] Let Operator diagnose and change only the governed bugfix experiment
      line, with target and Regression feedback.
- [ ] Freeze H_bug and run the paired six-case sealed comparison.
- [ ] Publish the case-level rescue/regression/cost/governance report without
      claiming statistical significance.

Rollback point: any Holdout regression or governance violation blocks H_bug
promotion and retains H0 as the active trusted release.

## Child 5: Experiment 2, general-agent evolution

Task: `07-11-general-agent-experiment`

- [ ] Independently initialize `experiment/general-main` from the exact H0 SHA,
      with a separate config/memory/artifact namespace and budget.
- [ ] Prove no H_bug commit, skill, memory, config, or artifact is in the
      Experiment 2 baseline.
- [ ] Run independent 3/8/16 grants on the five/ten visible Verified cases.
- [ ] Let Operator evolve the bugfix-specialized H0 subject using only visible
      SWE optimization evidence.
- [ ] Freeze H_general and run the six-case SWE-bench-Live sealed comparison.
- [ ] Require positive visible net improvement, at least one Holdout rescue,
      zero Holdout regression, and mixed task-type coverage for success.
- [ ] Run a secondary Defects4J non-regression report without exposing those
      results as Experiment 2 optimization input.
- [ ] Compare H_general directly with H0 and report separately from Experiment
      1.

Rollback point: a failed generalization contract is a valid negative
experiment result and must not be rewritten as success or merged into main.

## Parent integration and final validation

- [ ] Verify all child artifacts use the same project constitution and typed
      contracts without merging loop state ownership.
- [ ] Verify H_bug and H_general independently name H0 as parent.
- [ ] Verify all 32 selected cases have real deterministic eligibility
      receipts and all 12 Holdout cases stayed sealed.
- [ ] Verify production runs used only `gpt-5.4-mini` and the Codex Python SDK.
- [ ] Run `uv run --cache-dir /tmp/uv-cache pytest -q`.
- [ ] Run `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`.
- [ ] Run `git diff --check`.
- [ ] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`.
- [ ] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_operator_policy.py`.
- [ ] Run the pinned public real-repository acceptance paths required by each
      completed child.
- [ ] Perform sequential Execution, Memory, Eval, Codex runtime,
      portability/privacy, Operator governance, and acceptance review passes.
      Inline mode uses the main agent for these passes and must not invent
      subagent reviews.
