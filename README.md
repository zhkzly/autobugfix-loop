# Autobugfix

Autobugfix is a local, repo-agnostic, Git-disciplined loop-engineering and
harness-engineering control system. Given a configured target repository and a
real bug/problem, its Execution loop repeatedly drives an isolated Codex
Writer, real verifier commands, a read-only evaluator, feedback, and human
gates until the task reaches a reproducible outcome. It is not the target
application repository.

Memory precompiles accepted Execution evidence into a reviewed LLM wiki and
reusable skills. Eval reproduces real cases and measures the actual Execution
loop against tests/oracles. Operator is the meta-loop that diagnoses and
improves Autobugfix itself through governed non-main experiments. LLM agents
are bounded nodes; services, Git, deterministic checks, scorers, artifacts,
and explicit gates own control flow and truth.

## Install

```bash
uv sync --prerelease allow
uv run autobugfix doctor
```

Authoritative Operator checks require an OS process sandbox. The current
adapter uses Bubblewrap (`bwrap`) on Linux; install it before running Operator
verification. The requirement cannot be disabled by project config while the
trusted machine constitution requires it.

The production runtime uses the local preview Python Codex SDK package
`openai-codex`. It does not use `codex exec` and it does not fall back to a
fake backend for `autobugfix run`.

## Configure A Target Repo

Create `.autobugfix/config.yaml` in this control project:

```yaml
task_root: .autobugfix/tasks
scheduler:
  default_max_concurrent: 1
  lock_timeout_seconds: 7200
  max_auto_iterations: 2
  codex_timeout_seconds: 1800
  writer_timeout_seconds: null
  evaluator_timeout_seconds: null
codex:
  default_model: null
  default_timeout_seconds: null
  writer_model: null
  evaluator_model: null
  controller_model: null
  role_runtime:
    enabled: true
    runtime_root: .autobugfix/runtime/codex-sdk
    # Optional app-server binary used by the Python SDK; this is not codex exec.
    codex_bin: null
    bridge_auth: true
    skill_guard: true
    strict_skill_guard: true
  roles:
    writer:
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/writer/autobugfix-writer/SKILL.md
    evaluator:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md
    operator_supervisor:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md
    operator_writer:
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md
    operator_verifier:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md
worker:
  tick_interval_seconds: 5
memory_worker:
  tick_interval_seconds: 10
eval:
  model_mode: codex
operator:
  state:
    root: .autobugfix/operator-v3
    database_name: governance.sqlite3
  artifacts:
    root: .autobugfix/operator-artifacts
  worktrees:
    root: .autobugfix/operator-worktrees
    branch_template: operator/experiment/{request_id}
  retry:
    max_attempts: 5
    max_auto_retries: 2
    auto_retry_deterministic_failures: true
  verification:
    fast_profiles: [operator]
    full_profiles: [full]
    require_semantic_verifier: true
    process_sandbox: auto
    require_process_sandbox: true
    network_access: false
    runtime_venv: .venv
  experiments:
    enabled: true
    trusted_ref: origin/main
    default_profile: real-e2e
  experiment_lines:
    root: .autobugfix/operator-line-worktrees
    checkpoint_root: .autobugfix/operator-checkpoints
    active_release_root: .autobugfix/operator-active-experiments
    branch_template: experiment/{study_id}-main
    remote: origin
    update_timeout_seconds: 300
  budgets:
    allowed_waves: [3, 8, 16]
    allowed_primary_models: [gpt-5.4-mini]
    max_calls_by_wave:
      3: 30
      8: 80
      16: 160
    default_case_concurrency: 1
    max_case_concurrency: 1
    default_max_writer_attempts: 2
    default_max_operator_revisions: 3
    default_wall_time_seconds: 7200
    allow_model_fallback: false
  promotion:
    release_root: .autobugfix/releases
    active_release_link: .autobugfix/active-release
    require_pull_request: true
    require_canary: true
    auto_rollback_on_canary_failure: true
    canary_profiles: [full]
repos:
  target_repo:
    main_checkout: ../target-repo
    remote: origin
    main_branch: main
    branch_template: fix/{date}_oncall_{slug}
    test_commands:
      targeted: uv run pytest --no-cov {target}
      full: uv run pytest
    ppe:
      enabled: false
      command_template: null
    codex:
      roles:
        writer:
          model: null
```

