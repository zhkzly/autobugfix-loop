# Design: trusted SWE Eval control and subject broker

## Architecture

```text
Trusted Eval control checkout
  -> verify protocol + upstream + dataset + Docker identities
  -> qualify case with official gold scorer
  -> materialize sanitized buggy Git snapshot from official image
  -> emit visible CaseBundle and target .autobugfix/config.yaml
  -> SubjectBroker materializes exact H0/candidate Autobugfix SHA
       -> real Execution service creates target task worktree
       -> production Codex SDK Writer edits only target worktree
       -> declared visible verifier commands provide bounded feedback
       -> read-only Execution evaluator reviews final candidate
  -> trusted broker derives and freezes final Git patch + trace
  -> official harness scores patch in a clean Docker instance
  -> recompute digests and write noninterference receipt
  -> Eval writes result or harness error

Direct Raw Codex comparator
  -> consumes the same visible case and sanitized buggy snapshot
  -> launches a locked standalone openai-codex worker with hooks disabled
  -> permits exactly one fresh thread and one turn
  -> freezes patch plus raw SDK evidence before any official result exists
  -> invokes the same trusted official scorer and noninterference check

External Holdout Guard
  -> authenticates encrypted six-case bundle
  -> requests exact subject binding from Operator Study
  -> invokes the same SubjectBroker and official scorer
  -> retains encrypted case-level artifacts
  -> signs only aggregate metric + executed subject SHA
```

The candidate subject never imports or controls the outer adapter. The outer
adapter never edits candidate code. Target repository changes remain a third,
separate worktree owned by Execution.

## Trust Domains

| Domain | May read | May write | Authority |
| --- | --- | --- | --- |
| Eval control | public + private case records, official runtime | trusted runtime artifacts | manifests, submissions, official results |
| Subject broker | visible case, exact subject checkout | external run roots and target task roots | observed SHA/tree and process evidence |
| Autobugfix subject | visible case and its own config/skills/Memory snapshot | target task worktree through Execution | no benchmark authority |
| Execution Writer | visible task/context, target task worktree | target task worktree only | no task/gate/scorer authority |
| Operator | visible Optimization artifacts and projections | requests through service | no Holdout or Eval authority |
| Holdout Guard | encrypted bundle, trusted adapter, exact subject | encrypted artifacts and signed aggregate | final Holdout metric |
| Raw Codex worker | visible case and isolated target checkout | target checkout and SDK log grant only | no benchmark or task-state authority |

## Source Layout

```text
harnesses/swebench/
  pyproject.toml
  uv.lock
  README.md

benchmarks/
  swe-experiment-2.yaml          # protocol, no sealed IDs

src/autobugfix/eval/benchmarks/
  swe_models.py                  # case/runtime/submission/result contracts
  swe_runtime.py                 # pinned checkout/dataset/Docker doctor
  swe_verified.py                # official Verified adapter
  swe_live.py                    # official Live adapter
  swe_materialize.py             # image -> sanitized Git snapshot
  subject_broker.py              # exact Autobugfix SHA execution
  swe_service.py                 # qualification, preparation, run, report

src/autobugfix/eval/baselines/
  isolation.py                   # shared process/Bubblewrap boundary
  swe_raw_models.py              # Raw treatment and frozen manifest contracts
  swe_raw_codex.py               # SWE Raw prepare/run/score service

baselines/raw_codex_sdk/         # separately locked SDK-only worker project

tests/
  test_swe_models.py
  test_swe_runtime.py
  test_swe_adapters.py
  test_subject_broker.py
  test_swe_isolation.py
```

Existing benchmark store, hashing, subprocess, Docker, Execution service, and
artifact primitives are reused where their contracts match. CLI remains a
thin adapter over services.

The Raw worker is intentionally not implemented as a flag on
`AutobugfixService`: doing so would let the control treatment inherit roles,
Memory, verifier feedback, evaluator behavior, or task state. Only the outer
Eval service is shared, so all treatments receive the same materialization and
official scorer while generation remains isolated.

## Runtime Pinning

`harnesses/swebench` is a separate uv project with exact `swebench==4.1.0`.
Its lockfile is part of every prepared manifest. Eval manages the Live source
checkout under the gitignored benchmark cache and verifies URL, commit, and
tree before adding that checkout to `PYTHONPATH` for
`evaluation.evaluation`. Formal commands run with the locked interpreter and
an explicit environment; host Python packages are not authoritative.

Datasets are downloaded at exact Hugging Face revisions into content-addressed
cache paths, exported to canonical JSONL, and hashed. Formal commands consume
the local canonical snapshot rather than resolving a network dataset name.

Docker image references are resolved to immutable IDs/digests during
qualification and copied into case receipts. Formal scoring rejects image
drift. Pulling a missing pinned image is a preparation action, never an SDK
side effect.

## Common Case Contract

The trusted case record includes both public and private sections. The visible
bundle is derived structurally rather than by deleting selected strings.

Public fields include:

- benchmark family, dataset revision, instance ID for Optimization only;
- repository, base commit, language, public issue/problem statement;
- public hints/comments, attachment copies and content digests;
- task type classification and provenance;
- sanitized source snapshot digest;
- visible verifier command IDs and environment description.

