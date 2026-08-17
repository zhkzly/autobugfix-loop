# Implementation plan: Exp2 resume-first MVP

## Preconditions

- Stay on the current Trellis task until final plan approval.
- Do not edit dirty main or the frozen `ebb994f` worktree.
- After task start, create a fresh non-main worktree/branch from `ebb994f`,
  suggested branch `experiment/exp2-resume-mvp-v2`.
- Record base SHA/tree, protocol/dataset/scorer digests, Docker/host facts, and
  unrelated dirty-main audit.
- No benchmark SDK call is authorized until source hardening and source checks
  pass on a committed apparatus candidate.

## Phase 1 — Freeze MVP protocol and records

1. Add a versioned resume-MVP protocol/manifest containing the exact 2+10 IDs,
   order, slices, visibility policy, one-used-revision cap, H0 feasibility
   gate, stop rules, and prohibited claims.
2. Resolve selected OCI image identities; record tags and immutable
   manifest/config/layer/platform digests. Pull/build only selected images.
3. Add v2 plan/record types:
   - plan with `study_kind`;
   - case-attempt intent and terminal receipt;
   - calibration terminal receipt;
   - source visibility bundle;
   - candidate-transition receipt reference;
   - paired resume-pilot report;
   - rollback/report terminal records.
4. Add a typed metric dictionary/reducer matching `metrics.md`, including
   source/transfer separation, invalid-arm nullability, usage/pricing
   authority, and claim lint.
5. Keep v1 records readable for audit and reject v1 state as a writable MVP
   study.

Exit gate:

- parser round trips every new record and rejects changed IDs/order/digests,
  non-content-addressed bindings, extra cases, invalid status combinations,
  or visibility leaks.

## Phase 2 — Implement per-case resume and calibration terminalization

1. Change CLI/coordinator execution from in-memory stage batching to:
   intent -> execute/reconcile one case -> terminal receipt -> next case.
2. Add open-intent reconciliation against the trusted run root.
3. Reuse valid completed receipts and never rerun completed Execution.
4. Restrict scorer retry to a verified frozen submission.
5. Add separate calibration state graph and state root.
6. Emit `CALIBRATION_COMPLETE` only for two official-terminal cases;
   otherwise emit `CALIBRATION_BLOCKED`.
7. Ensure another `resume --execute` is terminal and cannot start H0.

Focused tests:

```text
uv run pytest -q tests/test_swe_exp2_records.py tests/test_swe_exp2_workspace_only.py
```

Required fault tests:

- interrupt after case-one terminal receipt;
- interrupt after intent but before SDK;
- preflight rejection;
- execution failure before submission;
- scorer failure after submission;
- tampered event/receipt/run root;
- repeated resume.

## Phase 3 — Implement H0 visibility and one-revision candidate handoff

1. Add pilot initializer requiring the terminal calibration receipt and same
   frozen identities.
2. Execute/reconcile H0 per case across all ten.
3. Keep transfer/reserve reports only in trusted Eval state.
4. Compute H0 feasibility privately and release only the source pair bundle.
5. Add one attribution record tied to the source bundle.
6. Add or expose an Operator service method that creates/exports a trusted
   Exp2 candidate-transition receipt from real governance state.
7. Validate request/grant/Writer/check/commit/integration/diff/allowlist and
   bind one content-addressed candidate record.
8. Reject caller-authored candidate transition YAML and a second revision.

Focused tests:

```text
uv run pytest -q tests/test_operator_budget.py tests/test_operator_policy.py tests/test_operator_integration.py tests/test_swe_exp2_records.py
```

Required fault tests:

- forged attribution author/approver;
- attribution references unknown projection;
- candidate not produced by bound request;
- stale grant/scope/check/integration;
- out-of-allowlist diff;
- overwritten candidate binding;
- future-case projection in Operator bundle.

## Phase 4 — Implement paired source/transfer decision and rollback/report

1. Run source H1 and transfer H1 from the same locked binding.
2. Reuse the paired reducer with explicit invalid arms.
3. Enforce zero transfer regressions.
4. On regression, invoke/consume Operator rollback and record
   `ROLLED_BACK`.
5. On non-regression, record `PILOT_COMPLETE`.
6. Make report append an immutable report event and reach `REPORTED`.
7. Generate Markdown and YAML reports plus a reproducibility index.

Focused tests:

```text
uv run pytest -q tests/test_swe_exp2_records.py tests/test_swe_guard.py tests/test_swe_holdout_guard.py tests/test_operator_integration.py
```

