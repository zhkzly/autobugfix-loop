# Freeze Experiment 2 Codex Runtime

## Project Contract

Autobugfix is a local, repo-agnostic, Git-controlled loop-engineering and
harness-engineering system. It is not a one-shot coding prompt and is not the
target repository.

- Execution owns the real repair state machine. Given a configured repository
  and visible issue evidence, it creates an isolated worktree, runs bounded
  Writer attempts, executes only predeclared visible verifier commands, runs a
  read-only evaluator, and freezes the final candidate and trace.
- Memory compiles accepted Execution evidence into reviewed wiki/skills. It
  does not participate in this runtime repair and must not consume benchmark
  oracle output.
- Eval owns benchmark materialization, qualification, case manifests, frozen
  submissions, independent official scoring, and experiment reports. Official
  scores, hidden tests, gold patches, and scorer diagnosis never become
  Execution feedback for the same case.
- Operator may diagnose public Optimization evidence and improve Autobugfix on
  a non-main experiment line. It does not own Execution or Eval state and may
  not inspect sealed Holdout case-level evidence.

LLMs are bounded execution nodes. Git identities, typed protocols, services,
static validation, official scorers, immutable artifacts, and Guard/human
transitions own experimental truth.

## Problem

Experiment 2 cannot currently produce a valid H0 result even though Docker
qualification and a direct SDK probe succeed:

- the committed root environment pins `openai-codex==0.1.0b3` through
  `uv.lock`, while the locally proven SDK/CLI pair is `0.144.4`;
- the SWE protocol freezes model and attempt/time budgets but not the SDK,
  bundled CLI, service tier, or reasoning effort;
- the exact-subject broker inherits `reasoning_effort: medium` from the frozen
  H0 config instead of deriving it from the experiment protocol;
- two real H0 runs disconnected before the first assistant/tool event at about
  220 seconds, while the same case completed a real read-only tool-using SDK
  turn with `gpt-5.4-mini`, SDK/CLI `0.144.4`, and low reasoning in about 50
  seconds;
- the current `SWERuntime.runtime_id` mixes official evaluator/materializer
  identity with the subject Codex runtime, so an SDK change invalidates gold
  qualification even though qualification makes no model call;
- the Raw Codex comparator still pins SDK `0.1.0b3` and medium reasoning, so it
  cannot be a paired runtime comparator for H0 and H_general.

The previous disconnected runs are infrastructure-invalid diagnostics. They
are neither failed repairs nor Experiment 2 scores.

## Goal

Create an executable, fail-closed Experiment 2 runtime contract that pins and
verifies the real Codex treatment independently from the official SWE
evaluator runtime. Use the same model, SDK/CLI pair, reasoning effort, service
tier, timeout, and serial scheduling for H0, H_general, and the Raw public
comparator where those properties are intended to be controlled variables.

The initial frozen subject treatment is:

```yaml
model: gpt-5.4-mini
reasoning_effort: low
service_tier: null
sdk_package: openai-codex
sdk_version: 0.144.4
cli_package: openai-codex-cli-bin
cli_version: 0.144.4
max_attempts: 2
timeout_seconds: 900
case_concurrency: 1
```

Raw Codex remains a distinct one-thread/one-turn comparator and does not gain
Autobugfix retries, Memory, skills, or verifier feedback.

## Requirements

### R1. Typed treatment runtime

- Upgrade the SWE experiment protocol schema and represent the complete
  subject treatment runtime as a typed object.
- The full protocol digest must bind H0, selected Optimization cohort, Holdout
  contract, model/runtime, attempt/time budgets, and upstream revisions.
- Unsupported fields, versions, reasoning levels, or runtime drift must fail
  before a model process starts.

### R2. Separate authority identities

- Define an evaluator/qualification contract digest containing only the
  dataset, official harness, image/materialization, platform, and repeated gold
  qualification contract.
- Define an evaluator runtime identity from the official scorer/materializer
  code and environment. It must exclude the Codex SDK and subject Writer code.
- Define a subject treatment runtime identity from the expected and observed
  SDK/CLI distributions, Python runtime, root lockfile, trusted subject broker,
  Codex bridge, and subject worker inputs.
- Qualification receipts bind the evaluator contract/runtime, not model or
  reasoning settings. Prepared/sealed manifests bind both evaluator and subject
  identities plus the full protocol.
- Old receipt schemas remain readable as historical artifacts but cannot be
  silently admitted to the new formal protocol.

### R3. Exact-subject execution

- The broker must derive `reasoning_effort`, service tier, model, and timeout
  from the typed protocol and write them into the isolated control config.
