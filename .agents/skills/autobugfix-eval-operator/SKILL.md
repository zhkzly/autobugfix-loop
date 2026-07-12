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

Defects4J uses two images built from one pinned Dockerfile: the materializer
contains checkout/oracle metadata, while the verifier image removes gold
patches and localization hints. The Writer edits a local task worktree;
Docker only materializes the official buggy revision and runs official tests.
Never add or request host Java, Perl, SVN, cpanm, Defects4J, `PERL5LIB`, or
library-path configuration. Before opening a governed wave, qualify each
suite through the service-owned commands:

```text
autobugfix eval benchmark seal --manifest <seed>
autobugfix eval benchmark run-case --manifest <seed> --case <visible-id>
  --out .autobugfix/eval-runs --run-id <id>
  --model gpt-5.4-mini --max-attempts 2
```

`seal` and `guard-run` are human Guard actions. They must execute from a clean
control checkout at `eval.benchmarks.guard.trusted_ref`; the encrypted bundle,
public manifest, and aggregate metric bind that Git tree, machine constitution,
and harness digest. Never run either action from an Operator candidate.

For a governed Study, use this aggregate flow:

```text
autobugfix operator study guard-binding --study-id <study> --kind BASELINE|CANDIDATE
autobugfix eval benchmark guard-run ... --study-binding <binding.yaml>
autobugfix operator study import-guard-metric --study-id <study>
  --kind BASELINE|CANDIDATE --metric <signed-metric.yaml>
```

The first command derives current Study/line/budget facts; it grants nothing.
The second signs only aggregate metrics with the human-held Guard secret. The
third re-verifies the signature, frozen harness/policy, Study binding, and
numeric success contract before the Operator service records metric authority.
Do not type aggregate values into a substitute receipt.

The current direct Guard runner measures only the clean trusted checkout and
therefore produces H0/Baseline authority. It must reject a binding whose
`subject_sha` differs from that checkout. Do not claim a CANDIDATE metric until
the experiment task provides a trusted isolated subject broker that reports
the exact experiment-line SHA; changing a YAML binding is not execution.

Do not interpret every fixed-revision failure as a benchmark failure.
Defects4J qualification deterministically requires a stable fixed failure
baseline and a buggy delta equal to official triggering tests. Generated
patches must eliminate all failures outside that baseline. Never move this
decision into Writer/Evaluator prose or expose Holdout identity maps to the
Operator.

Every Execution verifier attempt must retain worktree-external Docker raw
logs. A missing verifier artifact path, SDK timeout event, Docker client hang,
or unstable fixed baseline is a harness diagnosis, not another Writer retry.

Classify import failures, Docker failures, generated build-artifact pollution,
and non-idempotent verifier results as Eval harness defects. They are not model
repair failures and must not be fed to another Writer attempt until fixed.

## Required Workflow

1. Read real artifacts and diagnose the owning layer. Do not jump directly to
   prompt or skill changes. SWE-bench inputs do not replace screenshots, logs,
   browser/API evidence, or human feedback in general on-call tasks.
2. Create immutable triage with at least one existing evidence path, digest, or
   URI.
3. Capture a trusted baseline from the configured experiment profile before
   changing a behavior layer. Never type metric values into an authority path.
4. Create request before patching. Request freezes base SHA, branch, layers,
   non-empty planned path patterns, validation profiles, baseline, expiry, and
   request digest.
5. Obtain only the required authority:
   - one low-risk layer: automatic preflight;
   - cross-layer/medium: independent reviewer, never the request creator;
   - constitutional: OpenSSH-signed human scope approval or allowlisted GitHub
     review evidence.
6. Request `operator start`; only the trusted service may create and register
   the real non-main candidate worktree.
7. Request `operator writer-start`. The Operator controls the run but does not
   patch candidate files itself. The Writer receives only its service-owned
   read view, effective scope, and feedback.
8. Request `operator verify --mode fast`. If checks fail, consume the resulting
   FeedbackPacket and request `writer-retry`; never turn a harness/policy error
   into another repair attempt.
9. If evidence proves another layer/path is needed, request `scope-change`,
   obtain authority for that exact scope version, and activate it before the
   next Writer run. Never lower risk to fit an existing patch.
10. Request `candidate-commit`, run the same experiment profile, then request
    `verify --mode full`. Guard derives patch-bound evidence from observed
    commands and logs; caller-supplied numeric metrics have no authority.
11. Promote only the current VERIFIED patch through `promotion-prepare`,
    `promotion-open-pr`, trusted-base CI, `promotion-observe-merge`, and canary.
    Constitutional scope and merge approval remain separate human authorities.

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
autobugfix operator start --request-id <id>
autobugfix operator writer-start --request-id <id>
autobugfix operator verify --request-id <id> --mode fast
autobugfix operator writer-retry --request-id <id>
autobugfix operator scope-change --request-id <id> ...
autobugfix operator scope-activate --request-id <id> --revision-id <revision>
autobugfix operator candidate-commit --request-id <id> --message <message>
autobugfix operator experiment-run --request-id <id> --profile <profile> [--value key=value]
autobugfix operator verify --request-id <id> --mode full
autobugfix operator integrate --request-id <id> --grant-id <grant>
autobugfix operator checkpoint create --line-id <line> --name H_bug|H_general --metric-receipt-id <metric-id>
autobugfix operator line rollback --line-id <line> --checkpoint-id <checkpoint> --reason <reason>
autobugfix operator promotion-prepare --request-id <id>
autobugfix operator promotion-open-pr --promotion-id <id> ...
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
