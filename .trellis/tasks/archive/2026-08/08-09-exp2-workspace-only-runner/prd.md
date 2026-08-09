# Validate Experiment 2 execution-only closed loop

## Decision and goal

Validate one bounded, evidence-driven Experiment 2 control loop:

~~~text
frozen H0 -> real Execution -> frozen patch/evidence -> frozen Eval score
-> trusted coordinator emits a public terminal result -> structured attribution
-> frozen-skill Operator diagnosis -> one scoped Execution-harness candidate
-> deterministic checks and later public cases -> regression gate -> frozen H1
-> sealed paired Holdout -> report or rollback
~~~

This is a process pilot and small-cohort engineering case study. It is not a
claim of broad SWE-bench, SWE-bench-Live, or real-world generalization.

The H1 treatment may change only the Execution harness. Memory does not evolve,
Eval/scoring does not evolve, and Operator skills or policy do not evolve. Eval
remains mandatory as the independent measurement plane. A trusted Exp2
coordinator may orchestrate the study, but it does not own candidate state or
replace the OperatorGovernanceService.

## Frozen starting facts

- main is a dirty production/control checkout and is never an implementation
  target for this task.
- The historical Experiment 2 runtime authority line is
  experiment/swe-runtime-freeze-v3 at 5946398.
- Its protocol names a fixed H0 subject, ten public SWE-bench Verified
  Optimization cases, six externally guarded SWE-bench-Live Holdout cases,
  model gpt-5.4-mini, low reasoning, two writer attempts, a 900-second
  timeout, and cumulative 3, 8, and 16 case waves.
- The current development route rejects a non-H0 subject and the current
  broker/SDK path uses Bubblewrap. A separate workspace-only execution route
  and an exact H1 selector are therefore missing infrastructure, not evidence
  that a treatment has improved.
- The existing formal path already owns exact-subject bindings, frozen
  submission evidence, post-freeze official scoring, noninterference receipts,
  external Holdout guarding, and governed candidate rollback.

## Experimental boundary

### Apparatus versus treatment

Before collecting a formal H0 outcome, implement and freeze one shared
measurement apparatus SHA. It may add exact H0/H1 selection, the
workspace-only dispatch mode, typed result/attribution records, a coordinator,
and paired reporting. It must not change the protocol, scorer, datasets, score
interpretation, model, attempt budget, timeout, Memory content, or Operator
role-skill/policy content after it is frozen. Explicitly allowlisted Execution
role-skill files are part of the H1 treatment surface and must be digest-bound;
Operator role skills are not.

The H0 subject remains the exact frozen historical subject. H1 is a named
non-main candidate SHA/tree produced by the governed Operator line; the formal
checkpoint name remains H_general, while H1 is the report label. A candidate
may not identify main, a protected ref, an ambiguous ref, or an uncommitted
tree.

### Mutable and immutable components

| Component | Rule |
| --- | --- |
| Execution harness, task-worktree setup, direct SDK dispatch, writer retry plumbing | H1 mutable, subject to the scoped candidate allowlist |
| Exact H0/H1 subject identity, protocol, model, reasoning, attempt/timeout/concurrency budgets | Frozen and digest-bound |
| Execution role-skill files | H1 mutable only when explicitly allowlisted and digest-bound |
| Memory | A versioned empty fixture is passed to the existing study snapshot mechanism, digest-bound, and never collected, maintained, approved, or used as feedback |
| Eval materialization, official scorer, dataset revisions, scoring configuration, reports used as score authority | Existing measurement plane remains frozen; only a separate Exp2 adapter/reducer is apparatus and freezes before H0 |
| Operator supervisor/writer/verifier skill files, policy, and candidate state machine | Existing service and skills are frozen; the coordinator calls them but does not replace or evolve them |
| Target repository main checkout and external Guard state | Trusted/read-only to the candidate |

Changing a frozen item invalidates the comparison and starts a new study ID.

## Cohort design and use

The existing ten public cases are clustered in six repositories, and the six
Holdout cases are too few for a strong rate estimate. The following roles are
therefore mandatory.

