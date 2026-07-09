# Operator Governance v2 Design

## Trust Model

The candidate branch and the Operator process are untrusted for authorization
purposes. They may propose policy changes, but the authoritative decision uses
constitution data loaded from a trusted Git ref or trusted external path. Local
candidate validation remains useful feedback but is not merge authority.

The system enforces official workspace, validation, commit, and merge paths.
It does not claim that a process with unrestricted operating-system access is
physically unable to edit arbitrary files; instead, unauthorized edits cannot
produce a valid Governance v2 authorization and validation chain.

## Components

### Models and canonical digests

`models.py` defines typed triage, request, approval, event, projection, and
validation records. Canonical JSON with sorted keys is hashed with SHA-256.
Mutable projection fields are excluded from immutable request payloads.

### Append-only store and projection

`store.py` owns `.autobugfix/operator/**`. Immutable records use exclusive
creation. `events/<request-id>.jsonl` is append-only. `projection.py` is the
single reducer from events to request state.

### Governance service

`service.py` owns transitions and validates cross-record references. CLI code
calls this service and never writes request, review, approval, workspace, or
validation files directly.

### Policy and trusted loading

`policy.py` classifies paths, computes risk floors, resolves required profiles,
and evaluates scope. `trusted.py` loads constitution bytes from:

1. an explicit trusted file;
2. `git show <trusted-ref>:src/autobugfix/operator/constitution.yaml`;
3. the installed package only for explicit bootstrap/local feedback mode.

Policy evaluation always compares frozen `request.base_sha` to the current
candidate, including untracked files.

### Approval providers

`approvals.py` supports:

- independent agent/script reviews bound to request digest;
- OpenSSH signed human approval payloads verified through `ssh-keygen -Y
  verify` and an allowed-signers file;
- imported GitHub review evidence whose reviewer and commit are checked against
  configured policy allowlists;
- interactive local experiment approval that is never sufficient for
  constitutional merge readiness.

### Operator workspaces

`workspace.py` creates a real Git worktree under
`.autobugfix/operator/worktrees/<request-id>` from `request.base_sha` and a
request-derived branch. Workspace metadata records path, branch, and base SHA.
Existing paths or branches are rejected unless they refer to the same request.

### Validation and metrics

`validator.py` resolves named profiles from trusted policy, executes argv arrays
without a shell, stores command artifacts, computes patch/head digests, and
emits events. `metrics.py` validates complete typed metric contracts and records
regression results.

## State Flow

```text
triage created
  -> request created (TRIAGED)
  -> authorization evaluation (REVIEW_PENDING or AUTHORIZED)
  -> workspace created (PATCHING)
  -> postflight (PATCHED)
  -> validation (VALIDATING)
  -> pass + approval chain (MERGE_READY)

Any stage may transition to REJECTED, EXPIRED, REVOKED, or
VALIDATION_FAILED through an append-only event.
```

## Permission Classes

- `layer_local`: one non-protected layer and computed low risk. Automatic
  authorization is permitted.
- `cross_layer`: more than one layer, tests shared across layers, or computed
  medium/high risk. Requires an independent reviewer.
- `constitutional`: protected files or semantics, governance trust code,
  production authority, merge, or release. Requires verified human evidence.

Requested risk is `max(requested_risk, computed_risk)` under an explicit order.

## Git Comparison

Request creation stores `base_sha=git rev-parse HEAD`. Validation uses the
equivalent of:

```text
git diff --name-only <base_sha>
git ls-files --others --exclude-standard
```

This includes commits made after request creation as well as staged and
unstaged changes. The request branch is also frozen and checked.

## GitHub Boundary

The repository includes a `pull_request_target` workflow with read-only
permissions. It checks out the trusted base into one directory and the
candidate into another, then runs the base validator against the candidate.
The workflow must not execute arbitrary candidate scripts with secrets. A
separate setup command documents/configures required branch protection.

## Compatibility and Migration

Governance v1 runtime records are not trusted as v2 approvals. They remain
readable for audit but must be migrated by creating a new v2 triage/request.
Existing `operator triage`, `request`, `review`, `preflight`, `validate`, and
`baseline` command names remain where semantics are compatible. Human-labelled
v1 review creation is removed.

## Rollback

The previous implementation remains available in Git commit `ac2eed2`. A code
rollback may restore it for inspection, but merge protection must not downgrade
to v1 after Governance v2 has become the trusted main policy.

