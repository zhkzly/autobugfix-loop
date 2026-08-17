# Exp2 resume-first execution-harness pilot

## Goal and user value

Produce the first real, quantified Exp2 result suitable for an honest resume
case study:

```text
real H0 executions -> sanitized failure evidence -> one governed
Execution-harness revision -> deterministic/full checks
-> paired source and repository-unexposed transfer cases
-> retain or rollback -> reproducible report
```

The MVP proves a bounded engineering loop, not benchmark superiority. It
prioritizes real experimental evidence over paper-scale baselines, Memory
evolution, additional datasets, or architectural completeness.

## Project and ownership contract

Autobugfix is a local, repo-agnostic loop/harness control system.

- Execution owns target task/worktree state and is the only treatment surface.
- Eval owns official scoring, immutable result evidence, and noninterference.
- OperatorGovernanceService owns candidate requests, Writer/check runs,
  integration, and rollback.
- Memory remains a fixed empty fixture and performs no collection,
  maintenance, approval, retrieval, or evolution.
- The fixture is a dedicated private root outside project/Eval/Operator/Guard
  state. Existing `.autobugfix-memory` content is preserved and excluded from
  both treatment and execution context.
- The trusted Exp2 coordinator owns only experiment scheduling, visibility,
  handoff receipts, and report state. It must not become a second Execution,
  Eval, or Operator state machine.
- LLM roles propose or review; trusted services, Git facts, deterministic
  checks, official scorers, and receipts own truth.

## Confirmed starting facts

- The execution-only apparatus implementation is committed as
  `ebb994f414e47e61aaf5a7bba2d9c57879f7cc47` on
  `experiment/exp2-execution-only-20260809`.
- Its source checks previously passed: 341 tests passed, one skipped, plus
  compile, diff, role-skill, and runtime-doctor checks.
- No real protected calibration or formal H0/H1 experiment has
  completed.
- Independent source review found release-blocking runtime gaps:
  - a two-case stage is recorded only after both cases return;
  - a case-two failure leaves a completed case-one run unrecorded and
    non-resumable;
  - calibration has no independent terminal state;
  - attribution is not bound to a service-issued candidate transition;
  - the plan has one mutable candidate-binding path;
  - report and rollback states are not propagated by trusted transitions.
- The pinned Verified snapshot revision is
  `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` and contains twelve
  repositories.
- SWE-bench Pro, paper-scale comparisons, and Memory ablations are deferred.

## Frozen MVP dataset

All IDs are frozen before any H0 model call. Selection is curated for a
resume-scale process pilot, not a random population sample.

### Apparatus calibration — formal effect denominator 0

| Order | Case | Repository | Human difficulty |
| ---: | --- | --- | --- |
| 1 | `pallets__flask-5014` | `pallets/flask` | <15 min |
| 2 | `pylint-dev__pylint-4970` | `pylint-dev/pylint` | <15 min |

Both cases validate execution/materialization/scoring/evidence behavior only.
Resolution status is not a calibration success criterion. A failed or invalid
case is preserved and never replaced.

### H0 baseline — fixed denominator 10 repositories

| Slice | Order | Case | Repository | Human difficulty |
| --- | ---: | --- | --- | --- |
| source | 1 | `astropy__astropy-13398` | `astropy/astropy` | 1–4 hours |
| source | 2 | `django__django-10097` | `django/django` | <15 min |
| transfer | 3 | `matplotlib__matplotlib-24627` | `matplotlib/matplotlib` | 15 min–1 hour |
| transfer | 4 | `pydata__xarray-2905` | `pydata/xarray` | 15 min–1 hour |
| transfer | 5 | `sympy__sympy-13091` | `sympy/sympy` | 15 min–1 hour |
| reserve | 6 | `mwaskom__seaborn-3187` | `mwaskom/seaborn` | 15 min–1 hour |
| reserve | 7 | `psf__requests-6028` | `psf/requests` | 15 min–1 hour |
| reserve | 8 | `pytest-dev__pytest-10051` | `pytest-dev/pytest` | 15 min–1 hour |
| reserve | 9 | `scikit-learn__scikit-learn-13439` | `scikit-learn/scikit-learn` | <15 min |
| reserve | 10 | `sphinx-doc__sphinx-9229` | `sphinx-doc/sphinx` | 1–4 hours |

