# Operator Governance Policy V3

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
- Python owners: `OperatorGovernanceService`, `OperatorStore`,
  `TransitionGuard`, `evaluate_policy`, `validate_bundle`, and
  `project_request`.
- DB authority tables: requests, events, writer_runs, check_runs, gates,
  feedback, scope_revisions, approvals, experiments, promotions, artifacts,
  and request_leases.

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
- WriterView excludes control-store paths and exposes only task, evidence,
  effective scope, filtered checks, and feedback.
- Authoritative commands run in a separate detached verification worktree.
  Bubblewrap mounts the candidate writable, hides authority roots, removes
  network by default, and binds the trusted runtime venv read-only while
  `PYTHONPATH` selects candidate source.

#### Roles

- `operator_supervisor`: read-only; diagnoses and requests typed actions.
- `operator_writer`: workspace-write only in one candidate; cannot approve,
  verify, promote, merge, or write control state.
- `operator_verifier`: read-only semantic review after deterministic checks;
  cannot override a failed check.

The service checks effective backend, sandbox, approval mode, and required
skill against the trusted machine constitution before launching a role.
Project config may choose model/timeouts but may not weaken permission minima.

#### Transition Protocol

1. `guide`: read project purpose, loop ownership, roles, and transitions.
2. `triage`: record evidence-backed diagnosis without granting write scope.
3. `request`: freeze triage digest, base SHA, branch, initial scope, profiles,
   baseline, creator, expiry, and constitution digest.
4. `start`: validate authority and create the real non-main worktree.
5. `writer-start|writer-retry|writer-cancel`: create one bounded WriterRun.
6. `verify --mode fast`: derive complete diff and publish feedback; it cannot
   transition to VERIFIED.
7. `scope-change`: append a version. Cross-layer approval binds to that exact
   version; protected scope requires verified human authority.
8. `candidate-commit`: generate an advisory PR manifest, stage, and commit the
   candidate through the service.
9. `experiment-run`: run configured toy/SWE-bench profiles in a detached
   shadow worktree/state root and preserve host-observed results.
10. `verify --mode full`: run policy, isolated deterministic profiles,
    regression checks, artifact capture, and patch-bound semantic review.
    Every required gate must pass before ACTIVE -> VERIFIED.
11. `promotion-prepare`: bind a clean VERIFIED patch/head and current full
    CheckRun into an external receipt.
12. `promotion-open-pr` -> `promotion-observe-merge` -> `promotion-canary`:
    merge and active-release activation remain separate.
13. `promotion-rollback`: atomically restore last-known-good and write a
    revert intent; `promotion-revert-pr` creates the non-force Git revert PR.

`operator advance` performs one legal scheduler step at a time. It may
auto-retry deterministic failures within budget, but stops on scope,
authority, semantic, or retry-budget blocks.

#### Authorization

- Layer-local, unprotected scope may start automatically.
- Cross-layer scope needs an independent reviewer bound to scope version.
- Protected/constitutional scope needs OpenSSH-signed human or allowlisted
  GitHub authority.
- Candidate risk is recomputed from the trusted constitution and complete diff.
- Requested risk may raise but never lower computed risk.
- A changed patch, HEAD, scope version, policy digest, or approval binding
  makes previous verification/promotion authority stale.

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
manifest revalidation, shadow experiments, stale patch reopening, canary,
last-known-good rollback, and retained raw SDK/check artifacts.

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
```
