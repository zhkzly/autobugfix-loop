# Design: Exp2 resume-first execution-harness pilot

## 1. Design objective

Turn the committed execution-only apparatus into one real, resumable,
resume-ready experiment with exactly one governed Execution-harness revision.

The design deliberately optimizes time-to-evidence:

- preserve the existing scorer, dataset revision, model, Memory, Operator
  policy, and role skills;
- harden only contracts required by the real run;
- use one revision and five paired H1 cases;
- stop after a report or rollback;
- leave reserve/Live/paper expansion for a later approved task.

## 2. Owners and boundaries

| Plane | State owner | MVP responsibility |
| --- | --- | --- |
| Execution | `AutobugfixService` and target task worktree | Real repair attempts, verifier, task/event/diff evidence |
| Eval | `EvalBenchmarkService`, official SWE scorer, trusted Eval store | Materialization, submission freeze, score, noninterference, H0/H1 reports |
| Operator | `OperatorGovernanceService`, Git, governance store | Attribution-backed request, Writer/check, candidate commit/integration, rollback |
| Memory | fixed-fixture authority | Dedicated external empty input only; canonical Memory is untouched |
| Exp2 coordination | trusted operator-host coordinator | Schedule, per-case journal, visibility, handoff validation, paired report |

The coordinator may reference another plane's receipts. It may not synthesize
that plane's authority from caller YAML.

## 3. Two-study lifecycle

Calibration and the formal pilot use separate state roots and study IDs so
calibration can terminate without falling into H0.

```text
calibration-v2
  PREPARED
    -> CALIBRATION_RUNNING
    -> CALIBRATION_COMPLETE | CALIBRATION_BLOCKED
    -> terminal calibration receipt

resume-pilot-v2
  PREPARED
    -> H0_RUNNING
    -> H0_COMPLETE | BLOCKED
    -> SOURCE_RELEASED
    -> ATTRIBUTION_AWAITING
    -> CANDIDATE_TRANSITION_AWAITING
    -> CANDIDATE_LOCKED
    -> SOURCE_REPLAY_RUNNING
    -> SOURCE_REPLAY_COMPLETE
    -> TRANSFER_RUNNING
    -> PILOT_COMPLETE | ROLLBACK_AWAITING | BLOCKED
    -> REPORTED | ROLLED_BACK
```

The pilot initializer requires the terminal calibration receipt and the same
apparatus/runtime/fixture/scorer identities. It never mutates or resumes the
calibration ledger.

## 4. Plan and manifest contracts

Introduce Exp2 plan schema v2 with an explicit `study_kind`:
`calibration` or `resume_pilot`. Continue reading v1 plans for audit only;
new writes use v2.

The content-addressed manifest binds:

- study kind/ID and apparatus SHA/tree;
- protocol and pinned Verified snapshot/scorer identities;
- exact ordered case IDs, repository, difficulty, and slice;
- H0 subject SHA/tree;
- model, reasoning, two-attempt loop budget, 900-second timeout, concurrency 1;
- empty Memory fixture and materialized empty-tree digest;
- absolute dedicated Memory root, private ownership/mode, no symlink
  components, and disjointness from project/Eval/Operator/Guard/canonical
  roots;
- Operator role-skill/config/policy digests;
- Execution allowlist and one-used-revision policy;
- result-visibility schedule;
- OCI registry/tag plus resolved manifest/config/layer/platform digests;
- disposable, artifact, worktree, Eval, Operator, Memory, and Guard roots;
- prohibited claims and stop rules.

Paths alone are not frozen identity. Initialization copies or references
content-addressed records and stores their digests in the plan.

The project `.autobugfix-memory` may be nonempty and is not an Exp2 input.
Plan construction requires a separately materialized tree containing only
`active/`, `skills/`, and `skills/approved/`. Eval validates its exact digest
before initialization and at every authority reopen. Operator accepts this
noncanonical root only with the explicit `empty_memory_fixture` capability,
revalidates isolation and digest, then snapshots it. A pre-dispatch failure
burns the study ID; repair uses a new ID rather than mutating prior state.

## 5. Per-case journal and recovery

### 5.1 Attempt intent

Before executing a case, append an immutable `case_attempt_started` event:

- study/stage/arm/case/slice;
- deterministic run ID and output root;
- subject/binding/frozen-input digests;
- attempt kind: normal execution or scorer-only retry;
- predecessor event digest and timestamp.

Only one open intent may exist for a case/stage/arm.

### 5.2 Terminal case receipt

Append exactly one `Exp2CaseAttemptReceipt`:

- terminal status;
- report or failure-artifact digest;
- subject, submission, official result, noninterference, execution/preflight,
  worktree, image, runtime and usage digests where available;
- whether an SDK call occurred;
- whether a frozen submission exists and scorer-only retry is legal;
- started-event digest and terminal timestamp.

Statuses:

- `official_terminal`;
- `preflight_rejected`;
- `execution_infrastructure_invalid`;
- `scorer_infrastructure_invalid`.

### 5.3 Resume reconciliation

On resume:

1. verify the append-only event chain;
2. skip cases with valid terminal receipts;
3. for an open intent, inspect only its trusted run root;
4. adopt an existing verified official report if complete;
5. adopt a broker/preflight failure as an invalid terminal receipt;
6. permit a scorer-only retry only from a verified frozen submission;
7. otherwise terminalize as interrupted infrastructure-invalid;
8. execute the next missing case.

No completed Writer execution is repeated. Stage aggregation occurs only after
all expected case receipts are terminal.

## 6. Calibration flow

For Flask then Pylint:

1. import the selected official image by frozen registry manifest digest,
   verify its platform/layers, and bind it to the local scorer tag;
