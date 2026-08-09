# Autobugfix Loop And Harness Contract

> Project-level contract for preserving Autobugfix's purpose before coding,
> debugging, eval work, or operator changes.

---

## Scenario: Autobugfix Loop And Harness Engineering Baseline

### 1. Scope / Trigger

- Trigger: Any new Autobugfix task, feature, refactor, eval integration,
  memory change, operator change, or review.
- This contract must be restated before starting a new task so the work does
  not drift into a mock CLI, a one-shot LLM prompt, or benchmark-only code.
- Autobugfix is a local, repo-agnostic, Git-controlled bugfix control system
  centered on loop engineering and harness engineering.
- The LLM agent is a bounded worker node. The control plane is the harness,
  state machine, scheduler, verifier, scorer, artifacts, memory, eval, and
  human/operator gates.

### 2. Signatures

- Execution entrypoints:
  - `AutobugfixService.create_task(repo_id, title, body) -> TaskRecord`
  - `AutobugfixService.add_context(task_id, kind, content) -> Path`
  - `AutobugfixService.run_task(task_id) -> TaskRecord`
  - `TaskRunner.run(task_id) -> TaskRecord`
- Memory entrypoints:
  - `MemoryService.collect(task_id) -> Path`
  - `MemoryService.digest(task_id) -> Path`
  - `MemoryService.maintain(task_id) -> Path`
  - `MemoryService.approve(proposal_id, note, confirm_review_digest) -> Path`
  - `MemoryService.approve_skill(proposal_id, skill_name, description, note,
    confirm_review_digest) -> Path`
- Eval entrypoints:
  - `run_eval(project_root, dataset, out, ...) -> Path`
  - `score_path(path) -> Path`
  - `EvalBenchmarkService.seal(manifest_path) -> projection`
  - `EvalBenchmarkService.run_case(...) -> report`
- Operator/adapter entrypoints:
  - CLI commands must call services or projections.
  - Gradio/UI/controller code must not mutate task, memory, branch, or eval
    state directly.

### 3. Contracts

- Loop engineering means turning recurring engineering work into a scheduled,
  observable, reproducible, automatically executable, feedback-driven control
  loop.
- A loop must define:
  - trigger or cadence;
  - goal and machine-checkable success contract;
  - durable state and memory;
  - execution nodes such as LLM workers, shell commands, test runners, static
    checkers, scorers, and human gates;
  - verification ladder favoring deterministic checks before LLM judgment;
  - logs, events, artifacts, traces, and diagnosis;
  - terminal states such as success, failure, blocked, retry, human review, or
    archive;
  - feedback path into memory, eval, or operator improvement.
- Harness engineering means designing the execution wrapper that lets an agent
  act in a real environment while remaining isolated, constrained, observable,
  and reproducible.
- A harness must define:
  - target repo, base commit, branch, and worktree;
  - injected config and skills;
  - tool and permission boundaries;
  - verifier commands and static checks;
  - raw logs, events, artifacts, diffs, and traces;
  - scorer/oracle behavior;
  - timeout, recovery, and escalation behavior.
- Execution loop purpose:
  - Given a configured target repo, bug/problem, and evidence/context, produce
    a real candidate fix in an isolated task worktree.
  - Writer may edit only the task worktree.
  - Writer receives no writable target Git metadata. Before verification and
    every human gate, Execution revalidates the task branch/common Git identity
    and a frozen patch digest.
  - Verifier runs the target repo's configured real commands.
  - Verifier uses an independent checkout so verifier commands cannot mutate
    target-repository Git authority. Evaluator is read-only by default and
    malformed or ambiguous evaluator output fails closed.
  - Human gate owns PPE approval, acceptance, and archive.