Private fields include:

- gold/developer patch and test patch;
- FAIL_TO_PASS/PASS_TO_PASS identities;
- official eval/rebuild/print commands and parsers;
- Docker image identity and private logs;
- Holdout instance identity and case mapping.

The visible schema rejects unknown private keys. Canary-based isolation tests
prove the subject cannot recover private fields through paths, environment,
Docker, logs, sibling cases, cache names, or error messages.

## Materialization

Eval creates an official instance container without applying any patch and
copies `/testbed` into a private staging directory only as a local Git object
source. Official image setup may leave tracked or untracked build artifacts in
that source worktree, so Eval neither copies its worktree files nor mutates it.
Instead, Eval verifies that the expected base commit object exists below the
image HEAD, fetches exactly that commit into a new repository, and rejects the
result unless it is a clean one-commit detached snapshot with no refs, remotes,
ignored files, or benchmark-only worktree artifacts. Execution clones that
sanitized cache into its ordinary task worktree. The source cache, container,
and target main snapshot remain read-only.

The target config uses a Docker-backed visible verifier. It mounts only the
task worktree over `/testbed`, hides Eval state, and runs predeclared public
compile/static/project smoke commands. Hidden official tests are applied only
inside the later scorer container.

## Subject Broker

The broker accepts a signed/digest-bound request containing subject SHA,
expected tree, Study/line/generation/grant binding, visible case digest, model,
and budget. It:

1. rejects dirty or non-ancestor control-root paths;
2. creates a detached subject worktree outside control and target roots;
3. resolves config, roles, skills, and read-only Memory from that worktree;
4. launches the subject with trusted dependencies and explicit candidate
   `PYTHONPATH` in Bubblewrap;
5. passes only the visible case and target snapshot reference;
6. retains request/result/stdout/stderr/events and observed Git identity;
7. returns an untrusted process result to Eval;
8. lets Eval independently derive the final target patch and submission.

The broker reports `executed_subject_sha` and `executed_subject_tree` from Git,
not caller text. H0 and H_general use the same broker binary and prepared
manifest. Candidate source cannot import the outer Guard or write its state.

## Official Scoring

### Verified

Eval writes one prediction JSONL and invokes
`swebench.harness.run_evaluation` with local pinned dataset JSON, one instance,
one worker, fixed timeout, explicit report directory, and a unique run ID. It
parses the official per-instance `report.json` and aggregate report. Missing
or malformed official output is a harness error, not an unresolved result.

### Live

Eval writes one predictions JSON object and invokes the pinned Live checkout's
`evaluation.evaluation` with local pinned dataset JSONL, Linux platform, one
worker, fixed output directory, and one instance. It parses official
`report.json` and `results.json` under the same failure taxonomy.

Official processes run after submission freeze and cannot launch Writer. Their
output root is not mounted into any SDK process.

## Qualification And Sealing

Preparation first builds a candidate inventory from the pinned public dataset
snapshots. Deterministic filters enforce architecture, image availability,
repository uniqueness, task-type coverage, source size, and supported
language. The trusted qualifier then runs each candidate's official gold patch
and may repeat the official baseline to detect infrastructure instability.

The Live cohort is selected only inside an external human Guard command. Its
ordering is HMAC-derived from the human secret and protocol digest, so an
Operator that knows the public dataset and source code cannot reconstruct the
chosen identities. The command excludes Optimization repositories and every
Live identity previously written to Operator-visible Eval evidence, resumes the
encrypted qualification catalog, and projects only aggregate counts plus the
encrypted catalog digest. Candidate IDs never appear in the human command's
argv or public output; official scorer child processes and their raw logs stay
inside the external Guard authority plane.

The selected ten Verified Optimization records become a visible manifest. The
six Live records are serialized into one canonical bundle, encrypted with the
human Guard secret, authenticated with associated data binding benchmark,
dataset/harness revisions, constitution digest, and Study cohort, and removed
from public staging. Public records contain only bundle/aggregate digests and
case count.

## State And Failure Semantics

Eval owns append-only states:

```text
inventory -> qualified -> prepared -> generated -> frozen -> scored
```

These are case artifact stages, not another Execution task state machine.
Execution task state remains owned by `AutobugfixService`.

Valid unsuccessful outcomes include empty patch, bounded Execution timeout,
source-path violation, or official unresolved verdict. Harness errors include
runtime/dataset/image drift, materialization failure, broker identity mismatch,
missing evidence, scorer crash, patch apply infrastructure error, or
post-score mutation. Harness errors stop a formal batch and never become model
feedback.

## Rollback

- Adapter code is isolated on `agent/swe-eval-adapters` and can be reverted
  without changing H0 or Raw baseline.
- Runtime caches and manifests are append-only by digest; invalid preparation
  receives a new ID rather than mutation.
- A formal harness defect invalidates the whole run under that prepared
  manifest. Selective case reruns cannot repair it.
- No experiment line is created until adapter, gold qualification, broker
  isolation, and one production acceptance case pass.