| Role | Size | Access and purpose | Counts in final claim? |
| --- | ---: | --- | --- |
| External calibration | 2-3 public cases, repository-disjoint from formal public cases | Validate direct SDK, artifacts, scorer, and recovery paths; never used to choose metrics | No |
| Optimization | 10 named SWE-bench Verified cases | Public development evidence for H1 diagnosis and engineering regression | Only as selection-biased development evidence |
| Paired H0 reference | H0 on every formal case | Same case, runtime, Memory digest, scorer, and fresh task worktree as H1 | Yes, as comparator |
| Sealed Holdout | 6 repository-unique, at least 4-language Live cases | External Guard only; H0 and final H1 paired after H1 is frozen | Descriptive small-cohort evidence only |

Calibration case IDs and repositories must be recorded in a non-formal
manifest before use. The Guard exposure audit must exclude them from the
Holdout pool. Calibration never enters a formal denominator.

The study keeps three distinct namespaces; they must not be conflated:

- protocol waves remain 3, 8, and 16;
- public Optimization execution counts are cumulative 2, 5, and 10;
- sealed execution counts are 0, 0, and 6 because Holdout is unavailable
  before final treatment lock.

The existing Operator budget API still requires exactly 3, 8, and 16 unique
`case_ids` on its ordered grants. The coordinator therefore supplies opaque
`exp2-budget-slot-*` identifiers to the Operator budget only. They are
allocation slots, not benchmark case IDs and cannot resolve Eval or Guard
data. The mapping is: wave 3 = two public cases plus one opaque reserved slot;
wave 8 = five cumulative public cases plus three opaque reserved slots; wave
16 = ten public replay cases plus six Guard-owned sealed slots. Actual sealed
identities are resolved only inside the Guard after H1 is locked.

No sealed case is executed or revealed at waves 3 or 8. The Guard is called
only at wave 16 after the final H1 SHA is locked.

## Bounded adaptation protocol

1. Freeze the apparatus and create the study with the versioned fixed empty
   Memory fixture, protocol/role digests, exact H0 identity, and a fixed
   success contract.
2. Run H0 once on all ten public Optimization cases. Preserve all failures in
   the denominator.
3. Run H1a on the first two public Optimization cases. The Operator receives
   only an allowlisted, immutable terminal-result projection after each run is
   complete; the coordinator records the corresponding attribution hypothesis.
4. Permit one scoped H1a-to-H1b Execution-harness revision. Run H1b on the
   next three previously unseen public cases. No official result may trigger a
   retry of the case that produced it.
5. Permit at most one scoped H1b-to-H1c revision. H1c is then frozen: no
   further code/config/skill/Memory/Eval change is legal.
6. Run frozen H1c across all ten public cases as the engineering-regression
   replay. Its result is development evidence because the cohort was used for
   selection.
7. If the public regression gate passes, run paired H0 and H1c across all six
   sealed Holdout cases through the external Guard at wave 16.
8. Report and either retain the candidate only on the experiment line or roll
   it back. No Holdout result may cause another H1 edit.

The study-specific maximum is two adaptive H1 revisions. A third change,
exposure of any Holdout identity/result/diagnosis before final freeze, or
changing any frozen digest burns the study; a new sealed cohort and study ID
are required.

## Information-flow constraints

- Patch, task, event, raw SDK, visible-verifier, config, selected-subject
  execution-role-skill, Memory, runtime, and subject-identity evidence are
  frozen before the official score.
- The target Execution Writer never receives gold patches, hidden tests, fixed
  behavior, official scorer diagnosis, or a post-score retry instruction.
- The Operator may see only an approved public Optimization projection:
  case token/identity, arm, resolved and harness-invalid flags, failure stage,
  terminal official result, runtime/call usage, immutable artifact digests, and
  visible Execution evidence. It may not see scorer diagnosis, gold patches,
  hidden tests, or private oracle material. The result is feedback for later
  unseen public cases only; it cannot authorize a same-case official retry.
- Each diagnosis is an attribution hypothesis, not a causal fact. It must bind
  the source projection digest, failure-stage classification, evidence digests,
  hypothesis, confidence, one expected mechanism, one allowlisted change
  scope, and a validation plan. The trusted service validates the record and
  scope; it does not promote the hypothesis to causal truth.
- Operator advance remains one governed legal action at a time. The writer
  cannot approve, verify, seal, expose Holdout data, or modify trusted state.