H0 runs once on all ten cases to establish a fixed repo-unique baseline and
feasibility gate. Before candidate lock, the Operator may see only the two
source-case projections and a trusted feasibility decision. Transfer and
reserve case contents/outcomes remain unavailable to the Operator.

### H1 MVP evaluation

- Exactly one governed H1 revision is permitted.
- H1 runs on the two source cases as selection-exposed development evidence.
- The same frozen H1 then runs on the three transfer cases whose contents and
  H0 outcomes did not formulate the candidate.
- The five reserve cases and six guarded Live cases are not MVP requirements.
  They become a separately approved extension only after positive transfer
  signal and zero observed transfer regressions.

## Functional requirements

### R1 — Versioned apparatus and protocol

- Create a new non-main implementation branch/worktree from `ebb994f`; do
  not mutate the frozen v1 apparatus or dirty main checkout.
- Issue a new protocol/study/apparatus identity for this resume MVP.
- Freeze exact case IDs/order, dataset/scorer/runtime/model/attempt/timeout,
  selected OCI image digests, empty Memory fixture, Operator skill/policy
  digests, source visibility, allowlist, and one-revision cap.
- Bind one explicit external empty-Memory root in the plan and revalidate its
  exact tree digest at Eval readiness and Operator Study creation; never
  clear, relocate, or substitute the project's canonical Memory.
- Acquire only selected instance images and required shared layers.

### R2 — Per-case durable execution and resume

- Record an append-only case-attempt receipt before advancing a multi-case
  stage.
- Each case reaches exactly one terminal classification:
  `official_terminal`, `preflight_rejected`,
  `execution_infrastructure_invalid`, or `scorer_infrastructure_invalid`.
- Resume reuses completed receipts and executes only missing cases.
- A scorer-only retry may reuse the same frozen submission; no result may
  trigger a new Writer attempt on that case.
- No invalid or failed case is silently dropped or replaced.

### R3 — Calibration-only lifecycle

- Calibration ends in `CALIBRATION_COMPLETE` or
  `CALIBRATION_BLOCKED`.
- It cannot fall through into formal H0 without a separately frozen pilot
  authorization.
- The terminal calibration report reconstructs both case outcomes and all
  frozen input, execution, scorer, noninterference, and protected-root facts.

### R4 — H0 feasibility and visibility

- H0 requires ten terminal, apparatus-valid reports; any invalid arm blocks
  the study.
- A trusted feasibility gate requires at least two resolved and two unresolved
  H0 cases and at least one allowlisted Execution-owned failure in the source
  pair. Otherwise terminate as saturation, floor, or no legal adaptation
  signal.
- Future transfer/reserve projections remain audience-restricted until their
  stage. The Operator never receives gold patches, hidden tests, scorer
  diagnosis, future H0 outcomes, or Holdout data.

### R5 — One evidence-bound governed candidate

- Source H0 projections produce one structured attribution hypothesis with
  supporting receipt digests, expected mechanism, allowlisted scope, and
  validation plan.
- Candidate creation must use the existing Operator request/Writer/check/
  commit/integration lifecycle.
- A service-issued transition receipt binds:
  attribution digest, request and grant, parent/new SHA and tree, Writer run,
  complete diff/scope digest, fast/full check, integration, candidate binding,
  role/policy/Memory/runtime identities, and usage.
- Only that receipt may move the coordinator to `CANDIDATE_LOCKED`.
- The candidate binding is content-addressed and immutable.

### R6 — Source replay, transfer, regression, and rollback

