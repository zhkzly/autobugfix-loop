AI Generation Task: Rebuild Autobugfix
Use this document as a paste-ready Trellis task, PRD, or implementation request for an AI coding agent.
Objective
Build a complete, production-capable Python project named autobugfix-loop that implements the Autobugfix control system described in docs/rebuild/.
The project must be repo-agnostic. It must work with any Git target repository configured in .autobugfix/config.yaml. It must not hardcode an internal repo, absolute local path, username, or organization-specific deployment command.
The implementation must be directly executable with uv run autobugfix .... Mock-only implementations are not acceptable.
Required Review Protocol
Before planning, before editing core services/config/runtime behavior, before final acceptance, and before merge or handoff, follow 08-review-protocol.md.
Use independent subagents for the required reviewer passes when the platform supports them. If the platform does not support subagents, the main agent must run the same reviewer passes sequentially and must not claim that subagents were used.
Every checkpoint must restate:
Autobugfix's project purpose;
execution loop ownership;
memory loop ownership;
eval loop ownership;
operator loop ownership;
the specific state owner for the planned change;
the real non-mock path that will validate the change.
Required Product Behavior
Implement four separated systems:
Execution loop:
task -> context -> worktree -> writer -> verifier -> evaluator -> feedback -> human gate -> archive

Memory loop:
task evidence -> raw packet -> digest -> proposal patch -> approval -> active memory

Eval loop:
dataset case -> isolated execution -> generated diff -> oracle diff -> scorers -> diagnosis

Operator loop:
Human/Codex supervision that runs real cases, reads artifacts, diagnoses
control-system issues, and changes code/skills only on non-main branches.
Required CLI Surface
Implement these commands:
autobugfix doctor
autobugfix create --repo <repo-id> --title <title> --from-stdin
autobugfix context add <task-id> --kind <kind> [--from-stdin|--file <path>]
autobugfix run <task-id>
autobugfix feedback <task-id> --decision needs_changes --from-stdin [--queue-only]
autobugfix gate <task-id> approve-ppe|accepted|abandoned|pause|resume
autobugfix deploy-ppe <task-id>
autobugfix archive <task-id> --result accepted|abandoned|...
autobugfix status
autobugfix inspect <task-id>
autobugfix watch <task-id> [--once]
autobugfix tick --max-concurrent <n>
autobugfix daemon --once
autobugfix worker start|ensure|status|stop
autobugfix ui --host <host> --port <port>
autobugfix memory init
autobugfix memory collect <task-id>
autobugfix memory digest <task-id>
autobugfix memory maintain <task-id>
autobugfix memory tick --max-tasks <n>
autobugfix memory status
autobugfix memory proposals
autobugfix memory review <proposal-id>
autobugfix memory show <proposal-id>
autobugfix memory approve <proposal-id> --note <text>
autobugfix memory reject <proposal-id> --reason <text>
autobugfix memory lint
autobugfix memory search <query>
autobugfix memory context --audience writer|evaluator|controller
autobugfix memory-worker start|ensure|status|stop
autobugfix memory-ui --host <host> --port <port>
autobugfix dataset build-raw --repo <repo-id> --out <jsonl> [--base-ref <ref>]
autobugfix eval run --dataset <jsonl> [--case <selector>] --out <dir>
autobugfix eval score <case-or-run-dir>
autobugfix eval diagnose <run-dir>
autobugfix eval improvements list|show|update
autobugfix eval iterate
autobugfix eval supervise
autobugfix codex probe-role --role <role>

If a command is implemented later as a thin wrapper, it must still call real service/projection code. Do not print placeholder text.
Required Configuration
.autobugfix/config.yaml:
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
worker:
  tick_interval_seconds: 5
memory_worker:
  tick_interval_seconds: 10
eval:
  model_mode: codex
  roles: {}
repos:
  target_repo:
    main_checkout: ../target-repo
    remote: origin
    main_branch: main
    # Optional. Default: .autobugfix/worktrees/<repo-id>
    worktree_root: .autobugfix/worktrees/target_repo
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

Default config must not include any target repo. Unknown repo IDs must fail before task state is written.
Implementation Constraints
Use Python 3.11+.
Use uv for dependency and command execution.
Use PyYAML for YAML.
Use Gradio for local operator UIs.
Use openai-codex Python SDK for production Codex calls.
Do not use codex exec as the production backend.
Do not shell-write code through heredocs or cat > in the writer role.
Do not implement target repo operations with mocks.
Do not let adapters edit durable state directly.
Do not let eval or memory mutate execution state.
Do not auto-approve PPE, final acceptance, memory proposals, or archive.
Preserve raw logs and event evidence.
Required File Structure
Implement the file structure in 03-file-structure-contract.md.
Required Tests
Add deterministic unit tests for:
config loading and repo defaults;
task store create/context/feedback/events;
lock behavior;
worktree safety and path containment;
service state transitions;
runner rework/pass path;
writer/evaluator raw logs;
verifier diff/test artifacts;
PPE gate enforcement;
scheduler and worker behavior;
projection output;
Gradio action helpers using service/projection;
Codex SDK backend parameter passing and environment sanitization;
role instruction loading and skill guard;
memory init/collect/digest/maintain/approve/reject/lint/search/context;
memory worker behavior;
dataset raw builder;
eval run, score, diagnose, improvements, supervision.
Unit tests may use fake Codex backends, but fake backends are only for deterministic tests. Production CLI paths must use the real backend.
Required Real Acceptance
Before declaring done, run 05-real-acceptance.md end to end with a real local toy Git repository. The acceptance must prove:
real Git remote/main/worktree flow;
real Codex SDK writer/evaluator execution;
real verifier command;
real memory maintainer execution;
real eval run that calls the execution loop;
generated diff equals oracle diff for the toy case.
Definition Of Done
The task is complete only when:
uv run pytest -q passes;
uv run python -m compileall -q src tests scripts passes;
git diff --check passes;
role skills validate if a skill validator is available;
08-review-protocol.md reviewer passes are completed and recorded;
the real toy repo acceptance passes;
the final branch contains no hardcoded private repo/path references;
the README tells users how to configure a different target repo;
all runtime state directories are gitignored.
Explicit Rejection Criteria
Reject the implementation if:
it only prints fake CLI output;
it modifies the target repo main checkout directly;
it creates worktrees outside the configured root;
it uses a mock writer/evaluator for production autobugfix run;
it treats eval as a second execution state machine;
it lets memory approve itself;
it hides failures by writing successful summaries without raw logs;
it hardcodes a repo name such as mars_agent or a local path.
