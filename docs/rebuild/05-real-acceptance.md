# Real Acceptance

## Purpose

The release acceptance must demonstrate that Autobugfix operates as a real
loop-engineering and harness-engineering control system. A toy repository is
useful for fast development, but it is not sufficient evidence for promotion.

The canonical acceptance uses a real public repository at a pinned Git commit,
adds a reproducible regression to a local fixture branch, and then exercises:

```text
real GitHub source -> injected failing regression -> configured target repo
-> isolated Execution worktree -> real Codex Writer -> real pytest verifier
-> read-only Codex Evaluator -> human gate/archive
-> accepted evidence -> Memory digest/proposal (pending only)
-> committed diagnostic oracle -> isolated Eval Execution -> independent test scoring
```

The target main checkout must never be modified. Every model run must retain
raw logs, events, run summaries, diffs, and verifier artifacts.

## Canonical Fixture

- Upstream: `https://github.com/pallets/itsdangerous.git`
- Pinned commit: `672971d66a2ef9f85151e53283113f33d642dabd`
- Fault: `base64_encode` incorrectly retains URL padding.
- Regression: `base64_encode(b"a")` must return `b"YQ"`.
- Real verifier: the pinned repository's encoding tests executed with pytest.
- Acceptance model default: `gpt-5.4-mini` (overridable with `--model`).

The upstream URL and commit identify acceptance data, not a production target.
The script writes the actual target repository exclusively through the
temporary control root's `.autobugfix/config.yaml`.

## Prerequisites

- Python 3.11 or newer and `uv`.
- Git and network access to clone the public upstream repository.
- A compatible preview `openai-codex` Python SDK in the project environment.
- Working local Codex authentication.
- The project's `uv` environment, including pytest.

Authentication or upstream-network failures are environment failures. They are
not permission to switch production Writer/Evaluator roles to fakes.

## Run

From the Autobugfix repository root:

```bash
uv sync --prerelease=allow
uv run python scripts/real_repository_acceptance.py --model gpt-5.4-mini
```

The script uses `/tmp/autobugfix-real-repository-e2e` by default. Override it
with `--root` when needed. It deletes only that configured temporary root before
starting so a previous run cannot provide false-positive state.

## Required Execution Assertions

The first Execution run must prove all of the following:

- the fault-injected baseline test fails before Autobugfix runs;
- the configured target repo and test command come from
  `.autobugfix/config.yaml`;
- Git creates a real task worktree from `origin/main`;
- the production Writer uses the Codex Python SDK and edits only
  `src/itsdangerous/encoding.py` in the task worktree;
- the real pytest verifier passes;
- the read-only Evaluator reaches `waiting_human_ppe_approval`;
- the target main checkout retains its original SHA, file digest, and clean
  status;
- `events.jsonl`, Writer/Evaluator raw logs, run summaries, `diff.patch`,
  `test-result.md`, and `ppe-brief.md` are present and non-empty;
- acceptance/archive happens only after the harness checks those facts.

## Required Memory Assertions

After the accepted Execution task is archived:

- `memory collect` accepts the archived result because it is `accepted`;
- raw packet, digest, maintainer logs, proposal patch, and evidence exist;
- `memory lint` passes;
- the proposal remains `pending`;
- Memory does not mutate the archived Execution task or self-approve active
  memory.

Collecting a ready, active, failed, abandoned, or otherwise unaccepted task
must fail.

## Required Eval Assertions

The accepted worktree fix is committed only to create the oracle pair. Eval
then creates a second isolated remote, main checkout, control root, task, and
worktree and invokes the real Execution loop again.

The Eval result must prove:

- the model mode is `codex`, with real Writer and Evaluator roles;
- generated diff and diagnostic oracle diff are both non-empty;
- the configured pytest command passes independently in the generated
  worktree;
- oracle diff equality is recorded only as diagnostic metadata and does not
  reject a behaviorally correct alternative patch;
- the report decision is `pass` and the run summary has no failures;
- the Eval-owned Execution task stops at the human gate;
- Eval does not accept, archive, deploy PPE, or approve Memory;
- the second Execution run also retains complete raw evidence.

## Operator Acceptance

Operator governance is validated independently because its state owner and
promotion lifecycle differ from Execution:

```bash
uv run python scripts/real_operator_acceptance.py --model gpt-5.4-mini
```

This must use a real Supervisor, process-isolated Writer, deterministic checks,
read-only semantic verifier, audit, and a `PREPARED` promotion. It must not
merge or activate main.

## Final Verification

```bash
uv run pytest -q
uv run python -m compileall -q src tests scripts
git diff --check
uv run python scripts/validate_role_skills.py
uv run python scripts/real_repository_acceptance.py --model gpt-5.4-mini
uv run python scripts/real_operator_acceptance.py --model gpt-5.4-mini
```

Use the skill validator when available. All other commands are mandatory.

## Benchmark Labeling

This acceptance uses real upstream code but a controlled injected regression.
It is not an official SWE-bench Verified score. Official SWE-bench claims
require its dataset instance and container evaluation harness. If Docker or the
official harness is unavailable, report that limitation rather than relabeling
this E2E.

`scripts/real_toy_acceptance.py` may be used as a quick developer smoke test,
but its result must not be used for release, promotion, or benchmark claims.