Required cases:

- source rescue only;
- transfer rescue;
- no gain;
- one transfer regression and verified rollback;
- invalid H0/H1 arm;
- report replay/tamper rejection.

## Phase 5 — Full source quality gate

Run from the clean MVP apparatus worktree:

```text
UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run python -m compileall -q src tests scripts
git diff --check
UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run python scripts/validate_role_skills.py
UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run autobugfix eval benchmark doctor --adapter swebench_verified
```

Review the complete diff for scorer/Memory/Operator-skill/model/budget drift,
credential exposure, direct-host paths, fake metrics, Holdout identities,
same-case retries, mutable bindings, or unrelated changes.

Freeze a clean apparatus commit/SHA/tree only after an independent
`trellis-check` review passes.

## Phase 6 — Qualify selected calibration and formal cases

1. Verify the pinned dataset snapshot and selected case metadata.
2. Resolve/pull/build selected instance layers only.
3. Run gold and null/base qualification through the official scorer for the
   two calibration and ten H0 cases.
4. Record OCI/scorer/materialization receipts and resource requirements.
5. Stop on qualification drift; do not replace a case inside the study.

## Phase 7 — Execute two-case calibration

1. Initialize the calibration study with the new apparatus receipt.
   Materialize and bind a dedicated private empty-Memory root outside all
   project/Eval/Operator/Guard roots; do not alter `.autobugfix-memory`.
2. Inspect readiness without `--execute`.
3. Execute Flask.
4. Force one controlled interruption after its terminal receipt.
5. Resume and prove Flask is not rerun.
6. Execute Pylint.
7. Verify `CALIBRATION_COMPLETE`, official score ordering,
   noninterference, empty Memory, direct cwd, and protected-root audit.
8. Generate the terminal calibration report.

Representative command shape:

```text
UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run autobugfix eval exp2 init ...

UV_CACHE_DIR=/tmp/autobugfix-uv-cache uv run autobugfix eval exp2 resume --study-id <calibration-study-id> --state-root <trusted-state-root> --execute
```

`build-plan-v2` must receive `--memory-root <dedicated-empty-root>`. Formal H0
Operator Study creation must use that same root with
`--empty-memory-fixture --guard-root <same-plan-guard-root>`. A study that
stopped before dispatch because its Memory binding was invalid is preserved for
audit and never reused.

## Phase 8 — Execute H0 baseline and feasibility gate

1. Initialize a fresh resume-pilot study from the calibration receipt.
2. Run H0 across the ten fixed cases with case-level resume.
3. Verify every report and fixed denominator.
4. Compute the private feasibility gate.
5. If blocked, write the negative/no-signal report and stop.
6. If passed, release only the source pair bundle.

## Phase 9 — Produce and verify one candidate

1. Record one source-evidence attribution.
2. Create a real Operator triage/request with the allowlisted Execution scope.
3. Obtain required authority; never self-enter a human approval phrase.
4. Run one service-owned Writer lifecycle.
5. Run fast feedback, candidate commit, full verification, and integration.
6. Export and validate the candidate-transition receipt.
7. Lock the content-addressed candidate binding.

Stop if no legal source hypothesis exists or candidate verification fails.

## Phase 10 — Run source replay and transfer

1. Run the locked H1 on source 2.
2. Run the same H1 on transfer 3.
3. Preserve all paired cells and attempt/cost evidence.
4. Roll back on any transfer regression.
5. Otherwise retain only on the experiment line.
6. Generate final report and reproducibility index.

No reserve or Live execution occurs in this task.

## Final review and handoff

- Run `trellis-check` against requirements, contracts, full diff, source
  checks, and real-run artifacts.
- Update project specs only for reusable executable contracts learned during
  implementation.
- Commit source and planning changes intentionally from their owning branches.
- Archive the task only when the final report and retain/rollback outcome exist.

## Rollback points

- Source: abandon the new apparatus branch; never reset or modify v1/main.
- Study: drift, invalid evidence, or a failed pre-dispatch authority binding
  burns the current study ID; preserve it and initialize a new ID.
- Candidate: use the trusted Operator rollback transition; never force-reset
  the experiment line.
- Dataset: no case replacement after freeze.
- Runtime: unsafe isolation or missing credential boundary stops before SDK.

## Deferred extension

Only after a transfer rescue with zero transfer regressions may a new task
consider H1 on reserve 5 and guarded Live 6. Paper-scale comparisons, Pro,
Memory ablations, multiple models, and statistical expansion remain separate.