- The final report may expose Holdout aggregate paired counts only; case IDs,
  per-case outcomes, and diagnoses remain Guard-private.

## Outcomes, gates, and honest claims

Primary process outcomes are terminal-run completion, valid result projection,
structured attribution, a scoped candidate revision, deterministic/full-check
success, and absence of information-flow violations. Secondary paired outcomes
are rescue (H0 fail/H1 pass), observed regression (H0 pass/H1 fail), both pass,
both fail, and net paired gain:

~~~text
net_paired_gain = (rescues - regressions) / fixed_denominator
~~~

Empty patches, timeouts, valid unresolved repairs, and completed invalid
generations are failures. A runtime/scorer/infrastructure-invalid run remains
separate, blocks the affected arm, and is never silently removed from a rate.

Process completion requires all integrity gates, frozen-digest checks,
noninterference receipts, complete artifacts, and real official scoring.
Candidate acceptance to the experiment line additionally requires zero observed
public H1c regressions and non-negative public net paired gain. Calling the
candidate an improvement additionally requires at least one public rescue.

The sealed result is a conservative no-observed-regression gate: any sealed
H1 regression rejects/rolls back the candidate. A six-case zero-regression
outcome is not a population safety guarantee; it has weak statistical power.
Report raw counts, fixed denominators, paired discordance, exact exploratory
paired tests/intervals, repository/task/language composition, runtime/token
usage, and all limitation language.

Prohibited claims include statistical superiority, unbiased Optimization
performance, broad benchmark generalization, language-specific capability,
population-level zero regression, leaderboard comparability, and causality
after Holdout exposure.

## Workspace-only safety precondition

Workspace-only means that the SDK request is made directly with the dedicated
task worktree as its writable cwd; it does not authorize an arbitrary candidate
to execute against a credentialed or production host.

Before a real candidate run, the apparatus must prove it is running in a
disposable experiment environment with no writable project main checkout,
Operator authority roots, external Guard root, credential home, or unrelated
host paths. If this isolation cannot be supplied without the removed Bubblewrap
layers, the formal path is blocked; only non-claiming toy/calibration work may
run. No host AppArmor, resolver, or nested-user-namespace workaround is in
scope.

## Acceptance criteria

- [ ] Planning documents define the apparatus/treatment split, formal
  denominators, cohort roles, H1 revision cap, Holdout-burn rule, and
  prohibited claims.
- [ ] A frozen shared apparatus can dispatch both exact H0 and explicit
  non-main H1 subjects through a direct workspace-only SDK path while proving
  the request cwd is the task worktree and neither Bubblewrap layer is used.
- [ ] H0/H1 runs bind the same protocol, evaluator/scorer runtime, model,
  budget, empty Memory digest, and frozen Operator role-skill digest. Each
  arm's Execution role-skill digest is recorded; H1 may differ only within the
  pre-registered Execution allowlist.
- [ ] Official scoring is post-freeze only and every score has a valid
  noninterference receipt.
- [ ] The Operator uses frozen skills and an allowlisted public evidence
  projection; no Memory maintenance role or state mutation occurs.
- [ ] A trusted, resumable Exp2 coordinator advances only from immutable
  receipts, uses opaque Operator budget slots, records structured attribution
  hypotheses, and never retries an officially scored case with its own result.
- [ ] The public waves complete under the two-revision limit, then H1c passes
  deterministic and paired public regression gates before any Holdout call.
- [ ] The external Guard performs only the final wave-16 paired Holdout run
  and releases aggregate evidence after H1c is frozen.
- [ ] Reports preserve required raw evidence and describe the result as a
  process pilot/small-cohort case study.
- [ ] Unit/integration checks, full project checks, role-skill validation,
  runtime doctor, real public calibration, and official Docker scoring pass.

## Out of scope

- Memory collection, digestion, maintenance, approval, or skill evolution.
- Eval/scorer/dataset evolution after apparatus freeze.
- Operator skill/policy/state-machine evolution as an H1 variable.
- Production promotion, main merge, canary/release activation, or claiming a
  production-safe rollout.
- Additional adaptive H1 revisions after final freeze or any change after
  Holdout access.