2. pass protected-process isolation validation;
3. run the H0 development endpoint through real Execution;
4. freeze submission/evidence;
5. invoke official scorer post-freeze;
6. verify noninterference;
7. append the case receipt.

Two official-terminal outcomes produce `CALIBRATION_COMPLETE`. Any invalid
outcome produces `CALIBRATION_BLOCKED`. Resolved/unresolved does not affect
calibration success.

Before calibration, each of the twelve selected Verified cases must pass two
official gold scores, one explicit unresolved null/base score, stable image
identity, and source materialization. Qualification never rebuilds a historical
open-ended dependency environment when a selected official OCI digest is
available.

The MVP treatment allowlist is one mechanism:
`.agents/role-skills/execution/writer/autobugfix-writer/SKILL.md`. Operator,
Eval, Guard, Memory, shared runtime, prompts, and the protected runner are not
candidate surfaces in this study.

The terminal receipt includes both case receipts and proves there is no formal
H0 event in that ledger.

## 7. H0 baseline and information release

H0 runs on all ten ordered repo-unique cases using per-case receipts. Trusted
Eval retains all reports.

The coordinator computes a private feasibility result:

- no invalid case;
- at least two resolved and two unresolved;
- source pair contains an allowlisted Execution-owned failure hypothesis.

Only after this gate, write `Exp2SourceProjectionBundle` containing the two
source projections and a feasibility decision. It excludes all transfer/
reserve identities, contents, per-case outcomes, gold, hidden tests, and
scorer diagnosis. The Operator SDK role receives only this bundle.

If the gate fails, write a terminal no-signal report and stop.

## 8. Attribution and governed candidate transition

The Operator supervisor creates one evidence-bound attribution from the source
bundle. It is a hypothesis, not causal truth.

Candidate creation follows existing governance:

```text
triage -> request -> approval/preflight -> workspace
-> Writer -> fast check/feedback -> candidate commit
-> full check -> experiment/integration
```

Add a trusted `Exp2CandidateTransitionReceipt` exporter/validator owned by
`OperatorGovernanceService`. It binds:

- source attribution and projection bundle;
- request, scope version, approval/grant, Writer and check IDs;
- parent/new SHA/tree and complete diff;
- allowlist/scope, role, policy, Memory, runtime and usage digests;
- candidate commit and integration/line-generation facts;
- content-addressed Eval candidate binding.

The coordinator re-derives or verifies this receipt against Operator state.
Caller-provided author/approver strings have no authority.

Only one transition is accepted. The resulting candidate binding is copied to
trusted content-addressed state and cannot be overwritten.

## 9. H1 source and transfer evaluation

Run the same locked H1:

- source replay: Astropy and Django;
- transfer: Matplotlib, Xarray, and SymPy.

H1 reports must match H0 frozen inputs except subject/tree, Execution-role
skill digest, and allowlisted candidate diff.

The reducer emits per case:

- both-pass;
- both-fail;
- rescue;
- observed-regression;
- invalid-H0-arm;
- invalid-H1-arm.

Source results are development evidence. Transfer results are prospective,
optimizer-unexposed repository evidence, not population inference.

## 10. Decision, rollback, and report

- Any invalid transfer arm -> `BLOCKED`.
- Any transfer observed-regression -> `ROLLBACK_AWAITING`.
- Operator rollback produces a service-issued receipt; the coordinator records
  it and reaches `ROLLED_BACK`.
- Otherwise record `PILOT_COMPLETE`, retain the candidate only on the
  experiment line, and write the final report.
- `report` appends a terminal report event and reaches `REPORTED`; it is
  not merely a read-only status print.

No main merge, promotion, canary, or deployment occurs.

## 11. Isolation and credentials

Formal v2 uses protected process execution. The earlier direct workspace-only
proposal is rejected because hidden-path metadata does not make readable host
authority state inaccessible to an in-process SDK.

Before each SDK call, the preflight and external environment must prove:

- task worktree is the intended writable target;
- project main, Eval/Operator/Memory/Guard authority roots are absent or
  read-only to the subject;
- credential paths and sensitive environment are not visible to the subject;
- SDK request cwd equals the task worktree;
- outer subject execution and the SDK worker both prove Bubblewrap isolation;
- the declared Guard root is masked by the outer sandbox and included in SDK
  hidden-path evidence even though Live/Guard execution is `not_run`;
- the canonical frozen evidence tree is Eval-owned and manifest-verified;
- output roots are fresh, trusted, and disjoint from mutable authority state.

Immediately before every case dispatch, the apparatus checkout must have the
frozen HEAD/tree and an empty tracked/untracked Git status.

If the current host cannot establish that boundary, stop before the model call.

## 12. Compatibility and tradeoffs

- Preserve v1 files/records for audit; do not rewrite the frozen v1 branch.
- Use a new branch, protocol ID, study IDs, record schemas, and apparatus
  receipt.
- One revision sacrifices optimization breadth for faster, interpretable
  resume evidence.
- H0 on ten cases costs more than a five-case smoke but establishes a fixed
  baseline and reserve pool without later case substitution.
- The MVP does not claim paper novelty, statistical significance, benchmark
  generalization, or production safety.

## 13. Required evidence package

The final package contains:

- source and environment manifests;
- per-case event/receipt chain;
- raw Execution/SDK/verifier/scorer logs;
- frozen submissions and noninterference;
- source projection and attribution;
- Operator candidate-transition and optional rollback receipt;
- H0/H1 paired tables;
- runtime/token/cost and attempt breakdown;
- exact commands and a concise resume-safe report.

The report reducer implements the exact numerator, denominator, nullability,
authority, and prohibited-metric rules in `metrics.md`.
