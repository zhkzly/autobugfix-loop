# Operator Governance V3: Verified State-Transition Harness

## Goal

Rebuild Operator governance as a real loop/harness control plane where Codex
agents can propose actions and modify isolated candidate worktrees, but only a
trusted transition service can advance authoritative state. The system must
support Writer attempts, deterministic verification, structured feedback,
retry, scope expansion, shadow experiments, promotion, canary activation, and
rollback without trusting candidate-authored state or validation files.

Autobugfix remains a local, repo-agnostic, Git-controlled loop-engineering and
harness-engineering system. Execution fixes target repositories, Memory
compiles accepted evidence into reviewed knowledge and skills, Eval measures
the real Execution loop, and Operator diagnoses and improves Autobugfix itself.
LLMs are bounded execution or semantic-review nodes, never state owners.

## Trust Model

- The host process, trusted main/package, machine constitution, local human,
  and trusted GitHub checks are trusted.
- Operator and Writer Codex agents, candidate worktrees, candidate-authored
  files, and model prose are untrusted.
- The threat model does not attempt to defend against a malicious host root
  user; it does defend against agents that forget, misclassify, or attempt to
  bypass workflow steps within their sandbox/tool surface.
- Candidate worktrees contain no authoritative state. Runtime authority lives
  outside every agent-writable worktree.

## Requirements

### R1: Hierarchical state model

- Operator Request lifecycle is only `REQUESTED`, `ACTIVE`, `VERIFIED`, and
  `CLOSED`.
- Writer attempts, check runs, gates, scope revisions, experiments, and
  promotions have independent typed records and statuses.
- Retry creates a new WriterRun while the Request remains `ACTIVE`.
- Failed checks remain evidence/check results rather than exploding the main
  lifecycle state machine.
- Any candidate patch or effective-scope change invalidates prior verification.

### R2: Trusted transition ownership

- Operator may request actions but cannot set lifecycle or run states.
- CLI and hooks are clients; only `OperatorGovernanceService` may write through
  the trusted store and append transition events.
- Every state transition has an explicit contract containing allowed source
  phases, deterministic/runtime/semantic/authority checks, pass event, and
  failure behavior.
- Natural-language claims never mutate state.

### R3: Isolated Operator roles

- `operator_supervisor` is read-only and may diagnose, inspect, and request
  transitions through typed governance tools.
- `operator_writer` is workspace-write only in the request candidate worktree.
  It cannot read or write the authoritative state root, approve, verify,
  promote, merge, or activate.
- `operator_verifier` is read-only and emits semantic verdicts bound to the
  request, patch digest, trusted policy, and deterministic check run.
- Writer receives task, evidence, scope, feedback, and check results through a
  filtered read-only view/CLI surface, not by opening control-store files.

### R4: Trusted state and evidence layout

- State root is configurable and defaults outside the candidate worktree.
- SQLite owns requests, events, writer runs, check runs, gates, scope revisions,
  approvals, experiments, promotions, and artifact references.
- Large raw logs and reports live in a separate artifact root. Records bind
  producer, trust class, request/run id, patch digest, path, and SHA-256 digest.
- Git owns source history; the state store does not duplicate source history.
- Legacy v2 YAML/JSONL records may be read for audit but do not grant v3
  authority.

### R5: Writer execution and feedback loop

- Operator explicitly requests Writer start/retry/cancel, or a configured
  Supervisor policy requests it through the same service.
- A request has at most one running WriterRun and one running CheckRun.
- Host Supervisor launches the real Codex Python SDK with the candidate
  worktree as cwd, configured role skills/model/sandbox, timeout, and logs.
- On completion, the host derives Git facts and writes a structured feedback
  packet. Operator does not manually copy feedback to Writer.
- Retry policy is configurable; deterministic failures may auto-retry within a
  budget, while scope, authority, and semantic blocks pause for Operator/human.

### R6: Scope and authorization

- Request freezes base SHA, branch, initial layers, planned paths, policy
  digest, validation profiles, baseline, creator, and expiry.
- Actual paths are always re-derived from complete Git state.
- Scope expansion is append-only as a versioned ScopeRevision; old approvals
  and verification become stale.
