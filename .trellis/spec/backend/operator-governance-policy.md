# Operator Governance Policy V4

> Executable and normative contract for constraining Operator self-improvement
> without turning the Operator into a trusted state owner.

## Scenario: Trusted Operator State-Transition Harness

### 1. Scope / Trigger

Trigger this contract for every Operator diagnosis, candidate modification,
scope change, experiment, verification, promotion, rollback, policy change,
or runtime-role/configuration change.

Autobugfix is a loop-engineering and harness-engineering control system.
Execution repairs configured target repositories, Memory compiles accepted
Execution evidence into reviewed wiki/skills, Eval reproducibly measures the
real Execution loop, and Operator diagnoses and improves Autobugfix itself.

The human constitution defines these purposes and ownership boundaries. The
machine constitution at `src/autobugfix/operator/constitution.yaml` repeats
them for every Operator role and compiles enforceable paths, risks, runtime
minimums, transitions, and validation profiles. Agents and candidate
worktrees are untrusted. `OperatorGovernanceService`, its external SQLite
store, Git facts, isolated checks, external approvals, and trusted-base CI own
authority.

### 2. Signatures

- Read/diagnosis: `operator guide|status|audit|supervise`.
- Request lifecycle: `triage`, `request`, `start`, `reopen`, `close`.
- Child runs: `writer-start|writer-retry|writer-cancel`, `verify`,
  `experiment-run`.
- Authority: `review`, `approval-payload`, `approve-signed`,
  `approve-github`, `scope-change`, `scope-activate`.
- Promotion: `candidate-commit`, `promotion-prepare`, `promotion-open-pr`,
  `promotion-observe-merge`, `promotion-canary`, `promotion-rollback`, and
  `promotion-revert-pr`.
- Study control: `study create|show|list`, `line init|show|list|rollback`,
  `budget request|approve|show`, `integrate`, and
  `checkpoint create|show`.
- Python owners: `OperatorGovernanceService`, `OperatorStore`,
  `TransitionGuard`, `evaluate_policy`, `validate_bundle`, and
  `project_request`.
- DB authority tables: requests, events, writer_runs, check_runs, gates,
  feedback, scope_revisions, approvals, experiments, promotions, artifacts,
  request_leases, studies, experiment_lines, integrations, checkpoints,
  budget_requests, budget_grants, usage_entries, and experiment_line_leases.

### 3. Contracts

#### Aggregate

Operator Request has exactly four phases:

```text
REQUESTED -> ACTIVE -> VERIFIED -> CLOSED
                 ^         |
                 +---------+  reopen on patch/scope change
```

WriterRun, CheckRun, GateSnapshot, FeedbackPacket, ScopeRevision, Experiment,
and Promotion are child records with independent statuses. Failed Writer or
Check runs do not create more Request phases. No API accepts an arbitrary
target state.

#### State And Isolation

- Trusted state defaults to `.autobugfix/operator-v3/governance.sqlite3` in
  the control checkout and is configurable under `operator.state`.
- Raw logs and large evidence live under the configured artifact root.
- Candidate code lives in a real Git worktree under `operator.worktrees.root`.
- State/artifact roots must be outside the candidate worktree.
- Study lines are real non-main Git refs. Their SQLite head/generation and Git
  ref advance together through expected-head/generation compare-and-swap.
- Checkpoint releases are read-only materializations outside candidate
  worktrees; active-release links are service-owned projections.
- WriterView excludes control-store paths and exposes only task, evidence,
  effective scope, filtered checks, and feedback.
- Authoritative commands run in a separate detached verification worktree.
  Bubblewrap mounts the candidate writable, overlays hidden authority roots,
  then overlays exact writable grants and the trusted runtime venv read-only.
  A later broad candidate mount must never shadow an authority mask or exact
  runtime grant. Network is removed by default and `PYTHONPATH` selects
  candidate source.

#### Roles

- `operator_supervisor`: read-only; diagnoses and requests typed actions. It
  may read Study/Line/Budget projections but cannot approve a grant, integrate,
  create a checkpoint, or activate a release.
