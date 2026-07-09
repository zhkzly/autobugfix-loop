# Operator Governance Policy

> Code-spec for the executable operator permission gate.

---

## Scenario: Operator Permission Gate

### 1. Scope / Trigger

- Trigger: Any change that lets the operator diagnose, patch, review, approve,
  or validate Autobugfix itself.
- Applies to operator CLI commands, `.autobugfix/operator/**` records,
  `src/autobugfix/operator/**`, operator skills, policy validation scripts, and
  governance tests.
- This gate exists to preserve the Autobugfix constitution: execution fixes real
  target repos in worktrees, memory compiles evidence into reviewed skills,
  eval runs reproducible harnesses around real execution, and operator improves
  Autobugfix without privately changing those boundaries.

### 2. Signatures

- CLI:
  - `autobugfix operator triage --summary ... --suspected-layer <layer>`
  - `autobugfix operator request --primary-layer <layer> --risk <risk>`
  - `autobugfix operator review <request-id> --kind agent|human|script|operator --decision approve|request_changes|require_human|reject`
  - `autobugfix operator preflight --request-id <id>`
  - `autobugfix operator validate --request-id <id> [--run-validation-commands]`
  - `autobugfix operator baseline record|compare ...`
- Script:
  - `uv run python scripts/validate_operator_policy.py --request-id <id>`
- Python:
  - `OperatorStore(project_root)`
  - `evaluate_policy(project_root, request, reviews, base_ref="HEAD") -> PolicyDecision`
  - `validate_operator_request(project_root, request_id, ...) -> dict`

### 3. Contracts

- Machine-readable constitution lives at
  `src/autobugfix/operator/constitution.yaml`.
- Runtime operator records live under `.autobugfix/operator/**` and remain
  gitignored runtime state.
- A triage record may be uncertain and layer-level:
  - `triage_id`
  - `summary`
  - `suspected_layers`
  - `confidence`
  - `evidence`
  - `next_actions`
- A request record owns patch scope:
  - `request_id`
  - `summary`
  - `primary_layer`
  - `secondary_layers`
  - `risk`
  - `triage_id`
  - `evidence`
  - `validation_commands`
  - `performance_baseline`
- Policy validation compares `git diff --name-only <base-ref>` plus untracked
  files against declared layers.
- Medium-risk or cross-layer requests require an approved review.
- High-risk, architecture-risk, and protected-path changes require a human
  approval review.
- The policy validator must also run static constitution checks such as default
  role sandbox/approval modes, runtime gitignore patterns, production SDK
  marker presence, and forbidden eval approval calls.

### 4. Validation & Error Matrix

- Current branch is `main` or `master` -> validation fails.
- Changed file has no layer and is not a common path -> validation fails.
- Changed file belongs only to undeclared layers -> validation fails.
- Cross-layer or medium-risk request has no approved review -> validation
  fails.
- High-risk, architecture-risk, or protected-path request lacks human approval
  -> validation fails.
- Static invariant fails -> validation fails.
- Validation command exits non-zero when `--run-validation-commands` is used ->
  validation fails.

### 5. Good/Base/Bad Cases

- Good: Eval harness change declares `primary_layer=eval`, records eval report
  evidence, runs eval tests, and passes policy validation on a non-main branch.
- Good: Eval plus config propagation change declares `primary_layer=eval` and
  `secondary_layers=[shared_runtime]`, then records an approved scope review.
- Good: Constitution or scoring semantics change has `risk=architecture` and a
  human approval record.
- Base: A low-risk operator skill change declares `primary_layer=operator` and
  passes policy validation.
- Bad: Operator patches `main`.
- Bad: Operator changes memory approval policy under an eval request.
- Bad: Operator edits the constitution with only agent self-approval.
- Bad: Operator optimizes one benchmark case while weakening toy repo E2E or
  removing evidence/artifact retention.

### 6. Tests Required

- Unit tests for:
  - non-main declared-layer changes pass;
  - protected branch changes fail;
  - cross-layer changes require review;
  - constitution/protected path changes require human approval;
  - CLI writes triage/request/review records and validates them.
- Integration/regression validation for implementation work:
  - `uv run pytest -q`
  - `uv run python -m compileall -q src tests scripts`
  - `git diff --check`
  - `uv run python scripts/validate_role_skills.py`
  - `uv run python scripts/validate_operator_policy.py --request-id <id>`
  - real toy repo E2E when loop behavior can be affected

### 7. Wrong vs Correct

#### Wrong

```text
Eval case failed, so edit writer prompts and commit directly to main.
```

This skips artifacts, scope, branch protection, layer diagnosis, and validation.

#### Correct

```text
Read eval artifacts -> write triage -> request eval scope -> review cross-layer
scope if needed -> patch on non-main branch -> run component tests and policy
validation -> compare baseline -> commit with evidence.
```

This keeps operator power inside the loop/harness constitution.
