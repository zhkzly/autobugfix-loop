# Design: Experiment 2 execution-only closed-loop pilot

## 1. Scope and ownership

Autobugfix is a loop-and-harness control system, not a one-shot model prompt.
For this task:

| Loop | Owner | This task |
| --- | --- | --- |
| Execution | Execution service and dedicated target task worktree | The only mutable treatment surface |
| Memory | Memory service plus human approval | Fixed empty snapshot only; no loop action |
| Eval | Eval service, official SWE scorer, and external Guard | Frozen measurement apparatus |
| Operator | Operator governance service, trusted store, Git, and human gate | Governs bounded candidate changes; skills remain frozen |
| Exp2 coordination | Trusted `operator_host` coordinator | Owns only study-stage orchestration and immutable stage receipts; never candidate state |

The target repository main checkout, Eval authority root, Operator database,
external Guard root, and project main checkout are not candidate-writable
state. Raw artifacts, immutable receipts, and source-controlled manifests own
the evidence trail.

## 2. Critical apparatus/treatment split

The workspace-only route is necessary infrastructure but must not silently
become an H1-only advantage. The implementation therefore has two commits of
meaning:

1. Apparatus commit: adds the generic exact-subject selector, direct
   workspace-only dispatch, typed result/attribution records, the resumable
   coordinator, paired comparison, and gates.
   It is reviewed, committed, and frozen before any formal arm runs.
2. Treatment commits: H0 is the historical exact subject SHA executed through
   the frozen apparatus; H1a/H1b/H1c are governed non-main candidate SHA/trees
   whose diffs are limited to the Execution-harness allowlist.

The report must call the baseline H0-under-workspace-apparatus, not imply that
it is an unmodified replay of a historical nested-Bubblewrap execution. The
exact subject identity remains historical; the dispatch apparatus is shared.

If the apparatus differs between arms, a scorer/dataset/model/budget digest
drifts, or a candidate edits an apparatus-only path after freeze, the run is
invalid rather than an H1 result.

## 3. End-to-end data flow

~~~text
Pinned protocol + frozen apparatus + empty Memory snapshot + frozen Operator skills
                                  |
                                  v
                   disposable workspace-only experiment environment
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
          exact H0 subject SHA          exact non-main H1 subject SHA
                    |                           |
                    +-------------+-------------+
                                  v
              dedicated target task worktree / visible verifier
                                  |
                                  v
          freeze patch + task/events + raw SDK + bindings + evidence manifest
                                  |
                                  v
                    official scorer, post-freeze and read-only
                                  |
                   +--------------+---------------+
                   |                              |
                   v                              v
      allowlisted public Optimization          encrypted Guard-private
      projection for Operator diagnosis          Holdout receipt/aggregate
                   |                              |
                   v                              v
      one governed candidate action          final report or rollback
~~~

Every arrow carries a typed digest rather than relying on a file name,
environment default, or model assertion. The coordinator can resume only from
terminal receipts; it cannot mutate the Operator store, candidate branch, Eval
authority, or Guard state directly.

## 4. Required records and contracts

### 4.1 Study and treatment binding

Add a typed workspace-treatment binding, owned by the frozen apparatus. Its
canonical digest must include:

- study, cohort, arm, and phase;
- apparatus SHA/tree and protocol digest;
- explicit H0 or H1 subject SHA/tree;
- direct workspace-only mode identifier;
- evaluator runtime, subject runtime, SDK/CLI observations, model, reasoning,
  timeout, attempts, concurrency, and visible verifier command;
- fixed empty Memory fixture digest, frozen Operator role-skill/config/policy
  digests, and the H0/selected-H1 Execution role-skill digest;
- protocol wave, public stage, target public case token/identity, and fresh
  output/worktree roots;
- opaque Operator budget-slot IDs. These IDs are not benchmark case IDs and
  cannot resolve a public or sealed dataset record;
- for H1 only, parent candidate SHA, candidate diff digest, allowed-path scope
  digest, public-evidence cutoff, revision number, and treatment-lock status.

The binding rejects protected/main or non-canonical subjects, a dirty subject
tree, duplicated output root, missing required digest, H1 scope outside the
allowlist, a revision number above two, or a mismatch between requested and
observed direct mode.

### 4.2 Frozen submission and scoring

Reuse the existing submission authority and noninterference receipt. The
apparatus must write, in order:

1. execution ledger and task/event/raw-SDK evidence;
2. frozen submission with patch SHA and evidence-manifest digest;
3. immutable official score record;
4. noninterference receipt that proves the scorer did not mutate or append to
   the frozen submission.

The official scorer is constructed only after step 2. A valid unresolved
score is a measured failure. A scorer infrastructure failure is a separate
invalid run and may rescore the same frozen submission only; it cannot trigger
a Writer retry or candidate revision.

### 4.3 Public feedback projection

Create one owner for the public handoff record rather than letting each
Operator consumer parse raw Eval artifacts. The projection contains only:

