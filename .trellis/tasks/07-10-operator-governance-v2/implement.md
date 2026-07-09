# Operator Governance v2 Implementation Plan

## Purpose and ownership

This work constrains the Operator loop without changing the responsibilities of
Execution, Memory, or Eval. Operator governance service owns only diagnosis,
authorization, workspace, validation, regression, and audit state.

## Ordered implementation

1. Replace v1 models and store.
   - Canonical SHA-256 payload digests.
   - Mandatory triage/evidence and frozen Git identity.
   - Immutable record creation and append-only events.
   - Projection reducer and transition validation.

2. Rebuild policy and trust loading.
   - Trusted ref/file constitution loader.
   - Layer classification including layer-aware tests.
   - Computed risk floor and permission class.
   - Frozen-base committed/staged/unstaged/untracked diff collection.

3. Implement approvals.
   - Independent reviewer constraints.
   - OpenSSH signed canonical human approvals.
   - GitHub review evidence import/allowlist validation.
   - Interactive approvals restricted to local experiments.

4. Implement Operator workspace and service.
   - Real request-specific Git branch/worktree.
   - Service-owned preflight, authorization, postflight, revoke, and status.
   - No direct CLI writes to governance files.

5. Replace validation and metrics.
   - Trusted named argv profiles, no shell.
   - Durable command logs and patch/head digests.
   - Typed complete baseline contracts.
   - Merge-ready decision from authorization + scope + validation + metrics.

6. Replace CLI and scripts.
   - Remove fake human review labels.
   - Add approve-sign/approve-import/workspace/status/revoke/trusted-validate.
   - Preserve compatible low-risk commands through the service.

7. Add Git/GitHub enforcement.
   - Trusted validator launcher.
   - Read-only workflow, CODEOWNERS, and hook/protection installer contracts.

8. Isolate acceptance and expose Codex compatibility.
   - Run toy acceptance in a temporary control copy/root.
   - Preserve current control Memory and Operator state.
   - Report SDK/runtime versions and explicit compatibility failures.

9. Add adversarial and integration tests.
   - Self-modified constitution.
   - Forged/mismatched/stale approval.
   - Request overwrite and post-approval scope expansion.
   - Committed diff after frozen base.
   - Unclassified/test bypass.
   - Missing profile/metric and regression.
   - Real worktree creation and protected branch rejection.

## Validation commands

```text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
uv run --cache-dir /tmp/uv-cache python scripts/validate_operator_policy.py ...
uv run --cache-dir /tmp/uv-cache python scripts/real_toy_acceptance.py
```

The toy acceptance is mandatory for final product acceptance. If real Codex
rejects the installed SDK/model combination, preserve that real failure as a
blocker and do not substitute a fake production backend.

## Rollback points

- After immutable store/projection tests, before CLI migration.
- After policy/approval tests, before workspace and GitHub integration.
- Before replacing v1 CLI surfaces.
- Before installing any external GitHub branch-protection setting.

