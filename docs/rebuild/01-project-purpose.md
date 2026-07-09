Project Purpose
Product Definition
Autobugfix is a local, Git-disciplined, observable bugfix operating system for on-call work. It is not the application being fixed. It is the control project that receives bug context, creates controlled task state, creates a target repository worktree, runs AI writer/evaluator roles, records evidence, and keeps human approval gates explicit.
The target repository is configurable. A rebuilt project must work with any Git repository declared in .autobugfix/config.yaml; it must not hardcode an internal repo name, local username, or absolute worktree root.
Core User Scenario
A human has an on-call bug report with evidence such as:
task description;
logs;
browser screenshots;
browser/API responses;
expected behavior;
follow-up feedback after PPE or local testing.
The human gives this context to the control system. The system creates a task, prepares a dedicated worktree, lets the writer produce the smallest code/test diff, runs the configured verifier, asks an evaluator to review the diff, then waits for human approval, rework feedback, PPE approval, acceptance, or archive.
Four Systems
Execution Loop
Purpose: turn one bug report into one controlled candidate fix.
task -> context -> worktree -> writer -> verifier -> evaluator -> feedback -> human gate -> archive

Execution owns:
.autobugfix/tasks/**;
.autobugfix/archive/**;
task state transitions;
Git worktree creation and branch naming;
writer/evaluator iteration;
verifier command execution;
PPE gate and deploy command;
raw logs, run summaries, artifacts, and events.
The execution loop is the only system allowed to mutate task state.
Memory Loop
Purpose: compile accepted task evidence into reviewed long-term context.
task evidence -> raw packet -> digest -> proposal patch -> approval -> active memory

Memory owns:
.autobugfix-memory/raw/**;
.autobugfix-memory/digests/**;
.autobugfix-memory/proposals/**;
.autobugfix-memory/rejected/**;
.autobugfix-memory/active/**;
.autobugfix-memory/skills/approved/**.
Memory may read execution evidence. It must not mutate task state, task locks, events, gates, branches, or target worktrees. Stable memory changes require an explicit approval command.
Evaluation Loop
Purpose: measure whether changes to Autobugfix improve bugfix outcomes.
dataset case -> isolated execution -> generated diff -> oracle diff -> scorers -> diagnosis

Eval owns:
.autobugfix-evals/** or the configured eval output directory;
run manifests;
case artifacts;
generated/oracle diffs;
scores;
diagnosis and improvement packages.
Eval may call the real execution loop inside an isolated case directory. Eval must not approve PPE, accept/archive tasks, mutate memory proposals, or become a second task state machine.
Operator Loop
Purpose: guide system improvement using evidence from real runs.
The operator is usually the current supervising Codex session or a human/AI pair. It runs cases, inspects artifacts, diagnoses system issues, and proposes code or skill changes on a non-main branch.
Operator must not:
auto-merge into main;
auto-approve memory proposals;
auto-approve PPE;
overfit to one case without evidence.
Non-Negotiable Product Constraints
The target repository main checkout is protected.
All code fixes happen in task worktrees.
Branch names and worktree paths are owned by deterministic code.
The LLM writer may edit only the task worktree.
The evaluator is read-only.
The verifier is a configured shell command owned by the target repo profile.
Humans own PPE approval, final acceptance, and memory approval.
Runtime evidence must remain observable through CLI/UI/projection surfaces.
Unit tests may use fakes, but acceptance must run the real CLI paths.