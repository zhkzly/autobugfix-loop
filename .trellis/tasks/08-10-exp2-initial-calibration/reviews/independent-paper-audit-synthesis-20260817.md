# Independent Exp2 paper/program audit synthesis — 2026-08-17

## Process

Four fresh reviewers were spawned in an independent Trellis channel:

- literature/novelty;
- experimental methods/statistics;
- benchmark/dataset validity;
- systems implementation and publication readiness.

They received authoritative project/task/protocol documents but not the prior
review conclusions. After first-round reports, all four received the same
anonymized contradiction list and performed a source-backed cross-examination.
No reviewer edited files, created tasks, mutated state, downloaded benchmark
images, or ran cases.

Channel: `exp2-independent-paper-audit-20260817`.

## Decision

**REVISE-STAGED. Do not execute calibration against `ebb994f`.**

The project remains valuable for a resume and can support a workshop/systems
case study. A full-paper path is conditional, and the broad novelty claim
"self-improving coding harness" is not defensible.

## Verified source blockers

### Calibration batch recovery

The CLI executes all calibration cases into an in-memory report list and calls
the coordinator only after the complete list exists. The coordinator accepts
only the complete stage. If case two fails after case one has an official
result, no case-one stage receipt exists; the next run reuses a deterministic
run ID that Eval rejects as already existing.

Required behavior: append-only per-case attempt receipts, completed-case reuse,
explicit infrastructure-invalid records, and resume of only missing cases.

### Calibration terminal state

`H0_CALIBRATION` transitions to `H0_CALIBRATED`, whose next action is formal
`H0_PUBLIC`. The H0-only Trellis child has no explicit terminal state or
terminal handoff receipt.

Required behavior: `CALIBRATION_COMPLETE`/`CALIBRATION_BLOCKED` semantics that
cannot fall through into formal H0 without a new task/study authorization.

### Attribution-to-candidate provenance

`Exp2AttributionRecord` binds a projection, parent SHA and caller labels, but
`record_attribution()` advances directly to `H1B_LOCKED`/`H1C_LOCKED`. It does
not consume a service-issued Operator request, grant, Writer run, verification,
integration, diff/scope or immutable candidate-transition receipt.

Required behavior for the later pilot: a trusted candidate-transition receipt
must bind attribution -> request/grant -> Writer/check -> integration -> new
subject/tree/binding before a candidate can become locked.

### Revision binding

The study plan carries one mutable `candidate_binding_path`; the strong
`Exp2WorkspaceTreatmentBinding` schema is not the production binding consumed
by the CLI path. Distinct H1a/H1b/H1c lineage is not established merely by a
revision integer or caller-provided attribution.

Required behavior: content-addressed per-revision bindings and candidate
lineage checked against Operator authority.

### Report and rollback propagation

The CLI report is read-only. `REPORTED` and `ROLLED_BACK` are recognized state
names without coordinator transitions, while failed gates become `BLOCKED`
without consuming an Operator rollback receipt.

Required behavior for the later pilot: immutable report and rollback handoff
events tied to trusted services.

## Research novelty finding

Recent primary work already covers most generic claims:

- ADAS, SICA and DGM: automated/self-referential agent-code search;
- Meta-Harness, AHE, RHO and Self-Harness: evidence-driven full-harness
  optimization and regression validation;
- VeRO and HarnessOpt-Bench: versioned candidates, controlled/hidden
  evaluation, budgets and auditability;
- SEA/SGM: versioned self-edits with statistical certificates;
- RewardHackingAgents: evaluator tampering and train/test leakage as measurable
  integrity outcomes.

The strongest remaining empirical question is:

> Given the same immutable candidate stream and compute budget, does the full
> Autobugfix control plane prevent more prespecified integrity failures and
> harmful promotions than a simpler reduced-control trusted runner, while
> preserving valid repair utility at acceptable cost?

This is a utility-integrity systems study, not a first self-improving-agent or
first harness-optimizer paper.

## Safe comparative design

Do not grant an unsafe arm real authority. Produce immutable candidate/evidence
packages once, then:

