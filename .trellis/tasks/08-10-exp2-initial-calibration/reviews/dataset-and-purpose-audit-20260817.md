# Exp2 dataset and project-purpose audit — 2026-08-17

## Verdict

**REVISE before task start.** Keep SWE-bench Verified for calibration and
public adaptation, keep the guarded SWE-bench-Live cohort as separate final
evidence, and defer SWE-bench Pro to a separately frozen replication. Before
the first formal H0 result, prefer a new repo-unique Verified cohort over the
historical ten-case/six-repository cohort.

This task remains a valid H0-only apparatus prerequisite. Completing it does
not complete Autobugfix's project objective. The project objective requires a
later governed chain:

```text
real Execution result -> bounded attribution -> allowlisted harness change
-> deterministic/full checks -> later repository-unseen cases
-> public regression replay -> locked external evidence or rollback
```

## Authoritative product test

Autobugfix is not a leaderboard wrapper. Execution owns target repair state,
Eval owns reproducible scores and evidence, and Operator governance owns
self-improvement transitions. Dataset design is valid only when it lets those
owners demonstrate one evidence-driven harness change without leaking future
results or changing Memory/Eval as treatment variables.

Therefore the primary experimental endpoint is process completion with a real,
governed candidate transition. Pass rate is secondary. Calibration validates
only the path into that experiment.

## Evidence inspected

- Project purpose and loop/harness contract.
- Current and archived Exp2 PRD, design, implementation plan, and research.
- Committed Exp2 coordinator/records/service implementation at `ebb994f`.
- Pinned local SWE-bench Verified snapshot
  `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`.
- Historical protocol `benchmarks/swe-experiment-2.yaml`.
- Local Docker image inventory and raw-eval qualification artifacts.
- Independent purpose, experiment, governance, operability, Verified, Pro,
  and cohort-opposition reviewers in Trellis channel
  `exp2-purpose-plan-review-20260817`.
- Official SWE-bench Verified, SWE-bench Docker, SWE-bench-Live, and
  SWE-bench Pro documentation available on 2026-08-17.

## Dataset findings

### 1. Calibration

Use exactly two easy, repository-distinct Verified cases. Recommended
calibration repositories are Flask and Pylint, leaving every formal repository
unexposed. Calibration success means both attempts produce terminal official
results and complete receipts; resolved versus unresolved does not matter.

No failed case is replaced. Infrastructure-invalid output blocks calibration
and is preserved as evidence. Only selected instance images plus required
shared layers are acquired.

### 2. Historical public cohort v1

The current ten IDs span only six repositories. The first two cases are both
Astropy; the next three contain two Django cases. This design can demonstrate a
bounded process, but it cannot honestly describe the later slices as
repository-unseen. Its final all-ten replay is selection-biased engineering
regression evidence.

The old cohort has no formal H0/H1 measurement. Existing external artifacts are
gold/scorer qualification for three cases, so changing the cohort now does not
discard a measured baseline.

### 3. Proposed public cohort v2

The pinned snapshot contains twelve repositories. Reserve Flask and Pylint for
calibration, then choose one formal case from each remaining repository:

```text
astropy, django, matplotlib, seaborn, requests, xarray,
pytest, scikit-learn, sphinx, sympy
```

Freeze an ordered 2/3/5 schedule:

- source/development: two repositories;
- later development: three previously unexposed repositories;
- frozen-candidate confirmation: five further unexposed repositories;
- optional all-ten replay: explicit selection-biased regression check.

This does not create statistical independence or broad generalization. It does
materially improve the process construct: each adaptive step is followed by
new repository environments rather than a second case from a repository that
already informed the change.

The cost is a new protocol/study/apparatus identity, changing the cohort audit
invariant from six to ten public repositories, targeted contract-test updates,
fresh gold/null qualification, and several selected image pulls/builds. The
scorer, dataset revision, model, H0, Memory fixture, attempts, timeout, public
2/5/10 schedule, sealed schedule, and Operator 3/8/16 budget shape stay fixed.

### 4. H0 feasibility gate

Before exposing H0 evidence to Operator, freeze this feasibility interpretation
for ten valid public H0 outcomes (`R` resolved, `U` unresolved, `I` invalid):

- require `I = 0`;
- require at least two resolved and two unresolved outcomes (`2 <= U <= 8`);
- require at least one actionable failure in the predeclared development
  source slice whose mechanism belongs to the Execution allowlist;
- do not expose future-slice H0 identities, problem contents, or outcomes to
  Operator before their wave;
- if the gate fails, terminate as saturation, floor, invalid apparatus, or no
  legal adaptation signal. Do not replace cases or rescue the same study with
  another dataset.

These are process-feasibility conditions, not statistical thresholds.

### 5. SWE-bench Pro

SWE-bench Pro is valuable for a later replication: the official public release
contains 731 cases from eleven repositories and four language classes, with
long-horizon, often multi-file tasks. Its metadata is small and selected images
can be pulled by per-instance Docker tag.

It should not enter this Exp2 because:

- Autobugfix currently has no Pro adapter or trusted scorer/receipt path;
- the official evaluator recommends Modal and labels local Docker beta;
- upstream tests/run scripts/leaderboard have received corrections;
- the current 900-second/two-attempt budget risks a floor effect;
- adding Pro changes dataset, scorer, image family, task horizon, and languages
  at once.

A later Pro replication should pin its dataset data-file digest, evaluator
commit/tree/submodules, dependencies, run-script digests, image tags and
resolved image digests, and use paired H0/frozen-H1 runs under a Pro-specific
budget. It cannot be pooled with Verified or Live.

## Reporting boundaries

Permitted current-calibration claim:

> The frozen H0 apparatus completed two named, repository-disjoint Verified
> cases with official post-freeze scoring and complete immutable evidence.

Permitted full-pilot claim, if later gates pass:

> In a bounded preregistered engineering pilot, public Verified evidence
> informed a scoped Execution-harness revision, followed by the reported raw
> paired outcomes on later public repositories and six guarded Live cases.

Still prohibited: statistical superiority, leaderboard comparability, broad
SWE-bench/Live/Pro generalization, population safety, language capability,
causal proof, or production readiness.

## Remaining user decision

Choose before exact case selection and any formal H0 data:

- retain v1 for historical protocol continuity and lower setup cost; or
- adopt v2 for repository-disjoint adaptive slices, accepting a new protocol,
  fresh qualification, and targeted apparatus updates.

Recommendation: **adopt v2**. The project is trying to demonstrate transfer of
an evidence-driven harness change, and repository-disjoint later slices are
more important than preserving an unexecuted historical cohort.
