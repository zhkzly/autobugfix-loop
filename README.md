# Autobugfix

Autobugfix is a local, repo-agnostic, Git-disciplined, observable on-call
bugfix control system. It is not the target application repository. It creates
controlled task state, target-repo worktrees, Codex writer/evaluator runs,
verifier artifacts, human gates, memory proposals, and eval reports.

## Install

```bash
uv sync --prerelease allow
uv run autobugfix doctor
```

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
worker:
  tick_interval_seconds: 5
memory_worker:
  tick_interval_seconds: 10
eval:
  model_mode: codex
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

Governance v2 treats the Operator as a bounded execution node. Requests are
immutable, freeze a Git base SHA and branch, and create a real dedicated
worktree. Merge authority uses policy from a trusted base ref; a candidate
cannot weaken its own constitution and approve itself.

```bash
uv run autobugfix operator triage \
  --triage-id triage-eval-diff \
  --summary "eval harness did not preserve generated diff" \
  --suspected-layer eval \
  --confidence medium \
  --evidence .autobugfix-evals/run/case/report.yaml

uv run autobugfix operator request \
  --request-id op-eval-diff \
  --triage-id triage-eval-diff \
  --summary "fix eval artifact capture" \
  --primary-layer eval \
  --risk low \
  --validation-profile eval

uv run autobugfix operator preflight --request-id op-eval-diff
uv run autobugfix operator workspace-create --request-id op-eval-diff
# Patch only the returned worktree.
uv run autobugfix operator postflight --request-id op-eval-diff
uv run autobugfix operator validate --request-id op-eval-diff
uv run autobugfix operator export-bundle --request-id op-eval-diff
```

Cross-layer requests require an independent reviewer. The request creator
cannot review itself:

```bash
uv run autobugfix operator review op-eval-config \
  --reviewer scope-reviewer \
  --decision approve \
  --allowed-layer eval \
  --allowed-layer shared_runtime \
  --reason "eval runner consumes isolated config generation"
```

Constitutional changes require a real OpenSSH signature or an allowlisted
GitHub approved review. A local `kind: human` label is not accepted:

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

After validation, create a merge-stage payload bound to the patch digest,
import its signature, run `finalize`, and export the bundle. GitHub runs the
base branch's trusted validator, not the PR's candidate validator.

GitHub approval review bodies must include
`Autobugfix-Request-Digest: <request-digest>` and may be imported with
`approve-github --stage scope|merge`.

The trusted script supports runtime records and exported bundles:

```bash
uv run python scripts/validate_operator_policy.py \
  --request-id op-eval-diff \
  --candidate-root .autobugfix/operator/worktrees/op-eval-diff \
  --trusted-ref origin/main
```

Regression baselines are versioned contracts under `.autobugfix-baselines/`.
Required metrics cannot be omitted and the candidate PR cannot provide its own
trusted baseline:

```bash
uv run autobugfix operator baseline record \
  --name toy-e2e \
  --metric pass_rate=1 \
  --metric artifact_completeness=1 \
  --metric runtime_seconds=12

uv run autobugfix operator baseline compare \
  --name toy-e2e \
  --metric pass_rate=1 \
  --metric artifact_completeness=1 \
  --metric runtime_seconds=14
```

Install repository-specific reviewer/public-key allowlists, CODEOWNERS, and
optional branch protection with `scripts/install_operator_governance.py`.

Runtime state under `.autobugfix/`, generated memory evidence, eval runs, and
UI screenshots are gitignored.
