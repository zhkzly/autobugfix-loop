# Exp2 paper-readiness audit — 2026-08-17

## Verdict

**NOT YET a full empirical paper.** The proposed 2-case calibration followed by
a 10-Verified/6-Live bounded pilot can produce a strong resume artifact and a
workshop/demo/engineering case study. It cannot support broad capability,
safety, non-regression, or generalization claims.

Design the pilot to be paper-compatible from the first run, but expand only
after it produces an actionable Execution-owned failure, a governed candidate,
and transfer evidence on repository-unseen cases.

## Novelty boundary

The general claim "an LLM improves its own coding-agent harness" is already
occupied by prior work:

- Darwin Godel Machine, arXiv:2505.22954;
- A Self-Improving Coding Agent, arXiv:2504.15228;
- Automated Design of Agentic Systems, arXiv:2408.08435;
- GEPA, arXiv:2507.19457;
- Retrospective Harness Optimization, arXiv:2606.05922;
- Meta-Harness, arXiv:2603.28052.

RHO and Meta-Harness are especially close because they already optimize full
harness code from trajectories/validation and evaluate held-out coding tasks.

The defensible research question is narrower:

> Under equal optimization and execution budgets, can an external fail-closed
> control plane preserve harness-optimization gains while reducing evaluator
> gaming, unauthorized scope, evidence drift, Holdout leakage, invalid
> promotion, and observed regression?

This makes governance and experimental integrity the treatment of scientific
interest; ordinary harness optimization is the substrate, not the novelty.

## Resume-ready pilot

The pilot remains valuable even if its result is negative. It should show:

- real repo repair through the production Execution loop;
- fixed Memory/Eval/model/runtime and repo-unique v2 dataset slices;
- immutable result -> attribution -> candidate-transition chain;
- deterministic/full checks before later cases;
- raw paired rescues/regressions/invalid outcomes;
- public and sealed results kept separate;
- rollback and burn behavior;
- wall time, calls/tokens, cost, patch size, and artifact completeness;
- explicit limitations and no benchmark-wide claim.

The two-case calibration is only apparatus evidence. The later 10+6 pilot is
an exploratory single optimization lineage, not statistical confirmation.

## Publication expansion gates

Do not start a paper-scale expansion unless the bounded pilot proves all of:

1. zero apparatus-invalid formal runs;
2. a mixed H0 baseline with legal Execution-owned headroom;
3. at least one evidence-bound, governed harness revision;
4. at least one rescue on a repository whose outcome did not formulate that
   revision;
5. no observed public or sealed regression under the frozen gates;
6. complete reproducibility and no information-flow violation.

Failure of a gate is a valid negative result, not permission to replace cases
or change protocol within the same study.

## Minimum comparative program after a positive pilot

Exact sample size must follow a power analysis using pilot estimates of paired
effect, repository clustering, stochastic variance, and budget. Do not promote
an unsupported fixed number such as 30, 50, or 100 repositories to a protocol
requirement before those estimates exist.

At minimum compare compute-matched arms:

1. frozen H0;
2. the same candidate generator/search budget without the trusted governance
   controls;
3. full governed evidence-driven optimization;
4. at least one closest optimization baseline, preferably RHO/Meta-Harness-
   style full-harness search or GEPA-style prompt-only optimization;
5. a matched human-engineered change when feasible.

Run multiple independent optimization lineages. The two Writer attempts inside
one case are conditional loop attempts, not random seeds or independent
replications. Preserve attempt-1 and rescued-attempt outcomes separately.

## Governance ablations and fault tests

Benign successful runs cannot establish governance value. Pre-register
isolated fault-injection tests that attempt:

- attribution-free or evidence-unbound changes;
- out-of-allowlist edits;
- stale/mutable candidate bindings;
- forged caller-supplied metrics or approvals;
- scorer-before-freeze or submission mutation;
- future-wave/Holdout exposure;
- regression-gate bypass;
- post-lock edits and rollback/burn violations.

Ablate attribution, scope enforcement, evidence freeze/noninterference,
disclosure control, and regression/rollback separately. Report blocked unsafe
transitions, false blocks, runtime/cost overhead, valid candidate yield, rescue
rate, and regression rate.

## External replication

SWE-bench Pro should remain a separate frozen replication with its own
evaluator, images, budget, H0, protocol, and analysis. A second model or a
second dataset is important for a full paper, but Pro results must not be pooled
with Verified/Live or used to rescue an unfavorable Exp2 result.

## Statistical framing

- Repository is the primary clustering unit; candidate revisions are adaptive
  history, not independent arms.
- Use raw fixed-denominator paired tables first.
- Exact paired tests and intervals are exploratory in the pilot.
- Paper-scale analysis should use a preregistered cluster-aware method selected
  after pilot variance estimation.
- Report first-attempt and loop-rescue outcomes separately.
- Never treat two attempts, qualification repeats, public replay, or multiple
  candidate revisions as independent observations.

## Claim ladder

### Calibration

Only claim that the frozen apparatus executed and scored two real cases with a
complete evidence chain.

### Resume/pilot

Claim a bounded governed engineering process and its exact observed outcomes.

### Workshop/case study

Add transparent comparisons and fault tests, still without population claims.

### Full empirical paper

Requires compute-matched baselines, independent optimization lineages,
cluster-aware preregistered analysis, larger held-out repository diversity,
and a separately frozen external replication.
