# Refactor Autobugfix configuration system

## Goal

Autobugfix must have a complete, executable configuration system that treats Codex roles, role skills, models, sandbox policy, approval policy, timeouts, workers, target repositories, memory, and eval runs as first-class configuration instead of scattered code constants.

The project purpose remains unchanged: Autobugfix is a local, repo-agnostic, Git-controlled, observable on-call bugfix control system. It controls target repositories through configured Git worktrees; it is not the target business repository and it must not mutate the target main checkout directly.

## Requirements

- Preserve four loop boundaries:
  - Execution loop owns task state and runs task -> context -> worktree -> writer -> verifier -> evaluator -> feedback -> human gate -> archive.
  - Memory loop owns memory state and reads execution evidence to produce raw packets, digests, proposal patches, and approved active memory.
  - Eval loop owns eval run state and may call the real execution loop in isolated repos without approving PPE, archiving tasks, or approving memory proposals.
  - Operator loop owns supervision through service/projection APIs, CLI, UI, workers, and artifacts; UI/CLI must not bypass services to mutate task state.
- Keep production execution real: production CLI must use the Python Codex SDK backend by default, writer must be able to edit the worktree, evaluator must be read-only by default, and verifier must run configured target repo test commands.
- Add a role-first configuration model for writer, evaluator, controller, memory_maintainer, and eval_judge.
- Make role skills configurable while preserving safe built-in defaults.
- Provide a single role resolver used by execution, memory, eval, and operator inspection.
- Support repo-level role overrides without hardcoding local paths, internal repo names, users, or company-specific commands.
- Keep backward compatibility for the existing `writer_model`, `evaluator_model`, and `controller_model` fields during migration.
- Improve `doctor` / role probing so operators can see effective role, model, sandbox, approval mode, timeout, skill paths, runtime root, and repo overrides.
- Add tests that prove role resolution, skill loading, execution role usage, memory role usage, eval config snapshots, and backward compatibility.

## Acceptance Criteria

- [ ] `uv run pytest -q` passes.
- [ ] `uv run python -m compileall -q src tests scripts` passes.
- [ ] `git diff --check` or a workspace-equivalent diff check passes.
- [ ] Role skill validation passes if the validator is available.
- [ ] The real toy repo E2E from `docs/rebuild/05-real-acceptance.md` passes.
- [ ] No production path defaults to fake writer/evaluator.
- [ ] Target repo modifications still happen only inside task worktrees.
- [ ] Memory proposals are not auto-approved by maintain runs.
- [ ] Eval remains a wrapper around isolated real execution, not a second task state machine.

## Notes

- The user explicitly requested a high-risk/high-reward refactor instead of a minimal compatibility patch. The implementation should centralize the configuration model even if that requires touching multiple modules.
