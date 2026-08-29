# Terra Max final executability review — 2026-08-17

## Reviewer identity and scope

- Provider: Codex.
- Model: `gpt-5.6-terra`.
- Reasoning: inherited user configuration
  `model_reasoning_effort = "max"`.
- Channel: `exp2-terra-max-final-review-20260817`.
- Sandbox: read-only.
- No source/task/state edit, image acquisition, real SDK call, benchmark case,
  budget approval, or human approval action was permitted.

OpenAI's official model ID is `gpt-5.6-terra`; `max` is the reasoning
effort, not a `terra-max` model suffix.

## Decision

**BLOCK real calibration now.**

The latest planning package is coherent and implementable, but frozen commit
`ebb994f` implements the old v1 flow. Source hardening, deterministic checks,
new manifests/bindings/OCI receipts, and host isolation proof must precede any
real calibration SDK call.

## Confirmed source blockers

1. CLI stage execution accumulates every case report in memory and records one
   stage only after all cases return.
2. Partial stage reports are rejected; a case-two failure can leave completed
   case one unrecorded and its deterministic run ID non-reusable.
3. `H0_CALIBRATED` continues to formal H0; no calibration terminal state
   exists.
4. Attribution is caller YAML and does not prove a service-owned Operator
   candidate transition.
5. One mutable candidate-binding path and two-revision v1 semantics do not
   implement the one-revision content-addressed MVP.
6. Report is read-only; report/rollback terminal transitions are absent.
7. Source-only projection delivery and v2 per-case metric receipts do not
   exist.

The generic Operator integration and workspace-only checks are reusable but
are not wired into the planned MVP provenance and visibility contracts.

## Planning and data findings

- Task metadata and JSONL manifests parse.
- Task-document whitespace check passes.
- Frozen `ebb994f` worktree is clean.
- All twelve selected rows exist in the pinned 500-row Verified snapshot; the
  snapshot digest matches its metadata.
- Cohort roles are coherent:
  - calibration 2;
  - H0 10;
  - source H1 2;
  - transfer H1 3;
  - reserve/Live/Pro not run.
- The old protocol does not contain the new v2 cohort/visibility/metric
  contracts.

## Deterministic checks

The read-only reviewer could not run pytest because `uv`/pytest required a
writable temporary/cache directory. This was a reviewer-sandbox limitation,
not a source assertion failure.

The supervising host then ran:

```text
UV_CACHE_DIR=/tmp/autobugfix-uv-cache PYTHONDONTWRITEBYTECODE=1 +uv run pytest -q tests/test_swe_exp2_records.py +tests/test_swe_exp2_workspace_only.py
```

Result: `12 passed in 1.26s`. These are v1 tests and do not prove any new MVP
contract.

## Host and image readiness

- Docker daemon is reachable from the supervising host; server version
  `29.7.2`.
- Six selected legacy formal images are present:
  Astropy, Django, Xarray, SymPy, Pytest, and scikit-learn.
- Selected Flask, Pylint, Matplotlib, Seaborn, Requests, and Sphinx images are
  not currently present.
- No selected OCI manifest/config/layer/platform digest set is frozen.
- A real host-bound workspace-only authority/credential/no-call proof remains
  missing.

The reviewer sandbox's Docker socket denial is not evidence that host Docker
is unavailable.

## Metric review

The proposed families are useful after corrections captured in
`metrics.md`:

- source and transfer paired cells must remain separate;
- net paired gain is null with any invalid arm;
- invalid cases remain in scheduled/terminal coverage;
- scorer-only retry is not a Writer attempt;
- transition completeness is N/A without a candidate;
- zero leakage requires audience audit coverage;
- pricing must be pinned and usage service-observed;
- reserve/Live/Pro are `not_run`, not zero;
- no combined five-case effectiveness or significance claim.

These metrics require new v2 trusted receipts and are not derivable
authoritatively from the current v1 coordinator.

## Proven versus missing

Proven:

- coherent final planning package;
- clean v1 source worktree;
- selected dataset rows and snapshot identity;
- old focused tests pass;
- host Docker daemon works;
- old CLI/help and reusable service components exist.

Missing:

- v2 source and fault tests;
- protocol/manifest/records/metric schema;
- content-addressed candidate and OCI bindings;
- six selected images;
- fresh full checks and doctor on the eventual v2 commit;
- host-bound isolation/credential proof;
- later human budget/approval authority.

## Safe next action

Do not run `autobugfix eval exp2 init` or `resume --execute`.

After explicit plan approval, start the Trellis task, create a fresh branch and
worktree from `ebb994f`, and implement source-hardening phases 1–4 before
images or model calls.