- Memory loop purpose:
  - Convert execution evidence into precompiled memory, LLM wiki content, and
    reusable skills.
  - Memory may collect only evidence that the Execution human gate accepted,
    either in `accepted` state or an archive whose result is `accepted`.
  - Memory must not mutate execution task state, locks, gates, branches, or
    target worktrees.
  - Memory proposals require explicit approval before becoming active memory or
    approved skills.
  - A reviewed proposal has one activation target. Human approval may append it
    to active wiki memory or publish one validated approved skill, but cannot do
    both or overwrite an existing skill.
  - `memory review` exposes one immutable review digest over proposal identity,
    accepted packet, deterministic digest, and patch. Human approval must echo
    that digest; changing any bound content invalidates the approval.
  - Memory authority reads and writes use descriptor-relative no-follow I/O and
    reject symlinks, non-regular files, and traversal. Wiki approval, skill
    activation, and rejection transitions are journaled and recoverable after
    a process interruption; no transition may duplicate content.
- Eval loop purpose:
  - Build reproducible benchmark/historical-case harnesses that call the real
    execution loop and measure system behavior.
  - All benchmark adapters implement one protocol: materialize a buggy
    repository and visible issue/evidence, run the complete Execution loop,
    freeze its final patch and trace, then invoke the dataset's independent
    official evaluator or scorer.
  - Execution-visible verifier results may drive bounded repair retries only
    when they are declared before the run and would be available in real use.
    Gold patches, fixed truth, hidden tests, official verdicts, and scorer
    diagnosis belong to Eval and must never become Execution feedback.
  - Eval may create isolated repos/control roots and collect generated diff,
    oracle diff/tests, reports, scores, and diagnosis.
  - Eval must not approve PPE, archive execution tasks, or approve memory.
  - Eval adapters materialize benchmark-specific repositories and oracles into
    one versioned Case contract. Tests/official harness results are the primary
    score; oracle diff equality is diagnostic only.
- Operator loop purpose:
  - Debug and improve Autobugfix itself by reading artifacts, locating the
    failing subsystem, and changing code/skills/config on non-main branches.
  - Operator should diagnose whether failures belong to repo config, task
    context, worktree isolation, writer role/skill/model, verifier command,
    evaluator, memory, eval harness, scheduler/state machine, or UI/projection.
  - Operator is the supervisor harness for the other loops, not their state
    owner. It requests transitions; a trusted Guard/service validates and
    records them.
  - The human constitution is normative soft guidance. The machine
    constitution injects that guidance into Operator roles and enforces path,
    risk, runtime-role, transition, and verification minima.
  - Project Codex hooks guard the supervising main-agent Operator session only;
    isolated SDK role runtimes disable hooks and remain safe without them.
  - Operator treatment studies use service-owned experiment lines, explicit
    Mini budget waves, trusted integration, immutable checkpoints, and
    history-preserving rollback. The generic `H_bug` and `H_general`
    checkpoint types are available for independent treatments from a frozen
    `H0`; neither treatment may inherit the other's feedback.
  - The current Experiment 1 is instead a descriptive Defects4J measurement of
    unchanged H0. Operator does not participate, create `H_bug`, or modify H0
    from official scores. Experiment 2 independently starts from the original
    H0 and may use Operator to produce `H_general` on SWE tasks.
  - Benchmark sources, Optimization/Holdout splits, and checkpoint names are
    experiment protocols. They may evolve through governed research changes
    without redefining the four loop purposes in this project constitution.
  - A Study manifest contains visible Optimization inputs only. Sealed
    Holdout case IDs, gold data, and case-level reports remain outside Operator
    storage under the Eval/Guard authority plane; Operator receives aggregate
    final metrics only.
  - Defects4J uses one Docker-based authority split into pinned materializer
    and verifier images. The verifier image retains only framework metadata
    needed by visible triggering-test commands and removes fixed truth, gold
    patches, and localization hints. Private qualification may establish a
    stable fixed baseline, but the full official evaluator may consume that
    baseline only after the generated patch and Execution trace are frozen.
  - Holdout identity and case-level evidence are authenticated encrypted Guard
    artifacts. Seal and Guard execution require a clean checkout at the
    configured trusted ref and bind its Git tree, machine constitution, and
    harness digest. Operator may import only a human-secret-signed aggregate
    whose Study/line/budget binding matches current service state.
  - A candidate Holdout binding closes its experiment line before scoring;
    aggregate pass/fail can never become feedback for another Writer attempt on
    that Study. Visible Optimization feedback reaches line-bound Writer roles
    only through Study/cohort/treatment/subject-bound evidence records.
  - The SWE Guard uses a persistent dedicated-VM Docker authority under the
    external Guard root. Socket/daemon/profile drift fails closed, benchmark
    cache content is read-only to official scorer code, and only per-run client
    state is writable.
  - Production Codex SDK nodes run in bounded Python worker processes. The
    parent service enforces timeouts and retains request/result/stdout/stderr;
    no production path may replace this with `codex exec` or an in-process
    unbounded call.
  - Production SDK processes run in Bubblewrap with an empty host home. The
    trusted source/venv/Python runtime are read-only; role cwd permissions,
    authority hiding, and linked-worktree Git metadata are explicit
    service-owned mounts. A read-only role must be read-only at both the Codex
    sandbox and OS mount layers. A workspace-write role must fail closed when
    its cwd is the trusted control root. When an allowed log or Git metadata
    child is nested below a hidden authority root, mount order is broad
    ancestor, hidden authority, then exact allowed child; sibling authority
    state must remain absent.
  - The current preview SDK requires the SDK process to read a private per-call
    auth copy. Hooks, apps, delegation, inherited credential variables, and tool
    network are disabled; output leakage scans and recursive cleanup fail
    closed. Every production launch path, including the cancellable Operator
    Writer worker, applies the same pre-publication scan to changed worktree
    files and private worker output. The scan treats the complete auth document
    and secret-bearing token/key/password fields as credentials without
    misclassifying ordinary SDK account metadata. This is credential confinement
    on a trusted Linux/WSL host, not a separate credential broker.

