# Implementation plan: official SWE Eval adapters

## 1. Freeze Runtime Contracts

- [x] Add the separately locked `harnesses/swebench` uv project with exact
      official dependencies.
- [x] Add machine-readable Experiment-2 protocol and common SWE contracts.
- [x] Implement canonical hashing, schema validation, upstream/dataset
      revision checks, and append-only stores.
- [x] Add tests rejecting mutable refs, unknown private fields, duplicate IDs,
      invalid 10+6 splits, and digest drift.

Validation:

```text
uv run --project harnesses/swebench python -c "import swebench; assert swebench.__version__ == '4.1.0'"
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_swe_models.py
```

## 2. Official Runtime And Doctor

- [x] Implement pinned framework checkout and Hugging Face snapshot managers.
- [x] Implement Docker/resource/cache/image doctor with no SDK side effects.
- [x] Implement official command construction and complete raw-log capture for
      Verified and Live.
- [x] Add CLI projections for doctor and inventory inspection.

Validation:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark doctor --adapter swebench_verified
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark doctor --adapter swebench_live
```

## 3. Materialization And Visible Verifier

- [x] Materialize exact `/testbed` snapshots from official Docker images into
      private staging and sanitized Git source caches.
- [x] Generate repo-agnostic target config plus language-aware Docker-backed
      visible verifier commands.
- [x] Prove all edits occur in real Execution task worktrees and snapshots stay
      unchanged.
- [x] Retain verifier stdout/stderr/events outside target worktrees.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_swe_adapters.py -k "materialize or verifier or worktree"
```

## 4. Official Scorers And Noninterference

- [x] Implement one-case Verified prediction/scorer adapter using official
      `swebench.harness.run_evaluation`.
- [x] Implement one-case Live prediction/scorer adapter using pinned official
      `evaluation.evaluation`.
- [x] Normalize only official resolved/harness-error facts into Eval reports.
- [x] Freeze submission before scoring and verify all digests afterward.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_swe_adapters.py -k "score or freeze or noninterference"
```

## 5. Exact-Subject Broker

- [x] Add digest-bound subject requests and results.
- [x] Materialize clean detached H0/candidate subject worktrees and observe real
      SHA/tree/config/role/skill/Memory identities.
- [x] Launch candidate source through trusted dependencies in Bubblewrap with
      hooks disabled and all authority roots hidden.
- [x] Bind generated submissions and Guard metrics to the executed subject,
      line generation, grant, case, model, and budget.
- [x] Add canary tests for control, Holdout, Operator state, Docker socket,
      sibling artifacts, H_bug, and Experiment-1 leakage.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_subject_broker.py tests/test_swe_guard.py
```

## 6. Qualification, Curation, And Sealing

- [x] Download canonical pinned Verified and Live snapshots.
- [x] Build candidate inventories and task-type classifications with retained
      provenance.
- [ ] Run official gold-patch qualification serially and reject unstable or
      broken cases.
- [ ] Select ten Verified Optimization cases and six unseen-repository Live
      Holdout cases satisfying coverage constraints.
- [x] Encrypt/authenticate Holdout state and emit public 3/8/16 projections
      without case-identity leakage.

Public status: all ten protocol-pinned Verified Optimization cases are eligible
under runtime `sha256:e83ab8521188fd47492b443a504116ff8d3bcfe77fba4c2990c8e1b8e87533cc`.
The six Live cases remain a human-Guard action, so the two cohort items above
must stay open until their encrypted receipts exist.

Real commands:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark qualify-swe --protocol benchmarks/swe-experiment-2.yaml --adapter <adapter> --instance <instance> [--guard-root <external-root>]
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark prepare-swe --protocol benchmarks/swe-experiment-2.yaml --guard-root <external-root>
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark seal-swe --prepared <prepared> --guard-root <external-root>
```

## 7. Real Production Acceptance

- [x] Run one eligible development case through real `gpt-5.4-mini`
      Execution, patch freeze, official scoring, and noninterference.
- [x] Fix only generic harness defects; never use official result to retry or
      optimize the candidate.
- [x] Re-run gold qualification after the final harness change.
- [ ] Freeze adapter/runtime/protocol digests for Experiment 2.

## 8. Quality And Reviews

- [ ] Run root and nested harness tests and compile checks.
- [ ] Run diff hygiene, role-skill validation, and task validation.
- [ ] Sequentially review Execution, Memory, Eval, Operator governance, Codex
      runtime, portability/privacy, and official acceptance boundaries.
- [ ] Confirm H0, Raw baseline, target snapshots, and main checkout are
      unchanged.
- [ ] Commit and push the adapter branch before creating
      `experiment/general-main`.

Required gates:

```text
uv run --cache-dir /tmp/uv-cache pytest -q
cd harnesses/swebench && uv run pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
uv run --project harnesses/swebench python -m compileall -q harnesses/swebench/scripts harnesses/swebench/tests
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
```

## Rollback Points

- After section 1: remove the nested runtime without changing Eval behavior.
- After section 4: retain scorer research but do not expose production CLI.
- After section 5: reject candidate metrics until the broker proves exact
  subject execution; H0-only measurement remains available.
- During qualification: discard the prepared ID and start a new immutable
  preparation after any harness fix.
- Before Experiment 2: do not create the line or request model budget unless
  all adapter acceptance criteria pass.
