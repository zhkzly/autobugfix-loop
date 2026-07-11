# Autobugfix Loop And Harness Contract

> Project-level contract for preserving Autobugfix's purpose before coding,
> debugging, eval work, or operator changes.

---

## Scenario: Autobugfix Loop And Harness Engineering Baseline

### 1. Scope / Trigger

- Trigger: Any new Autobugfix task, feature, refactor, eval integration,
  memory change, operator change, or review.
- This contract must be restated before starting a new task so the work does
  not drift into a mock CLI, a one-shot LLM prompt, or benchmark-only code.
- Autobugfix is a local, repo-agnostic, Git-controlled bugfix control system
  centered on loop engineering and harness engineering.
- The LLM agent is a bounded worker node. The control plane is the harness,
  state machine, scheduler, verifier, scorer, artifacts, memory, eval, and
  human/operator gates.

### 2. Signatures

- Execution entrypoints:
  - `AutobugfixService.create_task(repo_id, title, body) -> TaskRecord`
  - `AutobugfixService.add_context(task_id, kind, content) -> Path`
  - `AutobugfixService.run_task(task_id) -> TaskRecord`
  - `TaskRunner.run(task_id) -> TaskRecord`
- Memory entrypoints:
  - `MemoryService.collect(task_id) -> Path`
  - `MemoryService.digest(task_id) -> Path`
  - `MemoryService.maintain(task_id) -> Path`
  - `MemoryService.approve(proposal_id, note) -> Path`
- Eval entrypoints:
  - `run_eval(project_root, dataset, out, ...) -> Path`
  - `score_path(path) -> Path`
- Operator/adapter entrypoints:
  - CLI commands must call services or projections.
  - Gradio/UI/controller code must not mutate task, memory, branch, or eval
    state directly.

### 3. Contracts

- Loop engineering means turning recurring engineering work into a scheduled,
  observable, reproducible, automatically executable, feedback-driven control
  loop.
- A loop must define:
  - trigger or cadence;
  - goal and machine-checkable success contract;
  - durable state and memory;
  - execution nodes such as LLM workers, shell commands, test runners, static
    checkers, scorers, and human gates;
  - verification ladder favoring deterministic checks before LLM judgment;
  - logs, events, artifacts, traces, and diagnosis;
  - terminal states such as success, failure, blocked, retry, human review, or
    archive;
  - feedback path into memory, eval, or operator improvement.
- Harness engineering means designing the execution wrapper that lets an agent
  act in a real environment while remaining isolated, constrained, observable,
  and reproducible.
- A harness must define:
  - target repo, base commit, branch, and worktree;
  - injected config and skills;
  - tool and permission boundaries;
  - verifier commands and static checks;
  - raw logs, events, artifacts, diffs, and traces;
  - scorer/oracle behavior;
  - timeout, recovery, and escalation behavior.
- Execution loop purpose:
  - Given a configured target repo, bug/problem, and evidence/context, produce
    a real candidate fix in an isolated task worktree.
  - Writer may edit only the task worktree.
  - Verifier runs the target repo's configured real commands.
  - Evaluator is read-only by default.
  - Human gate owns PPE approval, acceptance, and archive.
- Memory loop purpose:
  - Convert execution evidence into precompiled memory, LLM wiki content, and
    reusable skills.
  - Memory may collect only evidence that the Execution human gate accepted,
    either in `accepted` state or an archive whose result is `accepted`.
  - Memory must not mutate execution task state, locks, gates, branches, or
    target worktrees.
  - Memory proposals require explicit approval before becoming active memory or
    approved skills.
- Eval loop purpose:
  - Build reproducible benchmark/historical-case harnesses that call the real
    execution loop and measure system behavior.
  - Eval may create isolated repos/control roots and collect generated diff,
    oracle diff/tests, reports, scores, and diagnosis.
  - Eval must not approve PPE, archive execution tasks, or approve memory.
  - Eval adapters materialize benchmark-specific repositories and oracles into
    one versioned Case contract. Tests/official harness results are the primary
    score; oracle diff equality is diagnostic only.