- public Optimization case token and known identity;
- arm/subject binding and immutable report digests;
- resolved, harness-invalid, and execution failure-stage flags;
- terminal official result/resolved label, available only after submission
  freeze;
- visible verifier outcome, elapsed time, bounded SDK/call usage, patch-empty
  flag, and artifact-completeness flag;
- links/digests to visible Execution evidence.

It excludes gold patch, fixed revision, hidden tests, raw oracle material,
scorer diagnosis, private Holdout identity, and private Guard paths. The target
Execution Writer never sees the projection for its own completed attempt. The
Operator supervisor may use it only after the run is terminal and only to plan
a later unseen public case; it cannot retry the same officially scored case.

### 4.4 Attribution hypothesis

The coordinator stores an immutable `Exp2AttributionRecord` after each public
projection. It contains:

- source projection ID and digest, study/arm/stage, and failure-stage
  classification;
- Operator hypothesis, confidence, supporting evidence digests, and one
  expected mechanism;
- exactly one proposed allowlisted change scope and a deterministic/full-test
  validation plan;
- parent candidate SHA, revision number, author/approver, and record digest.

The record is explicitly a hypothesis, not a causal finding. The trusted
Operator service validates schema, evidence availability, scope, and revision
budget; it does not convert the model's attribution into ground truth.

### 4.5 Paired comparison and final report

The comparison reducer owns arm pairing and denominator handling. For every
case it emits exactly one of:

| H0 | H1 | Classification |
| --- | --- | --- |
| pass | pass | both-pass |
| fail | fail | both-fail |
| fail | pass | rescue |
| pass | fail | observed-regression |
| invalid | any | invalid-H0-arm |
| any | invalid | invalid-H1-arm |

No invalid row is silently removed. The reducer produces separate public and
sealed aggregate reports. Only public reports may contain per-case identity;
the sealed report contains aggregate counts, fixed denominator, study/treatment
digests, and limitation text.

## 5. Workspace-only execution mode

The current broker invokes an outer Bubblewrap exact-subject command and the
default Codex SDK backend can launch a second Bubblewrap worker. Add an
explicit direct workspace-only mode that:

- preserves the existing exact-subject checkout, isolated target bare remote,
  target main read-only checks, fresh task worktree, capability ledger,
  visible-verifier server, patch freezing, and evidence capture;
- invokes the subject worker directly from the disposable experiment
  environment rather than through the broker Bubblewrap argv;
- configures the SDK adapter for in-process direct dispatch so the adapter does
  not create its own Bubblewrap worker;
- asserts that each SDK request cwd is the dedicated task worktree, never the
  trusted control root or target main checkout;
- records an execution-mode receipt proving no broker or SDK Bubblewrap argv
  was used;
- fails closed if the runtime attempts a different backend, sandbox/approval
  contract, SDK/CLI version, model, or workspace path.

This is not a post-hoc security check. Before live use, an environment
preflight must attest that only the disposable experiment root and target task
worktree are writable to the direct subject process. Credential home,
project-control checkout, Operator state/artifacts, Eval authority, and Guard
roots must be absent or read-only. A host that cannot provide that separation
does not qualify for formal direct-mode runs.

## 6. Cohort and wave design

### 6.1 Data roles

The protocol names ten public cases but only six repository clusters:
astropy, django, sympy, and xarray each appear twice; scikit-learn and pytest
appear once. This makes a random per-case train/test split invalid because
repository knowledge would leak.

Use:

- 2-3 external public calibration cases selected from repositories outside the
  public formal set; exclude their repositories from the Guard pool;
- all ten named Verified cases as public Optimization/development data;
- one H0 run on every public case as the paired reference;
- the external six-case, repository-unique and at-least-four-language Live
  cohort only as final sealed descriptive evidence.

The two official qualification repeats validate materialization/scoring
stability; they are not H0/H1 treatment replications.

### 6.2 Adaptation schedule

Keep three counters separate:

| Counter | Wave 3 | Wave 8 | Wave 16 |
| --- | ---: | ---: | ---: |
| Historical protocol wave | 3 | 8 | 16 |
| Cumulative public cases executed | 2 | 5 | 10 |
| Sealed cases exposed | 0 | 0 | 6 |
| Operator budget slots | 3 | 8 | 16 |

The existing Operator budget API requires exactly `wave` unique identifiers.
Those identifiers are opaque `exp2-budget-slot-*` values supplied by the
coordinator, not public or Holdout case IDs. The unused slots at waves 3 and 8
are reserved opaque allocations. At wave 16, six slots are resolved only by
the external Guard after treatment lock. No Operator projection contains a
sealed identity or result.

| Stage | Public action | Candidate action |
| --- | --- | --- |
| Baseline | H0 on all 10 public cases | no candidate |
| Wave 3 | H1a on first 2 public cases | one permitted diagnosis/revision; 3 opaque budget slots |
| Wave 8 | H1b on next 3 previously unseen public cases | one permitted diagnosis/revision; 8 cumulative opaque budget slots |
| Lock | commit H1c and lock its SHA/tree/diff | no further candidate mutation |
| Wave 16 public | H1c on all 10 public cases | public regression decision only |
| Wave 16 sealed | H0/H1c pairs on all 6 sealed cases | report/rollback only |

