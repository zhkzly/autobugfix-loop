# Matplotlib qualification causal trace — 2026-08-17

## Trace boundary

- Observed: `matplotlib__matplotlib-24627` applied the official gold patch and
  passed its F2P test, but both locally built gold scores were unresolved with
  316 P2P failures.
- Intended: two official gold scores resolve, a null/base score remains
  unresolved, all use one OCI image, and source materialization succeeds.
- Scope: qualification/build apparatus only; no H0 model call had occurred.

The intended behavior comes from `prd.md` R1, `implement.md` Phase 6, and the
Exp2 harness contract. This comparison sheet was not an input to SWE-bench or
its containers.

## Time-ordered trace

1. Eval loaded the pinned Verified row and asked upstream SWE-bench 4.1.0 for
   a local image. The row, base commit, gold patch, and scorer were correct.
2. Upstream read the historical Matplotlib `environment.yml`. Its broad
   constraints were resolved against 2026 package channels, yielding, among
   other packages, pytest 9.1.1, pandas 3.0.5, and vcs-versioning 2.2.4.
3. A later pip step pinned numpy 1.25.2, packaging 23.1, setuptools-scm 7.1.0,
   and other packages without closing or checking the entire environment.
   `pip check` subsequently reported eight dependency conflicts.
4. Docker nevertheless emitted an image. The official scorer could only see a
   runnable container, not that its dependency graph violated the historical
   test environment.
5. The gold patch applied and F2P passed. P2P produced 313 failures and 23
   errors, so the official scorer correctly emitted `resolved=false` twice.
6. Qualification correctly wrote an ineligible receipt and stopped the queue;
   it did not replace the case or weaken the scorer.

The first causal deviation is step 2/3: local image construction treated a
historical open-ended dependency declaration plus a partially overriding pip
step as a reproducible environment. The first error message occurred later and
was not the cause.

## Distinguishing counterfactual

The same dataset row, gold patch, test command, and official scorer were run
against the selected SWE-bench registry image pinned to amd64 manifest
`sha256:526b4c5c1b786ecf4dbfecb7c3ef847a0749d2de721932871abb46d8ca3dd6d8`.
Its dependency graph passed `pip check`; sampled P2P tests passed; and the full
official gold score resolved in about 235 seconds. This falsified a gold-patch
or scorer defect and supported image construction as the cause.

## Correction and falsifier

The apparatus now imports each of the twelve selected official images by
platform manifest digest, verifies descriptor/platform/layers and its local
tag, then requires two resolved gold scores, one unresolved null/base score,
stable image identity, and materialization. Qualification-v4 records are not
eligible for the v5 pool.

The correction is falsified if any pinned pull/descriptor differs, any gold is
unresolved, null/base resolves or has a harness error, image IDs differ, or
materialization fails. In every such case the study remains blocked before
protocol freeze.
