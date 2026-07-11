# Design: governed independent benchmark experiments

## 1. Purpose and boundaries

This design extends Autobugfix as a loop-engineering and harness-engineering
control system. It does not replace the four loops with benchmark-specific
automation.

- Execution still owns real target-repository repair tasks and task worktrees.
- Memory remains a reviewed evidence-to-wiki/skills loop and is frozen during
  paired experiments.
- Eval owns benchmark materialization, sealed cases, official oracles, scores,
  and diagnoses while invoking the real Execution service.
- Operator owns requests for Autobugfix self-improvement but not authoritative
  state transitions, integration, budgets, or promotion.

The implementation introduces five independently verifiable deliverables:

1. named governed experiment integration lines;
2. a Defects4J adapter and eligible 16-case manifest;
3. SWE-bench Verified and SWE-bench-Live adapters and eligible 16-case
   manifest;
4. the independent `H0 -> H_bug` experiment;
5. the independent `H0 -> H_general` experiment.

## 2. Three-plane isolation

```text
Trusted host/control plane
  protected origin/main policy and Guard implementation
  external Operator SQLite state and raw artifacts
  signed/manual budget grants and immutable receipts
  full case manifests, sealed holdout data, official oracles
               |
               | typed service calls and filtered projections
               v
Subject plane
  H0, H_bug, H_general Autobugfix releases
  experiment/bugfix-main and experiment/general-main
  per-request Operator candidate worktrees
               |
               | real AutobugfixService Execution tasks
               v
Target-data plane
  benchmark repository at pinned buggy base
  generated .autobugfix/config.yaml
  per-case target task worktree
  Writer changes, verifier logs, generated patch
```

The trusted host may execute candidate subject code in an isolated process,
but candidate imports, config, and files never replace the host's policy,
store, approval, budget, case-sealing, or Git-admission implementation.

Project Codex hooks apply only to the trusted interactive Operator host. All
SDK Writer, Evaluator, supervisor, operator-writer, and semantic-verifier
roles use isolated `CODEX_HOME` runtimes with hooks disabled.

## 3. Baseline and branch topology

Common neutral experiment plumbing is implemented and admitted before subject
optimization begins. The trusted host records both its evaluation-harness SHA
and the subject SHA. If plumbing changes subject-visible behavior, a
compatibility run is required before declaring the common subject checkpoint
`H0`.

```text
protected origin/main
        |
        +-- H0 subject SHA (frozen)
              |
              +-- experiment/bugfix-main
              |      +-- operator/<bugfix-request>
              |      +-- ... trusted integrations ...
              |      +-- H_bug
              |
              +-- experiment/general-main
                     +-- operator/<general-request>
                     +-- ... trusted integrations ...
                     +-- H_general
```

`H_bug` and `H_general` both have `H0` as their experimental parent. Temporal
scheduling may run Experiment 1 before Experiment 2, but no Experiment 1
commit, role skill, memory packet, config override, or artifact may enter the
Experiment 2 baseline.

The machine constitution is always loaded from protected `origin/main`, never
from an experiment line or checkpoint.

## 4. Authoritative records

The external Operator SQLite store gains typed append-oriented records. It
does not add arbitrary state setters or new Operator Request phases.

### ExperimentStudy

Defines one independent causal comparison:

- `study_id` and purpose;
- `base_checkpoint_id` and `base_subject_sha`;
- trusted harness SHA and policy digest;
- experiment line ID;
- benchmark manifest ID/digest;
- fixed role/model/skills/memory configuration digest;
- primary metrics and success contract.

### ExperimentLine

Tracks one Guard-managed untrusted integration branch:

- line ID and branch ref;
- frozen base SHA;
- current head SHA and monotonically increasing generation;
- active release/checkpoint pointer;
- remote and update policy;
- open/closed administrative status.

The branch ref and Git object graph remain the source of code truth. The row is
a digest-protected control record and must agree with Git before every action.

### IntegrationReceipt

Records a compare-and-swap integration:

- line ID/generation and expected old head;
- Operator request, candidate head, patch digest, scope version, approval IDs,
  CheckRun IDs, and experiment receipt IDs;
- merge result SHA and resulting tree digest;
- policy and budget grant digests;
- host-observed validation artifacts.

### ExperimentCheckpoint

Freezes `H0`, `H_bug`, or `H_general`:

- checkpoint and study identity;
- subject SHA, tree digest, and parent subject SHA;
- harness/policy/config/model/skills/memory/manifest digests;
- budget usage and result summary digest;
- immutable release path and optional Git tag/ref.

### BudgetGrant and UsageEntry

A grant authorizes one study wave:

- wave size `3`, `8`, or `16`;
- exact allowed case IDs and roles;
- model fixed to `gpt-5.4-mini`;
- maximum SDK calls, Writer attempts, Operator revisions, retries, wall time,
  and case concurrency;