The final public replay is deliberately selection-biased. It is an engineering
regression gate, not an independent validation result.

## 7. Operator behavior

Use the existing governance state machine rather than a parallel candidate
state machine. Add a trusted Exp2 coordinator for study orchestration only:

~~~text
PREPARED -> H0_COMPLETE -> H1A_RUNNING -> H1A_TERMINAL
-> ATTRIBUTION_AWAITING -> H1B_LOCKED -> H1B_RUNNING -> H1B_TERMINAL
-> ATTRIBUTION_AWAITING -> H1C_LOCKED -> PUBLIC_REPLAY
-> SEALED_UNLOCKED -> HOLDOUT_COMPLETE -> REPORTED | ROLLED_BACK | BLOCKED
~~~

Each transition requires the prior immutable receipt and is idempotent. A
`resume` command re-reads the stage ledger and continues the first missing
terminal action; it never overwrites a run directory or retries an officially
scored case. The coordinator invokes the existing Operator APIs for triage,
request, Writer, checks, candidate integration, and rollback. Operator store
state remains authoritative for those transitions; the coordinator stores only
stage bindings and references to Operator/Eval/Guard receipts.

Create a named exp2-execution-only experiment profile before the study starts.
It binds model, role permissions, frozen role-skill/policy digests, direct-mode
runtime contract, command timeouts, and a sequence containing only the roles
necessary for supervisor/writer/verifier operation. It must exclude a Memory
maintainer and must not change the existing Operator constitution.

The trusted service, not the Operator model, enforces:

- maximum two revisions;
- candidate diff scope and non-main isolation;
- exact mapping between public case manifests and opaque budget slots;
- only public Optimization projections before lock;
- no Holdout call until a treatment-lock receipt proves H1c is unchanged,
  public regression checks passed, and no earlier Holdout exposure occurred;
- rollback when a deterministic check, scope check, public regression, or
  sealed regression gate fails.

This permits automation of bounded transitions, not blind self-improvement.
Human approval remains required for scope expansion, attribution acceptance,
budget grants, or any action outside the pre-registered allowlist.

## 8. Measurement and statistical interpretation

The primary result for this task is process completion: terminal execution,
valid projection, structured attribution, a scoped revision, verification, and
no regression/leakage gate violation. The sealed paired comparison is secondary
safety/effect evidence, not a population-level causal estimate. Report:

- fixed denominators and raw paired cells;
- rescues, observed regressions, net paired gain, pass rate, and exact
  exploratory paired interval/test;
- public and sealed results separately;
- case/repository/task/language composition, but no unearned subgroup claim;
- first-attempt success, loop rescue, failure stage, runtime, calls/tokens,
  patch size/empty patch, artifact completeness, and noninterference.

With six sealed cases, zero observed regressions still allows an approximately
39 percent upper 95 percent bound for an underlying regression probability.
It can be a conservative study gate, never a broad reliability claim.

## 9. Stop, rollback, and burn conditions

Stop before a model call for missing runtime/skill/Memory/protocol identity,
unsafe direct-mode environment, non-canonical subject, duplicate output root,
or Holdout exposure audit failure.

Rollback and stop the candidate for an out-of-scope diff, deterministic check
failure, invalid submission, scorer-before-freeze, missing evidence, public
observed regression, or sealed observed regression.

Mark the Holdout burned and prohibit further H1 change when any Holdout
identity, aggregate, case result, scorer output, or diagnosis becomes
Operator/Writer/Memory-visible. A new study requires a new independently
sealed cohort.

## 10. Verification strategy

Tests must prove contracts rather than merely call helpers:

- exact H0/H1 identity selection and rejection of protected/ambiguous subjects;
- direct mode uses no Bubblewrap argv and the SDK cwd is exactly the task
  worktree;
- target main/control inputs/authority roots remain unchanged;
- unchanged frozen digests across arms and explicit empty Memory snapshot;
- post-freeze-only scorer invocation and noninterference receipt;
- allowlisted public projection includes only terminal result and visible
  evidence, redacts oracle/private fields, and forbids same-case retries;
- attribution records bind evidence, hypothesis, scope, mechanism, and test
  plan without claiming causal proof;
- coordinator resume is idempotent and cannot become a second candidate state
  authority;
- protocol/public/holdout counters and opaque Operator budget slots cannot be
  confused;
- two-revision cap, treatment lock, wave-16-only Holdout, and burn behavior;
- fixed-denominator paired reduction and no dropped invalid outcome;
- external Guard aggregate does not reveal case identities.

The complete source-validation ladder is listed in implement.md. A real public
calibration plus the official Docker scorer is mandatory after unit and
integration checks; a toy run is only a developer smoke test.
