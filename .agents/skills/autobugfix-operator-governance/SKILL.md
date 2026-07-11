---
name: autobugfix-operator-governance
description: Govern Autobugfix self-improvement through trusted transitions, isolated Writer attempts, verification feedback, scope revision, experiments, promotion, and rollback.
---

# Autobugfix Operator Governance

Autobugfix is a loop/harness control system. Execution fixes configured target
repositories, Memory compiles accepted evidence into reviewed knowledge and
skills, Eval measures the real Execution loop, and Operator improves
Autobugfix without owning other loops' state. Agents propose actions; the
trusted service owns state.

## Protocol

1. Run `autobugfix operator guide`, restate the project purpose and four loop
   boundaries, then name the affected loop, its state owner, and the real
   validation command. Do this before every new Operator task.
2. Read `autobugfix operator status`. It is a service-derived view, not
   permission to write SQLite.
3. Diagnose the owning layer from real evidence before requesting changes.
4. Capture the configured trusted-ref experiment baseline before changing a
   behavior layer. Metrics typed by an Operator are not evidence.
5. Create a request with frozen base, layers, at least one planned path pattern,
   profiles, and baseline. Never write governance records directly.
6. Request `start`; only the service may create the experiment worktree.
7. Request one Writer attempt. Do not edit main or candidate directly.
8. Request fast verification and consume Guard feedback rather than model
   claims.
9. Retry deterministic failures within budget. Revert unnecessary scope via
   Writer; request a versioned scope expansion when evidence proves it needed.
10. Cross-layer expansion needs approval bound to that scope version and at
   least one path pattern for every added layer.
   Protected/constitutional expansion needs verified human authority.
11. Commit through `operator candidate-commit`; it writes the advisory PR
    manifest before the trusted commit. Then request full verification.
12. Run the same configured experiment profile. Guard derives a metric receipt
    bound to profile inputs, HEAD, and patch digest. A generic local dataset
    profile is not an official SWE-bench run; official claims require the
    official adapter and harness. Toy runs are developer smoke only.
13. Promote only a current VERIFIED patch. PR merge, canary activation, and
    rollback are separate trusted transitions.

Request phases are only REQUESTED, ACTIVE, VERIFIED, and CLOSED. WriterRun,
CheckRun, gates, scope revisions, experiments, and promotions are child
records. Never invent or directly set any status.

## Failure Routing

- Scope failure: revert the candidate change or request scope expansion.
- Test failure: inspect authoritative output and request Writer retry.
- Semantic failure: diagnose the requirement; deterministic checks still win.
- Approval pending: stop and wait for external authority.
- Patch changed after verification: reopen and verify again.
- Canary failed: restore last-known-good, preserve evidence, and create a
  revert PR.

Candidate-authored state, approval, test, and receipt files are untrusted.
Only Git facts, host verifier output, external approvals, and trusted CI may
drive transitions. Hooks prevent common mistakes but are not authority.

## Hook Assignment

The project `PreToolUse` and `UserPromptSubmit` hooks belong only to
`operator_host`: the human/current main agent supervising Autobugfix from its
trusted source checkout. They block obvious direct merge/state mutation and
inject governance context for that host session.

Do not expect these hooks in `operator_supervisor`, `operator_writer`,
`operator_verifier`, Execution Writer/Evaluator, Memory maintainer, Eval judge,
or Eval's inner Execution roles. Those SDK roles run with an isolated
`CODEX_HOME` and `hooks=false`. Their boundaries are service-owned state,
sandbox, worktree, verifier, scorer, approval, and CI. If a role observes the
host Operator hook, stop and report a runtime-isolation defect.

Use `autobugfix operator advance` only for policy-based automatic progression.
It performs one legal action at a time and stops on semantic, scope, authority,
or retry-budget blocks. Never loop blindly around a block.