- The broker must not trust the frozen subject's old runtime defaults for any
  experimental treatment variable.
- Subject request, binding, result, submission, and case report artifacts must
  retain expected and observed runtime digests.
- Writer remains workspace-write only in the target task worktree; evaluator
  remains read-only; Codex hooks remain disabled for SDK roles.

### R4. Raw comparator parity

- Pin the standalone Raw project and its lockfile to SDK/CLI `0.144.4`.
- Set its Experiment 2 reasoning effort to low and bind it to the upgraded SWE
  protocol.
- Verify model, SDK, CLI, reasoning, service tier, timeout, sandbox, approval,
  and network settings before the Raw model call.
- Preserve the one-thread/one-turn/no-feedback Raw treatment.

### R5. Generation/scoring noninterference

- Execution may retry only from the predeclared visible verifier and its normal
  read-only evaluator within the two-attempt budget.
- The final patch and Execution evidence are frozen before the official scorer
  runs.
- Official result data is written only to Eval artifacts and is never passed to
  Writer, Execution feedback, Memory, or a same-case retry.
- Transport/sandbox/materialization failures are harness-invalid runs; a valid
  unresolved official result is a measured failure and is not rerun for repair.

### R6. Real observability

- Every runtime check and case run retains request, events, stdout, stderr,
  generated config, version observations, patch, task evidence, verifier logs,
  official scorer output, and noninterference receipt.
- Reports distinguish `resolved`, valid unresolved repair, and harness-invalid
  execution.

### R7. No scope drift

- Do not redefine the four loop purposes, change Memory approval, weaken target
  main-checkout protection, enable fake production backends, expose Holdout
  identity, or feed oracle information into Execution.
- Do not globally lower the default reasoning effort for ordinary Autobugfix
  tasks. Low reasoning is an Experiment 2 treatment value injected by Eval.

## Acceptance Criteria

- [ ] Root and standalone Raw uv environments resolve exactly
      `openai-codex==0.144.4` and `openai-codex-cli-bin==0.144.4`.
- [ ] A protocol/runtime unit test proves SDK, CLI, reasoning, service tier,
      model, timeout, or lock drift is rejected before model invocation.
- [ ] Qualification identity tests prove a treatment-only change does not alter
      evaluator qualification identity, while scorer/materializer drift does.
- [ ] Exact-subject tests prove the generated config contains
      `model_reasoning_effort = low` through Autobugfix config resolution and
      that subject bindings retain both runtime identities.
- [ ] Raw comparator tests prove runtime parity and preserve exactly one SDK
      turn with no managed verifier feedback.
- [ ] Submission tests prove the official scorer sees only a previously frozen
      patch and cannot mutate it or trigger another Writer attempt.
- [ ] `uv run --cache-dir /tmp/uv-cache pytest -q` passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`
      passes.
- [ ] `git diff --check` passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`
      passes.
- [ ] The standalone Raw project tests pass in its own uv project.
- [ ] SWE doctor passes with the pinned official SWE-bench runtime and Docker.
- [ ] `astropy__astropy-12907` is requalified under the new qualification
      receipt schema with two successful official gold runs.
- [ ] One real H0 development run completes the full Execution loop, freezes
      its patch/evidence, then receives exactly one independent official score.
- [ ] The real report records runtime identities and no official result is
      present in Writer/Execution feedback artifacts.
- [ ] A valid unresolved H0 result, if produced, is retained as the result and
      is not retried using official feedback.

## Non-Goals

- Running all 10 Optimization and six sealed Holdout cases in this task.
- Producing H_general before the calibrated H0 and Raw runtime paths are valid.
- Changing the general production default from medium reasoning to low.
- Using the official scorer as Execution verifier feedback.
- Repairing a valid unresolved case by looking at its gold patch or score.

## Risks

- SDK `0.144.4` may contain API changes requiring real adapter updates rather
  than version-only edits.
- New receipt schemas intentionally invalidate the single legacy qualification
  artifact for formal use; it must be regenerated, not rewritten.
- Low reasoning changes the experimental treatment. H0, Raw, and H_general
  must all use it for a valid comparison.
- A broad runtime identity split touches sealed manifests and Guard records;
  missing one binding could admit incomparable runs.
- A successful one-case calibration proves harness validity, not repair-system
  quality or Experiment 2 generalization.

## Rollback

Revert the candidate commit and restore both uv lockfiles and protocol files.
Never mutate or relabel old run artifacts. Any runtime produced under the old
protocol remains historical and incomparable with the new treatment.