The default config contains no target repositories. Unknown repo IDs fail
before task state is written. If `worktree_root` is omitted, Autobugfix derives
`.autobugfix/worktrees/<repo-id>` under the control project root.

`codex.roles` is the primary configuration surface for production roles and
skills. The legacy `writer_model`, `evaluator_model`, and `controller_model`
fields are still accepted as compatibility inputs, but new configs should set
models, sandboxes, approval modes, timeouts, and skill paths under
`codex.roles.<role>`. Repo profiles may override allowed roles under
`repos.<repo-id>.codex.roles`.

## Basic Flow

```bash
printf 'Bug report and evidence\n' \
  | uv run autobugfix create --repo target_repo --title "fix bug" --from-stdin

uv run autobugfix run <task-id>
uv run autobugfix inspect <task-id>
uv run autobugfix gate <task-id> accepted
uv run autobugfix archive <task-id> --result accepted
```

Memory and eval are separate loops:

```bash
uv run autobugfix memory init
uv run autobugfix memory collect <task-id>
uv run autobugfix memory digest <task-id>
uv run autobugfix memory maintain <task-id>

uv run autobugfix dataset build-raw --repo target_repo --out raw.jsonl
uv run autobugfix eval run --dataset problem_prompts.jsonl --out .autobugfix-evals/runs
```

## Operator Governance

Governance V4 makes the Operator a bounded supervisor, not a state owner.
Request phases are only `REQUESTED`, `ACTIVE`, `VERIFIED`, and `CLOSED`;
Writer attempts, checks, gates, scope revisions, experiments, and promotions
are independent records in the control checkout's SQLite store. Candidate
worktrees contain no authoritative state.

The default directory name `.autobugfix/operator-v3` is retained so existing
authority databases migrate in place; its SQLite `user_version` is 4 and the
loaded machine constitution is V4. The directory name is not the active policy
version.

The control checkout is the authority plane: the service owns SQLite records,
Git experiment refs, budget usage, deterministic check output, integration
receipts, and checkpoint pointers. Candidate worktrees are data planes: Writer
may change source and tests there, but files it creates cannot authorize scope,
claim a check passed, consume or expand a budget, advance an experiment line,
or activate a release. A Git hook is only an accident guard; service checks and
trusted-base CI remain the merge authority.

```bash
uv run autobugfix operator guide

uv run autobugfix operator triage \
  --triage-id triage-eval-diff \
  --summary "eval harness did not preserve generated diff" \
  --suspected-layer eval \
  --confidence medium \
  --evidence .autobugfix-evals/run/case/report.yaml

uv run autobugfix operator baseline record \
  --name real-e2e \
  --profile real-e2e

# Human/CI authority step: publish only the protected baseline receipt.
git add .autobugfix-baselines/real-e2e.yaml
git commit -m "Record trusted real-e2e baseline"

uv run autobugfix operator request \
  --request-id op-eval-diff \
  --triage-id triage-eval-diff \
  --summary "fix eval artifact capture" \
  --primary-layer eval \
  --planned-path 'src/autobugfix/eval/**' \
  --risk low \
  --validation-profile eval \
  --performance-baseline real-e2e

uv run autobugfix operator preflight --request-id op-eval-diff
uv run autobugfix operator start --request-id op-eval-diff
uv run autobugfix operator writer-start --request-id op-eval-diff
uv run autobugfix operator verify --request-id op-eval-diff --mode fast
uv run autobugfix operator candidate-commit \
  --request-id op-eval-diff --message "Repair eval artifact capture"
uv run autobugfix operator experiment-run \
  --request-id op-eval-diff --profile real-e2e
uv run autobugfix operator verify --request-id op-eval-diff --mode full
uv run autobugfix operator audit --request-id op-eval-diff
uv run autobugfix operator promotion-prepare --request-id op-eval-diff
```

Named experiment lines wrap that same Request lifecycle with frozen study,
budget, integration, and checkpoint authority. The manifest, success contract,
and metric receipts are produced by the trusted benchmark adapter/Guard; they
are not candidate-authored score claims.

Study creation copies the visible Optimization manifest and active Memory into
separate read-only H0 snapshots under the trusted checkpoint root. Operator
projections expose their digests but not snapshot paths. Sealed Holdout
manifests, gold data, and case-level results must remain outside all Operator
roots under an external Guard; only an aggregate metric receipt may enter the
Operator store after final evaluation.

