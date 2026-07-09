# Configuration System Refactor Design

## Project Purpose Anchor

Autobugfix is a local, repo-agnostic, Git-controlled, observable on-call bugfix control system. It coordinates bugfix work against target repositories configured under `.autobugfix/config.yaml`; all target code changes must happen in task worktrees, never directly in the target main checkout.

## Loop Boundaries

- Execution: `AutobugfixService`, `TaskStore`, `TaskRunner`, `worktree`, `verifier`, and evaluator decisions own task execution state.
- Memory: `MemoryService` and `MemoryStore` own `.autobugfix-memory` state and read execution evidence without mutating execution task state.
- Eval: `eval.runner` owns eval run artifacts and calls isolated real execution.
- Operator: CLI/UI/worker code uses services and projections; it does not directly own task state.

## Configuration Model

Introduce a role-first model:

- `RoleConfig`: backend, model, sandbox, approval_mode, timeout_seconds, skill_paths, raw_log_template, stderr_log_template, allow_repo_overrides.
- `CodexConfig.roles`: global role map for `writer`, `evaluator`, `controller`, `memory_maintainer`, and `eval_judge`.
- `RepoProfile.codex_roles`: per-repo role overrides.
- `WorkerConfig`: execution and memory worker intervals.
- `EvalConfig`: default eval model mode, timeout overrides, and role overrides for isolated eval configs.

Keep `writer_model`, `evaluator_model`, and `controller_model` as deprecated compatibility inputs that feed role defaults when `roles.<role>.model` is unset.

## Role Resolver

Add a single resolver module that returns `ResolvedRoleConfig` from:

1. built-in defaults,
2. global `codex.roles.<role>`,
3. loop-specific overrides where applicable,
4. repo-specific `repos.<repo>.codex.roles.<role>`.

The resolver also validates skill paths and resolves relative paths against the control project root. All runtime callers use the resolver instead of manually passing role constants.

## Call Site Changes

- Execution writer/evaluator: `TaskRunner` resolves `writer` and `evaluator` with `repo_id`.
- Memory maintainer: `CodexMemoryMaintainerBackend` resolves `memory_maintainer`.
- Eval: isolated control configs copy full role defaults and apply eval overrides; `resolved-config.yaml` snapshots effective role config.
- Operator: `doctor` and `codex probe-role` print resolved role configs.

## Compatibility

Existing config files keep working. If only `codex.writer_model` is set, it becomes the effective `writer` model unless `codex.roles.writer.model` is configured. Existing scheduler timeout fields remain accepted but are mapped into role timeout fallback.

## Risks

- Config merging can silently drop nested role values if implemented as a shallow merge. Use explicit deep merge for mappings.
- Eval isolated configs must copy role skills and role defaults consistently.
- Role skill guard must continue preventing role instruction leakage.
- Tests must assert behavior through resolved roles, not duplicate the resolver logic.