- Low-risk single-layer scope may auto-authorize. Cross-layer changes require
  independently verified review authority. Protected/constitutional changes
  require externally verifiable human authority.
- Candidate policy cannot authorize itself; trusted base policy classifies all
  current changes.

### R7: Verification ladder

- Fast checks provide Writer feedback: Git/scope/protected paths, compile, and
  targeted tests.
- Full checks gate `VERIFIED`: trusted profiles, full tests, toy acceptance,
  configured benchmark smoke cases, baseline regression, and artifact
  completeness.
- Semantic verification is read-only, patch-bound, and cannot override a
  deterministic failure.
- Only evidence produced by trusted host/verifier/human/GitHub sources may
  drive state; candidate-authored reports are advisory.

### R8: Shadow experiment lane

- `main` remains the only trust root.
- Each Operator Request may create an isolated experiment branch/worktree,
  shadow state root, and artifact root from a frozen trusted SHA.
- Candidate Autobugfix may run real toy/SWE-bench cases inside the experiment,
  but the outer trusted harness independently scores results.
- Optional staging is non-authoritative and may only combine already-verified
  experiments for integration tests.

### R9: Promotion and rollback

- Promotion is separate from Request lifecycle and begins only from a current
  `VERIFIED` patch.
- Promotion statuses cover prepared, PR open, merged, canary, active, failed,
  and rolled back without adding Request lifecycle states.
- GitHub/base validator independently rechecks candidate facts before merge.
- Merge and activation are separate: an immutable release passes post-merge
  canary before the active-release pointer changes.
- Every promotion records before SHA, merge SHA, candidate release, previous
  active release, policy/check digests, and schema compatibility.
- Rollback first restores the exact last-known-good active release, then uses a
  Git revert PR; shared main is never reset or force-pushed.

### R10: CLI, skills, and hooks

- Operator CLI exposes typed request/start/writer/verify/scope/experiment/
  promote/rollback/status actions and no arbitrary `set-state` command.
- Writer CLI exposes read-only task/context/scope/feedback/check-result views.
- Operator, Writer, and semantic-verifier roles each have independent skills.
- Hooks remain narrow: inject governance context and guard merge/push commands.
  They never own state or replace the service/remote gate.

### R11: Configuration system

- Typed config owns state/artifact/worktree roots, retry budgets, role models,
  role skills, sandbox/approval modes, verification ladders, experiment
  profiles, promotion checks, canary policy, and rollback policy.
- Target repositories remain configured only through `.autobugfix/config.yaml`.
- No internal paths, repositories, usernames, commands, or model assumptions
  are hardcoded.

## Acceptance Criteria

- [ ] Writer cannot mutate authoritative state through its configured role.
- [ ] Candidate-authored fake state/test/approval files cannot advance state.
- [ ] Operator cannot directly set Request, WriterRun, CheckRun, or Promotion status.
- [ ] Request lifecycle uses exactly four public phases.
- [ ] Writer start, failure, feedback, and retry create distinct durable runs.
- [ ] Scope expansion creates a revision, raises risk, and invalidates checks.
- [ ] A changed patch invalidates `VERIFIED` and merge authority.
- [ ] Real Codex SDK can execute an Operator Writer attempt in a real worktree.
- [ ] Writer read-only views expose feedback without exposing store paths.
- [ ] Shadow experiment state and target repos are isolated from production.
- [ ] Promotion cannot start from a stale or unverified patch.
- [ ] Post-merge canary can activate or restore last-known-good release.
- [ ] Trusted CI rechecks candidate policy and does not trust candidate code.
- [ ] `uv run pytest -q` passes.
- [ ] `uv run python -m compileall -q src tests scripts` passes.
- [ ] `git diff --check` passes.
- [ ] Role-skill validation passes.
- [ ] Real toy repo acceptance passes without a fake production backend.

## Out Of Scope

- Defending against a malicious host root user.
- Building a distributed consensus system for local state.
- Treating an experimental/staging branch as a second trust root.
- Allowing a semantic LLM verdict to override deterministic verification.
