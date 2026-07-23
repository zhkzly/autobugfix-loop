# Implementation Plan

## Phase 1: Protocol And Identity Types

1. Add `SWESubjectTreatmentRuntime` and schema-v3 parsing/serialization to
   `swe_models.py`.
2. Add full, qualification, and subject-runtime contract digests with tests for
   field inclusion/exclusion.
3. Split `SWERuntime.runtime_id` into evaluator and subject runtime identity
   builders, including host-observed SDK/CLI distribution records.
4. Add fail-closed runtime assertion tests; no fake backend is used in the
   production assertion path.

Exit gate:

```text
uv run --cache-dir /tmp/uv-cache pytest -q \
  tests/test_swe_models.py tests/test_swe_runtime.py
```

## Phase 2: Qualification, Preparation, And Guard Bindings

1. Introduce qualification v4 keyed by qualification contract and evaluator
   runtime identity.
2. Upgrade qualification pool, preparation, private cohort, sealed manifest,
   and Guard bundle schemas to bind the separated identities.
3. Reject legacy/mixed identity records without modifying them.
4. Add tests proving treatment-only changes preserve qualification identity and
   scorer changes invalidate it.

Exit gate:

```text
uv run --cache-dir /tmp/uv-cache pytest -q \
  tests/test_swe_adapters.py tests/test_swe_guard.py \
  tests/test_swe_holdout_guard.py
```

## Phase 3: Exact-Subject Runtime Injection

1. Pass the typed treatment runtime through development and formal service
   paths into `SWESubjectBroker`.
2. Generate isolated config with explicit model, low reasoning, service tier,
   role permissions, and timeouts.
3. Bind expected/observed runtime identities in subject requests, frozen
   submissions, reports, and failure artifacts.
4. Replace formal hardcoded model/budget values with manifest-bound typed
   values.
5. Test that no official result can enter the capability request, Writer
   feedback, or Execution ledger.

Exit gate:

```text
uv run --cache-dir /tmp/uv-cache pytest -q \
  tests/test_subject_broker.py tests/test_swe_models.py \
  tests/test_swe_adapters.py
```

## Phase 4: SDK Pin And Raw Comparator

1. Pin root and standalone Raw dependencies to SDK/CLI `0.144.4`; regenerate
   both lockfiles with uv.
2. Update root config validation and Raw protocol to low reasoning and the new
   source protocol digest.
3. Extend Raw runner metadata/receipts to bind CLI as well as SDK identity.
4. Preserve one-turn/no-feedback behavior and add drift tests.
5. Update Operator Eval skill text only where needed to teach the frozen
   runtime and invalid-run semantics; do not change Operator governance policy.

Exit gates:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_swe_raw_codex.py
uv run --directory baselines/raw_codex_sdk --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
```

`--project` selects the Raw uv environment but does not change pytest's
working directory. Use `--directory baselines/raw_codex_sdk` so pytest reads
the standalone project's `pyproject.toml` and collects only its six tests.

## Phase 5: Full Static And Integration Verification

Run from the candidate branch:

```text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark doctor \
  --adapter swebench_verified
```

Inspect the complete diff for hardcoded host paths, proxy settings, internal
names, target-main writes, fake production paths, and oracle feedback.

## Phase 6: Real Public SWE Calibration

1. Requalify `astropy__astropy-12907` under qualification v4. Require two
   official gold scorer passes and stable image/source identities.
2. Run one real H0 development case using `gpt-5.4-mini`, low reasoning,
   SDK/CLI `0.144.4`, two Writer attempts maximum, and timeout 900 seconds.
3. Require the full Execution loop to finish before official scoring.
4. Inspect frozen request/events/config/task/verifier/evaluator/patch artifacts
   and verify official output is absent from Execution feedback.
5. Run the official scorer exactly once on the frozen final submission. If the
   scorer harness fails, repair only the scorer and reuse the same frozen
   submission. If it returns valid unresolved, retain it without repair retry.
6. Run the Raw development comparator on the same public case after its runtime
   is proven, still as one thread/one turn.

Representative commands:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark qualify-swe \
  --protocol benchmarks/swe-experiment-2.yaml \
  --adapter swebench_verified --instance astropy__astropy-12907

uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark run-swe-development \
  --protocol benchmarks/swe-experiment-2.yaml \
  --adapter swebench_verified --instance astropy__astropy-12907 \
  --run-id exp2-runtime-v3-h0-astropy-12907 \
  --subject-sha f529f09de53183d7ddbf9e05b31a9d3b3fbde008 \
  --model gpt-5.4-mini --max-attempts 2 --timeout-seconds 900

uv run --cache-dir /tmp/uv-cache autobugfix eval baseline \
  run-swe-raw-development \
  --source-protocol benchmarks/swe-experiment-2.yaml \
  --treatment benchmarks/swe-experiment-2-raw-codex.yaml \
  --instance astropy__astropy-12907 \
  --run-id exp2-runtime-v3-raw-astropy-12907
```

## Phase 7: Review And Promotion Readiness

Perform six sequential main-agent review passes because this session is in
inline mode and no independent subagents are available:

1. Execution boundary reviewer.
2. Memory noninterference reviewer.
3. Eval/scoring reviewer.
4. Codex runtime reviewer.
5. Portability/privacy reviewer.
6. Real acceptance reviewer.

Record findings in the task review artifact. Fix all correctness or experiment
validity findings before requesting governed promotion. Commit and push only
after the implementation and real calibration gates pass.

## Stop Conditions

- Stop before any model call if SDK/CLI/runtime identity differs from protocol.
- Stop if Docker/scorer qualification is not stable.
- Stop if generated config does not resolve to low reasoning.
- Stop if Writer can see official/gold/hidden evidence.
- Stop if the candidate tries to modify target main checkout or trusted
  authority state.
- Stop formal Experiment 2 claims until all 16 cases are properly prepared and
  sealed; a one-case run is calibration evidence only.