- `operator_writer`: workspace-write only in one candidate; cannot approve,
  verify, promote, merge, read sealed cases, or write control/line/budget state.
- `operator_verifier`: read-only semantic review after deterministic checks;
  cannot override a failed check or declare integration/checkpoint success.

The service checks effective backend, sandbox, approval mode, and required
skill against the trusted machine constitution before launching a role.
Project config may choose model/timeouts but may not weaken permission minima.

#### Transition Protocol

1. `guide`: read project purpose, loop ownership, roles, and transitions.
2. `triage`: record evidence-backed diagnosis without granting write scope.
3. `baseline record`: execute a configured experiment profile on the frozen
   trusted ref and derive an immutable host-observed metric receipt containing
   the executable profile contract. Every configured command must execute and
   pass; an empty, failed, or timed-out profile retains logs but must not write
   a publishable baseline receipt.
4. Baseline publication: a human or trusted CI publisher reviews and commits
   the protected receipt to the trusted base. Operator Writer cannot publish
   it. The measured SHA may differ from the request base only by intervening
   `.autobugfix-baselines/**` metadata commits.
5. `request`: freeze triage digest, base SHA, branch, initial layers, non-empty
   planned path patterns, profiles, baseline, creator, expiry, and constitution
   digest.
6. `start`: validate authority and create the real non-main worktree.
7. `writer-start|writer-retry|writer-cancel`: create one bounded WriterRun.
8. `verify --mode fast`: derive complete diff and publish feedback; it cannot
   transition to VERIFIED.
9. `scope-change`: append a version. Adding a layer requires a planned path
   classified to that layer;
   cross-layer approval binds to that exact version and protected scope
   requires verified human authority.
10. `candidate-commit`: generate an advisory PR manifest, stage, and commit the
   candidate through the service.
11. `experiment-run`: run a configured profile in a detached shadow
    worktree/state root and derive a host-observed receipt bound to profile
    inputs, HEAD, and patch digest.
12. `verify --mode full`: run policy, isolated deterministic profiles,
    receipt-based regression checks, artifact capture, and patch-bound semantic review.
    Every required gate must pass before ACTIVE -> VERIFIED.
13. `promotion-prepare`: bind a clean VERIFIED patch/head and current full
    CheckRun into an external receipt.
14. `promotion-open-pr` -> `promotion-observe-merge` -> `promotion-canary`:
    merge and active-release activation remain separate.
15. `promotion-rollback`: atomically restore last-known-good and write a
    revert intent; `promotion-revert-pr` creates the non-force Git revert PR.

#### Governed Study Protocol

This protocol wraps, rather than replaces, the four-phase Request aggregate:

1. `study create` freezes `H0`, harness/policy/config/role/skill/Memory/model
   and benchmark-manifest digests, the target checkpoint, and a success
   contract. Visible Optimization manifest and Memory content are copied into
   independent read-only H0 snapshots; ordinary Operator projections expose
   digests, not snapshot paths. Sealed Holdout manifests, gold data, and
   case-level results remain external Guard state and never enter Operator
   roots; only aggregate final metrics may be registered.
   `H_bug` and `H_general` studies in one cohort must match every frozen H0
   digest; a different base commit, skill set, config, model, policy, or Memory
   snapshot is rejected before line creation.
2. `line init` creates one real experiment ref at `H0`, records generation 0,
   materializes the read-only `H0` release, and leaves the control checkout
   unchanged. It accepts only a baseline `StudyMetricRecord` previously
   registered by the trusted benchmark Guard.
3. `budget request` proposes exactly wave 3, 8, or 16 for named case IDs. A
   human confirms the canonical request digest through `budget approve`.
4. Each line-bound Request freezes line head/generation and grant ID/digest.
   Supervisor, Writer, and semantic Verifier calls reserve usage atomically
   before invoking the Python Codex SDK and finalize it afterward. Exceptions
   count as consumed `INDETERMINATE` calls; there is no model fallback.
5. `integrate` accepts only a current clean VERIFIED candidate and current
   grant, reruns policy and trusted profiles in a separate worktree, compares
   the real patch, then advances Git and SQLite with compare-and-swap.
