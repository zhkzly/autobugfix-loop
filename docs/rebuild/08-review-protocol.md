Review Protocol And Purpose Checkpoints
This protocol is mandatory for rebuilding or materially changing Autobugfix. It exists to prevent an AI agent from implementing a plausible CLI while missing the actual product boundaries.
Purpose Restatement
At every key node, the operator must restate the project purpose in concrete terms:
Autobugfix is a local, repo-agnostic, Git-disciplined, observable on-call
bugfix control system. It is not the target application. It coordinates real
task state, target-repo worktrees, Codex writer/evaluator roles, verifier
commands, human gates, memory extraction, eval scoring, and operator diagnosis.

The operator must also restate the four loop boundaries:
Execution loop: owns task state, worktrees, writer/evaluator iteration,
verifier artifacts, PPE gates, and archive.
Memory loop: reads accepted execution evidence and proposes reviewed long-term
memory changes; it must not mutate task state.
Eval loop: runs isolated real execution cases, compares generated and oracle
diffs, scores outcomes, and writes diagnosis; it must not approve execution or
memory.
Operator loop: supervises real runs and system improvements on non-main
branches; it must not auto-merge, auto-approve PPE, or overfit one case.
Required Checkpoints
Run this protocol at these points:
Before implementation planning.
Before editing state models, services, runners, configs, or role prompts.
Before adding or changing any CLI command.
Before changing Codex runtime behavior or role instructions.
Before changing memory approval or eval scoring behavior.
Before final acceptance testing.
Before merge or handoff.
Each checkpoint must produce this short note in the session, task journal, PR description, or implementation report:
purpose_restatement: <one paragraph>
loop_boundaries_checked:
  execution: <what this change may affect>
  memory: <what this change may affect>
  eval: <what this change may affect>
  operator: <what this change may affect>
state_owner: <which service/module is allowed to mutate state>
non_mock_path: <how the real path is preserved>
next_validation: <specific command or acceptance step>

Multi-Reviewer Passes
When the platform supports subagents, dispatch independent reviewers before the final implementation or merge decision. When the platform does not support subagents, the main agent must run the same passes sequentially and must not claim that subagents were used.
Execution Reviewer
Scope to read:
src/autobugfix/config.py
src/autobugfix/models.py
src/autobugfix/task_store.py
src/autobugfix/service.py
src/autobugfix/runner.py
src/autobugfix/worktree.py
src/autobugfix/verifier.py
src/autobugfix/evaluator.py
src/autobugfix/scheduler.py
src/autobugfix/worker.py
src/autobugfix/ppe.py
execution CLI commands and tests
Questions:
Does every task mutation go through the execution service or store contract?
Are main checkouts protected and worktrees deterministic?
Does the writer run in the task worktree with write access?
Is evaluator read-only?
Are verifier output, diff, raw logs, and events durable?
Can rework loop back to the same task without creating a new task?
Memory Reviewer
Scope to read:
src/autobugfix/memory/store.py
src/autobugfix/memory/service.py
src/autobugfix/memory/collect.py
src/autobugfix/memory/digest.py
src/autobugfix/memory/maintain.py
src/autobugfix/memory/maintainer_backend.py
src/autobugfix/memory/lint.py
src/autobugfix/memory/patch.py
src/autobugfix/memory/search.py
src/autobugfix/memory/context.py
src/autobugfix/memory_worker.py
memory CLI/UI commands and tests
Questions:
Does memory collect from execution evidence rather than inventing evidence?
Are raw packets, digests, proposal patches, and approval states traceable?
Does the maintainer produce proposals without auto-approving them?
Can lint reject malformed memory and malformed approved skills?
Does memory context stay read-only for writer/evaluator roles?
Eval Reviewer
Scope to read:
src/autobugfix/dataset.py
src/autobugfix/eval/models.py
src/autobugfix/eval/artifacts.py
src/autobugfix/eval/runner.py
src/autobugfix/eval/scorers.py
src/autobugfix/eval/diagnosis.py
src/autobugfix/eval/improvements.py
src/autobugfix/eval/supervision.py
eval CLI commands and tests
Questions:
Does eval create an isolated execution environment per case?
Does eval call the real execution loop unless explicitly skipped?
Are run configs snapshotted for repeatable experiment comparison?
Do static and model scorers read artifacts, not hidden local state?
Does diagnosis create evidence-backed improvement packages without changing
execution or memory state?
Codex Runtime And Role Reviewer
Scope to read:
src/autobugfix/codex_backend.py
src/autobugfix/codex_sdk.py
src/autobugfix/codex_runtime.py
src/autobugfix/prompts.py
.agents/role-skills/**/SKILL.md
role-runtime tests
Questions:
Is production using the Codex Python SDK rather than codex exec?
Are role instructions injected explicitly through SDK developer instructions?
Does the runtime preserve the user's local Codex authentication?
Does the writer have only the worktree write scope it needs?
Are evaluator, controller, memory maintainer, and eval judge roles scoped to
their own instructions and cwd?
Does the skill guard expose accidental role leakage?
Portability And Privacy Reviewer
Scope to read:
README.md
AGENTS.md
.autobugfix/config.yaml examples
docs/rebuild/**
.gitignore
tests and fixtures
Questions:
Can a user configure a different target repo without editing code?
Are worktree roots derived from config or documented defaults?
Are private names, internal paths, and organization commands absent from
generated public-facing defaults?
Are runtime directories ignored by Git?
Are PPE commands disabled by default and explicitly configured per repo?
Acceptance Reviewer
Scope to run:
uv run pytest -q
uv run python -m compileall -q src tests scripts
git diff --check
available role-skill validators
the pinned public real-repository acceptance in 05-real-acceptance.md
Questions:
Did production paths use real Git, worktrees, Codex SDK, verifier commands,
memory maintainer, and eval execution?
Are fake backends limited to unit tests or explicitly selected eval model
scoring?
Did the acceptance leave raw evidence files that can be inspected afterward?
Reviewer Output Contract
Each reviewer must output:
reviewer: execution|memory|eval|codex_runtime|portability|acceptance
purpose_restatement: <must include all four loops>
scope_read:
  - <files or commands inspected>
contracts_confirmed:
  - <confirmed invariant>
risks:
  - <remaining risk or empty list>
required_changes:
  - <blocking change or empty list>
acceptance_evidence:
  - <command output path, artifact, or observation>
decision: pass|rework_required|blocked

The final merge or handoff may proceed only if every reviewer is pass, or if remaining risks are explicitly documented as non-blocking.
Anti-Patterns
Reject reviews that:
say only "looks good" without naming loop boundaries;
inspect only CLI files and skip services/runners;
inspect only unit tests and skip real acceptance;
change memory or eval state to make a run look successful;
silently replace production Codex with a fake backend;
ignore stale block reasons, locks, or missing raw logs;
optimize one eval case in a way that weakens the general execution loop.