- let the full governance path make real trusted-state decisions;
- replay the same packages through reduced-control policies offline;
- deny shadow arms credentials, real Eval/Guard state, merge and deployment;
- inject blinded faults: scorer mutation, leakage, stale/replayed receipts,
  subject substitution, out-of-scope edits, same-case retry, post-lock change,
  missing/reordered evidence and rollback bypass;
- compare block/detection rate, false blocks, accepted valid utility, harmful
  acceptance, reconstruction success, runtime, tokens and cost.

The contribution is falsified if full governance makes materially the same
decisions as the simpler baseline, prevents no distinct faults, damages valid
utility without compensating integrity gain, or loses all apparent benefit on
repository-unexposed evaluation.

## Dataset decision

For the descriptive pilot:

- 2 calibration-only Verified cases from 2 exclusive repositories;
- 10 constrained-random, repository-unique Verified cases in 2/3/5 adaptive
  slices;
- 6 Guard-qualified Live cases across repository/language strata;
- immutable OCI manifest/config/layer digests for selected images;
- Pro denominator zero until its task/evaluator/image defects are remediated.

Verified and Pro both have substantial 2026 quality/contamination audits. The
pilot must include case qualification and narrow claims. Live requires a pinned
host/evaluator and preregistered repeated gold/null qualification. None of the
three datasets measures the full on-call product scenario.

The 2+10+6 design is descriptive and estimates no population effect. Exact
paper sample size must follow pilot estimates of paired effects, repository
clustering, optimizer-lineage variance, integrity-event rates, false-block
rates and cost. Fixed numbers proposed without that model were rejected.

## Rejected reviewer claims

- "The only missing step is a real run" — false; the source blockers above
  precede calibration and the formal H1 loop.
- "Attribution provenance is fully implemented because a record contains
  parent SHA/author/approver" — false; caller labels are not a service-issued
  candidate transition.
- Fixed requirements such as 30, 50, 100 or 245 repositories/pairs — rejected
  without an estimand, minimum relevant effect and variance model.
- A live ungoverned arm — rejected; use authority-free shadow replay and fault
  injection.
- Pro as a guaranteed future replication — rejected for now; it is only
  conditionally eligible after an audited repaired release.

## Minimum staged parent program

### P0 — Research contract and preregistration

Freeze the single utility-integrity claim, estimands, candidate-stream
comparator, fault catalogue, dataset selection algorithm, visibility/stopping
rules, artifact contract and falsification conditions. No model call.

### P1 — Apparatus source hardening

Implement per-case calibration resume, calibration-only terminalization,
candidate-transition provenance, content-addressed revision bindings, and
report/rollback propagation. Add forced-interruption and synthetic fault tests;
freeze a new apparatus commit and receipt.

### P2 — Two-case calibration

Run each case independently through real Execution and official Eval. Prove
that completed cases are not rerun, invalid outcomes are terminal evidence,
official scoring is post-freeze, protected inputs are unchanged, and the child
terminates without entering formal H0.

Exit claim: resume/reproducibility artifact only.

### P3 — Descriptive pilot plus safe integrity contrast

Run the frozen 10+6 process pilot and at least one genuine
evidence->attribution->governed-candidate->later-repository lineage. Replay the
same immutable candidate stream through offline reduced-control policies and
run preregistered faults.

Exit claim: workshop/systems case study, including a valid negative result.

### P4 — Conditional full-paper study

Only if P3 yields an identifiable utility/integrity phenomenon and complete
evidence: estimate effects/variance/cost, preregister sample size and analysis,
run multiple independent optimizer lineages, compare closest compute-matched
baselines, and use a newly qualified external replication population.

Entering P4 does not guarantee a paper. If P3 falsifies the contribution, stop
the paper path while retaining the resume/system artifact.

## Recommended immediate action

Create a planning-only parent task for P0-P4, make the current calibration task
the P2 child, and create P0/P1 as the only initially actionable children. Do
not start P2 until P0 and P1 have passed independent review and frozen a new
apparatus identity.