6. `checkpoint create` accepts only the Study's declared `H_bug` or
   `H_general` target after a current candidate integration, terminal usage,
   and a Guard-owned `StudyMetricRecord` satisfying the frozen success
   contract. Guard registration copies the receipt into content-addressed
   artifact storage; line/checkpoint transitions accept the record ID, verify
   the artifact hash, and reject arbitrary candidate paths.
7. `line rollback` creates a new commit whose tree equals a prior checkpoint,
   reruns the trusted full profile, and advances history normally. Reset and
   force-push are forbidden.

Budget waves are exactly `3 -> 8 -> 16`, the model is `gpt-5.4-mini`, case
concurrency is one, and grants never transfer between studies. Expired,
exhausted, stale-line, wrong-model, replayed-call, or concurrent-call attempts
fail before the SDK call.

The experiment protocol has two independent treatments sharing only `H0`:

- Defects4J: 10 visible Optimization cases plus 6 sealed unseen-repository
  Holdout cases may produce `H_bug`.
- General-agent study: 10 SWE-bench Verified Optimization cases plus 6
  SWE-bench-Live sealed unseen-repository Holdout cases may produce
  `H_general`.

`H_general` must not inherit `H_bug` code, skills, Memory, artifacts, results,
or case-level feedback, and the reverse is also forbidden. This is an
experiment protocol, not a redefinition of the four loop purposes.

`operator advance` performs one legal scheduler step at a time. It may
auto-retry deterministic failures within budget, but stops on scope,
authority, semantic, or retry-budget blocks.

#### Authorization

- Layer-local, unprotected scope may start automatically.
- Cross-layer scope needs an independent reviewer bound to scope version.
- Protected/constitutional scope needs OpenSSH-signed human or allowlisted
  GitHub authority.
- Candidate risk is recomputed from the trusted constitution and complete diff.
- Layer ownership uses the most-specific matching constitution rule; equal
  top-specificity cross-layer matches are rejected as ambiguous.
- Behavior-layer full verification requires a baseline and a matching trusted
  experiment receipt. Caller-provided numeric metrics have no authority.
- Trusted-base PR admission loads the committed baseline from the PR base,
  reruns its embedded experiment profile against the candidate, compares the
  host-derived receipt, and uploads raw Guard logs. Candidate manifests and
  candidate configuration cannot supply that authority.
- Requested risk may raise but never lower computed risk.
- A changed patch, HEAD, scope version, policy digest, or approval binding
  makes previous verification/promotion authority stale.
- A changed line head/generation, grant digest, manifest digest, or Study
  configuration makes integration/checkpoint authority stale.

#### Manifest And Remote Admission

`.autobugfix-governance/<request-id>/bundle.yaml` is an advisory transport
manifest, not authority. It is generated before the candidate commit to avoid
a head/digest cycle. A Writer could forge it, so trusted CI:

- loads validator and constitution from the PR base checkout;
- derives the actual full diff and risk from the candidate checkout;
- re-reads allowlisted GitHub reviews and binds them to request/head/PR;
- ignores candidate local phase/check claims for the merge decision;
- mounts a trusted read-only venv but imports candidate source;
- reruns required trusted validation profiles in Bubblewrap.

The project hook blocks obvious direct merge/protected push/state-write
commands, but hooks are only accident prevention. It must allow read-only
inspection of authority metadata and raw artifacts; observability cannot depend
on bypassing the hook. Service and remote admission remain authoritative.

`.codex/hooks.json` is a project-checkout guard for the human/main Codex session
acting as Operator. It must not be injected into Execution Writer/Evaluator,
Memory maintainer, Eval case agents, or bounded Operator role subprocesses.
Production SDK roles use an isolated `CODEX_HOME` whose generated config sets
`features.hooks = false`; their real boundaries are role sandbox, service-owned
state, Git worktrees, deterministic verification, and external admission.

### 4. Validation & Error Matrix

