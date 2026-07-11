# Operator Governance V3 Sequential Review

No reviewer subagents were used because this Codex session runs in inline
mode. The main agent performed the six required reviewer passes sequentially
and fixed findings before recording a pass.

## Execution Reviewer

- Purpose/state owner: `AutobugfixService` owns target task state; Writer may
  modify only the configured task worktree.
- Real evidence: pinned ItsDangerous commit
  `672971d66a2ef9f85151e53283113f33d642dabd` with a committed fault injection.
- Confirmed: `gpt-5.4-mini` changed only
  `src/itsdangerous/encoding.py`; nine real pytest cases passed; read-only
  Evaluator stopped at the human gate; target main SHA/digest/status remained
  unchanged; raw logs/events/diff/test/PPE artifacts were non-empty.
- Decision: pass.

## Memory Reviewer

- Purpose/state owner: `MemoryService` compiles accepted Execution evidence
  into reviewed wiki/skill proposals without owning Execution state.
- Finding fixed: `memory collect` previously allowed an unaccepted task.
  Collection now rejects every task except `accepted` or an archive whose
  result is `accepted`.
- Real evidence: the ItsDangerous archive produced raw packet, digest,
  maintainer run, proposal patch, and a `pending` proposal. The archived task
  remained unchanged and the proposal did not self-approve.
- Decision: pass.

## Eval Reviewer

- Purpose/state owner: `EvalRunner` owns isolated case artifacts and scoring,
  not Execution gates or Memory approvals.
- Confirmed: a second isolated ItsDangerous Execution used real Codex
  Writer/Evaluator roles and the real pytest command. Generated and committed
  oracle diffs were non-empty and byte-for-byte equal; report decision was
  `pass`; summary had no failures; the Eval task remained at the human gate and
  was not archived.
- Evidence: `/tmp/autobugfix-real-repository-e2e/eval-runs/itsdangerous-real-e2e/`.
- Decision: pass.

## Codex Runtime Reviewer

- Production remains `CodexSDKBackend` using preview `openai-codex`; no
  `codex exec` or production fake fallback exists.
- Findings fixed: unsupported SDK isolation parameters now fail closed instead
  of falling back to global runtime; missing control config and disabled role
  runtime also fail closed.
- Hook ownership is explicit: project `PreToolUse`/`UserPromptSubmit` hooks are
  only for `operator_host` (human/current main agent). Execution, Memory, Eval,
  and bounded Operator SDK roles use isolated `CODEX_HOME` with
  `hooks = false` and `multi_agent = false`.
- Machine constitution, role context, skills, config validation, trusted static
  checks, and tests all enforce this mapping.
- Decision: pass.

## Portability And Privacy Reviewer

- Production default model remains runtime-selected (`null`); acceptance
  explicitly and overrideably uses `gpt-5.4-mini`.
- No private repository, company name, local username, internal command, or
  fixed user path was added. The public ItsDangerous URL/SHA is acceptance data;
  the actual target profile is still written through `.autobugfix/config.yaml`.
- Runtime SQLite, raw artifacts, eval/experiment output, and Memory raw state
  remain gitignored. Linux Bubblewrap requirement is documented and fails
  closed when required.
- Decision: pass.

## Acceptance Reviewer

- Passed: `uv run --cache-dir /tmp/uv-cache pytest -q` (`50 passed`).
- Passed: `uv run --cache-dir /tmp/uv-cache python -m compileall -q src tests scripts`.
- Passed: `git diff --check`.
- Passed: `uv run --cache-dir /tmp/uv-cache python scripts/validate_role_skills.py`.
- Passed: Operator governance skill quick validation.
- Passed: `uv run --cache-dir /tmp/uv-cache python scripts/real_repository_acceptance.py --model gpt-5.4-mini`.
- Passed: `uv run --cache-dir /tmp/uv-cache python scripts/real_operator_acceptance.py --model gpt-5.4-mini`.
- The toy script is optional developer smoke only and is not a promotion gate.
- Decision: pass.

## Remaining Bootstrap Risk

V3 is not on `origin/main` until this branch is reviewed and merged. Remote
CODEOWNERS/reviewer allowlists/required check/branch protection are implemented
but are not active until the repository-specific installer is run by a human.
This branch must not claim that local Hook policy substitutes for trusted-base
GitHub admission.
