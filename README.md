# Autobugfix

Autobugfix is a local, repo-agnostic, Git-disciplined loop-engineering and
harness-engineering control system. Given a configured target repository and a
real bug/problem, its Execution loop repeatedly drives an isolated Codex
Writer, real verifier commands, a read-only evaluator, feedback, and human
gates until the task reaches a reproducible outcome. It is not the target
application repository.

Memory precompiles accepted Execution evidence into a reviewed LLM wiki and
reusable skills. Eval reproduces real cases and measures the actual Execution
loop against tests/oracles. Operator is the meta-loop that diagnoses and
improves Autobugfix itself through governed non-main experiments. LLM agents
are bounded nodes; services, Git, deterministic checks, scorers, artifacts,
and explicit gates own control flow and truth.

## Install

```bash
uv sync --prerelease allow
uv run autobugfix doctor
```

All production Codex roles and authoritative Operator checks require an OS
process sandbox. The current adapter uses Bubblewrap (`bwrap`) on Linux/WSL;
install it before running Execution, Memory, Eval, or Operator SDK nodes. The
role process sees an empty host home, a read-only trusted Python runtime, and
only its explicit control/worktree/log/Git-metadata mounts. This requirement
cannot be disabled by project config while the trusted machine constitution
requires it.

The production runtime uses the local preview Python Codex SDK package
`openai-codex`. It does not use `codex exec` and it does not fall back to a
fake backend for `autobugfix run`. Each SDK call runs in an isolated Python
worker process so the parent service can enforce the configured timeout and
retain request/result/stdout/stderr artifacts.

With `bridge_auth: true`, each call copies only the local Codex authentication
file and minimal installation metadata into a fresh mode-0700 runtime home.
Project hooks, apps, delegation, tool network access, and inherited shell
credentials are disabled; role-controlled outputs are scanned/redacted and the
call home is recursively removed after completion. This is credential
confinement for the current preview SDK, not a separate credential broker: the
SDK process itself must still read its private auth copy. Run production roles
only on a trusted Linux/WSL host until the SDK provides brokered credentials.

## Configure A Target Repo

Create `.autobugfix/config.yaml` in this control project:

```yaml
task_root: .autobugfix/tasks
scheduler:
  default_max_concurrent: 1
  lock_timeout_seconds: 7200
  max_auto_iterations: 2
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
    # Optional app-server binary used by the Python SDK; this is not codex exec.
    codex_bin: null
    bridge_auth: true
    skill_guard: true
    strict_skill_guard: true
  roles:
    writer:
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/writer/autobugfix-writer/SKILL.md
    evaluator:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      timeout_seconds: null
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/execution/evaluator/autobugfix-evaluator/SKILL.md
    operator_supervisor:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/supervisor/autobugfix-operator-supervisor/SKILL.md
    operator_writer:
      model: null
      sandbox: workspace-write
      approval_mode: auto_review
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/writer/autobugfix-operator-writer/SKILL.md
    operator_verifier:
      model: null
      sandbox: read-only
      approval_mode: deny_all
      skill_paths:
        - .agents/role-skills/base/autobugfix-runtime-base/SKILL.md
        - .agents/role-skills/operator/verifier/autobugfix-operator-verifier/SKILL.md
worker:
  tick_interval_seconds: 5
memory_worker:
  tick_interval_seconds: 10
eval:
  model_mode: codex
operator:
  state:
    root: .autobugfix/operator-v3
    database_name: governance.sqlite3
  artifacts:
    root: .autobugfix/operator-artifacts
  worktrees:
    root: .autobugfix/operator-worktrees
    branch_template: operator/experiment/{request_id}
  retry:
    max_attempts: 5
    max_auto_retries: 2
    auto_retry_deterministic_failures: true
  verification:
    fast_profiles: [operator]
    full_profiles: [full]
    require_semantic_verifier: true
    process_sandbox: auto
    require_process_sandbox: true
    network_access: false
    runtime_venv: .venv
  experiments:
    enabled: true
    trusted_ref: origin/main
    default_profile: real-e2e
  experiment_lines:
    root: .autobugfix/operator-line-worktrees
    checkpoint_root: .autobugfix/operator-checkpoints
    active_release_root: .autobugfix/operator-active-experiments
    branch_template: experiment/{study_id}-main
    remote: origin
    update_timeout_seconds: 300
  budgets:
    allowed_waves: [3, 8, 16]
    allowed_primary_models: [gpt-5.4-mini]
    max_calls_by_wave:
      3: 30
      8: 80
      16: 160
    default_case_concurrency: 1
    max_case_concurrency: 1
    default_max_writer_attempts: 2
    default_max_operator_revisions: 3
    default_wall_time_seconds: 7200
    allow_model_fallback: false
  promotion:
    release_root: .autobugfix/releases
    active_release_link: .autobugfix/active-release
    require_pull_request: true
    require_canary: true
    auto_rollback_on_canary_failure: true
    canary_profiles: [full]
repos:
  target_repo:
    main_checkout: ../target-repo
    remote: origin
    main_branch: main
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
```

