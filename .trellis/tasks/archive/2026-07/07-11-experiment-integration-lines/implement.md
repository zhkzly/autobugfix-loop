# Implementation plan: governed experiment integration lines

## Phase 1: models and additive store migration

- [x] Add typed study, line, integration, checkpoint, budget-grant, and usage
      models with canonical digest payloads.
- [x] Add transactional tables, foreign keys, indexes, line leases, and
      compare-and-swap store methods.
- [x] Add migration, old-database, digest, tamper, duplicate, and concurrency
      tests.
- [x] Run focused store/model tests (`tests/test_operator_experiment_store.py`)
      plus the existing Operator policy suite.

## Phase 2: configuration and policy

- [x] Add typed experiment-line/integration/checkpoint/budget config.
- [x] Validate roots, branch templates, protected refs, model allowlist,
      positive limits, and one-case default concurrency.
- [x] Extend the trusted constitution with experiment lineage, budget,
      authority,
      roots, role capabilities, and validation minima.
- [x] Add config/policy tests and run role-skill validation.

## Phase 3: study, line, and request services

- [x] Implement study/H0 creation and independent line initialization.
- [x] Refactor request base resolution to support a line ID while preserving
      legacy explicit current-HEAD behavior.
- [x] Freeze line generation/head in request records and projections.
- [x] Reject stale/protected/conflicting line requests and refs.
- [x] Add CLI read/mutation adapters through service/projection methods.
- [x] Run focused service/store/CLI tests with real Git refs.

## Phase 4: budget metering

- [x] Implement typed budget requests/grants for exact 3/8/16 waves.
- [x] Implement atomic reservation/finalization and indeterminate accounting.
- [x] Wrap study SDK calls with a metered backend whose production delegate is
      the real `CodexSDKBackend`.
- [x] Enforce Mini-only, allowed case/role, attempts, revisions, calls, time,
      and concurrency before launch.
- [x] Test denial, quota failure, crash, duplicate call key, and no-Spark
      fallback.

## Phase 5: trusted candidate integration

- [x] Implement line/request leases and current Git/SQLite consistency checks.
- [x] Create isolated integration worktrees and merge with hooks disabled.
- [x] Re-run trusted profiles and bind command artifacts.
- [x] Create Guard commit, compare-and-swap local refs, optional remote sync,
      and immutable
      IntegrationReceipt.
- [x] Add reconciliation or expected-SHA ref restoration for partial
      Git/store/remote failures.
- [x] Test stale/concurrent, dirty, changed patch, failed validation,
      partial-store failure, and successful integration.

## Phase 6: checkpoints and rollback

- [x] Implement digest-complete H0/H_bug/H_general checkpoint validation.
- [x] Enforce independent H0 parent lineage.
- [x] Materialize read-only releases and active experiment pointers.
- [x] Implement tree-restoring, history-preserving rollback commits.
- [x] Test activation, rollback, failed rollback validation, rejected metric
      receipts, and preserved Git history.

## Phase 7: docs, skills, and validation

- [x] Update README/config examples and Operator supervisor/writer/verifier
      skills with line, budget, integration, checkpoint, and rollback flows.
- [x] Update active Operator governance spec after behavior is verified.
- [x] Run `uv run --cache-dir /tmp/uv-cache pytest -q`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`.
- [x] Run `git diff --check`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_operator_policy.py`.
- [x] Run the real Operator acceptance with `gpt-5.4-mini` only after a minimal
      explicit budget grant; retain raw artifacts.
- [x] Perform the required sequential reviewer passes in the main inline agent
      and label them honestly as non-subagent reviews.

## Rollback points

- Store migration must pass before service code uses new tables.
- Budget metering must pass before any benchmark model run.
- Successful integration and rollback tests are required before adapter work
  may depend on experiment lines.
- Any incomplete authority path fails closed; do not add a direct Git/SQLite
  fallback in CLI or role code.
