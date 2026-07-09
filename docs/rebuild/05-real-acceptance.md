Real End-To-End Acceptance
This acceptance plan is mandatory for a rebuild. It is intentionally not a unit test and not a mock. It uses a real local Git repository, real worktrees, real Autobugfix CLI commands, real Codex SDK writer/evaluator calls, real verifier commands, memory maintenance, and eval scoring.
Prerequisites
uv is installed.
Git is installed.
The local Codex Python SDK can authenticate using the user's normal Codex
login/config.
Network/model access is available for real writer/evaluator calls.
The control project is on a non-main feature branch or a disposable checkout
for testing.
Create A Toy Target Repo
Create a temporary bare remote, seed repo, and main checkout.
ROOT=/tmp/autobugfix-real-e2e
rm -rf "$ROOT"
mkdir -p "$ROOT"

git init --bare "$ROOT/toy-remote.git"
git init -b main "$ROOT/toy-seed"
git -C "$ROOT/toy-seed" config user.email toy@example.com
git -C "$ROOT/toy-seed" config user.name "Toy User"

cat > "$ROOT/toy-seed/calc.py" <<'PY'
def add(a, b):
    return a + b + 1
PY

cat > "$ROOT/toy-seed/test_calc.py" <<'PY'
import unittest

from calc import add


class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)


if __name__ == "__main__":
    unittest.main()
PY

git -C "$ROOT/toy-seed" add .
git -C "$ROOT/toy-seed" commit -m "base bug"
git -C "$ROOT/toy-seed" remote add origin "$ROOT/toy-remote.git"
git -C "$ROOT/toy-seed" push -u origin main
git clone "$ROOT/toy-remote.git" "$ROOT/toy-main"
git -C "$ROOT/toy-main" config user.email toy@example.com
git -C "$ROOT/toy-main" config user.name "Toy User"

Verify the target repo really fails:
cd "$ROOT/toy-main"
python3 -m unittest discover

Expected: test fails because add(1, 2) returns 4.
Configure Autobugfix
From the control project root, create .autobugfix/config.yaml:
task_root: .autobugfix/tasks
scheduler:
  default_max_concurrent: 1
  lock_timeout_seconds: 7200
  max_auto_iterations: 2
  codex_timeout_seconds: 500
  writer_timeout_seconds: 500
  evaluator_timeout_seconds: 300
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
  toy_repo:
    main_checkout: /tmp/autobugfix-real-e2e/toy-main
    remote: origin
    main_branch: main
    # Omit worktree_root once to verify the default:
    # .autobugfix/worktrees/toy_repo
    branch_template: fix/{date}_oncall_{slug}
    test_commands:
      targeted: python3 -m unittest discover
      full: python3 -m unittest discover
    ppe:
      enabled: false
      command_template: null

Run:
uv run autobugfix doctor

Expected:
repo toy_repo is printed.
worktree_root resolves under .autobugfix/worktrees/toy_repo.
No private hardcoded repo is printed.
Create And Run A Real Task
printf '%s\n' \
  'Bug: calc.add(1, 2) returns 4 instead of 3. Fix the smallest possible code path and verify with python3 -m unittest discover.' \
  | uv run autobugfix create --repo toy_repo --title "fix toy add off by one" --from-stdin

Capture the task id, then inspect:
uv run autobugfix inspect <task-id>
git -C /tmp/autobugfix-real-e2e/toy-main worktree list

Expected:
task state is ready;
repo is toy_repo;
branch follows fix/{date}_oncall_{slug};
worktree lives under .autobugfix/worktrees/toy_repo/<task-id>;
target repo main checkout remains clean.
Run the real execution loop:
uv run autobugfix run <task-id>

Expected:
production path uses the real Codex SDK writer/evaluator;
writer changes calc.py;
verifier runs python3 -m unittest discover;
evaluator returns pass;
task state becomes waiting_human_ppe_approval;
block_reason is empty;
artifacts exist:
artifacts/diff.patch
artifacts/test-result.md
artifacts/ppe-brief.md
writer/evaluator raw logs under logs/.
Check the diff:
git -C .autobugfix/worktrees/toy_repo/<task-id> diff origin/main -- calc.py

Expected diff:
-    return a + b + 1
+    return a + b

Gate, Archive, And Memory
uv run autobugfix gate <task-id> accepted
uv run autobugfix archive <task-id> --result accepted

uv run autobugfix memory init
uv run autobugfix memory collect <task-id>
uv run autobugfix memory digest <task-id>
uv run autobugfix memory lint
uv run autobugfix memory maintain <task-id>
uv run autobugfix memory status

Expected:
archive path exists under .autobugfix/archive/accepted/<task-id>;
raw packet exists under .autobugfix-memory/raw/tasks/<task-id>;
digest exists under .autobugfix-memory/digests/tasks/<task-id>.md;
lint passes;
maintainer writes a proposal or no_change record;
memory does not mutate execution task state.
Dataset And Eval
Commit the toy fix worktree so it can be an oracle:
git -C .autobugfix/worktrees/toy_repo/<task-id> add calc.py
git -C .autobugfix/worktrees/toy_repo/<task-id> commit -m "fix toy add off by one"

Build raw dataset:
uv run autobugfix dataset build-raw \
  --repo toy_repo \
  --base-ref origin/main \
  --out "$ROOT/raw_commit_pairs.jsonl"

Convert the raw row into an eval problem JSONL row with fields:
raw_id
repo
branch
worktree_path
base_commit
final_commit
task_kind
problem_statement
agent_prompt
expected_behavior
change_summary
evidence
confidence

Run eval:
uv run autobugfix eval run \
  --dataset "$ROOT/problem_prompts.jsonl" \
  --case fix-toy-add-off-by-one \
  --out "$ROOT/eval-runs" \
  --run-id toy-e2e \
  --model-mode fake \
  --test-command 'python3 -m unittest discover' \
  --codex-timeout-seconds 500 \
  --writer-timeout-seconds 500 \
  --evaluator-timeout-seconds 300

--model-mode fake is allowed here only for eval scorer cost control. The eval case must still call the real execution loop and real Codex writer/evaluator.
Expected:
eval creates an isolated repo/control root;
setup contains "repo": "toy_repo";
generated diff is non-empty;
generated diff equals oracle diff;
report decision is pass;
run summary has no failures.
Final Verification
From the control project root:
uv run pytest -q
uv run python -m compileall -q src tests scripts
git diff --check
uv run python <path-to-skill-validator>/quick_validate.py .agents/skills/oncall-bugfix

Skill validation is optional only if the validator is unavailable. All other checks are mandatory.
Failure Interpretation
Codex auth/state errors are environment failures, not a reason to replace the
production backend with mocks.
Target repo dependency failures should be recorded in test-result.md, not
hidden.
A successful rerun must clear stale block_reason.
If generated eval diff is empty, the eval did not truly exercise execution.