```bash
uv run autobugfix operator study create \
  --study-id defects4j-bugfix \
  --cohort-id autobugfix-h0-v1 \
  --purpose "Improve the bugfix harness" \
  --manifest .autobugfix/benchmark-manifests/defects4j-bugfix.yaml \
  --success-contract .autobugfix/benchmark-manifests/bugfix-success.yaml \
  --base-ref <frozen-h0-sha> \
  --model gpt-5.4-mini \
  --target-checkpoint H_bug

uv run autobugfix operator line init \
  --study-id defects4j-bugfix \
  --metric-receipt-id <registered-h0-metric-id>

uv run autobugfix operator budget request \
  --study-id defects4j-bugfix --wave 3 \
  --case <case-1> --case <case-2> --case <case-3> \
  --reason "Run the first Optimization wave"

uv run autobugfix operator budget approve \
  --budget-request-id <budget-request-id> \
  --approver <human-identity> \
  --confirm-request-digest <displayed-request-digest>

uv run autobugfix operator request \
  --triage-id <triage-id> \
  --summary "repair diagnosed harness layer" \
  --primary-layer execution \
  --planned-path src/autobugfix/runner.py \
  --risk medium \
  --validation-profile full \
  --experiment-line defects4j-bugfix \
  --budget-grant <grant-id>

# Run start/Writer/check/commit/experiment/full-check as above.
uv run autobugfix operator integrate \
  --request-id <verified-request-id> --grant-id <grant-id>

uv run autobugfix operator checkpoint create \
  --line-id defects4j-bugfix --name H_bug \
  --metric-receipt-id <registered-study-metric-id>

uv run autobugfix operator line rollback \
  --line-id defects4j-bugfix --checkpoint-id defects4j-bugfix-H0 \
  --reason "validated regression against the frozen baseline"
```

`budget approve` refuses non-interactive stdin and asks the human to type
`APPROVE <request-digest>` exactly. Supplying an `--approver` label from an
agent or CI process is not sufficient authority.

The trusted benchmark adapter registers H0 and candidate receipts through the
Guard service API, which copies them into content-addressed storage and writes
immutable `StudyMetricRecord` rows. There is intentionally no generic Operator
CLI for importing a score claim. Line/checkpoint commands accept only those
registered IDs and recheck the artifact hash and every frozen binding.

Budget waves are exactly `3 -> 8 -> 16`, run at case concurrency one, and use
only `gpt-5.4-mini`; quota, expiry, or model mismatch stops before the SDK call.
Integration reruns trusted policy and validation in a separate worktree and
advances the Git ref plus SQLite generation with compare-and-swap. Rollback
creates a new commit whose tree equals the selected checkpoint; it never resets
or force-pushes history.

The planned studies use the same `--cohort-id` and therefore must match frozen
H0 Git, harness, policy, role-config, config, model, skills, and Memory digests.
They otherwise share only frozen `H0`. Experiment 1 uses 10 visible
Defects4J Optimization cases and 6 sealed unseen-repository Holdout cases to
produce `H_bug`. Experiment 2 independently uses 10 SWE-bench Verified
Optimization cases and 6 SWE-bench-Live sealed unseen-repository Holdout cases
to produce `H_general`. Experiment 2 must not inherit `H_bug` code, skills,
Memory, artifacts, results, or case-level feedback. These are experiment
protocols, not changes to the four-loop project constitution.

`operator advance` performs one legal scheduler action at a time: start,
Writer, fast check, candidate commit, matching experiment, full check, or stop
for intervention.
Writer has only the filtered read surface `autobugfix writer
task|context|scope|feedback|check-result`; it cannot call Operator mutations.

Cross-layer scope expansion is versioned. An earlier approval does not grant a
new revision:

```bash
uv run autobugfix operator scope-change \
  --request-id op-eval-diff \
  --add-layer shared_runtime \
  --add-path src/autobugfix/config.py \
  --risk medium \
  --reason "isolated config generation is in the failing data flow"

uv run autobugfix operator review op-eval-diff \
  --reviewer scope-reviewer \
  --decision approve \
  --allowed-layer eval \
  --allowed-layer shared_runtime \
  --scope-revision-id <revision-id> \
  --reason "cross-layer diagnosis and path scope verified"

uv run autobugfix operator scope-activate \
  --request-id op-eval-diff --revision-id <revision-id>
```

Constitutional changes require a real OpenSSH signature or an allowlisted
GitHub review. A local `kind: human` label is not accepted. Add
`--scope-revision-id` when approving a proposed revision.

