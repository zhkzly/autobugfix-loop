# Implementation Plan

## Purpose Restatement

Autobugfix must remain a real local control system for on-call bugfixes. The configuration refactor must make roles and skills explicit without weakening worktree isolation, Codex SDK production execution, verifier realism, memory approval gates, or eval isolation.

## Steps

1. Add role-related dataclasses and config parsing:
   - `RoleConfig`, `ResolvedRoleConfig`, `WorkerConfig`, `EvalConfig`.
   - Deep config merge.
   - Built-in role defaults for writer/evaluator/controller/memory_maintainer/eval_judge.
   - Repo-level role overrides.

2. Add a role resolver:
   - Resolve model, sandbox, approval mode, timeout, skill paths, backend, and log templates.
   - Validate and load role instructions.
   - Preserve compatibility with legacy model fields and scheduler timeouts.

3. Replace scattered role configuration:
   - `codex_runtime.build_codex_request` accepts a resolved role or resolves internally.
   - `runner.py` resolves writer/evaluator.
   - `memory/maintainer_backend.py` resolves memory_maintainer.
   - `eval/runner.py` snapshots and propagates effective role config.

4. Improve operator visibility:
   - Extend `doctor`.
   - Extend `codex probe-role`.
   - Optionally add config inspection helpers if needed by tests.

5. Update tests:
   - Config role parsing and backward compatibility.
   - Skill path resolution and strict missing-skill behavior.
   - Runner request uses resolved model/sandbox/timeout.
   - Memory maintainer request uses resolved role.
   - Eval snapshots role config.
   - CLI doctor/probe output includes effective role fields.

6. Run verification:
   - `uv run pytest -q`
   - `uv run python -m compileall -q src tests scripts`
   - `git diff --check` or publish-copy equivalent if root Git remains invalid
   - `uv run python scripts/validate_role_skills.py` if present
   - `uv run python scripts/real_toy_acceptance.py`

## Rollback Point

If role resolver integration destabilizes execution, revert call sites to old fields only after preserving the new dataclasses and tests that describe the desired contract.
