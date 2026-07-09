---
name: autobugfix-eval-operator
description: Operator workflow for Autobugfix eval experiments.
---

# Autobugfix Eval Operator

Autobugfix is a loop-engineering and harness-engineering control system.
Execution fixes real target repositories in task worktrees. Memory compiles
execution evidence into reviewed LLM wiki content and skills. Eval builds
reproducible benchmark and historical-case harnesses around the real execution
loop. Operator work diagnoses and improves Autobugfix itself.

The operator is not an unconstrained autonomous maintainer. It must use the
operator governance gate before changing code, skills, config, or eval harnesses.

## Required Workflow

1. Observe artifacts first.
   - Read task state, events, logs, diffs, verifier results, eval reports,
     diagnosis packets, and memory proposals.
   - For SWE-bench Verified, remember that the input may be smaller than a real
     on-call case. Do not remove support for screenshots, logs, browser/API
     responses, or human feedback from the general execution context model.
2. Triage by loop and layer.
   - Classify the likely owner as execution, memory, eval, operator, or
     shared runtime/config.
   - State evidence and confidence. Do not jump directly to a writer prompt or
     skill change.
   - Create a triage record:
     `autobugfix operator triage --summary ... --suspected-layer ...`
3. Request scope before patching.
   - Create an operator request:
     `autobugfix operator request --primary-layer ... --risk ...`
   - Use secondary layers only when the evidence justifies cross-layer work.
   - Declare real validation commands in the request.
4. Review and approval.
   - Medium-risk or cross-layer requests need an approved review.
   - High-risk and architecture changes need human approval.
   - The operator may propose architecture changes, but must not approve them.
5. Patch only on a non-main branch.
   - Never patch or commit on `main`/`master`.
   - Run `autobugfix operator preflight --request-id ...` before treating a
     patch as valid.
6. Validate before trust.
   - Run component tests for the layer changed.
   - Run `uv run pytest -q`, compileall, `git diff --check`, role skill
     validation, operator policy validation, and real toy repo E2E when the
     change can affect loop behavior.
   - Run relevant eval/SWE-bench smoke cases after eval harness changes.
7. Record the improvement.
   - Keep request, review, validation, and baseline records under
     `.autobugfix/operator/**`.
   - Summarize what changed, why, which artifacts proved it, and remaining risk.

## Constitution Boundaries

The following require human approval:

- changing the project constitution;
- changing execution/memory/eval/operator loop responsibilities;
- changing task state-machine semantics;
- changing memory approval policy;
- changing eval scoring or oracle semantics;
- changing writer/evaluator sandbox authority;
- making production default to a fake backend;
- allowing direct target main checkout edits;
- removing raw logs, events, artifacts, or reproducibility requirements.

## Diagnosis Rules

- If worktree isolation or target repo config is wrong, fix execution/config
  before changing skills.
- If writer lacks project strategy while evidence, verifier, and harness are
  correct, improve writer skills or memory.
- If tests pass but semantics are wrong, improve verifier/evaluator/scorer
  coverage rather than trusting LLM prose.
- If eval setup is wrong, fix the eval harness or adapter; do not overfit the
  execution loop to one benchmark case.
- If the same failure class repeats, propose memory or approved skill updates
  from accepted evidence.

Do not use eval to approve PPE, archive execution tasks, or approve memory.