- Missing evidence, stale base/policy, protected branch -> request/start fails.
- Running Writer/Check exists -> concurrent transition fails.
- Out-of-scope/unclassified/protected diff -> CheckRun fails with feedback.
- Scope revision approval bound to another version -> activation fails.
- Candidate config weakens role/process sandbox -> preflight or role launch fails.
- Preview Codex SDK cannot accept isolated `env`/`codex_bin` configuration ->
  production role launch fails closed instead of using global runtime state.
- Validation command fails/times out/harness errors -> CheckRun fails closed.
- Semantic verifier errors -> full check fails closed.
- Candidate changes during check -> check fails.
- Full check on dirty/uncommitted/no-Writer patch -> fails.
- Patch/head changes after VERIFIED -> request reopens before promotion.
- Canary fails -> active pointer stays/restores last-known-good and rollback
  evidence is preserved.
- Invalid wave/case set/model, exhausted grant, duplicate call key, or active
  concurrent call -> usage reservation fails before Codex SDK invocation.
- Integration validation fails or Git/SQLite CAS loses -> experiment line does
  not advance; raw logs are retained.
- Checkpoint receipt is forged, stale, failed, or bound to another grant/head
  -> checkpoint and active release do not advance.
- Rollback validation fails -> Git ref, SQLite line, and active release remain
  unchanged.

### 5. Good / Base / Bad Cases

- Good: Eval-only diagnosis creates a real candidate worktree, runs one
  production Writer, commits an advisory manifest, passes isolated fast/full
  checks, and reaches VERIFIED without writing main or control state.
- Good: A required shared-runtime path creates ScopeRevision v2; an approval
  bound to v1 is rejected and an independent v2 approval activates it.
- Base: A deterministic test failure leaves Request ACTIVE, writes CheckRun,
  GateSnapshot, raw logs, and FeedbackPacket, then permits a bounded retry.
- Bad: Candidate YAML claims VERIFIED, edits protected paths, or supplies fake
  approvals/results. Trusted Git/policy/review/check derivation rejects it.
- Bad: Operator bypasses promotion with direct merge/force push. Hook blocks
  the common command and branch protection remains the authoritative backstop.

### 6. Tests Required

```text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
uv run --cache-dir /tmp/uv-cache python scripts/real_repository_acceptance.py --model gpt-5.4-mini
uv run --cache-dir /tmp/uv-cache python scripts/real_operator_acceptance.py --model gpt-5.4-mini
```

Assertions must cover four Request phases, event/record tamper detection,
request leases, Writer cancellation/timeout/retry, scope-version authority,
candidate constitution self-amendment, process/root isolation, advisory
manifest revalidation, shadow experiment receipts, stale patch reopening, canary,
last-known-good rollback, independent same-`H0` study lines, Git/SQLite CAS,
budget reservation/finalization, no fallback, integration failure atomicity,
immutable checkpoint lineage, history-preserving rollback, and retained raw
SDK/check artifacts.

Acceptance oracles assert observable behavior, changed-path scope, Git
identity, and evidence completeness rather than requiring one exact source
spelling. Real-repository smoke tests must pin the upstream commit and identify
fault injection explicitly; they are not SWE-bench scores. When a verifier
borrows the trusted project venv, preserve the `.venv/bin/python` entry path:
resolving that symlink can bypass `pyvenv.cfg` and select the base interpreter.
Toy-repository runs may remain as fast developer fixtures but cannot satisfy a
promotion or release acceptance gate.

### 7. Wrong vs Correct

Wrong:

```text
Operator edits candidate and a local status YAML -> model says tests passed ->
direct push/merge main.
```

Correct:

```text
evidence -> triage/request -> trusted start -> one WriterRun -> fast feedback
-> versioned scope authority -> service commit -> shadow experiment/full check
-> VERIFIED -> promotion receipt/PR -> trusted-base CI -> observed merge ->
canary -> activate or rollback/revert PR

frozen H0 -> Study/Line -> human budget grant -> line-bound Request -> metered
roles -> trusted integrate -> Guard metric receipt -> H_bug or H_general
checkpoint -> read-only release; regression -> validated rollback commit
```
