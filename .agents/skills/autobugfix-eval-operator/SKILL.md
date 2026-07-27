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

The Operator is a bounded execution node. Use Governance v4 before modifying
code, tests, config, skills, validation, or baselines.

Two studies are deliberately independent. Experiment 1 starts at frozen `H0`,
uses Defects4J, exposes 10 Optimization cases, seals 6 unseen-repository
Holdout cases externally, and may produce `H_bug`. Experiment 2 separately
starts at the same named `H0` cohort, exposes 10 SWE-bench Verified
Optimization cases, seals 6 unseen-repository SWE-bench-Live Holdout cases
externally, and may produce `H_general`. Never initialize Experiment 2 from
`H_bug` or transfer code, skills, Memory, artifacts, results, or case-level
feedback. Both use only `gpt-5.4-mini`, human-granted `3 -> 8 -> 16` waves,
concurrency one, and no fallback. Holdout manifests/gold/results remain outside
Operator roots; only aggregate final metrics may be registered.

## Required Workflow

1. Read real artifacts and diagnose the owning layer. Do not jump directly to
   prompt or skill changes. SWE-bench inputs do not replace screenshots, logs,
   browser/API evidence, or human feedback in general on-call tasks.
2. Create immutable triage with at least one existing evidence path, digest, or
   URI.
3. Capture a trusted baseline from the configured experiment profile before
   changing a behavior layer. Never type metric values into an authority path.
   `baseline_mode: strict` is the default and requires all commands to pass.
   A benchmark H0 whose target tests intentionally fail must declare
   `baseline_mode: measure`; that records real failures as a metric but still
   rejects timeout, sandbox/broker/harness failure, or missing raw logs.
4. Create request before patching. Request freezes base SHA, branch, layers,
   non-empty planned path patterns, validation profiles, baseline, expiry, and
   request digest.
5. Obtain only the required authority:
   - one low-risk layer: automatic preflight;
   - cross-layer/medium: independent reviewer, never the request creator;
   - constitutional: OpenSSH-signed human scope approval or allowlisted GitHub
     review evidence.
6. Run `operator start` (or `workspace-create`) to let the service create and
   baseline the real candidate Git worktree. Its path is for observation only:
   neither Operator nor Writer may patch or run Git commits in it. Request
   `writer-start`; the Writer receives a separate service-owned staging
   directory and the trusted service applies only the validated staging diff.
7. Run postflight. If actual paths elevate risk, obtain the new approval; never
   lower requested risk to fit the patch.
8. Commit the candidate and run the same experiment profile. Guard derives a
   patch-bound metric receipt from observed commands and logs. Fast and full
   validation run in disposable detached worktrees, so test-side effects do
   not alter the candidate snapshot or its Writer application chain.
9. Run trusted validation profiles and compare the experiment receipt with the
   baseline. Do not inject shell commands or numeric metrics into a request.
10. Constitutional work requires merge approval bound to patch digest or PR
   HEAD, then `finalize`. Use `approval-payload --stage merge --merge-binding
   patch` for an approval embedded in the candidate bundle; reserve HEAD
   binding for an external final-PR review.
11. Export the authorization bundle and let the base-version GitHub check rerun
   policy and validation before merge.

For a service-owned study profile, the trusted benchmark harness derives H0
with `record_observed_baseline_metric` and derives the candidate metric after
integration with `record_observed_candidate_metric`. These APIs take no caller
metric values: they bind the record to the service-captured baseline or shadow
experiment receipt, raw-log digests, the integration, and the budget grant.
Use an `observed_guard` success contract with `require_all_commands_pass`,
numeric metric rules, and a protected ref pinned to H0. An official external
adapter remains responsible for producing its own independently trusted
receipt; never replace it with a local typed score.

For `local-git` cases, freeze the reference commit before starting Execution.
The adapter must populate the isolated Execution remote from `base_commit`
only; reference/oracle refs and commit objects must not be reachable there.
Compute or expose the oracle diff only on the Eval host after Execution has
frozen the generated patch. This protects the normal Git-facing agent surface,
but it is not a substitute for an official container harness when claiming a
sealed benchmark result.

## Commands

```text
autobugfix operator study create ... --cohort-id <cohort> --target-checkpoint H_bug|H_general
autobugfix operator line init --study-id <study> --metric-receipt-id <metric-id>
autobugfix operator budget request --study-id <study> --wave 3|8|16 ...
autobugfix operator budget approve --budget-request-id <id> --confirm-request-digest <digest> --approver <human>
autobugfix operator triage ... --evidence <artifact>
autobugfix operator baseline record --name <baseline> --profile <profile> [--value key=value]
autobugfix operator request ... --triage-id <id> --planned-path <glob> --performance-baseline <baseline>
autobugfix operator review <id> --reviewer <independent-id> ...
autobugfix operator approval-payload <id> --stage scope|merge ...
autobugfix operator approve-signed <id> --payload <json> --signature <sig>
autobugfix operator approve-github <id> --repository <owner/repo> --pull-request <n> --review-id <n>
autobugfix operator preflight --request-id <id>
autobugfix operator workspace-create --request-id <id>
autobugfix operator writer-start --request-id <id>
autobugfix operator verify --request-id <id> --mode fast
autobugfix operator candidate-commit --request-id <id> --message <message>
autobugfix operator postflight --request-id <id>
autobugfix operator experiment-run --request-id <id> --profile <profile> [--value key=value]
autobugfix operator validate --request-id <id>
autobugfix operator integrate --request-id <id> --grant-id <grant>
autobugfix operator checkpoint create --line-id <line> --name H_bug|H_general --metric-receipt-id <metric-id>
autobugfix operator line rollback --line-id <line> --checkpoint-id <checkpoint> --reason <reason>
autobugfix operator finalize --request-id <id>
autobugfix operator export-bundle --request-id <id>
```

`budget approve` is a human terminal action. The CLI requires an interactive
TTY and the exact phrase `APPROVE <request-digest>`; an agent must stop and
present the pending request rather than supplying that phrase itself.

Never use local `--bootstrap-policy` as merge authority. It exists only for the
first Governance v4 installation and local feedback. Do not use Eval to approve
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
