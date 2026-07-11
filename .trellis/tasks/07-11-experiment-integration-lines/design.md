# Design: governed experiment integration lines

## Scope

This child changes Operator and shared runtime only. It does not download a
benchmark, select cases, run an experiment, alter Execution task ownership, or
change Memory approval semantics.

Authority remains:

```text
protected origin/main constitution + host service implementation
  -> OperatorGovernanceService
  -> external OperatorStore SQLite database
  -> trusted Git facts/worktrees/checks
```

Experiment lines and candidate worktrees are untrusted code locations.

## Existing integration points

- `OperatorGovernanceService.create_request()` currently freezes
  `rev_parse(project_root, "HEAD")` and creates one branch name.
- `create_operator_workspace()` creates a real worktree at the frozen request
  SHA and rejects protected branches.
- `OperatorStore` already owns transactional migrations, digest-protected
  records, leases, events, experiments, promotions, and artifacts.
- `verify --mode full` already binds policy, complete diff, command results,
  experiment receipts, semantic review, and approvals.
- Promotion already materializes read-only releases and canary rollback.

The new design composes these mechanisms instead of adding a second request
state machine.

## Data model

### StudyRecord

```text
study_id
cohort_id
purpose
base_checkpoint_id
base_subject_sha
harness_sha
policy_digest
line_id
primary_model
target_checkpoint_name
manifest_digest
role_config_digest
memory_digest
base_config_digest
base_model_digest
base_skills_digest
memory_snapshot_path
manifest_snapshot_path
success_contract
created_at
record_digest
```

Manifest and Memory snapshot paths are authority-only record fields. Public
Operator projections remove them and expose the corresponding digests.

### ExperimentLineRecord

```text
line_id
study_id
branch
base_sha
head_sha
generation
active_checkpoint_id
status: OPEN | CLOSED
remote
created_at
record_digest
```

Only `head_sha`, `generation`, `active_checkpoint_id`, and administrative
status may change, through typed compare-and-swap service methods.

### IntegrationRecord

Immutable receipt containing expected old line state, candidate/request/check
bindings, resulting merge SHA/tree, policy/budget digests, command artifacts,
and actor.

### CheckpointRecord

Immutable subject release identity with parent checkpoint/SHA and all
experiment reproducibility digests. Checkpoint names are unique within a
study. `H_bug` and `H_general` require a parent checkpoint named `H0` whose SHA
matches the study baseline.

### BudgetGrantRecord

Immutable authorization for one study wave. Exact case IDs are part of the
signed/digested payload. Allowed wave transitions are `none -> 3 -> 8 -> 16`;
there is no arbitrary integer expansion.

### UsageEntryRecord

Host-observed SDK reservation with `RESERVED`, `COMPLETED`, or
`INDETERMINATE` status. Reserved/indeterminate calls count as consumed. A
unique call key prevents retry double-launch after process uncertainty.

### StudyMetricRecord

Immutable Guard registration for one baseline or candidate benchmark receipt.
It binds Study/line/subject/manifest/success-contract and, for candidate
metrics, the exact wave and grant. Raw YAML is copied into content-addressed
artifact storage. State transitions consume only the record ID and recheck the
artifact hash; arbitrary paths are never score authority.

## Store migration

Add tables transactionally with foreign keys and request/study indexes:

```text
studies
study_metrics
experiment_lines
integrations
checkpoints
budget_requests
budget_grants
usage_entries
```

Every read verifies record digest. Updates recompute digests inside
`BEGIN IMMEDIATE`. The line compare-and-swap update includes current
generation/head in its SQL predicate; zero affected rows means stale state.
Existing tables and records are never rewritten or deleted.

## Service APIs

```python
create_study(...)
register_guard_metric_receipt(...)  # trusted adapter API; no generic Operator CLI
initialize_experiment_line(..., metric_receipt_id=...)
create_request(..., experiment_line_id=None, base_ref=None)
integrate_candidate(request_id, line_id, ...)
create_checkpoint(line_id, checkpoint_name, metric_receipt_id=...)
create_budget_request(study_id, wave, ...)
approve_budget_grant(...)
reserve_usage(study_id, role, case_id, ...)
finalize_usage(usage_id, ...)
rollback_experiment_line(line_id, checkpoint_id, ...)
```

`base_ref` is allowed only for trusted callers and is resolved to a canonical
SHA before request persistence. Normal experiment requests use a line ID; the
service derives base SHA/generation and does not accept a caller-provided line
head.