The default config contains no target repositories. Unknown repo IDs fail
before task state is written. If `worktree_root` is omitted, Autobugfix derives
`.autobugfix/worktrees/<repo-id>` under the control project root.

`codex.roles` is the primary configuration surface for production roles and
skills. The legacy `writer_model`, `evaluator_model`, and `controller_model`
fields are still accepted as compatibility inputs, but new configs should set
models, sandboxes, approval modes, timeouts, and skill paths under
`codex.roles.<role>`. Repo profiles may override allowed roles under
`repos.<repo-id>.codex.roles`.

### SWE Eval Docker Build Network

SWE-bench Verified uses the upstream official scorer to construct each local
Docker image and evaluate a patch. Its image-build network is owned by Eval,
not by the Execution Writer. Configure it under `eval.benchmarks.swe`:

```yaml
eval:
  benchmarks:
    swe:
      verified_namespace: null
      verified_build_network_mode: default
```

`default` keeps Docker's normal build network. `host` is an explicit,
host-local workaround when that bridge cannot fetch a public upstream during
official image construction. It grants host networking only to the official
Docker build, never to the Codex Writer, Raw Codex model process, or Execution
verifier. The selected mode is included in the official evaluator runtime
identity and command artifacts, so changing it requires fresh SWE
qualification before a result is comparable.

## Basic Flow

```bash
printf 'Bug report and evidence\n' \
  | uv run autobugfix create --repo target_repo --title "fix bug" --from-stdin

uv run autobugfix run <task-id>
uv run autobugfix inspect <task-id>
uv run autobugfix gate <task-id> accepted
uv run autobugfix archive <task-id> --result accepted
```

Memory and eval are separate loops:

```bash
uv run autobugfix memory init
uv run autobugfix memory collect <task-id>
uv run autobugfix memory digest <task-id>
uv run autobugfix memory maintain <task-id>
uv run autobugfix memory review <proposal-id>
uv run autobugfix memory approve <proposal-id> --note "reviewed" \
  --confirm-review-digest <digest-from-review>
# Or activate the same reviewed proposal as one reusable skill, never both:
uv run autobugfix memory approve-skill <proposal-id> \
  --skill-name preserve-verifier-evidence \
  --description "Preserve verifier evidence before accepting a repair." \
  --note "reviewed as a reusable procedure" \
  --confirm-review-digest <digest-from-review>

uv run autobugfix dataset build-raw --repo target_repo --out raw.jsonl
uv run autobugfix eval run --dataset problem_prompts.jsonl --out .autobugfix-evals/runs
```

### Defects4J Cases

All benchmark adapters follow the same measurement contract:

```text
repository@buggy-revision + issue/evidence
-> complete Execution loop
-> frozen patch and trace
-> dataset official evaluator
-> immutable score
```

Defects4J is one Case source and official evaluator; Docker provides its pinned
checkout and test environment. The Codex Writer still edits an ordinary local
task worktree. Host Java, Perl, SVN, cpanm, and Defects4J installations are
neither required nor configurable.

Build the two pinned roles from the same Dockerfile once. The materializer has
official checkout/oracle metadata; the verifier removes gold patches and
localization hints while retaining metadata required by `defects4j test`. The
verifier keeps only the `project_repos/README` bootstrap sentinel, not project
Git repositories or archives:

