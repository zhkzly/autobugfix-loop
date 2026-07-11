# Harden Operator and Eval benchmark foundations

## Goal

Make Operator self-improvement safe to measure before introducing a
SWE-bench-derived dataset. Operator scope must be deterministic and explicit,
performance evidence must come from trusted experiment observations, and Eval
must score behavior through real tests rather than oracle-patch equality.

Autobugfix remains a local, repo-agnostic loop/harness control system:
Execution repairs a configured target repository in an isolated worktree,
Memory compiles accepted evidence into reviewed knowledge, Eval measures the
real Execution loop, and Operator improves Autobugfix on governed non-main
candidates. This task changes Operator and Eval contracts only; it does not
change Execution or Memory state ownership.

## Requirements

### R1: Deterministic layer ownership

- The machine constitution must resolve every governed source path to exactly
  one effective layer.
- Broad fallback patterns such as `docs_skills` may overlap more specific
  patterns, but the resolution strategy must be declarative: the most-specific
  matching rule wins and equal-specificity cross-layer matches fail closed.
- Policy and tests must no longer depend on the current hard-coded
  `docs_skills` removal in `layers_for_file()`.
- Tracked governed paths must have coverage and ambiguity tests.

### R2: Explicit planned scope

- Every Operator request must declare at least one `planned_paths` pattern.
- CLI request creation must require `--planned-path`.
- A scope expansion that adds a layer must also add a path pattern for that
  layer; effective scope remains append-only and versioned.
- The complete Git diff remains authoritative. A changed path outside both the
  declared layer and planned path set must fail verification and generate
  feedback.

### R3: Trusted performance evidence

- Behavior-affecting layers (`execution`, `memory`, `eval`, `operator`, and
  `shared_runtime`) must have an immutable baseline before full verification.
  Pure documentation changes may omit a performance baseline.
- Caller-supplied `--metric key=value` values must not authorize verification,
  validation, or promotion.
- Baseline and candidate metrics must be derived from host-observed experiment
  command results and bound to profile, inputs, Git SHA, and patch digest.
- A baseline must be committed in the trusted request base. Its measured SHA
  may precede that base only through baseline-metadata commits; intervening
  behavior changes make it stale.
- Candidate verification must use a completed experiment for the current patch
  and the same profile/input contract as the referenced baseline.
- Trusted-base PR admission must rerun the committed baseline's embedded
  experiment contract and preserve its raw logs.
- The generic metric receipt may initially report host-observable command pass
  rate, log completeness, and runtime. A later benchmark adapter may extend the
  receipt with independently scored case metrics.

### R4: Truthful experiment profiles

- The default configuration must not call a generic JSONL Eval run
  `swebench-verified-smoke`.
- Rename that profile to a benchmark-neutral local dataset profile until an
  official SWE-bench adapter and scorer exist.
- Eval CLI failure must produce a non-zero process result so an Operator
  experiment cannot report success when cases failed.

### R5: Versioned Case schema and adapter boundary

- Define a versioned canonical Eval case model that can identify the source
  dataset/revision/split/instance, repository/base revision, task text,
  typed attachments, container environment, execution verifier command,
  oracle type/visibility, and task type.
- Keep a compatibility decoder for current local historical JSONL rows.
- Introduce an adapter registry. This task implements a real `local-git`
  adapter; the official SWE-bench adapter is deferred.
- Adapter materialization must still invoke the real Execution service and
  create a real task worktree. Eval must not create a second task state
  machine or approve/archive the Execution task.

### R6: Tests-first Eval scoring

- A generated patch is primarily judged by an independently executed oracle
  verifier in the generated task worktree.
- Oracle diff equality is diagnostic metadata only and must not be a pass
  condition.
- Record separate signals for generated patch presence, Execution verifier
  outcome, Execution terminal-at-human-gate outcome, oracle outcome, and
  oracle-diff equality.
- Harness/setup/oracle errors must be distinguishable from a model repair
  failure and must fail closed.
- Preserve raw oracle stdout/stderr, Execution logs/events/artifacts,
  generated diff, optional oracle diff, normalized observation, report,
  summary, and diagnosis.

### R7: Backward compatibility and portability

- Existing local dataset building and Eval CLI workflows remain usable through
  the `local-git` compatibility decoder.
- No target repository, local username/path, company command, or benchmark
  instance is hard-coded.
- Production roles continue to use the Codex Python SDK; tests may use the
  existing fake backend only for deterministic unit coverage.
- Runtime state remains gitignored.

## Acceptance Criteria

- [ ] Every tracked governed path resolves to exactly one effective layer;
      intentionally ambiguous top-priority rules are rejected by tests.
- [ ] Request construction and CLI parsing reject an empty planned path list.
- [ ] Scope expansion cannot add a new layer without a corresponding path.
- [ ] Full verification of a behavior-affecting request rejects missing,
      stale, caller-forged, wrong-profile, or wrong-patch metrics.
- [ ] Baseline capture and candidate comparison use host-derived experiment
      receipts rather than CLI numeric input.
- [ ] Default config and active docs no longer describe the generic JSONL
      experiment as an official SWE-bench run.
- [ ] A valid alternative patch that differs from the oracle diff passes when
      real oracle tests and the Execution loop pass.
- [ ] A byte-identical patch fails when oracle tests fail.
- [ ] Harness errors are reported separately and cause a non-zero Eval CLI
      exit code.
- [ ] Legacy local JSONL cases still execute through the real Execution loop.
- [ ] `uv run --cache-dir /tmp/uv-cache pytest -q` passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`
      passes.
- [ ] `git diff --check` passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`
      passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python scripts/validate_operator_policy.py`
      passes.
- [ ] `uv run --cache-dir /tmp/uv-cache python scripts/real_repository_acceptance.py --model gpt-5.4-mini`
      passes with real Git, verifier commands, SDK Writer/Evaluator, and
      retained artifacts.
- [ ] `uv run --cache-dir /tmp/uv-cache python scripts/real_operator_acceptance.py --model gpt-5.4-mini`
      passes or any environment-only blocker is reported with raw evidence.

## Out Of Scope

- Downloading or filtering SWE-bench cases.
- Claiming an official SWE-bench score.
- Selecting target/regression/holdout instance IDs.
- Capturing the first benchmark baseline; that follows after the bug-only
  dataset manifest is frozen.
- Changing Memory approval semantics, Execution human gates, or target-repo
  main-checkout protection.