## Candidate integration

1. Acquire both the request lease and a line-specific lease in deterministic
   order.
2. Load policy from trusted `origin/main`.
3. Require request state VERIFIED and no active Writer/Check.
4. Recompute candidate snapshot and match the latest full CheckRun and Gate.
5. Match request base SHA/generation to the current line/Git ref.
6. Create a detached temporary worktree at the current line head under the
   configured trusted integration root.
7. Merge the candidate with hooks disabled and without running candidate
   scripts as authority.
8. Run trusted validation profiles using current process sandbox rules.
9. Commit with Guard-controlled identity and metadata trailer.
10. Atomically update the local line ref using `git update-ref <ref> <new>
    <old>` and update SQLite with expected old generation/head.
11. Push a fast-forward remote update when configured. A remote mismatch is a
    failed integration requiring reconciliation; it cannot force push.
12. Persist receipt/artifacts and close the Operator request.

If Git succeeds but SQLite fails, reconciliation inspects the unique
integration trailer and Git ref before recording or reverting. The inverse
ordering is avoided so SQLite never claims a head Git did not create.

## Budget enforcement

A `MeteredCodexBackend` decorates the configured real `CodexSDKBackend` for
study runs. It does not implement generation itself and cannot substitute a
fake result.

```text
build request
  -> reserve usage atomically
  -> run real SDK backend
  -> finalize usage with raw log/result identity
```

The reservation checks exact case, role, model, wave, remaining calls,
attempts, revisions, wall-time deadline, and concurrency. Budget denial occurs
before SDK launch and writes an event/artifact. Spark is rejected when the
grant's model is Mini.

Operator roles without a case ID use a typed revision/role allowance in the
same grant. Non-study production calls continue through the ordinary backend.

## Checkpoints and release activation

Checkpoint creation recomputes the line head, tree, study, model/config/skill/
memory/manifest, usage, and report digests. It requires a current passing full
line validation, terminal usage, and a content-addressed `StudyMetricRecord`
registered by the benchmark Guard. A detached read-only release is created
under the configured release root.

H0 may be created from protected main before line initialization. H_bug and
H_general may only be created on their corresponding study line and must have
H0 as their declared parent.

## Rollback

Runtime rollback creates and validates a new line commit whose tree exactly
matches the selected checkpoint before the line can advance:

1. trusted worktree at current line head;
2. restore tracked tree from checkpoint and remove post-checkpoint additions;
3. compare the staged Git tree and materialized release content with the
   checkpoint;
4. commit and run trusted full validation;
5. switch the active release, then compare-and-swap the line ref and SQLite
   generation, restoring the prior pointer/ref on failure;
6. record rollback integration receipt or reconciliation if remote sync fails.

This preserves every failed/intervening commit and avoids reset/force push.

## CLI and projections

CLI adds nested service-backed commands for study, experiment-line,
checkpoint, and budget operations. Read commands use filtered projections.
Mutations return structured YAML records and non-zero on denial.

Writer view is unchanged and cannot expose studies, grants, usage, checkpoints,
or authority artifacts. Operator supervisor projection may see remaining
aggregate budget but not approval secrets or sealed case identities.

## Configuration

Add typed config sections with safe defaults and root validation:

- experiment-line branch template and remote;
- trusted integration worktree root;
- checkpoint/release root;
- budget defaults and allowed primary models;
- line/update timeouts.

Authority/artifact/integration roots must not be nested under candidate
worktrees. Branch templates require `{study_id}` and may not target protected
branches.

## Tests

- additive database migration and old-record preservation;
- digest/tamper checks for every new record;
- same-H0 independent line initialization;
- explicit-base request and existing request compatibility;
- stale line generation and concurrent compare-and-swap;
- dirty/changed/failed/unapproved candidate rejection;
- integration validation and receipt binding;
- H_bug/H_general parent enforcement;
- 3/8/16 grant ordering, exact case IDs, Mini-only model, and exhaustion;
- crash/indeterminate usage accounting;
- rollback tree/release/history behavior;
- CLI adapters never call store/Git mutation directly;
- trusted policy remains loaded from origin/main.

## Compatibility and rollback

Existing requests without an experiment line retain current HEAD-based
behavior during the compatibility window. No experiment branches are created
by config loading or database migration. If integration-line rollout fails,
new commands fail closed while existing per-request verification/promotion
continues unchanged.