```bash
docker build --target materializer \
  -t autobugfix/defects4j:3.0.1 \
  -f containers/defects4j/Dockerfile .
docker build --target verifier \
  -t autobugfix/defects4j-verifier:3.0.1 \
  -f containers/defects4j/Dockerfile .
```

Then qualify and run a real case through Docker, the production Python Codex
SDK Execution loop, submission freeze, and an independent official evaluator:

```bash
uv run autobugfix eval benchmark doctor --adapter defects4j

uv run autobugfix eval benchmark preflight \
  --manifest benchmarks/defects4j-v3.0.1-pilot.yaml \
  --case d4j-jacksoncore-2

uv run autobugfix eval benchmark run-case \
  --manifest benchmarks/defects4j-v3.0.1-pilot.yaml \
  --case d4j-jacksoncore-2 \
  --out .autobugfix/eval-runs \
  --run-id h0-jacksoncore-2 \
  --model gpt-5.4-mini \
  --max-attempts 2
```

`AUTOBUGFIX_DOCKER_BIN` may select an installed Docker executable for one
process. The configured image tag is inspected first; all case commands bind
to the resulting immutable image ID. Runtime cases, receipts, SDK logs,
events, diffs, and official-test artifacts remain gitignored below
`.autobugfix/`.

Preflight may privately inspect buggy and fixed revisions to establish that the
official benchmark is runnable. Those facts remain below the trusted Eval
root. The Writer never receives fixed code, developer patches, private failure
baselines, modified-class hints, or official scores.

During Execution, the managed verifier runs only the predeclared visible
triggering tests. Its compiler/test output is legitimate bounded-loop feedback.
After Execution terminates, Eval freezes the generated patch, task/events
digests, subject SHA, and iteration count. Only then does a fresh scoring
checkout run the official full-suite evaluator. A noninterference receipt
proves that scoring did not alter the patch, task state, trace, or attempts.

Experiment 1 pre-registers 16 `evaluation` cases and measures the same frozen
H0 once per case. A failed official score is a valid unsuccessful repair, not
permission to rerun, tune H0, or feed oracle output to Writer. Existing sealed
Holdout and Study commands remain available for separate Operator treatment
studies. The formal H0 measurement uses these commands:

```bash
# No model calls: qualify all cases and freeze H0/config/roles/skills/Memory.
uv run autobugfix eval benchmark prepare-evaluation \
  --manifest benchmarks/defects4j-v3.0.1-evaluation.yaml

# Use the prepared_manifest path printed above. Repair failures are measured
# results; only a harness error makes the experiment command fail.
uv run autobugfix eval benchmark run-evaluation \
  --manifest .autobugfix/trusted-eval-cases/manifests/defects4j-v3.0.1-h0-16/<prepared>.yaml \
  --out .autobugfix/eval-runs \
  --run-id defects4j-h0-16

# Rebuild the digest-bound aggregate from immutable case artifacts without
# rerunning Execution or any official scorer.
uv run autobugfix eval benchmark report-evaluation \
  --run-dir .autobugfix/eval-runs/defects4j-h0-16
```

Both commands require a clean Autobugfix checkout. `prepare-evaluation` binds
the commit/tree, `.autobugfix/config.yaml`, resolved Writer/Evaluator roles,
skill contents, active Memory, model, attempt budget, and every qualified case
receipt. `run-evaluation` rejects any drift and writes a final
`subject-noninterference.yaml` receipt. The final `evaluation-report.yaml`
derives first-attempt success, loop rescue, SDK calls, verifier/oracle
agreement, confidence interval, runtime distribution, and per-case digests.

### Raw Codex SDK Comparator

The Raw comparator is a separate experimental treatment, not another
Execution backend. It gives the same buggy repository snapshot and visible
issue bundle to one fresh direct Codex Python SDK thread/turn, freezes the Git
patch produced by that turn, and then uses the same independent Defects4J
official scorer as H0. It deliberately receives no Autobugfix role skills,
Memory, managed verifier callback, evaluator feedback, gold patch, fixed
revision, hidden tests, or official verdict.

