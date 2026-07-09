# Operator Governance Policy

> Executable contract for constraining the Operator as a bounded Autobugfix
> execution node.

---

## Scenario: Trusted Operator Permission Gate

### 1. Scope / Trigger

- Trigger: Any Operator-created change to Autobugfix code, config, skills,
  tests, validation, baselines, workflow, or constitution.
- Execution continues to own target-repo task state, Memory owns reviewed
  knowledge state, and Eval owns benchmark run state. Operator governance owns
  only diagnosis, authorization, workspaces, validation, regression, and audit.
- Candidate branches are untrusted. Merge authority must use policy and code
  from a trusted base ref or explicit trusted external file.

### 2. Signatures

- CLI lifecycle:
  - `autobugfix operator triage --evidence ...`
  - `autobugfix operator request --triage-id ... --primary-layer ...`
  - `autobugfix operator review <id> --reviewer ... --decision ...`
  - `autobugfix operator approval-payload <id> --stage scope|merge ...`
  - `autobugfix operator approve-signed <id> --payload ... --signature ...`
  - `autobugfix operator approve-github <id> --repository ... --pull-request ... --review-id ...`
  - `autobugfix operator preflight --request-id ...`
  - `autobugfix operator workspace-create --request-id ...`
  - `autobugfix operator postflight --request-id ...`
  - `autobugfix operator validate --request-id ... --metric key=value`
  - `autobugfix operator finalize --request-id ...`
  - `autobugfix operator export-bundle --request-id ...`
- Trusted scripts:
  - `scripts/validate_operator_policy.py --bundle|--request-id ...`
  - `scripts/validate_operator_pr.py --trusted-root ... --candidate-root ...`
- Python owners:
  - `OperatorGovernanceService`
  - `OperatorStore`
  - `project_request(events)`
  - `evaluate_policy(candidate_root, request, approvals, constitution=...)`
  - `validate_bundle(...)`

### 3. Contracts

- Triage requires evidence and has a canonical SHA-256 digest.
- Request is immutable and freezes `triage_digest`, `base_sha`, patch branch,
  layer scope, validation profiles, expiry, and request digest.
- Store writes immutable YAML records with exclusive creation. Request events
  are append-only JSONL with a verified SHA-256 hash chain.
- Permission classes:
  - `layer_local`: automatic after preflight;
  - `cross_layer`: independent reviewer, where reviewer != creator;
  - `constitutional`: OpenSSH-signed human scope/merge approval or allowlisted
    GitHub approved review.
- Candidate risk is computed from trusted path rules. Requested risk can raise
  but never lower computed risk.
- Postflight compares frozen `base_sha` with candidate HEAD and includes
  committed, staged, unstaged, and untracked files.
- Operator patching occurs in a real request-specific Git worktree under
  `.autobugfix/operator/worktrees`.
- Validation profiles come from trusted constitution argv arrays and run with
  `shell=False`. Request-provided shell commands are forbidden.
- Baselines under `.autobugfix-baselines/**` are versioned trusted contracts;
  runtime measurements cannot overwrite them.
- Runtime records remain in ignored `.autobugfix/operator/**`. PR authority is
  exported to `.autobugfix-governance/<request-id>/bundle.yaml`; trusted CI
  rechecks the bundle and reruns validation profiles.
- Bundle metadata is excluded from code patch digest to avoid circular signing,
  but its schema and digest are validated.

Environment/configuration:

- `AUTOBUGFIX_OPERATOR_ALLOWED_SIGNERS`: optional local OpenSSH allowed-signers
  path.
- Trusted CI reads `.github/autobugfix-allowed-signers` from the base checkout.
- `approval.github_allowed_reviewers` is configured in trusted constitution.

### 4. Validation & Error Matrix

- Candidate loads its own policy for merge authority -> reject; use trusted
  base/file.
- Missing triage evidence -> request creation fails.
- Existing request id -> immutable-write failure.
- Event hash/previous hash mismatch -> projection fails.
- Candidate branch differs from frozen branch -> policy failure.
- Frozen base is not an ancestor of HEAD -> policy failure.
- Changed file is unclassified or outside declared layers -> policy failure.
- Protected file without verified human scope approval -> policy failure.
- Signed payload fields differ from approval record -> signature proof failure.
- GitHub review belongs to another repository/PR/commit -> proof failure.
- Medium+ change has no trusted baseline -> validation failure.
- Required metric is missing or regresses beyond threshold -> validation
  failure.
- Validation command fails/times out -> `VALIDATION_FAILED` with durable logs.
- Patch or HEAD changes after validation -> finalize failure.
- Bootstrap policy attempts to produce merge authority -> reject.

### 5. Good/Base/Bad Cases

- Good: An Eval-only low-risk request creates a worktree, changes only Eval,
  runs trusted Eval profile, exports a bundle, and reaches `MERGE_READY`.
- Good: Eval + shared runtime uses an independent reviewer and trusted baseline.
- Good: Governance code has signed scope approval before patch and signed merge
  approval bound to final patch digest.
- Base: `preflight` may authorize a low-risk workspace, but postflight can
  elevate actual risk after reading the real diff.
- Bad: `review --kind human` or a hand-written YAML claims human authority.
- Bad: Validation defaults to `HEAD`, sees an empty post-commit diff, and passes.
- Bad: Candidate removes its own protected paths and runs candidate validator.
- Bad: PR supplies its own performance baseline.

### 6. Tests Required

- Immutable request overwrite and event-chain tamper rejection.
- Independent reviewer requirement and self-review rejection.
- Real OpenSSH signing and signed-payload field binding.
- Candidate constitution self-modification checked by trusted policy.
- Committed changes remain visible from frozen base.
- Real request-specific Git worktree creation.
- Named validation process execution, timeout/log artifacts, and no shell.
- Required/missing/regressed metric behavior against trusted baseline.
- Bundle digest/projection/policy/profile round trip.
- GitHub repository/PR/review/commit binding.
- Full project tests, compileall, diff check, role-skill validation, and isolated
  real toy acceptance.

### 7. Wrong vs Correct

#### Wrong

```text
Operator edits governance policy -> writes kind: human YAML -> commits ->
validator compares HEAD -> reports empty diff -> merge.
```

#### Correct

```text
Evidence -> immutable triage/request at base SHA -> trusted preflight -> real
Operator worktree -> complete postflight diff -> independent/signed approval ->
trusted argv validation + baseline -> bundle -> base-version GitHub gate ->
human merge.
```