```bash
uv run autobugfix operator approval-payload op-architecture \
  --stage scope \
  --approver human-owner \
  --reason "authorize governance change" \
  --out /tmp/op-architecture.json

ssh-keygen -Y sign -f ~/.ssh/operator_signing_key \
  -n autobugfix-operator /tmp/op-architecture.json

uv run autobugfix operator approve-signed op-architecture \
  --payload /tmp/op-architecture.json \
  --signature /tmp/op-architecture.json.sig \
  --allowed-signers ~/.config/autobugfix/allowed_signers
```

Promotion is separate from verification:

```bash
uv run autobugfix operator promotion-open-pr \
  --promotion-id <promotion-id> \
  --title "Repair eval artifact capture" \
  --body "Autobugfix-Request-Digest: <request-digest>"

uv run autobugfix operator promotion-observe-merge \
  --promotion-id <promotion-id> --repository owner/repository
uv run autobugfix operator promotion-canary --promotion-id <promotion-id>
```

On a regression, `promotion-rollback` restores the exact last-known-good
active-release link. `promotion-revert-pr` then creates a normal Git revert
branch/PR; it never resets or force-pushes shared main.

GitHub approval review bodies must include
`Autobugfix-Request-Digest: <request-digest>`. The committed
`.autobugfix-governance/<request-id>/bundle.yaml` is advisory transport only.
The GitHub check loads code/policy from the trusted base, recalculates the
actual diff, re-reads reviews, and reruns profiles in Bubblewrap.

The trusted script supports runtime records and exported bundles:

```bash
uv run python scripts/validate_operator_policy.py \
  --request-id op-eval-diff \
  --candidate-root .autobugfix/operator-worktrees/op-eval-diff \
  --trusted-ref origin/main
```

Regression baselines are immutable contracts under `.autobugfix-baselines/`.
The trusted service captures them by running a configured experiment profile
on the frozen trusted ref. Candidate metrics come only from a Guard-observed
experiment bound to the current HEAD and patch digest; CLI callers cannot type
numeric metrics into an authority path. The captured receipt must be reviewed
and committed to the trusted base before creating a request, so remote CI can
read it without trusting candidate files. Its measured SHA may precede the
request base only by baseline-metadata commits; any intervening code/skill/config
change makes it stale:

```bash
uv run autobugfix operator baseline record \
  --name real-e2e \
  --profile real-e2e

# Performed by a human or trusted CI publisher, never Operator Writer.
git add .autobugfix-baselines/real-e2e.yaml
git commit -m "Record trusted real-e2e baseline"

uv run autobugfix operator experiment-run \
  --request-id op-eval-diff \
  --profile real-e2e

uv run autobugfix operator baseline compare \
  --name real-e2e \
  --request-id op-eval-diff
```

Install repository-specific reviewer/public-key allowlists, CODEOWNERS, and
optional branch protection with `scripts/install_operator_governance.py`.
Codex hooks block obvious direct merge/protected push/state-write commands,
but hooks are not authority; Service and trusted-base CI are the final gates.
These project hooks apply to the supervising main Codex session in the
Autobugfix source checkout. Isolated SDK roles explicitly disable hooks and are
constrained by their role sandbox, worktree, service, and verifier instead.

The mandatory docs acceptance uses a pinned ItsDangerous upstream commit and a
reproducible fault injection to cover Execution, Memory, and Eval with real
Codex roles. It is deliberately reported as a real-repository E2E, not as an
official SWE-bench score:

```bash
uv run python scripts/real_repository_acceptance.py --model gpt-5.4-mini
```

The script clones the public upstream commit, commits a reproducible regression
to a local fixture remote, verifies that Execution changes only its task
worktree, compiles accepted evidence into a pending Memory proposal, and runs a
second isolated Eval execution whose generated patch must pass an independent
real pytest oracle. The committed oracle diff is retained only for diagnosis;
an equivalent alternative implementation is valid. The configured target main
checkout must remain byte-for-byte clean.
Official SWE-bench Verified scoring still requires its container harness; a
local run without that harness must not be labeled as a benchmark result.

`scripts/real_toy_acceptance.py` remains an optional fast development fixture;
it is not a release or promotion acceptance gate.

Runtime state under `.autobugfix/`, generated memory evidence, eval runs, and
UI screenshots are gitignored.
