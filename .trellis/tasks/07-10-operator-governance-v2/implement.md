# Operator Governance V3 Implementation Plan

1. Extend typed config and role contracts.
   - Add Operator state/artifact/worktree/retry/verification/experiment/
     promotion configuration.
   - Add `operator_supervisor`, `operator_writer`, and `operator_verifier`.

2. Replace the V2 aggregate and projection.
   - Four Request phases.
   - Typed WriterRun, CheckRun, GateSnapshot, FeedbackPacket, ScopeRevision,
     Experiment, Promotion, and event contracts.

3. Replace the V2 runtime store.
   - Configurable external SQLite store with WAL/transactions.
   - Content-addressed artifact registry with provenance.
   - Legacy V2 audit reader only.

4. Implement Transition Guard and service.
   - Explicit command dispatcher and transition registry.
   - Request/start/writer/retry/cancel/verify/scope/reopen/close.
   - Complete Git snapshots and stale-patch invalidation.

5. Implement real Writer and verification loops.
   - Launch production Codex Python SDK in candidate worktree.
   - Read-only WriterView and structured feedback.
   - Trusted deterministic profiles and patch-bound semantic verifier.

6. Implement shadow experiments and promotion.
   - Isolated experiment state/worktrees and outer trusted scoring.
   - PR preparation, merge observation, canary, immutable release activation,
     last-known-good rollback, and revert intent.

7. Replace CLI and agent guidance.
   - Typed Operator mutation/read commands.
   - Writer read-only commands.
   - Independent role skills and minimal lifecycle hooks.

8. Enforce remote admission.
   - Trusted-base bundle/receipt validation.
   - Required GitHub check and branch-protection installer updates.

9. Add migration, adversarial, integration, and acceptance tests.
   - Fake candidate state/approval/result rejection.
   - Retry, timeout, scope revision, stale verification, promotion, canary,
     rollback, and real worktree behavior.
   - Real pinned-upstream E2E and production SDK path; fake backend only in tests.

## Completion Record

- [x] Machine constitution carries project/loop purpose plus executable policy.
- [x] Four-phase Request aggregate and typed child records implemented.
- [x] External SQLite authority, hash-chain events, request leases, artifact
  registry, audit, and crash-time worktree recovery implemented.
- [x] Production Operator Supervisor/Writer/Verifier use Codex Python SDK;
  Writer runs in a cancellable host worker process.
- [x] Bubblewrap verification worktrees hide authority roots and bind the
  trusted runtime venv read-only while importing candidate source.
- [x] Version-bound scope expansion and external constitutional approval
  implemented.
- [x] Shadow experiment, full verification, promotion, canary activation,
  last-known-good rollback, and revert PR paths implemented.
- [x] Advisory candidate manifest and trusted-base GitHub revalidation
  implemented.
- [x] Operator/Writer/Verifier skills and Codex accident-prevention hook added.
- [x] Real Operator SDK acceptance passed.
- [x] Hook read-only artifact access and direct-state-mutation regression tests
  added after real observability review.
- [x] Pinned ItsDangerous real-repository fault-injection E2E passed with
  `gpt-5.4-mini`; two Execution runs, pending Memory proposal, and exact
  generated/oracle Eval scoring completed while target main remained clean.
- [x] Sequential Memory review enforced accepted-evidence-only collection.
- [x] Sequential Codex runtime review removed the isolation-weakening SDK
  compatibility fallback and added a fail-closed regression test.
- [x] Machine constitution assigns project hooks only to `operator_host`,
  projects that mapping to role context, and enforces `hooks=false` for every
  isolated SDK role.

## Validation

```text
uv run --cache-dir /tmp/uv-cache pytest -q
uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts
git diff --check
uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py
uv run --cache-dir /tmp/uv-cache python scripts/real_repository_acceptance.py --model gpt-5.4-mini
uv run --cache-dir /tmp/uv-cache python scripts/real_operator_acceptance.py --model gpt-5.4-mini
```

## Rollback

The V2 implementation remains in commit `534e5cb`. V3 is a new commit and
does not amend or erase V2 history. Legacy runtime files remain untouched.