### 4. Validation & Error Matrix

- Missing target repo config -> fail before task execution mutates target code.
- Attempt to edit target repo main checkout -> invalid harness behavior.
- Writer outside task worktree -> invalid execution harness behavior.
- Production CLI defaulting to fake backend -> invalid production behavior.
- Missing verifier command -> invalid repo profile.
- No raw logs/events/artifacts for a run -> invalid observability.
- Official verifier output stored only in a temporary directory -> invalid
  benchmark observability.
- Memory auto-approves its own proposal -> invalid memory loop behavior.
- Memory collects an unaccepted/failed/active task -> invalid memory input.
- Eval creates a second task state machine -> invalid eval loop behavior.
- Operator changes main directly -> invalid operator loop behavior.
- Workspace-write Codex role targets the trusted control root -> fail before
  worker launch.
- Reopening one role log directory exposes sibling authority records ->
  invalid SDK filesystem isolation.
- Operator or Writer edits line/budget/checkpoint authority directly, reuses a
  grant across studies, or transfers treatment between `H_bug` and
  `H_general` -> invalid operator experiment behavior.
- Guard decrypts Holdout state before validating its trusted code identity, or
  Operator accepts an unsigned/self-authored benchmark metric -> invalid Guard
  behavior.
- New task starts without restating this baseline -> process violation.

### 5. Good/Base/Bad Cases

- Good: A SWE-bench Verified adapter creates an isolated case repo, writes
  `.autobugfix/config.yaml`, calls `AutobugfixService.create_task`, calls the
  real execution loop, then scores generated diff/tests against an oracle.
- Good: A failed verifier run records command, exit code, stdout/stderr,
  diff, task event, and terminal state so the operator can diagnose the
  failing subsystem.
- Base: A manual CLI task uses `create`, `context add`, `run`, `inspect`,
  `feedback`, `gate`, and `archive` through service methods.
- Bad: A CLI command prints fake success without creating a worktree, running
  tests, or writing artifacts.
- Bad: Eval compares only LLM prose or evaluator opinion without a generated
  diff, verifier output, scorer, or reproducible setup.

### 6. Tests Required

