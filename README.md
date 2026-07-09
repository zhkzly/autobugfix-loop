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
  writer_model: null
  evaluator_model: null
  controller_model: null
  role_runtime:
    enabled: true
    runtime_root: .autobugfix/runtime/codex-sdk
    bridge_auth: true
    skill_guard: true
    strict_skill_guard: true
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
```

The default config contains no target repositories. Unknown repo IDs fail
before task state is written. If `worktree_root` is omitted, Autobugfix derives
`.autobugfix/worktrees/<repo-id>` under the control project root.

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

Runtime state under `.autobugfix/`, generated memory evidence, eval runs, and
UI screenshots are gitignored.
