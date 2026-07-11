# Design: Operator and Eval benchmark foundations

## Boundaries

The change spans two control surfaces without merging their state:

```text
Operator request/experiment state
  -> trusted Git/policy/experiment observations
  -> baseline comparison and promotion gate

Eval case manifest
  -> adapter materializes repository
  -> real Autobugfix Execution task
  -> independent oracle verifier
  -> normalized observation/scorer/diagnosis
```

`OperatorGovernanceService` and its external SQLite store remain authoritative
for Operator transitions. `AutobugfixService` remains authoritative for the
inner Execution task. Eval owns only case setup and result artifacts.

## 1. Layer resolution

Replace the special-case `docs_skills` removal with a resolver that evaluates
all matching patterns and computes a specificity tuple from exactness, literal
path segments, and literal character count. Only matches at the highest
specificity are effective.

```text
.agents/role-skills/**
.agents/role-skills/execution/**  <- higher specificity, owns the file
```

One top match produces one owner. Multiple layers tied at the top are an
ambiguity and policy fails closed. The constitution declares
`layer_resolution.strategy: most_specific` and
`layer_resolution.ambiguity: reject` so behavior is not hidden in code.

## 2. Planned paths

`OperatorRequest` validates a non-empty tuple. CLI request parsing requires at
least one `--planned-path`. Paths remain glob patterns, so diagnosis need not
know the exact eventual file. The Guard still checks every actual changed path
against both effective layers and planned patterns.

Scope revisions preserve the old paths. Adding a new layer without adding at
least one new path is rejected before a revision record is written. A Writer
that discovers a new path receives scope feedback and the Operator requests a
versioned expansion.

## 3. Experiment metric receipts

Remove numeric metrics from authority-bearing CLI paths. The trusted host
derives a receipt from command results it launched and observed:

```yaml
source: operator_experiment
profile: local-dataset-e2e
input_digest: ...
base_sha: ...
head_sha: ...
patch_digest: ...
metrics:
  pass_rate: 1.0
  artifact_completeness: 1.0
  runtime_seconds: 42.3
artifact_ids: [...]
receipt_digest: ...
```

The first implementation derives:

- `pass_rate`: passed configured commands / total commands;
- `artifact_completeness`: expected stdout/stderr logs present and non-missing;
- `runtime_seconds`: host monotonic elapsed time;
- optional future metrics remain absent rather than invented.

Baseline capture runs a configured experiment profile against a frozen trusted
ref in a detached worktree and stores an immutable baseline plus raw logs. The
receipt embeds the executable profile contract. A human or trusted CI publisher
commits the protected receipt before request creation; the Guard permits the
measured SHA to precede the request base only by baseline-metadata commits.
Candidate experiments bind the same profile and normalized input digest to the
candidate head/patch. Full verification locates a completed matching receipt;
it never accepts `--metric` values.

The trusted-base PR validator reads the baseline from the actual base commit,
runs both deterministic profiles and the embedded experiment contract, derives
its own receipt, and uploads raw logs. It never trusts the candidate bundle for
baseline values or commands.

The machine constitution declares `baseline_required_layers`. Documentation
fallback paths may skip a baseline; every behavior layer must provide one.

## 4. Case and adapter model

The canonical schema is versioned and structured around stable upstream
identity, not local cache paths:

```yaml
schema_version: 1
case_id: local-add-off-by-one
source:
  adapter: local-git
  benchmark: autobugfix-local
  revision: <manifest revision>
  split: regression
  instance_id: local-add-off-by-one
task:
  type: bugfix
  problem_statement: ...
  agent_prompt: ...
  attachments: []
repository:
  repo_id: toy_repo
  worktree_path: /resolved/local/source
  base_commit: <sha>
  reference_commit: <sha>
environment:
  image: null
  platform: null
  setup_commands: []
execution:
  test_command: python3 -m unittest discover
oracle:
  type: command
  command: python3 -m unittest discover
  require_patch: true
  visibility: hidden
```

The legacy decoder maps existing flat rows to this model and takes the CLI
`--test-command` as an override. A registry resolves `source.adapter` to an
adapter implementation. `LocalGitAdapter` clones the historical repository
into an isolated bare remote/main checkout and can derive an optional oracle
diff. SWE-bench later supplies another adapter without changing the runner or
scorer contracts.

## 5. Tests-first observation and score

After one real Execution iteration, Eval gathers a normalized observation:

```text
generated_patch_non_empty
execution_verifier_passed
execution_state
execution_reached_human_gate
oracle_status: passed | failed | error
oracle_exit_code
generated_equals_oracle (diagnostic)
```

The independent command oracle runs in the task worktree and writes structured
YAML plus raw stdout/stderr. An adapter may later substitute an official
container harness while returning the same normalized oracle result.

Case pass requires a non-empty patch when configured, successful Execution
verification, arrival at the human gate, and a passed oracle. Diff equality is
never a gate. Setup/oracle exceptions become `harness_error`, not
`repair_failed`, and the run summary records both separately.

The Eval CLI reads the summary and exits non-zero whenever any case failed or
had a harness error. Operator experiments can therefore trust command status
as a coarse host-observed metric while later official adapters add richer
case-level scoring.

## Compatibility And Migration

- Flat local rows continue to decode during a deprecation window.
- `swebench-smoke` becomes `local-dataset-e2e`; no compatibility alias may
  claim official SWE-bench semantics.
- Existing `generated.diff` and `oracle.diff` artifacts remain for diagnosis.
- `score_path` replays a stored normalized observation instead of comparing
  diff text.
- Existing arbitrary metric CLI options are removed from authority paths;
  callers must run/capture a trusted experiment.

## Rollback

The work is isolated on the current non-main branch. Each implementation phase
is testable independently. If the Eval migration blocks real acceptance, keep
the legacy decoder but revert the runner call site; do not restore exact-diff
as an authority gate. If metric receipt integration is incomplete, full
verification must fail closed rather than fall back to caller metrics.