- Unit tests for service boundaries: adapters call services/projections, not
  task or memory files directly.
- Unit tests for role/config resolution before Codex requests.
- Integration tests for worktree isolation and verifier command execution.
- Bubblewrap tests for OS-level read-only cwd, writable task worktree, linked
  Git metadata, hidden host home, and exact-child authority reopening.
- Memory tests for collect/digest/maintain/proposal/approve separation.
- Eval tests that call real execution-loop surfaces with a fake backend only in
  tests, then write generated/oracle artifacts and reports.
- Acceptance tests that run the pinned public real-repository E2E path from
  `docs/rebuild/05-real-acceptance.md`.
- Before finishing implementation work, run:
  - `uv run pytest -q`
  - `uv run python -m compileall -q src tests scripts`
  - `git diff --check`
  - role skill validation when available
  - real repository E2E when the change can affect execution, memory, eval, or
    operator behavior

### 7. Wrong vs Correct

#### Wrong

```text
Ask an LLM to fix a bug, accept its answer, and call the task done.
```

This treats Autobugfix as a prompt wrapper and bypasses loop state, worktree
isolation, real verification, artifacts, and feedback.

#### Correct

```text
Create task -> add evidence/context -> create isolated worktree -> run writer
node -> run deterministic verifier/static checks -> preserve diff/logs/events
-> run read-only evaluator -> wait for human gate -> feed accepted evidence
into memory -> measure system behavior through eval -> let operator improve the
right subsystem on a non-main branch.
```

This keeps LLM execution inside a real loop and a real harness.

---

## Scenario: Experiment 2 Execution-Only Study Apparatus

### 1. Scope / Trigger

- Trigger: Any change to the Exp2 execution-only coordinator, direct
  workspace-only SWE dispatch, formal Exp2 adapter, or its study records.
- This apparatus is measurement infrastructure. It may select exact H0/H1
  subjects, record transitions, and dispatch the existing Eval scorer, but it
  must not evolve Memory, Eval semantics, Operator skills/policy, or the
  Operator state authority.
- The formal path is fail-closed. A missing isolation proof is a blocked
  calibration/formal run, not permission to fall back to a weaker execution
  mode.

### 2. Signatures

- `Exp2StudyPlan(...) -> Exp2StudyPlan`: requires absolute paths for the
  cohort audit, policy, apparatus receipt, and empty-Memory fixture, in
  addition to the protocol, public manifest, and H0/H1 binding paths.
- `Exp2Coordinator.initialize(plan) -> Mapping`: validates and persists the
  immutable plan and its four frozen reference records.
- `Exp2Coordinator.record_stage(stage, reports, subject_sha,
  execution_mode) -> Mapping`: accepts only terminal reports for the frozen
  stage schedule and appends a digest-chained receipt.
- `Exp2Coordinator.record_attribution(record) -> Mapping`: accepts one
  approved, allowlisted hypothesis for the awaiting H1 revision.
- `Exp2Coordinator.record_public_regression_gate(record) -> Mapping` and
  `record_sealed_aggregate(record) -> Mapping`: advance only from the public
  gate and Guard-released aggregate contracts respectively.
- `EvalBenchmarkService.run_swe_exp2_case(...) -> Mapping` and
  `run_swe_exp2_calibration_case(...) -> Mapping`: adapt the existing official
  scorer; they do not implement a second scorer or Memory loop.
- `SWECodexServer(..., execution_mode, expected_task_worktree)`: in
  `workspace_only` mode it uses an explicitly in-process SDK backend and
  rejects any request cwd other than the dedicated task worktree.

### 3. Contracts

- Every plan binds these frozen references before initialization:
  `Exp2CohortAudit`, `Exp2PolicyRecord`, `Exp2ApparatusReceipt`, and an
  `Exp2EmptyMemoryFixture`. The policy and apparatus must agree on the empty
  Memory and frozen Operator-role digests.
