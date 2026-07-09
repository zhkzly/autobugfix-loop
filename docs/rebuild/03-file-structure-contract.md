File Structure Contract
The rebuilt repository must be importable as a Python package named autobugfix and expose the CLI entry point autobugfix = autobugfix.cli:main.
Repository Root
Required root files:
AGENTS.md
README.md
pyproject.toml
uv.lock
.gitignore
.agents/
examples/
scripts/
src/
tests/
docs/rebuild/

Optional if using Trellis:
.trellis/

Do not require .trellis/ at runtime. The production CLI must work in a normal Python package checkout without Trellis commands.
Python Package
Required package tree:
src/autobugfix/
  __init__.py
  cli.py
  codex_backend.py
  codex_runtime.py
  codex_sdk.py
  codex_sdk_worker.py
  config.py
  controller.py
  dataset.py
  evaluator.py
  events.py
  git_utils.py
  gradio_app.py
  locks.py
  memory_gradio_app.py
  memory_worker.py
  models.py
  ppe.py
  projection.py
  prompts.py
  runner.py
  scheduler.py
  service.py
  task_store.py
  verification_policy.py
  verifier.py
  worker.py
  worktree.py
  eval/
    __init__.py
    artifacts.py
    diagnosis.py
    improvements.py
    models.py
    runner.py
    scorers.py
    supervision.py
  memory/
    __init__.py
    collect.py
    config.py
    context.py
    digest.py
    lint.py
    maintain.py
    maintainer_backend.py
    patch.py
    projection.py
    search.py
    service.py
    store.py

Role Skills
Required role skill tree:
.agents/role-skills/
  base/autobugfix-runtime-base/SKILL.md
  execution/writer/autobugfix-writer/SKILL.md
  execution/evaluator/autobugfix-evaluator/SKILL.md
  memory/maintainer/autobugfix-memory-maintainer/SKILL.md
  eval/judge/autobugfix-eval-judge/SKILL.md

.agents/skills/
  oncall-bugfix/SKILL.md
  autobugfix-eval-operator/SKILL.md

The runtime must load only the shared base role skill plus the current role skill. Project-level operator/Trellis skills must not bleed into writer, evaluator, memory maintainer, or eval judge prompts.
Tests
Required test files:
tests/test_cli.py
tests/test_codex_runtime.py
tests/test_codex_sdk.py
tests/test_config_task_store.py
tests/test_controller.py
tests/test_dataset.py
tests/test_eval.py
tests/test_evaluator.py
tests/test_gradio_app.py
tests/test_locks.py
tests/test_memory.py
tests/test_memory_gradio_app.py
tests/test_memory_worker.py
tests/test_ppe.py
tests/test_projection.py
tests/test_prompts.py
tests/test_runner.py
tests/test_scheduler.py
tests/test_service.py
tests/test_worker.py

Unit tests may use deterministic fake Codex backends. They must still verify that production code passes cwd, sandbox, model, developer instructions, logs, and state transitions correctly.
Runtime Directories
These directories are runtime state and must be gitignored:
.autobugfix/
.autobugfix-memory/raw/
.autobugfix-memory/digests/
.autobugfix-memory/proposals/
.autobugfix-memory/rejected/
.autobugfix-memory/maintainer-runs/
.autobugfix-memory/worker*.*
.autobugfix-evals/
.autobugfix-experiments/
.ui-screenshots/

Approved memory surfaces may be versioned if the user chooses:
.autobugfix-memory/active/user-preferences.md
.autobugfix-memory/skills/approved/**/SKILL.md

Configuration Files
Execution config:
.autobugfix/config.yaml

Memory config:
.autobugfix-memory/config.yaml

Eval config:
.autobugfix-evals/configs/*.yaml

Never introduce one global model setting that silently affects execution, memory, and eval together. Each loop owns its own model and timeout settings.
Public Portability
The repository must not contain hardcoded private paths such as a local username or a company-internal target repo. Documentation examples must use names like sample_repo, toy_repo, or target_repo.