- human actor/approval reference and expiration.

Each host-observed SDK launch atomically reserves one usage entry before the
call and finalizes it afterward. A crashed or indeterminate call remains
consumed until trusted reconciliation. The Guard refuses to launch a call that
would exceed the grant. Quota/provider failures cannot switch to Spark.

## 5. Experiment-line transitions

Existing Operator Request phases remain unchanged:

```text
REQUESTED -> ACTIVE -> VERIFIED -> CLOSED
                 ^         |
                 +---------+
```

Experiment-line actions are typed child operations:

### Initialize

1. Resolve and freeze the exact `H0` SHA from the trusted host.
2. Reject protected or existing conflicting branch refs.
3. Create the named line ref without checking it out over `main`.
4. Create an immutable read-only H0 release and line record.

### Request against a line

1. Caller names the line, not an arbitrary mutable checkout HEAD.
2. Guard freezes the line ID, generation, and current head in the request.
3. Candidate worktree branches from that exact SHA.
4. Any line-head advance makes the request stale for integration.

### Integrate

1. Require a clean, currently VERIFIED request and current patch-bound full
   CheckRun.
2. Re-derive scope, approvals, candidate head, diff, policy digest, experiment
   receipt, and budget usage from trusted sources.
3. Require request base to equal the line's current head/generation.
4. Create a temporary detached integration worktree outside candidate and
   authority roots.
5. Merge the candidate without permitting candidate hooks or scripts to own
   authority.
6. Run required deterministic and experiment profiles in the trusted process
   sandbox.
7. Create the integration commit and atomically update the line ref with
   expected-old-SHA compare-and-swap.
8. Persist the IntegrationReceipt, then close the request as integrated.

Failure leaves the line ref unchanged and retains raw merge/check evidence.

### Checkpoint

1. Require a clean line head and completed study wave receipt.
2. Recompute every checkpoint digest from trusted sources.
3. Materialize a detached read-only release.
4. Create an immutable record and optional Guard-owned Git tag.

### Roll back

1. Atomically repoint the active experiment release to a known checkpoint.
2. In a trusted line worktree, restore the checkpoint tree as a new commit
   whose parent is the current line head.
3. Run deterministic validation and compare the resulting tree digest with the
   checkpoint.
4. Advance the line through compare-and-swap and record rollback/integration
   receipts.

No reset, history rewrite, or force push is required.

## 6. CLI and service surface

CLI remains an adapter over services and projections. It never edits SQLite,
task files, refs, or manifests directly.

Proposed commands:

```text
autobugfix operator experiment-line init|show|list
autobugfix operator experiment-line integrate|rollback|close
autobugfix operator checkpoint create|show|list
autobugfix operator budget request|approve|show|usage
autobugfix operator request --experiment-line <line-id> ...

autobugfix eval benchmark doctor
autobugfix eval benchmark preflight --manifest <path>
autobugfix eval benchmark seal --manifest <path>
autobugfix eval run --study <study-id> --wave 3|8|16
autobugfix eval report --study <study-id>
```

Writer-facing CLI remains read-only. It receives only filtered task, context,
scope, feedback, and check results.

## 7. Case schema and sealing

The canonical case schema evolves compatibly. New structured fields include:

- `experiment.role`: optimization or sealed_holdout;
- `experiment.wave`: first eligible wave;
- `source.framework_revision` and dataset revision;
- upstream repo/base/gold identity;
- visible evidence and attachment digests;
- benchmark runtime/image identity;
- eligibility oracle contract and receipt;
- hidden oracle/prediction adapter contract;
- repository-group split identity.

Two projections are produced from one trusted manifest:

### Execution projection

Provides the selected case's target repository, problem statement, visible
evidence, attachments, and configured verifier to the bounded Execution
Writer/Evaluator. It excludes gold patches, hidden tests, modified-file hints,
and other cases.

### Operator projection

For Optimization cases, exposes execution artifacts and diagnosis permitted by
the study. For Holdout, exposes only aggregate checkpoint metrics after the
trusted run. Case identity, prompt, generated patch, failure logs, and
case-level diagnosis remain sealed until the study is permanently closed.

Full manifests, sealed artifacts, and official oracle data live under a
trusted configurable root outside candidate and subject worktrees. Candidate
configuration cannot relocate or weaken this root.

## 8. Adapter contract

Extend `EvalCaseAdapter` with explicit lifecycle methods:

```text
doctor() -> runtime capability report
preflight(case) -> EligibilityReceipt
materialize(case) -> MaterializedCase
execution_contract(case) -> generated target-repo config
verify(case, generated_patch) -> OracleResult
cleanup(case_run) -> retained-resource report
```

All subprocesses use structured argument vectors, explicit timeouts,
configurable cache roots, and retained stdout/stderr. Harness setup errors are
reported separately from repair failures.