The treatment is a separately locked uv project at
`baselines/raw_codex_sdk`, pinned to `openai-codex==0.144.4`. The trusted Eval
service launches it in Bubblewrap with only the target worktree, visible case
bundle, isolated `CODEX_HOME`, read-only runner environment, and raw output
mount. The SDK process cannot write prepared manifests, submissions, scores,
or comparison reports. The treatment pins `ApprovalMode.deny_all`,
`Sandbox.workspace_write`, and disabled tool network access. The SDK control
connection remains available, but model-issued commands cannot request a
permission escalation or use the network to retrieve benchmark truth.

Run one already exposed development case before freezing the formal runner:

```bash
uv run autobugfix eval baseline pilot-raw-codex \
  --protocol benchmarks/defects4j-v3.0.1-raw-codex-baseline.yaml \
  --source-manifest .autobugfix/trusted-eval-cases/manifests/defects4j-v3.0.1-h0-16/<prepared-h0>.yaml \
  --case d4j-jacksoncore-2 \
  --run-id raw-codex-pilot
```

After pilot-only harness fixes, commit the runner and use a clean checkout to
freeze and run all 16 cases exactly once:

```bash
uv run autobugfix eval baseline prepare-raw-codex \
  --protocol benchmarks/defects4j-v3.0.1-raw-codex-baseline.yaml \
  --source-manifest .autobugfix/trusted-eval-cases/manifests/defects4j-v3.0.1-h0-16/<prepared-h0>.yaml \
  --h0-report .autobugfix/eval-runs/<h0-run>/evaluation-report.yaml

uv run autobugfix eval baseline run-raw-codex \
  --manifest .autobugfix/trusted-eval-cases/manifests/defects4j-v3.0.1-raw-codex-sdk/<prepared-raw>.yaml \
  --run-id raw-codex-formal-16

uv run autobugfix eval baseline report-raw-codex \
  --run-dir .autobugfix/raw-codex-baseline/formal-runs/raw-codex-formal-16 \
  --h0-report .autobugfix/eval-runs/<h0-run>/evaluation-report.yaml
```

A model timeout, empty patch, out-of-policy patch, or official rejection is a
measured failed repair. A transport, sandbox, materialization, patch-apply, or
scorer infrastructure failure invalidates the complete formal run; fix the
harness under a new code/manifest digest and restart all 16 cases. The primary
paired report contains the 13 cases not used during H0 development; the three
exposed cases and all-16 result are secondary diagnostics.

Experiment 2 has an independent Raw comparator for the ten public
SWE-bench Verified Optimization cases. It reuses only the generic locked SDK
worker; no Defects4J result, artifact, Memory, or case feedback enters the SWE
experiment. Run one real development case before committing the formal
treatment:

```bash
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline \
  run-swe-raw-development \
  --source-protocol benchmarks/swe-experiment-2.yaml \
  --treatment benchmarks/swe-experiment-2-raw-codex.yaml \
  --instance astropy__astropy-12907 \
  --run-id raw-swe-development
```

After the shared scorer and Raw runner are stable, commit the non-main Eval
branch, prepare from a clean checkout, and execute all ten cases serially:

```bash
uv run --cache-dir /tmp/uv-cache autobugfix eval baseline \
  prepare-swe-raw-codex \
  --source-protocol benchmarks/swe-experiment-2.yaml \
  --treatment benchmarks/swe-experiment-2-raw-codex.yaml

uv run --cache-dir /tmp/uv-cache autobugfix eval baseline \
  run-swe-raw-codex \
  --manifest <prepared-swe-raw-manifest> \
  --run-id raw-swe-formal-10
```

The shared official scorer starts only after Raw SDK events and the generated
patch are frozen. A failed official test is a measured outcome and cannot
start another turn. This is a system-level one-turn reference; the primary
Operator evolution comparison remains the budget-matched H_general versus H0
comparison on the sealed Holdout.

For a separate Operator treatment study, derive a binding, run the encrypted
Holdout, and import the signed aggregate through service-owned transitions:

```bash
uv run autobugfix operator study guard-binding \
  --study-id bugfix-study --kind BASELINE > /tmp/h0-binding.yaml

uv run autobugfix eval benchmark guard-run \
  --manifest .autobugfix/eval-manifests/<manifest>/manifest.yaml \
  --wave-token <opaque-token> \
  --study-binding /tmp/h0-binding.yaml \
  --run-id h0-holdout --out .autobugfix/guard-results

uv run autobugfix operator study import-guard-metric \
  --study-id bugfix-study --kind BASELINE \
  --metric .autobugfix/guard-results/h0-holdout.metric.yaml
```

The binding grants no authority. Import re-verifies the human-held Guard
secret, current Study/line/budget binding, frozen harness and constitution,
and aggregate-only metric schema before writing Operator state.

## Operator Governance

Governance V4 makes the Operator a bounded supervisor, not a state owner.
Request phases are only `REQUESTED`, `ACTIVE`, `VERIFIED`, and `CLOSED`;
Writer attempts, checks, gates, scope revisions, experiments, and promotions
are independent records in the control checkout's SQLite store. Candidate
worktrees contain no authoritative state.

The default directory name `.autobugfix/operator-v3` is retained so existing
authority databases migrate in place; its SQLite `user_version` is 4 and the
loaded machine constitution is V4. The directory name is not the active policy
version.

The control checkout is the authority plane: the service owns SQLite records,
Git experiment refs, budget usage, deterministic check output, integration
receipts, and checkpoint pointers. Candidate worktrees are data planes: Writer
may change source and tests there, but files it creates cannot authorize scope,
claim a check passed, consume or expand a budget, advance an experiment line,
or activate a release. A Git hook is only an accident guard; service checks and
trusted-base CI remain the merge authority.

```bash
uv run autobugfix operator guide

uv run autobugfix operator triage \
  --triage-id triage-eval-diff \
  --summary "eval harness did not preserve generated diff" \
  --suspected-layer eval \
  --confidence medium \
  --evidence .autobugfix-evals/run/case/report.yaml

uv run autobugfix operator baseline record \
  --name real-e2e \
  --profile real-e2e

# Human/CI authority step: publish only the protected baseline receipt.
git add .autobugfix-baselines/real-e2e.yaml
git commit -m "Record trusted real-e2e baseline"

uv run autobugfix operator request \
  --request-id op-eval-diff \
  --triage-id triage-eval-diff \
  --summary "fix eval artifact capture" \
  --primary-layer eval \
  --planned-path 'src/autobugfix/eval/**' \
  --risk low \
  --validation-profile eval \
  --performance-baseline real-e2e

uv run autobugfix operator preflight --request-id op-eval-diff
uv run autobugfix operator start --request-id op-eval-diff
uv run autobugfix operator writer-start --request-id op-eval-diff
uv run autobugfix operator verify --request-id op-eval-diff --mode fast
uv run autobugfix operator candidate-commit \
  --request-id op-eval-diff --message "Repair eval artifact capture"
uv run autobugfix operator experiment-run \
  --request-id op-eval-diff --profile real-e2e
uv run autobugfix operator verify --request-id op-eval-diff --mode full
uv run autobugfix operator audit --request-id op-eval-diff
uv run autobugfix operator promotion-prepare --request-id op-eval-diff
```

Named experiment lines wrap that same Request lifecycle with frozen study,
budget, integration, and checkpoint authority. The manifest, success contract,
and metric receipts are produced by the trusted benchmark adapter/Guard; they
are not candidate-authored score claims.

Study creation copies the visible Optimization manifest and active Memory into
separate read-only H0 snapshots under the trusted checkpoint root. Operator
projections expose their digests but not snapshot paths. Sealed Holdout
manifests, gold data, and case-level results must remain outside all Operator
roots under an external Guard; only an aggregate metric receipt may enter the
Operator store after final evaluation.

