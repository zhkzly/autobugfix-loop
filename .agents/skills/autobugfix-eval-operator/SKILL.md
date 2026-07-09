---
name: autobugfix-eval-operator
description: Operator workflow for Autobugfix eval experiments.
---

# Autobugfix Eval Operator

Autobugfix is a loop-engineering and harness-engineering control system.
Execution fixes real target repositories in task worktrees. Memory compiles
execution evidence into reviewed LLM wiki content and skills. Eval measures the
real execution loop in reproducible harnesses. Operator diagnoses and improves
Autobugfix but does not own the other loops' state.

The Operator is a bounded execution node. Use Governance v2 before modifying
code, tests, config, skills, validation, or baselines.

## Required Workflow

1. Read real artifacts and diagnose the owning layer. Do not jump directly to
   prompt or skill changes. SWE-bench inputs do not replace screenshots, logs,
   browser/API evidence, or human feedback in general on-call tasks.
2. Create immutable triage with at least one existing evidence path, digest, or
   URI.
3. Create request before patching. Request freezes base SHA, branch, layers,
   validation profiles, baseline, expiry, and request digest.
4. Obtain only the required authority:
   - one low-risk layer: automatic preflight;
   - cross-layer/medium: independent reviewer, never the request creator;
   - constitutional: OpenSSH-signed human scope approval or allowlisted GitHub
     review evidence.
5. Run `operator workspace-create`. Patch only the returned real Git worktree.
6. Run postflight. If actual paths elevate risk, obtain the new approval; never
   lower requested risk to fit the patch.
7. Run trusted validation profiles and baseline metrics. Do not inject shell
   commands into a request.
8. Constitutional work requires merge approval bound to patch digest or PR
   HEAD, then `finalize`.
9. Export the authorization bundle and let the base-version GitHub check rerun
   policy and validation before merge.

## Commands

```text
autobugfix operator triage ... --evidence <artifact>
autobugfix operator request ... --triage-id <id>
autobugfix operator review <id> --reviewer <independent-id> ...
autobugfix operator approval-payload <id> --stage scope|merge ...
autobugfix operator approve-signed <id> --payload <json> --signature <sig>
autobugfix operator approve-github <id> --repository <owner/repo> --pull-request <n> --review-id <n>
autobugfix operator preflight --request-id <id>
autobugfix operator workspace-create --request-id <id>
autobugfix operator postflight --request-id <id>
autobugfix operator validate --request-id <id> --metric key=value
autobugfix operator finalize --request-id <id>
autobugfix operator export-bundle --request-id <id>
```

Never use local `--bootstrap-policy` as merge authority. It exists only for the
first Governance v2 installation and local feedback. Do not use Eval to approve
PPE, archive Execution tasks, or approve Memory proposals.

## Diagnosis Routing

- Worktree/repo config failure -> Execution/shared runtime.
- Missing task evidence -> context/evidence schema.
- Writer lacks a stable repair strategy after harness correctness is proven ->
  writer skill or reviewed Memory proposal.
- Tests pass but semantics fail -> verifier/evaluator/scorer coverage.
- Eval setup differs from the real case -> Eval adapter/harness.
- Repeated accepted failure pattern -> Memory proposal/skill review.
- Operator misclassifies artifacts or scope -> Operator protocol/governance.