### Defects4J adapter

- Pin framework 3.0.1 and Java 11 environment.
- Checkout the buggy revision and preserve required Defects4J metadata in the
  isolated materialized Git repository without exposing the fixed revision.
- Capture original issue evidence and triggering failure while excluding
  modified-class/fault-location metadata from Writer context.
- Run triggering tests plus configured full tests for the Execution verifier.
- Preflight buggy-fail and fixed/gold-pass behavior before eligibility.
- Use official Defects4J tests as authority; gold diff is diagnostic only.

### SWE adapters

- Pin SWE-bench and SWE-bench-Live framework/dataset/image revisions.
- Use official Docker-compatible harnesses for gold preflight and generated
  patch scoring.
- Materialize the full base repository for the real Execution task while
  retaining official hidden fail-to-pass/pass-to-pass tests on the trusted
  side.
- Submit generated patches through official prediction/harness contracts.
- Distinguish image/setup/harness failures from unresolved issues.
- Preserve issue attachments and screenshots when supplied by the source.

## 9. Experiment protocols

Both studies use model `gpt-5.4-mini`, identical role permissions, at most two
Writer attempts per case unless a later grant explicitly changes both paired
arms, one case at a time, and a frozen Memory snapshot.

### Experiment 1

```text
H0 + Defects4J manifest
  -> wave 3 baseline and sealed pair
  -> human budget grant
  -> wave 8
  -> human budget grant
  -> wave 16 (10 Optimization, 6 Holdout)
  -> Operator changes on experiment/bugfix-main
  -> visible Regression
  -> H_bug checkpoint
  -> paired sealed Holdout comparison
```

Primary outcomes: known-case rescue, regression, first-attempt and loop
success, unseen-repository Holdout rescue/regression, cost, artifact
completeness, verifier/oracle agreement, and governance violations.

### Experiment 2

```text
same H0 + independent SWE manifest
  -> independent wave 3/8/16 grants
  -> visible Verified Optimization cases
  -> Operator changes on experiment/general-main
  -> H_general checkpoint
  -> paired SWE-bench-Live Holdout comparison
```

Primary success requires positive visible net improvement, at least one sealed
Holdout rescue, zero sealed Holdout regression, and task-type coverage across
bugfix, feature, and maintenance. A Defects4J non-regression report is
secondary and cannot feed Experiment 2 optimization.

## 10. Metrics and reports

Reports must prefer paired case outcomes over one mixed percentage:

```text
fail -> pass: rescue
pass -> fail: regression
pass -> pass: preserved
fail -> fail: unresolved
```

Each report separates Optimization, dynamic Regression, sealed Holdout,
unseen-repository, task type, harness error, and budget termination. It records
subject/harness SHAs, all role config digests, case revisions, SDK call counts,
iterations, runtime, and artifacts. Small Holdout sets are reported as an
engineering case study, not a statistically significant leaderboard result.

## 11. Configuration and portability

New configuration remains repo-agnostic and path-relative where possible:

```yaml
operator:
  experiment_lines:
    root: .autobugfix/operator-experiment-lines
    remote: origin
    branch_template: experiment/{study_id}-main
  budgets:
    default_case_concurrency: 1
    allowed_primary_models: [gpt-5.4-mini]

eval:
  trusted_case_root: .autobugfix/trusted-eval-cases
  artifact_root: .autobugfix-evals
  cache_root: .autobugfix/benchmark-cache
  container_runtime: docker
```

Runtime roots remain gitignored. No internal repo, username, absolute home
path, company command, case ID, or credential is hard-coded. Doctor commands
must explain missing Java, Subversion, Perl modules, Docker, memory, disk, and
image prerequisites before mutation or SDK use.

## 12. Compatibility and migration

- Existing local-git Eval cases continue to decode and run.
- Existing Request phases, Writer/Check records, approval semantics, and main
  promotion remain valid.
- Existing per-request experiments are retained; named studies/lines compose
  them rather than replacing them.
- SQLite migration is additive and transactional. Old databases can be opened
  and upgraded without deleting authority records.
- Config defaults do not create experiment branches or download benchmark
  data automatically.
- Production default remains Codex SDK; fake backend injection remains a test
  seam only.

## 13. Rollout and rollback

1. Implement and test the trusted experiment-line control plane.
2. Merge neutral infrastructure and freeze `H0` only after compatibility
   validation.
3. Implement and no-model validate each adapter before model runs.
4. Run Experiment 1 through explicit wave gates.
5. Independently initialize Experiment 2 from the same `H0` and run its gates.
6. Promote a treatment checkpoint to `main` only through the existing
   protected PR/canary flow and only after its study-specific success contract.

Any incomplete adapter or budget feature fails closed. Rollback restores a
known checkpoint/release and retains all evidence; it never reclassifies a
failed or exposed case as Holdout.
