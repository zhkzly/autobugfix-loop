# Current code gap and cost model

## Existing foundations

- `EvalCase` already models source identity, task type, issue prompt,
  attachments, repository/base revision, environment, execution command, and
  hidden oracle metadata.
- `LocalGitAdapter` creates an isolated bare remote/main checkout and uses real
  Git state.
- `run_eval` generates `.autobugfix/config.yaml`, creates a real
  `AutobugfixService` task, invokes the production Execution loop, and retains
  generated diff and oracle artifacts.
- Production Eval rejects non-Codex model modes unless a backend is explicitly
  injected by tests.
- Scoring is tests-first. Oracle-diff equality is diagnostic and does not
  determine pass/fail.
- Operator Governance V4 owns external SQLite authority, per-request
  candidate worktrees, Writer/Check child records, versioned scope,
  host-observed experiment receipts, promotion PRs, canary, active release,
  and rollback intent.
- Writer has filtered read-only CLI views and cannot mutate Operator state.

## Missing capabilities

- No named long-lived experimental integration line exists. Request base is
  currently the control checkout's `HEAD`.
- There is no compare-and-swap Guard integration from a VERIFIED candidate to
  an untrusted experimental line.
- There are no immutable `H0`, `H_bug`, or `H_general` checkpoint records,
  staged case/call budget grants, or independent-treatment lineage checks.
- The only registered Eval adapter is `local-git`; declared container
  environments fail closed.
- There is no official Defects4J, SWE-bench Verified, or SWE-bench-Live
  materializer/oracle implementation.
- Eval has no trusted sealed-case store or filtered aggregate projection that
  prevents Operator from reading holdout case-level evidence.
- Current metrics do not account for per-role Codex calls, case-execution
  budgets, or staged `3 -> 8 -> 16` authorization.

## Call model from current code

One Execution iteration always performs one Writer SDK call. If the real
verifier fails, the iteration stops without an Evaluator call. If it passes,
the iteration performs one additional Evaluator SDK call. With two permitted
iterations, one case execution therefore consumes two to four SDK calls.

One Operator system revision normally consumes:

- an optional supervisor diagnosis call;
- one operator-writer call per attempt;
- one operator-verifier semantic call during full verification.

Repeated H0/H1 and Regression executions are not new unique cases, but they
consume SDK calls and wall time. A 16-case final manifest must therefore be
selected and preflighted independently of model-budget release.

## Budget design implication

- Each experiment starts with three authorized case identities and at most 30
  primary `gpt-5.4-mini` calls.
- A trusted usage ledger records role, case, attempt, start/end time, result,
  and call count from host-observed SDK runs.
- Human budget grants expand authorized identities from 3 to 8 and then 16;
  they do not merely increase a numeric counter.
- The Guard stops before launching a call that would exceed the active grant.
- No quota error may trigger fallback to `gpt-5.3-codex-spark`.
- Full-call caps for the 8- and 16-case waves are derived from the observed
  three-case receipt and require explicit approval rather than being guessed
  in advance.