```bash
uv run autobugfix operator study create \
  --study-id defects4j-bugfix \
  --cohort-id autobugfix-h0-v1 \
  --purpose "Improve the bugfix harness" \
  --manifest .autobugfix/benchmark-manifests/defects4j-bugfix.yaml \
  --success-contract .autobugfix/benchmark-manifests/bugfix-success.yaml \
  --base-ref <frozen-h0-sha> \
  --model gpt-5.4-mini \
  --target-checkpoint H_bug

uv run autobugfix operator line init \
  --study-id defects4j-bugfix \
  --metric-receipt-id <registered-h0-metric-id>

uv run autobugfix operator budget request \
  --study-id defects4j-bugfix --wave 3 \
  --case <case-1> --case <case-2> --case <case-3> \
  --reason "Run the first Optimization wave"

uv run autobugfix operator budget approve \
  --budget-request-id <budget-request-id> \
  --approver <human-identity> \
  --confirm-request-digest <displayed-request-digest>

uv run autobugfix operator request \
  --triage-id <triage-id> \
  --summary "repair diagnosed harness layer" \
  --primary-layer execution \
  --planned-path src/autobugfix/runner.py \
  --risk medium \
  --validation-profile full \
  --experiment-line defects4j-bugfix \
  --budget-grant <grant-id>

# Run start/Writer/check/commit/experiment/full-check as above.
uv run autobugfix operator integrate \
  --request-id <verified-request-id> --grant-id <grant-id>

uv run autobugfix operator checkpoint create \
  --line-id defects4j-bugfix --name H_bug \
  --metric-receipt-id <registered-study-metric-id>

uv run autobugfix operator line rollback \
  --line-id defects4j-bugfix --checkpoint-id defects4j-bugfix-H0 \
  --reason "validated regression against the frozen baseline"
```

`budget approve` refuses non-interactive stdin and asks the human to type
`APPROVE <request-digest>` exactly. Supplying an `--approver` label from an
agent or CI process is not sufficient authority.

The trusted benchmark adapter registers H0 and candidate receipts through the
Guard service API, which copies them into content-addressed storage and writes
immutable `StudyMetricRecord` rows. There is intentionally no generic Operator
CLI for importing a score claim. Line/checkpoint commands accept only those
registered IDs and recheck the artifact hash and every frozen binding.

Budget waves are exactly `3 -> 8 -> 16`, run at case concurrency one, and use
only `gpt-5.4-mini`; quota, expiry, or model mismatch stops before the SDK call.
Integration reruns trusted policy and validation in a separate worktree and
advances the Git ref plus SQLite generation with compare-and-swap. Rollback
creates a new commit whose tree equals the selected checkpoint; it never resets
or force-pushes history.

The two planned experiments share only the same frozen H0 definition.
Experiment 1 is a descriptive capability measurement: 16 pre-registered
Defects4J cases run against unchanged H0, and official scoring occurs only
after each final submission is frozen. It creates no Operator treatment and no
`H_bug`. Experiment 2 independently starts from the original H0 and uses 10
visible SWE-bench Verified cases plus 6 sealed SWE-bench-Live cases to test
whether Operator can produce `H_general`. Experiment 2 must not inherit
Experiment 1 outcomes or case-level feedback. These are experiment protocols,
not changes to the four-loop project constitution.

`operator advance` performs one legal scheduler action at a time: start,
Writer, fast check, candidate commit, matching experiment, full check, or stop
for intervention.
Writer has only the filtered read surface `autobugfix writer
task|context|scope|feedback|check-result`; it cannot call Operator mutations.

Cross-layer scope expansion is versioned. An earlier approval does not grant a
new revision:

```bash
uv run autobugfix operator scope-change \
  --request-id op-eval-diff \
  --add-layer shared_runtime \
  --add-path src/autobugfix/config.py \
  --risk medium \
  --reason "isolated config generation is in the failing data flow"

uv run autobugfix operator review op-eval-diff \
  --reviewer scope-reviewer \
  --decision approve \
  --allowed-layer eval \
  --allowed-layer shared_runtime \
  --scope-revision-id <revision-id> \
  --reason "cross-layer diagnosis and path scope verified"

uv run autobugfix operator scope-activate \
  --request-id op-eval-diff --revision-id <revision-id>
```

Constitutional changes require a real OpenSSH signature or an allowlisted
GitHub review. A local `kind: human` label is not accepted. Add
`--scope-revision-id` when approving a proposed revision.

