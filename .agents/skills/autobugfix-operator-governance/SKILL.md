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
   behavior layer. Metrics typed by an Operator are not evidence. `strict`
   baseline mode requires every configured command to pass. Use explicit
   `baseline_mode: measure` only for a benchmark H0 where target tests are
   expected to fail; it records those observed failures but still rejects
   timeout, sandbox/broker/harness failures, and incomplete raw logs.
5. Create a request with frozen base, layers, at least one planned path pattern,
   profiles, and baseline. Never write governance records directly.
6. Request `start`; only the service may create the real candidate worktree and
   freeze its clean Writer-admission baseline in trusted artifacts.
7. Request one Writer attempt. Do not edit main or candidate directly. The
   Writer receives an ephemeral service-owned staging directory as its working
   directory. A Writer may make or even commit changes there, but only the
   service may validate the staging diff and apply approved files to the real
   candidate.
8. Request fast verification and consume Guard feedback rather than model
   claims. Both fast and full verification execute in a disposable detached
   worktree materialized from the trusted candidate snapshot; verifier caches,
   test output, and command side effects must never mutate the candidate.
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
13. For an embedded OpenSSH merge approval, use `approval-payload --stage
    merge --merge-binding patch` after trusted verification. The bundle is
    committed after signing, so a pre-bundle HEAD binding would become stale.
    External PR reviews may bind the final PR HEAD instead.
14. Promote only a current VERIFIED patch. PR merge, canary activation, and
    rollback are separate trusted transitions.

For a governed benchmark study, insert these service-owned steps around the
same Request lifecycle:

1. Create a Study from a named cohort and frozen `H0` commit. The service
   freezes harness, policy, role/config/model/skill digests, a read-only Memory
   snapshot, the visible Optimization manifest, and a machine-readable success
   contract. Sealed Holdout manifests and case-level results stay outside all
   Operator roots under the external Guard.
2. Initialize exactly one experiment line for that Study from a registered H0
   aggregate metric. The service creates the real Git ref and read-only `H0`
   release without changing main.
3. Request a budget wave and obtain a digest-bound interactive human grant.
   Waves advance only `3 -> 8 -> 16`; `gpt-5.4-mini` is the only model and
   fallback is forbidden.
4. Bind each Request to current line generation/head and current grant.
   Supervisor, Writer, and semantic Verifier reserve usage before the SDK call
   and record a terminal outcome afterward.
5. Integrate only a clean, committed, VERIFIED candidate through the trusted
   service. It reruns policy and validation in a separate worktree, compares
   the candidate patch, then advances Git ref and SQLite generation with CAS.
6. Create `H_bug` or `H_general` only from an integrated line whose
   content-addressed aggregate Guard metric satisfies the frozen success
   contract. A checkpoint records immutable Git/config/skill/Memory/policy/
   budget/metric digests and activates a read-only release.
7. Roll back by creating a new history-preserving commit with the selected
   checkpoint tree. Never reset or force-push the line.

For a service-owned benchmark profile, the trusted benchmark harness must call
`record_observed_baseline_metric` from a committed `capture_baseline` receipt
and `record_observed_candidate_metric` from the matching completed shadow
experiment after integration. Those methods copy only service-observed metrics,
command observations, evidence digests, integration binding, and budget
binding; they accept no numeric score input. Their Study contract must be an
`observed_guard` contract that requires all commands to pass, declares numeric
metric rules (`eq`, `gte`, or `lte`), and pins a protected ref to frozen H0.
For an official external benchmark such as SWE-bench, import only the external
Guard's independently produced receipt through the dedicated Guard API. There
is intentionally no generic Operator CLI for either path.

The two studies share one named H0 cohort but no treatment: Experiment 1 uses
Defects4J with 10 visible Optimization cases and 6 sealed unseen-repository
Holdout cases to produce `H_bug`; Experiment 2 uses 10 SWE-bench Verified
Optimization cases and 6 SWE-bench-Live sealed unseen-repository Holdout cases
to produce `H_general`. Experiment 2 must not inherit `H_bug` code, skills,
Memory, artifacts, results, or case-level feedback.

Request phases are only REQUESTED, ACTIVE, VERIFIED, and CLOSED. WriterRun,
CheckRun, gates, scope revisions, experiments, and promotions are child
records. Never invent or directly set any status.

## Failure Routing

- Scope failure: revert the candidate change or request scope expansion.
- Test failure: inspect authoritative output and request Writer retry.
- Semantic failure: diagnose the requirement; deterministic checks still win.
- Approval pending: stop and wait for external authority.
- Patch changed after verification: reopen and verify again.
- Budget pending/exhausted/expired or wrong model: stop before SDK call,
  preserve usage evidence, and request next legal human grant when needed.
- Stale experiment line or cohort mismatch: discard stale transition result,
  reload service projection, and create request against current generation/head.
- Canary failed: restore last-known-good, preserve evidence, and create a
  revert PR.

Candidate-authored state, approval, test, and receipt files are untrusted.
Only Git facts, host verifier output, external approvals, and trusted CI may
drive transitions. Hooks prevent common mistakes but are not authority.

The service records a content-addressed baseline when it creates the candidate
and records every successful Writer application with before/after Git and
content digests. A patch made before a WriterRun, between WriterRuns, or after
the final WriterRun is not part of that chain. `writer-start`,
`candidate-commit`, and verification reject it rather than allowing a later
WriterRun to claim it. Treat a Writer-admission rejection as a blocked request:
preserve the artifact, discard/recreate the candidate from the frozen base, and
start a new governed attempt. Do not repair the candidate with shell commands.

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
