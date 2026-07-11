# User Preferences

## Autobugfix Purpose Restatement

Before every new Autobugfix task, restate the project baseline:

- Autobugfix is a local, repo-agnostic, Git-controlled loop-engineering and
  harness-engineering control system for real target-repo bug fixing.
- Loop engineering means recurring engineering work is triggered, scheduled,
  executed, verified, observed, stopped, and fed back into future runs.
- Harness engineering means each run has an isolated, permissioned,
  reproducible environment with real tools, verifier/scorer, logs, events, and
  artifacts.
- Execution loop performs real bugfix work in task worktrees.
- Memory loop compiles execution evidence into reviewed LLM wiki content,
  precompiled memory, and reusable skills.
- Eval loop builds reproducible benchmark/historical-case harnesses that call
  the real execution loop and score generated artifacts against oracles.
- Operator loop diagnoses and improves Autobugfix itself on non-main branches.
- LLM agents are bounded execution nodes, not the control plane, not final
  authority, and not substitutes for deterministic validation.

Also state which loop is affected, who owns the state being changed, and which
real validation commands will prove the change.
