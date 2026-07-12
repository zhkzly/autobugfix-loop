# Implementation plan: Raw Codex SDK baseline

## 1. Contracts And Standalone Project

- [ ] Add the separately locked `baselines/raw_codex_sdk` uv project with only
      the pinned preview SDK as a runtime dependency.
- [ ] Define canonical visible case and untrusted process-result schemas.
- [ ] Add the generic prompt template and stable digest calculation.
- [ ] Implement direct `openai_codex` thread/turn execution and streamed JSONL
      event capture without importing `autobugfix`.
- [ ] Add static and runtime import-isolation tests.

Validation:

```text
uv run --project baselines/raw_codex_sdk pytest -q baselines/raw_codex_sdk/tests
uv run --project baselines/raw_codex_sdk python -m compileall -q baselines/raw_codex_sdk/src baselines/raw_codex_sdk/tests
rg -n "(^|[[:space:]])(from|import)[[:space:]]+autobugfix" baselines/raw_codex_sdk
```

## 2. Trusted Eval Baseline Control

- [ ] Add prepared-manifest, visible-bundle, process-result,
      frozen-submission, noninterference, and comparison-report contracts.
- [ ] Derive Raw case references from the existing digest-verified Defects4J
      receipts and H0 report without copying private fields into visible input.
- [ ] Add real per-case Git worktree materialization and trusted diff capture.
- [ ] Add production-source path validation and deterministic failure
      classification.
- [ ] Add Bubblewrap launch, ephemeral hooks-disabled `CODEX_HOME`, process
      timeout, process-group cleanup, and mount-boundary verification.
- [ ] Reuse the existing clean-checkout official evaluator only after trusted
      submission freeze.
- [ ] Add post-score noninterference verification.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_raw_codex_baseline.py
```

## 3. Service And CLI Flow

- [ ] Implement `prepare-raw-codex`, `pilot-raw-codex`, `run-raw-codex`, and
      `report-raw-codex` as thin CLI adapters over the Eval baseline service.
- [ ] Reject dirty source, digest drift, unpinned SDK/model/runtime, invalid
      cohort assignment, duplicate run IDs, partial formal reruns, and formal
      concurrency greater than one.
- [ ] Preserve transport/harness errors separately from valid unsuccessful
      model outcomes.
- [ ] Add deterministic paired reporting against the frozen H0 report.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_cli.py tests/test_raw_codex_baseline.py
```

## 4. Security And Non-Leakage Tests

- [ ] Test that the SDK process cannot read a canary in Autobugfix source,
      Memory, trusted receipt, official output, sibling case output, host home,
      or Docker path.
- [ ] Test that the process can write the target worktree and raw-log mount but
      cannot mutate the visible CaseBundle or standalone runner source.
- [ ] Test hooks and multi-agent features are disabled in the generated
      `CODEX_HOME`.
- [ ] Test the trusted host derives patch and score facts independently of a
      forged Raw process-result file.
- [ ] Test official scoring cannot change the frozen patch, events, timeout
      status, or SDK usage record.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_raw_codex_baseline.py -k "isolation or forged or noninterference"
```

## 5. Real Pilot And Freeze

- [ ] Run Docker doctor against the pinned immutable Defects4J images.
- [ ] Run one production `gpt-5.4-mini` pilot on an already exposed development
      case, preferably `d4j-jacksoncore-2`.
- [ ] Diagnose only harness defects; retain model failure as a pilot result.
- [ ] Run all static/unit/integration checks after the final pilot-driven
      harness fix.
- [ ] Commit the frozen runner and prepare a digest-bound formal Raw manifest.

Real commands:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark doctor --adapter defects4j
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline prepare-raw-codex ...
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline pilot-raw-codex ...
```

## 6. Formal Baseline Experiment

- [ ] Confirm the prepared manifest, branch commit, runner source, prompt,
      lockfile, model, case cohorts, H0 report, and Docker IDs have not drifted.
- [ ] Run all 16 cases once, serially, with no prompt changes or result-driven
      retries.
- [ ] On any harness defect, mark the whole run invalid and stop; never repair
      and resume the same manifest.
- [ ] Generate the deterministic comparison report from completed frozen
      artifacts without new SDK or scorer calls.
- [ ] Report 13-case primary, three-case development, and all-16 secondary
      results separately.

Real commands:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline run-raw-codex ...
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline report-raw-codex ...
```

## 7. Final Quality And Review

- [ ] Run the full root and standalone test suites.
- [ ] Run both compileall checks, diff hygiene, and role-skill validation.
- [ ] Sequentially review Execution, Memory, Eval, Codex runtime,
      portability/privacy, and acceptance boundaries. Inline mode keeps these
      reviews in the main session rather than dispatching implement/check
      sub-agents.
- [ ] Confirm target snapshots and H0 artifacts are unchanged.
- [ ] Record experiment commit, manifest digest, runtime IDs, result digest,
      commands, and limitations in the task and journal.

Required gates:

```text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --project baselines/raw_codex_sdk pytest -q baselines/raw_codex_sdk/tests
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
uv run --project baselines/raw_codex_sdk python -m compileall -q baselines/raw_codex_sdk/src baselines/raw_codex_sdk/tests
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
```

## Rollback Points

- After section 1: remove the standalone project without changing Eval.
- After section 4: revert trusted baseline integration before any model call.
- After the pilot: keep pilot artifacts but do not prepare the formal manifest
  until the runner is committed and all gates pass.
- During the formal run: abort and invalidate the entire run on a harness
  defect; never mutate or selectively resume it.
