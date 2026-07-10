# Operator Governance V3 Design

## Architecture

```text
trusted main/package
  constitution + config + skills + Transition Guard
                  |
                  v
external control plane
  OperatorGovernanceService + SQLite event store + artifact registry
        |                                  |
        | typed Operator requests          | read-only WriterView
        v                                  v
Operator Supervisor                    Operator Writer
read-only diagnosis                 candidate worktree-write only
        |                                  |
        +-------------- feedback ----------+
                          |
                  deterministic checks
                          |
                  semantic verifier (RO)
                          |
                   Request VERIFIED
                          |
                Promotion + trusted CI
                          |
              canary -> activate/rollback
```

The host process is the control plane. Agents do not own state. Candidate
worktrees are untrusted execution planes and contain no authoritative state.

## Aggregate Model

`OperatorRequest` is the root aggregate with exactly four phases:
`REQUESTED`, `ACTIVE`, `VERIFIED`, `CLOSED`. Child records are independent:

- `WriterRun`: queued/running/completed/failed/timed_out/cancelled.
- `CheckRun`: pending/running/passed/failed/error/cancelled.
- `GateSnapshot`: scope/tests/semantic/approval/merge pass/fail/pending/stale.
- `ScopeRevision`: proposed/approved/rejected/superseded.
- `Experiment`: created/running/completed/failed/closed.
- `Promotion`: prepared/pr_open/merged/canary/active/failed/rolled_back.

Events are the source of truth. Projection rows are rebuildable caches. No API
accepts an arbitrary target state.

## Transition Contracts

- `request`: validate evidence and trusted Git identity; create REQUESTED.
- `start`: validate authority and workspace contract; create ACTIVE.
- `writer_start/retry`: enforce ACTIVE, concurrency, budget, and role contract;
  launch one SDK run without changing Request phase.
- `verify`: derive Git snapshot; run trusted profiles; produce CheckRun and
  feedback; transition ACTIVE to VERIFIED only when every required gate passes.
- `scope_change`: append revision and invalidate checks; activate only after
  risk-derived authority succeeds.
- `promote`: require current VERIFIED patch and create Promotion PREPARED.
- `merge_observed`: accept only externally verified PR/head/merge facts.
- `activate`: require post-merge canary and compatible state schema.
- `rollback`: restore recorded previous active release and create revert intent.
- `close`: record merged/abandoned/rejected/superseded/rolled_back outcome.

Failures generally retain the Request phase and write a typed CheckRun,
GateSnapshot, and FeedbackPacket. This preserves retry without state explosion.

## Storage

The configured state root defaults to an XDG state path. SQLite uses WAL,
foreign keys, explicit transactions, and append-only events. Runtime tables
store typed projections and references. Large artifacts are content-addressed
under a separate root. Every artifact has producer and trust class metadata.

Candidate-produced artifacts are advisory. Host raw SDK captures, Git facts,
verifier process output, external approvals, and GitHub facts are authoritative
for their declared purpose. The store path is never included in WriterView.

## Agent Surfaces

Operator Supervisor receives project constitution, request projection, latest
gate snapshot, evidence references, and allowed transition actions. It can call
typed Operator tools only.

Operator Writer receives the task, evidence excerpts, active scope, changed
path constraints, latest feedback, and test artifacts through read-only views.
Its filesystem write root is the candidate worktree. It cannot invoke Operator
mutation, approval, promotion, or state APIs.

Semantic Verifier receives trusted diff/test evidence and has a read-only
candidate cwd. Its report is advisory until the Guard validates its binding.

## Configuration

Configuration adds `operator.state`, `operator.artifacts`, `operator.worktrees`,
`operator.retry`, `operator.verification`, `operator.experiments`,
`operator.promotion`, and three Codex roles. Trusted policy determines minimum
requirements; project config may tighten them but cannot weaken them.

## Merge And Rollback

The candidate transports a promotion manifest/receipt, but trusted CI derives
the actual patch and reruns required checks. Main remains the only trust root.
Merge creates an immutable candidate release; canary activation is a separate
step. Rollback switches to the recorded last-known-good release before opening
a Git revert PR. State events are never rewound.

## Migration

Governance v2 records remain readable as legacy audit evidence. V3 starts a new
SQLite authority namespace and does not treat v2 approvals or projections as
current authority. Existing CLI names may remain as compatibility aliases only
when they preserve V3 transition semantics.

## Acceptance Findings

- Hook policy is deliberately narrow: artifact/state reads are allowed, while
  obvious direct mutation, direct merge, protected push, and force push are
  denied. The service and remote check remain the actual authority boundary.
- Project hooks belong only to the external `operator_host` main session.
  Execution, Memory, Eval, and bounded Operator SDK roles use isolated
  `CODEX_HOME` runtimes with hooks disabled; their services and harnesses own
  enforcement.
- Semantic acceptance uses behavior contracts rather than exact patch text so
  a correct stronger implementation is not scored as a failure.
- Model-backed acceptance sets the temporary config model explicitly without
  changing the production `runtime-default` setting.
- The real-repository smoke pins an ItsDangerous upstream commit, injects and
  commits a reproducible regression to a local fixture remote, and verifies the
  target main checkout remains unchanged. It is not labeled SWE-bench because
  the official container harness is unavailable in this environment.