- Run the frozen candidate on source 2 and transfer 3 with the same H0
  protocol/model/budget/scorer/Memory inputs.
- Pair every result as both-pass, both-fail, rescue, observed-regression, or
  invalid arm.
- Any transfer regression rejects the candidate and requires an
  Operator-issued rollback receipt.
- A transfer rescue with zero transfer regressions supports the narrow phrase
  “observed transfer rescue on this three-repository pilot.”
- Source-only rescue supports only “development rescue.”
- No rescue yields a valid negative result; do not force another revision.

### R7 — Resume-ready report

Publish a reproducibility index and machine-readable report conforming to
`metrics.md`, including:

- raw fixed-denominator H0/source/transfer paired cells;
- case/repository/difficulty/slice composition;
- first-attempt versus loop-rescue outcomes;
- failure stages, patch size, verifier results, runtime, calls/tokens, and cost;
- every Git/config/skill/policy/Memory/dataset/scorer/image/receipt identity;
- candidate diff/scope and retain/rollback decision;
- integrity failures and invalid outcomes;
- exact commands and limitation language.

Source and transfer paired metrics remain separate; net paired gain is null
when any arm is invalid; reserve/Live/Pro are `not_run`; unknown usage/cost is
null rather than estimated from untrusted logs.

Private credentials, hidden tests, gold patches, Guard-private identities, and
scorer-private diagnosis remain excluded.

## Acceptance criteria

- [ ] A new frozen MVP protocol contains the exact 2 calibration and 10 H0 IDs
  above, one case per repository, source/transfer/reserve visibility, and
  selected OCI digests.
- [ ] Focused and full source validation passes on a clean non-main apparatus
  commit.
- [ ] Forced interruption after calibration case one proves case two resumes
  without rerunning case one.
- [ ] Calibration reaches an explicit terminal state with two complete or
  explicit invalid receipts and cannot start H0 automatically.
- [ ] Protected receipts prove outer and SDK-worker Bubblewrap, exact task
  worktree cwd, and hidden authority/credential roots; missing isolation proof
  produces no SDK call.
- [ ] Plan/readiness/Operator all bind the same dedicated empty-Memory digest,
  while the pre-existing canonical Memory tree remains byte-for-byte intact.
- [ ] H0 produces ten fixed-denominator, apparatus-valid terminal reports or
  an honest feasibility stop.
- [ ] Only source projections are released before candidate lock.
- [ ] One attribution is bound to one real, VERIFIED, integrated,
  content-addressed candidate transition.
- [ ] The frozen candidate completes source and transfer evaluation without
  same-case result-driven retries.
- [ ] A transfer regression records trusted rollback; otherwise retain/no-gain
  is reported according to raw outcomes.
- [ ] The final report is reproducible from immutable artifacts and contains
  no unsupported paper, leaderboard, broad generalization, production-safety,
  or population-level claim.

## Out of scope

- Memory evolution or Memory-on ablation.
- Eval/scorer/dataset-revision evolution.
- More than one H1 revision.
- H1 execution on reserve 5 or guarded Live 6.
- SWE-bench Pro, additional models, independent optimization lineages,
  statistical power studies, publication baselines, or paper claims.
- Production promotion, main merge, PPE, canary, or deployment.
- Automatic continuation after a negative/no-signal/rollback result.

## Stop conditions

- Any frozen digest, case order, result-visibility rule, model/budget, or
  authority identity drifts.
- Workspace isolation or credential/protected-root proof fails.
- A selected image cannot be resolved to the pinned OCI identity.
- Any formal H0 run is apparatus-invalid.
- H0 has no legal adaptation signal.
- Candidate provenance, allowlist, verification, or integration receipt is
  incomplete.
- Any transfer regression occurs.
- Any scorer-private/future/Guard evidence reaches Operator, Writer, or Memory.

Every stop preserves artifacts and produces a report. It never substitutes a
case, weakens isolation, expands scope, or edits the protocol in place.
