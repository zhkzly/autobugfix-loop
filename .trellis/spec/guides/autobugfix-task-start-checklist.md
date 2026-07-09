# Autobugfix Task Start Checklist

> Use before starting any new Autobugfix task.

---

## Required Restatement

Before implementation, review, eval work, memory work, or operator work, restate
the following in the working thread:

- Autobugfix purpose:
  - Autobugfix is a local, repo-agnostic, Git-controlled bugfix loop/harness
    control system, not the target repository and not a mock LLM CLI.
  - It turns recurring bugfix work into scheduled, observable, reproducible,
    automatically executable loops with deterministic verification and feedback.
  - LLM agents are bounded execution nodes, not the control plane or final
    source of truth.
- Loop engineering:
  - Design how recurring work is triggered, executed, verified, observed,
    stopped, and fed back into future runs.
- Harness engineering:
  - Design the isolated, permissioned, observable, reproducible environment in
    which each agent/workflow run acts and is scored.
- Execution loop:
  - Real target repo bugfix in a task worktree, with writer, verifier,
    evaluator, feedback, human gate, and archive.
- Memory loop:
  - Evidence-to-memory/skills pipeline; read execution evidence, propose
    reviewed knowledge, never mutate execution state or auto-approve itself.
- Eval loop:
  - Reproducible benchmark/historical-case harness calling the real execution
    loop and scoring generated artifacts against oracles.
- Operator loop:
  - Meta-loop for diagnosing and improving Autobugfix itself on non-main
    branches using real artifacts and validation.

## Required Ownership Questions

Before editing, answer:

- Which loop does this task affect: execution, memory, eval, operator, or
  shared config/runtime?
- Who owns the state being changed?
- Which files or directories are durable state versus source code?
- Which validation command proves the change is real rather than mocked?
- Does this change preserve target repo main-checkout protection?
- Does this change preserve raw logs, events, artifacts, and reproducibility?

## Required Validation Framing

State the intended real validation path before changing code:

- Unit/integration command, usually `uv run pytest -q`.
- Compile command, `uv run python -m compileall -q src tests scripts`.
- Diff hygiene command, `git diff --check`.
- Role skill validation when the validator is available.
- Real toy repo E2E when execution, memory, eval, operator, or runtime behavior
  may be affected.

## Common Drift To Avoid

- Treating loop engineering as only LLM reflection.
- Treating harness engineering as only tests.
- Letting LLM judgment replace deterministic checks.
- Adding a benchmark adapter that bypasses the real execution loop.
- Letting memory become automatic self-approval.
- Letting operator work modify main directly.
- Calling fake output a production path.