- Every formal report carries an
  `autobugfix-exp2-execution-receipt-v1` with:
  `execution_mode`, `direct_sdk_in_process`, `outer_bubblewrap`,
  `broker_command_digest`, `broker_result_digest`, `task_worktree_path`, and
  `workspace_only_preflight_digest` (required for direct mode).
- A workspace-only stage receipt persists direct-SDK/no-Bubblewrap flags and
  one preflight digest per case. A protected stage persists the converse
  Bubblewrap contract and no direct-mode preflight digests.
- The public projection contains terminal labels and immutable artifact
  digests only; official raw result fields, oracle material, and Guard-private
  case data remain outside the projection.
- The only formal sealed completion input is the Guard aggregate. The CLI
  exposes `record-sealed-aggregate`; the older metric-only completion path is
  not a formal Exp2 transition.
- Backend mode is explicit. An unknown backend wrapper is not considered
  in-process; workspace-only dispatch rejects it before an SDK call.
- New Exp2 tests use the existing `tests/test_swe_*.py` classification
  convention, so the frozen Operator constitution does not need an Exp2-only
  policy edit.

### 4. Validation & Error Matrix

| Condition | Required result |
|---|---|
| Any required plan reference is missing, relative, redirected, malformed, or digest-inconsistent | `Exp2ContractError`/`Exp2CoordinatorError` before ledger creation or execution |
| Workspace-only mode lacks a dedicated disposable root or preflight proof | `SWESubjectBrokerError`; no SDK call |
| Workspace-only report lacks a valid direct/no-Bubblewrap execution receipt | `Exp2CoordinatorError`; stage is not journaled |
| SDK/backend wrapper does not explicitly identify in-process mode | `SWERuntimeError` in workspace-only mode |
| Protected report claims direct SDK or omits outer Bubblewrap | `Exp2CoordinatorError` |
| Same-arm official retry or H1 frozen-input drift | `Exp2CoordinatorError`; no retry/transition |
| Public gate has invalid runs, a regression, or negative paired gain | gate fails and Holdout remains locked |
| Sealed aggregate arrives before a passing public gate or has a non-six denominator | `Exp2CoordinatorError`/`Exp2ContractError` |

### 5. Good/Base/Bad Cases

- Good: Initialize with all four frozen references, run a terminal report with
  a digest-valid execution receipt, and recover an interrupted journal by
  replaying only its append-only event.
- Base: Use protected mode for development tests; use explicit
  `workspace_only` plus a fresh disposable root only on a host whose
  preflight proves authority roots and credentials are absent/read-only.
- Bad: Omit the empty-Memory fixture because it is empty, infer direct mode
  from a CLI flag alone, or accept an opaque backend wrapper as in-process.
- Bad: Let a Guard aggregate be recorded through a metric-only shortcut or
  expose case-level Holdout labels to the coordinator.

### 6. Tests Required

- `tests/test_swe_exp2_records.py`: assert required plan references, digest
  round trips, projection redaction, stage transitions, attribution/revision
  limits, public gate, sealed aggregate, and crash recovery.
- `tests/test_swe_exp2_workspace_only.py`: assert explicit in-process mode,
  disposable-root disjointness, credential rejection, and authority-root
  rejection.
- `tests/test_subject_broker.py` and the full project suite: preserve the
  existing protected path and target-main/worktree invariants.
- Run `uv run pytest -q`, compileall, `git diff --check`, role-skill
  validation, and the read-only benchmark doctor. Real direct formal runs
  additionally require a passing Docker/Bubblewrap environment doctor and a
  supplied disposable root.

### 7. Wrong vs Correct

#### Wrong

```text
Initialize Exp2 with only a public manifest, assume an empty Memory is
implicitly fixed, and let the coordinator trust execution_mode="workspace_only"
without a signed preflight/execution receipt.
```

#### Correct

```text
Freeze cohort + policy + apparatus + empty-Memory records -> prove the
disposable direct-mode boundary -> execute the dedicated task worktree -> freeze
and officially score -> persist the execution receipt -> expose only the public
projection -> advance the append-only coordinator from typed receipts.
```
