# Implementation plan: Operator and Eval benchmark foundations

## Phase 1: Operator scope contracts

- [x] Add declarative layer-resolution policy and a specificity-aware resolver.
- [x] Fail closed on equal-specificity cross-layer ambiguity.
- [x] Add complete tracked-path uniqueness tests.
- [x] Require planned paths in model, service, CLI, and scope expansion.
- [x] Update Operator role skills and active governance spec.
- [x] Run `uv run --cache-dir /tmp/uv-cache pytest -q tests/test_operator_policy.py tests/test_cli.py`.

## Phase 2: Trusted experiment metrics

- [x] Add host-derived experiment metric receipts bound to profile, inputs,
      base/head SHA, and patch digest.
- [x] Record command runtime with a monotonic host clock.
- [x] Add trusted-ref baseline capture through Operator service/CLI.
- [x] Require protected baseline publication in the trusted Git base and reject
      any intervening non-baseline behavior commit.
- [x] Rerun the embedded baseline contract in trusted-base PR admission and
      retain remote Guard artifacts.
- [x] Remove caller-provided metrics from verify/validate/baseline authority.
- [x] Require matching baseline/experiment receipts for behavior layers.
- [x] Test missing, stale, wrong-profile, wrong-input, and forged metric cases.
- [x] Rename the misleading generic SWE profile to `local-dataset-e2e`.
- [x] Run `uv run --cache-dir /tmp/uv-cache pytest -q tests/test_operator_policy.py tests/test_cli.py`.

## Phase 3: Eval case and adapter contracts

- [x] Replace the flat Eval case with versioned typed source/task/repository/
      execution/oracle models and a legacy decoder.
- [x] Add an adapter protocol/registry and implement `LocalGitAdapter`.
- [x] Preserve attachments and stable source metadata in resolved case
      artifacts.
- [x] Preserve container/platform/setup and hidden-oracle metadata for future
      official adapters without pretending `local-git` can execute it.
- [x] Keep target repo configuration generated through
      `.autobugfix/config.yaml` and real Execution service calls.
- [x] Add schema, invalid adapter, missing oracle, and legacy compatibility
      tests.

## Phase 4: Tests-first scoring and diagnosis

- [x] Run the independent command oracle in the generated task worktree.
- [x] Persist raw oracle logs and normalized observations.
- [x] Refactor scorer to consume observations and make diff equality
      diagnostic only.
- [x] Distinguish repair failure, Execution verifier/evaluator failure, and
      harness error in reports and diagnosis.
- [x] Make `autobugfix eval run` return non-zero for failed/error runs.
- [x] Add an alternative-correct-patch test and an identical-patch/
      failing-oracle test.
- [x] Run `uv run --cache-dir /tmp/uv-cache pytest -q tests/test_eval.py tests/test_dataset.py tests/test_cli.py`.

## Phase 5: Documentation and full validation

- [x] Update README/config examples, Operator skills, and Trellis specs to
      match the implemented contracts without claiming SWE-bench support.
- [x] Run `uv run --cache-dir /tmp/uv-cache pytest -q`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`.
- [x] Run `git diff --check`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/validate_operator_policy.py`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/real_repository_acceptance.py --model gpt-5.4-mini`.
- [x] Run `uv run --cache-dir /tmp/uv-cache python scripts/real_operator_acceptance.py --model gpt-5.4-mini`.
- [x] Perform the required sequential Execution, Memory, Eval, Codex runtime,
      portability/privacy, and acceptance reviewer passes. No reviewer
      subagents are used in inline Codex mode.

## Follow-up Task

After this task passes, create a separate SWE-bench task to curate a reviewed
bugfix manifest, implement the official Docker adapter/scorer, freeze
target/regression/holdout splits, capture the first baseline, and run governed
Operator optimization experiments.
