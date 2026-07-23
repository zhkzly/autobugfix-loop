# Review: SWE Runtime Freeze And Bridge

Date: 2026-07-23

This review was completed sequentially by the main agent. No independent
subagents were available, so this document does not claim independent review.

## Scope

Commit `2b67800` adds an Eval-owned `verified_build_network_mode` configuration
field, binds it into the official evaluator runtime identity, and dispatches
the locked SWE-bench official module through a narrow bridge. The portable
default remains `default`; `host` is an explicit local Docker-build workaround.
It does not change Writer, Memory, Operator, or target-repository state
ownership.

## Sequential Reviews

1. **Execution boundary**: no Execution service, Writer permissions, target
   main checkout behavior, or visible verifier contract changed. The real H0
   development run retained one Writer call, one visible verifier call, one
   read-only evaluator call, and a clean target main checkout.
2. **Memory boundary**: no Memory code or active-memory state changed. The H0
   candidate remains pending human PPE approval, so no new Memory collection
   or approval was permitted.
3. **Eval/scoring**: the selected build network mode appears in both the
   evaluator runtime identity and recorded official command argv. A real
   `sympy__sympy-12481` qualification produced two resolved gold scorer runs
   with one stable local image and an eligible v4 receipt. Both H0 and Raw
   official scores ran only after their submissions were frozen.
4. **Codex runtime**: the H0 run used the protocol-pinned subject SHA and
   treatment guard. The independent Raw run retained exactly one SDK thread,
   one SDK turn, `deny_all`, workspace-write, and disabled tool network.
5. **Portability/privacy**: the implementation uses no host-specific source
   paths or credentials. The local `host` setting is documented as a config
   choice, not a source default. Official scorer artifacts show Bubblewrap
   masks control/credential roots and record the bridge argv.
6. **Acceptance**: host-level root tests completed with `330 passed`; focused
   SWE tests completed with `43 passed`; standalone Raw tests completed with
   `6 passed`; compileall, `git diff --check`, role-skill validation, and the
   real SWE doctor passed.
   A documented Raw test command was corrected to use `uv run --directory` so
   pytest collects the standalone project rather than the repository root.

## Findings

- No source correctness or loop-boundary defect remains from this change.
- The initial SWE doctor correctly blocked at 9.4 GiB free rather than
  weakening its 10 GiB threshold. A clean, registered historical worktree was
  duplicated and verified on persistent storage before its `/tmp` copy was
  removed; the rerun passed with 10.58 GiB free.
- The H0 development calibration was a valid unresolved repair and the Raw
  calibration was a valid resolved repair on the same public case. This is one
  development observation, not a formal result or authorization to change H0,
  skills, Memory, or Operator behavior.

## Required Follow-up

Continue only with the sealed formal Experiment 2 preparation flow. The two
development calibration outcomes remain observational and must not become
feedback for H0 or Raw.