```bash
uv run autobugfix operator approval-payload op-architecture \
  --stage scope \
  --approver human-owner \
  --reason "authorize governance change" \
  --out /tmp/op-architecture.json

ssh-keygen -Y sign -f ~/.ssh/operator_signing_key \
  -n autobugfix-operator /tmp/op-architecture.json

uv run autobugfix operator approve-signed op-architecture \
  --payload /tmp/op-architecture.json \
  --signature /tmp/op-architecture.json.sig \
  --allowed-signers ~/.config/autobugfix/allowed_signers
```

Promotion is separate from verification:

```bash
uv run autobugfix operator promotion-open-pr \
  --promotion-id <promotion-id> \
  --title "Repair eval artifact capture" \
  --body "Autobugfix-Request-Digest: <request-digest>"

uv run autobugfix operator promotion-observe-merge \
  --promotion-id <promotion-id> --repository owner/repository
uv run autobugfix operator promotion-canary --promotion-id <promotion-id>
```

On a regression, `promotion-rollback` restores the exact last-known-good
active-release link. `promotion-revert-pr` then creates a normal Git revert
branch/PR; it never resets or force-pushes shared main.

GitHub approval review bodies must include
`Autobugfix-Request-Digest: <request-digest>`. The committed
`.autobugfix-governance/<request-id>/bundle.yaml` is advisory transport only.
The GitHub check loads code/policy from the trusted base, recalculates the
actual diff, re-reads reviews, and reruns profiles in Bubblewrap.

The trusted script supports runtime records and exported bundles:

```bash
uv run python scripts/validate_operator_policy.py \
  --request-id op-eval-diff \
  --candidate-root .autobugfix/operator-worktrees/op-eval-diff \
  --trusted-ref origin/main
```

Regression baselines are immutable contracts under `.autobugfix-baselines/`.
The trusted service captures them by running a configured experiment profile
on the frozen trusted ref. Candidate metrics come only from a Guard-observed
experiment bound to the current HEAD and patch digest; CLI callers cannot type
numeric metrics into an authority path. The captured receipt must be reviewed
and committed to the trusted base before creating a request, so remote CI can
read it without trusting candidate files. Its measured SHA may precede the
request base only by baseline-metadata commits; any intervening code/skill/config
change makes it stale:

```bash
uv run autobugfix operator baseline record \
  --name real-e2e \
  --profile real-e2e

# Performed by a human or trusted CI publisher, never Operator Writer.
git add .autobugfix-baselines/real-e2e.yaml
git commit -m "Record trusted real-e2e baseline"

uv run autobugfix operator experiment-run \
  --request-id op-eval-diff \
  --profile real-e2e

uv run autobugfix operator baseline compare \
  --name real-e2e \
  --request-id op-eval-diff
```

Install repository-specific reviewer/public-key allowlists, CODEOWNERS, and
optional branch protection with `scripts/install_operator_governance.py`.
Codex hooks block obvious direct merge/protected push/state-write commands,
but hooks are not authority; Service and trusted-base CI are the final gates.
These project hooks apply to the supervising main Codex session in the
Autobugfix source checkout. Isolated SDK roles explicitly disable hooks and are
constrained by their role sandbox, worktree, service, and verifier instead.

The mandatory docs acceptance uses a pinned ItsDangerous upstream commit and a
reproducible fault injection to cover Execution, Memory, and Eval with real
Codex roles. It is deliberately reported as a real-repository E2E, not as an
official SWE-bench score:

```bash
uv run python scripts/real_repository_acceptance.py --model gpt-5.4-mini
```

The script clones the public upstream commit, commits a reproducible regression
to a local fixture remote, verifies that Execution changes only its task
worktree, compiles accepted evidence into a pending Memory proposal, and runs a
second isolated Eval execution whose generated patch must pass an independent
real pytest oracle. The committed oracle diff is retained only for diagnosis;
an equivalent alternative implementation is valid. The configured target main
checkout must remain byte-for-byte clean.
Official SWE-bench Verified scoring still requires its container harness; a
local run without that harness must not be labeled as a benchmark result.

`scripts/real_toy_acceptance.py` remains an optional fast development fixture;
it is not a release or promotion acceptance gate.

Runtime state under `.autobugfix/`, generated memory evidence, eval runs, and
UI screenshots are gitignored.
