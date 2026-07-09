# Rebuild operator governance as a trusted permission gate

## Goal

Replace the advisory Operator Governance v1 prototype with a real control plane
that constrains the Operator as a bounded execution node. The gate must prevent
self-approved constitutional changes, scope drift, post-commit empty-diff
validation, fabricated human approval, and unmeasured regressions while keeping
low-risk single-layer iteration practical.

Autobugfix remains a loop-engineering and harness-engineering system:
Execution fixes real target repositories in isolated worktrees, Memory compiles
accepted execution evidence into reviewed knowledge and skills, Eval measures
the real execution loop in reproducible harnesses, and Operator diagnoses and
improves Autobugfix without owning the other loops' state.

## Background

Governance v1 introduced machine-readable layers, requests, reviews, policy
checks, validation records, and baselines. Review found that it remains
advisory:

- candidate code loads its own candidate constitution;
- callers can label a local review as human;
- requests and approvals are mutable and not bound to Git or content digests;
- the default `HEAD` diff loses committed changes;
- triage, validation commands, and baselines are optional;
- there is no dedicated Operator worktree or merge-enforced trusted check;
- all tests are treated as unscoped common files;
- the real toy acceptance can delete tracked Memory and Operator state.

## Requirements

### R1: Trusted constitution and policy source

- The authoritative gate must support loading constitution and policy data from
  a trusted Git ref or an explicitly supplied trusted policy file.
- Candidate changes to policy or constitution must not weaken the rules used to
  validate that candidate.
- Bootstrap mode must be explicit and require constitutional human approval;
  it must never be the silent default.

### R2: Immutable request and audit state

- Triage is mandatory and must reference at least one evidence path or digest.
- Request creation captures branch, base SHA, declared layers, requested
  validation profiles, expiry, and a canonical request digest.
- Request records are immutable. Scope expansion creates a new request/version.
- State transitions are written to an append-only event log and projected by a
  single governance service.
- Approval and validation records bind to request digest, base SHA, patch
  digest, and head SHA as applicable.

### R3: Three permission classes

- `layer_local`: low-risk single-layer changes may be automatically authorized
  after triage and preflight.
- `cross_layer`: cross-layer or medium-risk changes require an independent
  reviewer approval. The request creator/operator cannot approve itself.
- `constitutional`: protected semantics, governance code, human gates,
  state-machine policy, sandbox authority, production backend, merge, and
  release require externally verifiable human approval.
- Policy computes a minimum risk from paths and semantics. A request may raise
  risk but cannot lower the computed minimum.

### R4: Real approval evidence

- Remove the ability to create a human approval by passing a string such as
  `--kind human`.
- Support signed local human approval using OpenSSH signatures and an
  allowlisted signers file.
- Support imported GitHub approval evidence with repository, pull request,
  reviewer, review id, commit SHA, and allowlist validation.
- Interactive local confirmation may authorize experiments but cannot make a
  constitutional request merge-ready.

### R5: Scoped Operator workspace

- A request can create a dedicated Git branch and worktree rooted under
  `.autobugfix/operator/worktrees`.
- Workspace creation uses the frozen base SHA and refuses protected branches.
- Preflight runs before patch execution. Postflight includes committed,
  staged, unstaged, and untracked files from the frozen base.
- Changed paths outside declared layers fail validation.
- The control-project main checkout is never the Operator patch workspace.

### R6: Trusted validation and regression policy

- Validation uses named profiles from trusted constitution data, represented as
  argv arrays and executed without a shell.
- Required profiles are derived from changed layers and risk; callers may add
  profiles but cannot remove required profiles.
- Every command preserves stdout, stderr, exit code, timing, cwd, and patch
  digest in durable artifacts.
- Typed metrics define direction and thresholds. Missing required metrics fail.
- Cross-layer and constitutional changes require a baseline or an explicit
  human-signed waiver.

### R7: Git and GitHub enforcement

- Provide a trusted-validator entrypoint that can validate a candidate root
  against policy loaded from a base/trusted ref.
- Provide installable local pre-push hooks as feedback, not as the sole trust
  boundary.
- Add a read-only GitHub workflow and CODEOWNERS contract suitable for a
  required branch-protection check.
- Validation binds the approved request to the candidate commit SHA.

### R8: Acceptance isolation and Codex compatibility

- The real toy acceptance must use an isolated control root and must not delete
  tracked active Memory or Operator audit records.
- Codex doctor/probe output must expose Python SDK and bundled runtime versions.
- Runtime/model incompatibility must fail in an explicit compatibility probe or
  remain a documented external blocker; production may not fall back to fake.

## Acceptance Criteria

- [ ] Candidate policy self-modification cannot weaken trusted validation.
- [ ] A request without triage/evidence cannot be authorized.
- [ ] Requests cannot be overwritten after creation.
- [ ] Reviews with a mismatched request digest or base SHA are rejected.
- [ ] A caller cannot create a human approval with an unverified CLI label.
- [ ] A committed unauthorized file remains visible from frozen `base_sha`.
- [ ] Cross-layer changes require independent review.
- [ ] Protected changes require a valid external human proof.
- [ ] Operator workspace creation produces a real Git worktree on a non-main
      branch at the frozen base SHA.
- [ ] Tests are layer-classified or explicitly reviewed; `tests/**` is not an
      unconditional bypass.
- [ ] Required validation profiles run without `shell=True` and retain logs.
- [ ] Missing baseline metrics or threshold regressions block merge readiness.
- [ ] GitHub enforcement files and a trusted-validator command are present.
- [ ] `uv run pytest -q` passes.
- [ ] `uv run python -m compileall -q src tests scripts` passes.
- [ ] `git diff --check` passes.
- [ ] Role-skill validation passes.
- [ ] Real toy acceptance passes, or a real SDK/model incompatibility is
      preserved as a blocking acceptance result without using a fake backend.

## Out Of Scope

- Replacing GitHub branch protection with a custom Git hosting server.
- Giving Operator ownership of Execution, Memory, or Eval state.
- Treating local hooks as an unbypassable security boundary.
- Replacing production Codex execution with a deterministic fake.

