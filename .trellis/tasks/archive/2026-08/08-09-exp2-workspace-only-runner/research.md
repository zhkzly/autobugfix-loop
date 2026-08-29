# Research: Experiment 2 cohort and boundary review

## Sources inspected

- Historical source of truth: experiment/swe-runtime-freeze-v3 at 5946398.
- Protocol: benchmarks/swe-experiment-2.yaml at that revision.
- Formal execution/scoring paths: subject broker, SWE benchmark service,
  submission authority, Guard, Holdout Guard, reporting, and Operator
  governance service at that revision.
- Project contracts: Trellis harness contract, Operator governance policy, and
  task-start checklist.
- Two independent read-only reviews: one statistical/cohort review and one
  architecture/boundary review.

## Confirmed implementation facts

1. The protocol fixes H0, gpt-5.4-mini, low reasoning, two attempts,
   900-second timeout, one-case concurrency, ten public Verified Optimization
   cases, six sealed Live Holdout cases, and cumulative 3/8/16 waves.
2. The public cohort has ten cases but only six repository clusters:
   astropy, django, sympy, and xarray occur twice; scikit-learn and pytest
   occur once. It covers bugfix, feature, and maintenance.
3. The external Holdout Guard enforces six repository-unique cases, no
   repository overlap with Optimization, and at least four language families.
4. The historical formal path already freezes patch/evidence before scoring and
   produces a noninterference receipt. It binds an approved Memory snapshot
   and supports protected external Holdout state.
5. The current development path explicitly rejects a subject that is not H0.
   The formal path uses the checkpoint name H_general for a candidate rather
   than a generic H1 string.
6. The current subject broker starts an outer Bubblewrap exact-subject process;
   the default Codex SDK backend can start another Bubblewrap worker. The
   workspace-only direct path has not been implemented.
7. The current formal study service uses canonical Memory and governance state.
   This study therefore supplies a committed, versioned empty fixture through
   the existing snapshot mechanism; it does not change Memory code/config or
   invoke Memory maintenance.
8. The current Guard can make Holdout cases available at waves 3 and 8. That
   capability conflicts with an adaptive H1 study and must be blocked until
   final treatment lock.

## Cohort conclusion

Ten public Optimization plus six sealed Holdout cases are enough to validate a
bounded process pilot:

~~~text
Execution -> frozen Eval result -> Operator diagnosis -> one scoped H1 change
-> tests -> later public cases -> final frozen H1 -> sealed evaluation
~~~

They are not enough for a defensible population-level generalization claim.
The public set is selection data after any H1 adaptation, and the Live
Holdout differs from Verified in both benchmark and language composition.

Six cases provide weak regression detection. With zero observed Holdout
regressions, the approximate one-sided 95 percent upper bound for an
underlying regression probability is 39 percent. Therefore zero observed
regression can be a conservative release gate for this study, never a claim
that regression risk is near zero in general.

## Selected cohort policy

| Population | Policy |
| --- | --- |
| Calibration | 2-3 public, repository-disjoint cases outside both formal public and sealed cohorts; excluded from all formal denominators |
| Public Optimization | all ten named Verified cases; allowed for diagnosis and final engineering regression only |
| H0 reference | one frozen-H0 run per formal case, paired with the H1 arm under the same frozen apparatus |
| Sealed Holdout | six external, repository-unique Live cases; no result/identity/diagnosis access until H1c is locked |

The pre-registered adaptation cap is two H1 revisions. The public assignment
uses 2/5/10 cumulative cases at protocol waves 3/8/16, while sealed execution
is 0/0/6. Because the existing Operator budget model requires exactly 3/8/16
identifiers, the coordinator uses opaque budget-slot IDs in a separate
namespace; they are not benchmark case IDs. No Holdout call is made until wave
16. A final H1c public replay catches obvious capability loss; it does not
restore independent validation after public selection.

## Architecture conclusion

The implementation should reuse, rather than recreate:

- exact subject/runtime bindings;
- frozen submission and post-freeze scoring;
- noninterference receipts;
- external encrypted Holdout Guard;
- Operator request/candidate/verification/rollback state machine;
- existing metric receipts and raw artifact retention.

Missing pieces are:

1. a frozen shared workspace-only measurement apparatus;
2. exact non-main H1 selection without weakening the old H0-only endpoint;
3. a direct SDK dispatch contract with worktree-cwd and no-Bubblewrap proof;
4. fixed empty Memory snapshot setup compatible with trusted study creation;
5. a frozen Operator profile that excludes Memory maintenance;
6. a redacted public Optimization handoff record;
7. structured attribution hypotheses that do not claim causal proof;
8. a resumable trusted Exp2 coordinator that owns study-stage receipts only;
9. paired comparison, fixed-denominator reporting, treatment lock, and
   wave-16-only sealed unlock enforcement.

## Risks that remain blocking until implementation proves them

- Direct execution without Bubblewrap is unsafe on a credentialed shared host
  unless a disposable experiment environment independently enforces writable
  roots. Absence of a Bubblewrap command alone is not isolation.
- Adding candidate support only in Eval code after H0 has run would confound
  the treatment. The apparatus must freeze first.
- The existing Operator budget API's `case_ids` length invariant is 3/8/16;
  treating those fields as the public 2/5/10 or sealed IDs would make the
  proposed loop invalid or leak Holdout state. The coordinator must use an
  opaque slot namespace.
- Letting official scorer diagnosis enter Writer or Memory would create oracle
  leakage even for public cases.
- Using Holdout outcomes at waves 3/8 would burn the final evaluation before
  H1 is frozen.
- A one-case pilot or a public-only final score cannot support an H1
  generalization claim.

## Explicitly prohibited reporting language

Do not report broad SWE-bench or SWE-bench-Live generalization, statistical
superiority, population-level zero regression, an unbiased Optimization score,
language/task subgroup capability, independent repeated-wave replication,
leaderboard comparability, or causal benefit after any Holdout exposure.

## Resolution of the independent task audit

The planning task is retained as the single Exp2 task, but its executable
contract is now narrower and explicit:

- H1 changes only Execution harness code/config/runtime and explicitly
  allowlisted Execution role-skill files; Operator role skills, Memory logic,
  Eval scorer semantics, and Operator policy/state ownership are frozen.
- Protocol wave, public evaluation count, sealed exposure count, and Operator
  budget slots are separate fields with a tested mapping.
- A trusted coordinator advances the study from immutable receipts and can
  resume safely, while OperatorGovernanceService, Eval, and Guard retain their
  state ownership.
- Public terminal results may inform later unseen cases. They cannot trigger a
  same-case official retry.
- Attribution is stored as an evidence-bound hypothesis with an allowlisted
  change and validation plan, never as an asserted causal explanation.
