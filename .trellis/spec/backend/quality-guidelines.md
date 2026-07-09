# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

<!--
Document your project's quality standards here.

Questions to answer:
- What patterns are forbidden?
- What linting rules do you enforce?
- What are your testing requirements?
- What code review standards apply?
-->

(To be filled by the team)

---

## Forbidden Patterns

<!-- Patterns that should never be used and why -->

(To be filled by the team)

---

## Required Patterns

<!-- Patterns that must always be used -->

(To be filled by the team)

---

## Testing Requirements

### Scenario: Autobugfix Role Configuration Resolver

#### 1. Scope / Trigger

- Trigger: Any change that affects Codex role configuration, role skills,
  model selection, sandbox policy, approval policy, or timeout behavior.
- Applies to execution writer/evaluator, memory maintainer, eval judge, and
  operator role inspection.

#### 2. Signatures

- `load_config(project_root) -> AutobugfixConfig`
- `resolve_role(config, role, repo_id=None, overrides=()) -> ResolvedRoleConfig`
- `build_codex_request(..., repo_id=None, role_override=None, resolved_role=None) -> CodexRequest`

#### 3. Contracts

- `.autobugfix/config.yaml` owns `codex.roles.<role>` defaults for backend,
  model, sandbox, approval mode, timeout, skill paths, and log templates.
- `repos.<repo-id>.codex.roles.<role>` may override roles only when the role
  allows repo overrides.
- Legacy `writer_model`, `evaluator_model`, and `controller_model` are
  compatibility inputs only; new code must not read them outside config
  parsing.
- Runtime callers must use `resolve_role`; they must not hardcode role model,
  sandbox, timeout, approval mode, or skill paths at call sites.

#### 4. Validation & Error Matrix

- Unknown role -> `RoleConfigError`.
- Role without skill paths -> `RoleConfigError`.
- Missing role skill with strict skill guard -> `PromptError`.
- Unknown repo id in a repo-overridable role -> config repo lookup error before
  task mutation.

#### 5. Good/Base/Bad Cases

- Good: `TaskRunner` resolves `writer` and `evaluator` with `repo_id` before
  building `CodexRequest`.
- Base: Existing configs with only `writer_model` still map to writer role
  model.
- Bad: Passing `"workspace-write"` or `config.codex.writer_model` directly from
  runner code.

#### 6. Tests Required

- Config role parsing and backward-compatible legacy model mapping.
- Repo role override precedence.
- Runner writer/evaluator request fields come from resolved roles.
- Memory maintainer request fields come from `memory_maintainer` role.
- Eval run snapshots resolved writer/evaluator roles.
- CLI doctor/probe exposes effective role fields.

#### 7. Wrong vs Correct

Wrong:

```python
build_codex_request(root, "writer", prompt, worktree, "workspace-write", cfg.codex.writer_model, timeout, raw, err)
```

Correct:

```python
writer_role = resolve_role(cfg, "writer", repo_id=task.repo_id)
build_codex_request(root, "writer", prompt, worktree, None, None, None, raw, err, resolved_role=writer_role)
```


---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
