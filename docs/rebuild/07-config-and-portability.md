Configuration And Portability
Repo-Agnostic Rule
The target repository is a profile, not a constant.
Every code path that needs a repository must receive or load a repo_id, then resolve it through .autobugfix/config.yaml.
Wrong:
service.create_task("mars_agent", title, body)
worktree_root = Path("/Users/name/pycodes/autobugfix-worktrees")

Correct:
repo = config.repo(repo_id)
service.create_task(repo_id, title, body)
worktree = repo.worktree_root / task_id

Control Config
.autobugfix/config.yaml owns shared runtime, Execution, Eval role selection,
and Operator harness settings. Memory content/approval state remains under its
separate boundary:
task_root: .autobugfix/tasks
scheduler:
  default_max_concurrent: 2
  lock_timeout_seconds: 7200
  max_auto_iterations: 3
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
    codex_bin: null
    bridge_auth: true
    skill_guard: true
    strict_skill_guard: true
  roles:
    writer:
      backend: codex
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/writer/autobugfix-writer/SKILL.md
    evaluator:
      backend: codex
      model: null
      sandbox: read-only
      approval_mode: deny_all
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md
    memory_maintainer:
      backend: codex
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/memory/maintainer/autobugfix-memory-maintainer/SKILL.md
    eval_judge:
      backend: codex
      model: null
      sandbox: read-only
      approval_mode: deny_all
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/eval/judge/autobugfix-eval-judge/SKILL.md
    operator_supervisor:
      backend: codex
      model: null
      sandbox: read-only
      approval_mode: deny_all
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md
    operator_writer:
      backend: codex
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md
    operator_verifier:
      backend: codex
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
  roles: {}
operator:
  state: {root: .autobugfix/operator-v3, database_name: governance.sqlite3}
  artifacts: {root: .autobugfix/operator-artifacts}
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
    profiles: {}
  promotion:
    release_root: .autobugfix/releases
    active_release_link: .autobugfix/active-release
    require_pull_request: true
    require_canary: true
    auto_rollback_on_canary_failure: true
    canary_profiles: [full]
repos: {}

Default config must contain no repo profiles. A user must configure the target repo explicitly.
`codex.roles` is the primary model/skill/sandbox/approval surface. Legacy
`writer_model`, `evaluator_model`, and `controller_model` remain compatibility
inputs only.
Operator role model/timeouts are configurable. There is no hardcoded model
default: `model: null` delegates to the installed Codex runtime. The trusted
machine constitution enforces backend, sandbox, approval mode, required skill,
and process-sandbox minima, so project config cannot widen Operator authority.
Repo Profile Defaults
If a repo profile omits worktree_root, derive:
<project-root>/.autobugfix/worktrees/<repo-id>

Default branch template:
fix/{date}_oncall_{slug}

Default test commands:
test_commands:
  targeted: uv run pytest --no-cov {target}
  full: uv run pytest

Default PPE:
ppe:
  enabled: true
  command_template: null

Deploy must fail clearly if PPE command is missing.

Codex Hook Placement
The repository `.codex/hooks.json` belongs only to `operator_host`: the human
or current main agent supervising Autobugfix from the trusted source checkout.
It must not be treated as a generic agent policy.

The following SDK roles run in isolated `CODEX_HOME` directories with
`features.hooks = false`: Execution `writer` and `evaluator`,
`memory_maintainer`, `eval_judge`, Eval's inner Execution roles, and Operator
`operator_supervisor`, `operator_writer`, and `operator_verifier`.

Disabling `codex.role_runtime.enabled` is invalid. If Autobugfix cannot locate
the control root config or create the isolated runtime, production role launch
fails instead of inheriting user/global hooks. The authoritative boundaries are
the owning service, role sandbox, Git worktree, verifier/scorer, approval, and
trusted CI.

Memory Config
.autobugfix-memory/config.yaml owns memory maintainer settings only:
maintainer:
  backend: codex
  model: null
  timeout_seconds: null
  role:
    model: null
    timeout_seconds: null

Changing memory maintainer model must not change execution writer/evaluator or eval judge behavior.

Memory authority writes require POSIX descriptor-relative I/O with
`O_NOFOLLOW`; production Memory activation is therefore supported on the same
Linux/WSL host profile as the Bubblewrap Codex runtime. Unsupported hosts fail
closed before writing approved wiki or skill state. A proposal becomes active
only through `memory approve` or `memory approve-skill` with the exact digest
returned by `memory review`.
Eval Config
Eval-owned YAML or CLI flags own experiment settings:
dataset: .autobugfix-experiments/datasets/problem_prompts.jsonl
cases:
  - fix/sample_bug
out: .autobugfix-evals/runs
experiment: memory-v1
repeat: 3
model_mode: codex
execution:
  codex_model: null
  writer_timeout_seconds: 1800
  evaluator_timeout_seconds: 1800
judge:
  model: null
test_command: uv run --all-extras pytest

Eval config must be snapshotted into every run directory as resolved YAML.
Public Repository Hygiene
Before publishing or using this project as a template, remove:
runtime task data under .autobugfix/**;
raw memory packets, proposals, rejected proposals, maintainer runs;
eval run artifacts containing task data;
screenshots, logs, browser responses, internal bug reports;
company-internal PPE commands;
private target repo names;
absolute private paths;
credentials or generated auth files.
Keep:
source code;
tests;
role skills;
public examples;
this rebuild dossier;
README and AGENTS guidance.
Runtime State Gitignore
At minimum:
.venv/
.pytest_cache/
__pycache__/
*.pyc
.autobugfix/
.autobugfix-experiments/
.autobugfix-evals/
.ui-screenshots/
.tmp-autobugfix-ui*.png

For .autobugfix-memory, ignore generated evidence by default. If approved memory is meant to be versioned, ignore generated subtrees but keep:
.autobugfix-memory/active/user-preferences.md
.autobugfix-memory/skills/approved/**/SKILL.md
