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

Operator changes are guarded by an Autobugfix-specific permission gate. The
operator must diagnose first, declare the affected layer scope, obtain review
or human approval when required, and validate the patch against the machine
constitution.

```bash
uv run autobugfix operator triage \
  --summary "eval harness did not preserve generated diff" \
  --suspected-layer eval \
  --confidence medium \
  --evidence .autobugfix-evals/run/case/report.yaml

uv run autobugfix operator request \
  --request-id op-eval-diff \
  --summary "fix eval artifact capture" \
  --primary-layer eval \
  --risk low \
  --validation-command "uv run pytest -q tests/test_eval.py"

uv run autobugfix operator preflight --request-id op-eval-diff
uv run autobugfix operator validate --request-id op-eval-diff --run-validation-commands
```

Cross-layer or medium-risk requests require an approved review. High-risk or
constitution-level changes require human approval:

```bash
uv run autobugfix operator review op-eval-config \
  --reviewer scope-reviewer \
  --kind agent \
  --decision approve \
  --reason "eval runner consumes isolated config generation"

uv run autobugfix operator review op-architecture \
  --reviewer human-owner \
  --kind human \
  --decision approve \
  --reason "explicit architecture approval"
```

The same policy can run as a script:

```bash
uv run python scripts/validate_operator_policy.py --request-id op-eval-diff
```

Regression baselines can be recorded and compared to prevent operator changes
from making the loop worse:

```bash
uv run autobugfix operator baseline record \
  --name toy-e2e \
  --metric pass_rate=1 \
  --metric runtime_seconds=12

uv run autobugfix operator baseline compare \
  --name toy-e2e \
  --metric pass_rate=1 \
  --metric runtime_seconds=14 \
  --min-metric pass_rate=1 \
  --max-regression-percent runtime_seconds=25
```

Runtime state under `.autobugfix/`, generated memory evidence, eval runs, and
UI screenshots are gitignored.
