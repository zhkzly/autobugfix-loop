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
- [x] Treat the image Git object database, rather than its setup-mutated
      worktree, as the source of the exact dataset base commit; reject any
      dirty or identity-drifted sanitized destination.
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
- [x] Add one interactive external-Guard cohort command that secret-orders
      Live candidates, excludes Operator-visible identities, resumes encrypted
      receipts, and emits no case IDs.
- [x] Encrypt/authenticate Holdout state and emit public 3/8/16 projections
      without case-identity leakage.

Historical status: all ten protocol-pinned Verified Optimization cases were
eligible under runtime
`sha256:3eb9ba95dbf997c098c1bb893a6123e66e2ebf5f90b7d7be4d0d52ffe4fb5083`.
That evidence does not authorize the rebuilt final candidate. On the current
candidate, `astropy__astropy-12907` passed two isolated official gold scorer
runs under
protocol
`a2f8e4d30a1b9d542b802367fd7c25cd6ecd0f5d2fbf7d3dc00855d36b66bd40`
and runtime
`sha256:3f6445541a9490719b70b37dba9b47d21333c0b61923c713462156ac603cc8f7`.
The immutable qualification receipt digest is
`4abcab1192e3f76e3fb10065008960a46cdaffe8baf824d761993f81f1ff8bc9`.
The other nine Verified qualifications and all six human-Guard Live cases
remain pending, so the cohort items above must stay open until current,
immutable receipts exist.

Real commands:

```text
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark qualify-swe --protocol benchmarks/swe-experiment-2.yaml --adapter <adapter> --instance <instance> [--guard-root <external-root>]
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark qualify-swe-holdout-cohort --protocol benchmarks/swe-experiment-2.yaml --guard-root <external-root> --max-candidates 24
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark prepare-swe --protocol benchmarks/swe-experiment-2.yaml --guard-root <external-root>
uv run --cache-dir /tmp/uv-cache autobugfix eval benchmark seal-swe --prepared <prepared> --guard-root <external-root>
```

Human Live qualification additionally requires a configured
`eval.benchmarks.guard.docker_host`: an owner-only mode-0600 Unix socket inside
the external Guard root whose independently administered VM daemon differs
from regular Eval and publishes
`autobugfix.guard.isolation=dedicated-vm-v1`. The first Guard action persists
the socket fingerprint, daemon ID/profile, and authority digest. Every later
qualification, preparation, seal, and formal run must match that record.
Official scorer code receives the pinned benchmark cache read-only and one
fresh writable client-state directory per run.

For Operator treatment feedback, generate an `OPTIMIZATION` Study binding,
freeze the public formal case report, then register it through
`operator study evidence-register`. Line-bound triage accepts only the returned
`study-evidence:<id>`. Before final Holdout scoring, an interactive
`CANDIDATE` binding closes the line with CAS; metric import cannot reopen it.

## 7. Real Production Acceptance

- [x] Run one eligible development case through real `gpt-5.4-mini`
      Execution, patch freeze, official scoring, and noninterference.
- [x] Fix only generic harness defects; never use official result to retry or
      optimize the candidate.
- [x] Re-run gold qualification after the final harness change.
- [x] Run the pinned real-repository Execution/Memory/Eval acceptance with
      production `gpt-5.4-mini`; preserve the target main checkout and pending
      Memory approval boundary.
- [x] Run the governed Operator Supervisor/Writer acceptance through registered
      Optimization evidence, matched performance baseline, terminal candidate
      binding, Guard metric, and immutable checkpoint.
- [ ] Freeze adapter/runtime/protocol digests for Experiment 2.

## 7a. Raw Codex SDK Comparator

- [x] Reuse the separately locked standalone Raw SDK worker without importing
      Defects4J manifests, results, or case feedback into Experiment 2.
- [x] Add an SWE-specific treatment protocol and prepared manifest bound to
      the frozen ten Optimization cases and current official runtime.
- [x] Implement one-case development and ten-case formal commands that clone
      sanitized source, run one SDK turn, freeze evidence, invoke the official
      scorer, and verify noninterference.
- [x] Record Raw/H0/H_general as distinct treatments. Raw is contextual; the
      primary evolution comparison remains budget-matched H_general versus H0.

Historical Raw pilots used superseded isolation/backend wiring and are not
authority for the current comparator. The current implementation launches the
standalone `baselines/raw_codex_sdk` package directly with
`openai-codex==0.1.0b3`, a one-call private `CODEX_HOME`, `deny_all`,
`workspace-write`, disabled tool network, and no Autobugfix Execution, Memory,
Evaluator, or production backend. Its root and nested tests pass, but no fresh
model run has yet been executed under this final direct-SDK architecture.
Therefore the Raw formal baseline and current Raw development acceptance
remain pending.

Validation:

```text
uv run --cache-dir /tmp/uv-cache pytest -q tests/test_swe_raw_codex.py baselines/raw_codex_sdk/tests
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline run-swe-raw-development --source-protocol benchmarks/swe-experiment-2.yaml --treatment benchmarks/swe-experiment-2-raw-codex.yaml --instance astropy__astropy-12907 --run-id <id>
```

## 8. Quality And Reviews

- [x] Run root and nested harness tests and compile checks.
- [x] Run diff hygiene, role-skill validation, and task validation.
- [ ] Independently review Execution, Memory, Eval, Operator governance, Codex
      runtime, portability/privacy, and official acceptance boundaries on the
      final integration candidate.
- [x] Confirm H0, Raw baseline, target snapshots, and main checkout are
      unchanged.
- [x] Commit and push the integrated adapter candidate as draft PR 10.
- [ ] Obtain trusted-base bootstrap approval and merge PR 10 before creating
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
