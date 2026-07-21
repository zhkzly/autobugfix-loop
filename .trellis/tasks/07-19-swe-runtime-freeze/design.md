# Design: Experiment 2 Runtime Authority Split

## 1. Ownership And Trust

| Data or action | Owner | Trust rule |
| --- | --- | --- |
| SWE protocol source | Eval/researcher in Git | Typed parse plus committed digest |
| Official gold qualification | Eval or external Holdout Guard | No model call; immutable receipt |
| Execution task/attempt/verifier feedback | Execution service | Isolated target worktree and declared visible verifier only |
| Frozen patch/evidence | Eval submission authority | Written once before official scoring |
| Official score | Eval official adapter | Post-freeze only; never Execution input |
| H_general diagnosis/change request | Operator | Public Optimization evidence, governed non-main line |
| Active Memory/skills | Memory plus human approval | Not changed by this task |

The candidate branch and target task worktrees are untrusted. Typed records,
Git identities, external Eval/Guard roots, and host-observed distributions are
authoritative.

## 2. Protocol Model

Upgrade `SWEExperimentProtocol` to schema v3 and add a frozen
`SWESubjectTreatmentRuntime` value:

```text
SWESubjectTreatmentRuntime
  model
  reasoning_effort
  service_tier
  sdk_package / sdk_version
  cli_package / cli_version
  max_attempts
  timeout_seconds
```

Keep `case_concurrency` as a cohort scheduling constraint. The protocol exposes:

- `protocol_digest`: all experiment variables;
- `qualification_contract_digest`: only official dataset/harness/platform,
  qualification repeat policy, and image/materialization semantics;
- `subject_runtime_contract_digest`: canonical treatment runtime values.

The Raw protocol repeats its independent runner constraints but must validate
that all shared runtime variables equal the source protocol. This makes the
comparison explicit rather than relying on matching defaults.

## 3. Runtime Identities

Replace the overloaded `SWERuntime.runtime_id` at SWE experiment boundaries
with two named identities.

### Evaluator runtime identity

Inputs:

- official SWE-bench and SWE-bench-Live revisions;
- scorer/materializer harness lock;
- official adapter, materializer, scorer, dataset, and Docker orchestration
  source digests;
- Python/platform facts required by the scorer;
- configured Docker platform/image mode.

It explicitly excludes root `uv.lock`, `openai-codex`, subject broker, role
skills, model, reasoning effort, and H0/H_general source.

### Subject runtime identity

Inputs:

- `subject_runtime_contract_digest`;
- observed SDK and bundled CLI versions plus distribution RECORD digests;
- root `uv.lock` digest;
- Python executable/version digest;
- trusted broker, capability server, submission freezer, and subject worker
  source digests.

The exact H0/H_general Git SHA/tree, injected config digest, copied skill
digest, and Execution evidence remain per-submission bindings rather than being
collapsed into the host runtime identity.

`SWERuntime.assert_subject_runtime()` compares expected protocol fields with
host observations before materialization or model launch and returns a typed
identity record. A mismatch is a harness error with retained observations.

## 4. Receipt Schemas

Use new schemas rather than rewriting v1/v3 records:

- qualification v4: `qualification_contract_digest` plus
  `evaluator_runtime_id`;
- preparation v2/private cohort v2: full protocol, qualification contract,
  evaluator runtime, and subject runtime contract/observation;
- sealed manifest v2/Guard bundle v2: the same public runtime bindings without
  private Holdout identities;
- subject request v4: expected and observed subject runtime identities;
- development/formal case v2: protocol, evaluator identity, subject identity,
  submission, scorer result, and noninterference digest.

Readers reject old schemas for new formal preparation. Historical files remain
untouched. Qualification lookup keys use qualification contract plus evaluator
runtime, so future Writer-only treatment changes do not require rerunning gold
qualification.

## 5. Exact-Subject Flow

```text
parse protocol
-> inspect installed SDK/CLI and locks
-> fail on runtime drift
-> materialize qualified buggy repository
-> exact-subject broker copies H0/H_general code and role skills
-> broker writes isolated config from treatment runtime
-> Execution creates task worktree and runs bounded Writer/verifier/evaluator
-> freeze patch, task record, events, logs, config and runtime bindings
-> close all Execution capabilities
-> official scorer reads frozen patch
-> write Eval report and noninterference receipt
```

The generated config sets `codex.reasoning_effort: low` and the model/service
tier explicitly. No change is made to the repository-wide default used by
ordinary non-experiment tasks.

The capability request does not include oracle fields. The official scorer is
constructed only after the broker returns `FrozenSWESubmission`; scoring cannot
call the capability sockets or append an Execution attempt.

## 6. Raw Comparator

Update both the standalone project and root-side launcher contract:

- exact SDK and CLI versions are inspected from the standalone environment;
- one fresh thread and one turn per case;
- `ApprovalMode.deny_all`, workspace-write, tool network disabled;
- same model, low reasoning, service tier, timeout, and case ordering as source
  public treatment where applicable;
- no Autobugfix role skills, Memory, evaluator, or verifier retry;
- generated patch is frozen and independently scored once.

Raw runtime receipts bind both expected and observed SDK/CLI records. A
transport failure is harness-invalid; a completed but unresolved patch is a
valid measured failure.

## 7. Configuration Strategy

- Pin root production SDK in `pyproject.toml` and `uv.lock` to `0.144.4`.
- Pin the standalone Raw project and lock to the same version.
- Update raw baseline config validation constants to `0.144.4`.
- Keep ordinary `CodexConfig.reasoning_effort` default at medium.
- Put Experiment 2 low reasoning in `benchmarks/swe-experiment-2.yaml` and
  derive all subject configs from it.
- Update `benchmarks/swe-experiment-2-raw-codex.yaml` and its source protocol
  digest after the v3 protocol is canonicalized.

No host proxy, username, local absolute path, or repository-specific solution
is committed.

## 8. Failure Semantics

| Failure | Classification | Allowed next action |
| --- | --- | --- |
| SDK/CLI/lock mismatch | harness-invalid before call | repair runtime, start a new run ID |
| transport disconnect before completion | harness-invalid | repair transport/runtime, new run ID |
| visible verifier failure | Execution feedback | bounded Writer retry |
| valid final patch unresolved by official scorer | measured failure | retain result; no same-case retry from score |
| official scorer/image failure | Eval harness-invalid | repair Eval harness; rescore only the same frozen patch |
| frozen patch/evidence mutation | authority violation | invalidate run; never score modified data |

## 9. Compatibility And Migration

- Existing runtime and qualification records are immutable historical data.
- Formal v3 preparation requires v4 qualifications, so the selected cases are
  requalified once after the schema change.
- The first calibration is a development-only public Optimization case. Its
  result cannot be promoted as the 16-case formal Experiment 2 outcome.
- Formal sealing begins only after all ten public and six external Guard
  qualifications exist under the new evaluator identity.

## 10. Security And Privacy

- Target main checkout remains read-only and unchanged; Writer edits only the
  task worktree.
- SDK role hooks stay disabled; the project hook applies only to the supervising
  Operator-host session.
- Holdout roots, IDs, Docker process details, and case reports remain external
  Guard authority.
- Logs redact credentials and do not persist proxy secrets or host home paths.
- Runtime versions and hashes are observable; credentials are not.
