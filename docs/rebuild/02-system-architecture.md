System Architecture
Layering
The rebuilt project must keep adapters, services, projections, and storage separate.
CLI / Gradio / Codex controller
  -> Service layer
  -> Store / runner / worker / memory / eval modules
  -> durable filesystem state
  -> Projection layer
  -> CLI / Gradio read models

Adapters must not edit YAML, JSONL, lock files, branches, worktrees, artifacts, or memory files directly. Mutating operations go through services. Read-only operator views go through projections.
Execution Components
Required modules:
src/autobugfix/config.py
src/autobugfix/models.py
src/autobugfix/task_store.py
src/autobugfix/service.py
src/autobugfix/runner.py
src/autobugfix/worktree.py
src/autobugfix/verifier.py
src/autobugfix/evaluator.py
src/autobugfix/prompts.py
src/autobugfix/projection.py
src/autobugfix/scheduler.py
src/autobugfix/worker.py
src/autobugfix/ppe.py
src/autobugfix/controller.py
src/autobugfix/gradio_app.py

Execution state:
.autobugfix/tasks/<task-id>/
  task.yaml
  events.jsonl
  context/
  feedback/
  runs/
  artifacts/
  logs/
  controller/

.autobugfix/archive/<result>/<task-id>/
.autobugfix/repos/<repo-id>.sync.lock
.autobugfix/worker.pid
.autobugfix/worker-heartbeat.json
.autobugfix/worker-events.jsonl
.autobugfix/worker.log

Task states:
new
ready
writing
verifying
evaluating
writer_rework_required
waiting_human_review
waiting_human_ppe_approval
ppe_approved
ppe_deployed
waiting_human_acceptance
feedback_available
accepted
abandoned
paused
archived
blocked

Runnable states:
ready
feedback_available
writer_rework_required

Codex Runtime
Required modules:
src/autobugfix/codex_backend.py
src/autobugfix/codex_sdk.py
src/autobugfix/codex_sdk_worker.py
src/autobugfix/codex_runtime.py

Production backend must use the Codex Python SDK, not codex exec.
Role rules:
Writer:
cwd is the task worktree;
sandbox is workspace-write;
may edit only the worktree.
Evaluator:
cwd is the task worktree;
read-only;
returns structured YAML decision.
Controller:
external runtime cwd;
calls CLI/service commands;
does not edit task files.
Memory maintainer:
external runtime cwd;
writes proposal output through memory service;
does not approve memory.
Eval judge:
eval case artifact cwd;
artifact-only scoring;
does not inspect arbitrary source trees unless the eval contract gives it
artifacts.
Role instructions are loaded from project-owned role skills and injected through SDK developer instructions. Do not isolate by replacing the user's Codex auth home; local auth must continue to work.
Memory Components
Required modules:
src/autobugfix/memory/store.py
src/autobugfix/memory/service.py
src/autobugfix/memory/projection.py
src/autobugfix/memory/collect.py
src/autobugfix/memory/digest.py
src/autobugfix/memory/maintain.py
src/autobugfix/memory/maintainer_backend.py
src/autobugfix/memory/lint.py
src/autobugfix/memory/patch.py
src/autobugfix/memory/search.py
src/autobugfix/memory/context.py
src/autobugfix/memory_worker.py
src/autobugfix/memory_gradio_app.py

Memory state:
.autobugfix-memory/
  README.md
  schema.md
  index.md
  log.md
  config.yaml
  active/user-preferences.md
  raw/tasks/<task-id>/
  digests/index.yaml
  digests/tasks/<task-id>.md
  proposals/<proposal-id>/
  rejected/<proposal-id>/
  skills/approved/<skill-name>/SKILL.md
  maintainer-runs/<proposal-id>/
  worker.pid
  worker-heartbeat.json
  worker-events.jsonl
  worker.log

Memory is deterministic except for the maintainer proposal text generation. Even the LLM maintainer must write into an isolated run directory; deterministic code validates and copies proposal files into the memory store.
Evaluation Components
Required modules:
src/autobugfix/dataset.py
src/autobugfix/eval/models.py
src/autobugfix/eval/artifacts.py
src/autobugfix/eval/runner.py
src/autobugfix/eval/scorers.py
src/autobugfix/eval/diagnosis.py
src/autobugfix/eval/improvements.py
src/autobugfix/eval/supervision.py

Eval case flow:
JSONL dataset row
  -> clone source worktree into isolated bare remote
  -> create isolated main checkout at base_commit
  -> write isolated .autobugfix/config.yaml using case.repo
  -> call AutobugfixService.create_task(case.repo, ...)
  -> call AutobugfixService.run_task(...)
  -> collect generated diff, logs, artifacts
  -> write oracle diff from base_commit..final_commit
  -> run scorers
  -> write report, summary, diagnosis packet

Eval must snapshot its resolved config into each run directory so repeated experiments are comparable.
Service Boundaries
Execution service:
create_task
add_context
run_task
add_feedback
apply_gate
deploy_ppe
archive
worker start/status/stop/ensure
Memory service:
init
collect
digest
maintain
tick
approve
reject
lint
search
context
memory worker start/status/stop/ensure
Eval service may be functional rather than class-based, but it must write only eval-owned artifacts and call execution through the real service layer.