- Operator loop purpose:
  - Debug and improve Autobugfix itself by reading artifacts, locating the
    failing subsystem, and changing code/skills/config on non-main branches.
  - Operator should diagnose whether failures belong to repo config, task
    context, worktree isolation, writer role/skill/model, verifier command,
    evaluator, memory, eval harness, scheduler/state machine, or UI/projection.
  - Operator is the supervisor harness for the other loops, not their state
    owner. It requests transitions; a trusted Guard/service validates and
    records them.
  - The human constitution is normative soft guidance. The machine
    constitution injects that guidance into Operator roles and enforces path,
    risk, runtime-role, transition, and verification minima.
  - Project Codex hooks guard the supervising main-agent Operator session only;
    isolated SDK role runtimes disable hooks and remain safe without them.
  - Named studies use service-owned experiment lines, explicit Mini budget
    waves, trusted integration, immutable checkpoints, and history-preserving
    rollback. `H_bug` and `H_general` are independent successors of the same
    frozen `H0`; neither may inherit the other's treatment or case feedback.
  - Benchmark sources, Optimization/Holdout splits, and checkpoint names are
    experiment protocols. They may evolve through governed research changes
    without redefining the four loop purposes in this project constitution.
  - A Study manifest contains visible Optimization inputs only. Sealed
    Holdout case IDs, gold data, and case-level reports remain outside Operator
    storage under the Eval/Guard authority plane; Operator receives aggregate
    final metrics only.

### 4. Validation & Error Matrix

- Missing target repo config -> fail before task execution mutates target code.
- Attempt to edit target repo main checkout -> invalid harness behavior.
- Writer outside task worktree -> invalid execution harness behavior.
- Production CLI defaulting to fake backend -> invalid production behavior.
- Missing verifier command -> invalid repo profile.
- No raw logs/events/artifacts for a run -> invalid observability.
- Memory auto-approves its own proposal -> invalid memory loop behavior.
- Memory collects an unaccepted/failed/active task -> invalid memory input.
- Eval creates a second task state machine -> invalid eval loop behavior.
- Operator changes main directly -> invalid operator loop behavior.
- Operator or Writer edits line/budget/checkpoint authority directly, reuses a
  grant across studies, or transfers treatment between `H_bug` and
  `H_general` -> invalid operator experiment behavior.
- New task starts without restating this baseline -> process violation.

### 5. Good/Base/Bad Cases

- Good: A SWE-bench Verified adapter creates an isolated case repo, writes
  `.autobugfix/config.yaml`, calls `AutobugfixService.create_task`, calls the
  real execution loop, then scores generated diff/tests against an oracle.
- Good: A failed verifier run records command, exit code, stdout/stderr,
  diff, task event, and terminal state so the operator can diagnose the
  failing subsystem.
- Base: A manual CLI task uses `create`, `context add`, `run`, `inspect`,
  `feedback`, `gate`, and `archive` through service methods.
- Bad: A CLI command prints fake success without creating a worktree, running
  tests, or writing artifacts.
- Bad: Eval compares only LLM prose or evaluator opinion without a generated
  diff, verifier output, scorer, or reproducible setup.

### 6. Tests Required

- Unit tests for service boundaries: adapters call services/projections, not
  task or memory files directly.
- Unit tests for role/config resolution before Codex requests.
- Integration tests for worktree isolation and verifier command execution.
- Memory tests for collect/digest/maintain/proposal/approve separation.
- Eval tests that call real execution-loop surfaces with a fake backend only in
  tests, then write generated/oracle artifacts and reports.
- Acceptance tests that run the pinned public real-repository E2E path from
  `docs/rebuild/05-real-acceptance.md`.
- Before finishing implementation work, run:
  - `uv run pytest -q`
  - `uv run python -m compileall -q src tests scripts`
  - `git diff --check`
  - role skill validation when available
  - real repository E2E when the change can affect execution, memory, eval, or
    operator behavior

### 7. Wrong vs Correct

#### Wrong

```text
Ask an LLM to fix a bug, accept its answer, and call the task done.
```

This treats Autobugfix as a prompt wrapper and bypasses loop state, worktree
isolation, real verification, artifacts, and feedback.

#### Correct

```text
Create task -> add evidence/context -> create isolated worktree -> run writer
node -> run deterministic verifier/static checks -> preserve diff/logs/events
-> run read-only evaluator -> wait for human gate -> feed accepted evidence
into memory -> measure system behavior through eval -> let operator improve the
right subsystem on a non-main branch.
```

This keeps LLM execution inside a real loop and a real harness.
