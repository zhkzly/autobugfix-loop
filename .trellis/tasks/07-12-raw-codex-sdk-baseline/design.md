# Design: isolated Raw Codex SDK comparator

## Architecture

The experiment has one trusted measurement control and one untrusted treatment
process:

```text
Trusted Eval host
  -> verify pinned prepared Raw manifest
  -> clone sanitized buggy snapshot into an isolated case worktree
  -> emit a digest-bound visible CaseBundle
  -> launch standalone Raw Codex process in an outer sandbox
       -> one direct openai_codex thread/turn
       -> inspect/edit only the target worktree
       -> stream SDK events to an untrusted raw-log directory
  -> terminate at completion or fixed deadline
  -> derive Git diff and changed paths from the real worktree
  -> freeze Submission and validate production-source policy
  -> apply the frozen patch to a fresh clean buggy checkout
  -> invoke the existing hidden Defects4J official evaluator
  -> verify post-score noninterference
  -> write immutable score and paired comparison report
```

The Raw process never imports or calls the Autobugfix Execution loop. The
trusted host may reuse Eval's benchmark store, receipt validation,
materialization, official oracle, hashing, and reporting primitives because
those are common measurement controls rather than treatment behavior.

## Source Layout

```text
baselines/raw_codex_sdk/
  pyproject.toml
  uv.lock
  src/raw_codex_sdk_baseline/
    __init__.py
    cli.py                 # standalone process entrypoint
    models.py              # visible input and untrusted process-result schemas
    prompt.py              # one generic, versioned prompt template
    runner.py              # direct openai_codex API and event capture
  tests/

src/autobugfix/eval/baselines/
  __init__.py
  models.py                # trusted prepared/submission/result contracts
  isolation.py             # bwrap launch and ephemeral CODEX_HOME
  raw_codex.py             # Eval-owned orchestration service
  reporting.py             # paired H0/Raw report

tests/
  test_raw_codex_baseline.py
```

The standalone project has no path dependency on the repository root and no
`autobugfix` dependency. Root integration tests may use a fake process fixture;
the production CLI always launches the real standalone SDK executable.

## State Ownership

| State | Owner | Authority |
|---|---|---|
| Prepared baseline manifest | Eval service | Digest-verified, immutable |
| Visible CaseBundle | Eval service | Derived only from visible receipt fields |
| Target case worktree | Eval service | Real Git clone; Raw process has bounded write access |
| SDK events/final response | Raw process | Observation only; never grants state transitions |
| Final patch/changed paths | Eval service | Recomputed from Git after process exit |
| Submission freeze | Eval service | Digest-bound before official scoring |
| Official score | Eval official evaluator | Hidden from Raw process |
| Comparison report | Eval reporting service | Deterministic over frozen artifacts |

There is no Execution task, Memory proposal, Operator request, human gate, or
archive transition in this treatment arm.

## Contracts

### Visible CaseBundle

Canonical JSON contains only:

- schema and case identity;
- benchmark and dataset revision;
- sanitized repository base SHA;
- problem statement and expected behavior;
- visible failure/reproduction evidence and attachment copies;
- model-visible environment description;
- bundle digest.

It excludes receipt paths, source-root policy, fixed revision, gold patch,
modified classes, private qualification, hidden tests, baseline failing-test
sets, Docker metadata, and official-evaluator configuration.

### PreparedRawBaselineManifest

The trusted manifest binds:

- source H0 prepared-manifest and report digests;
- 16 receipt digests and immutable runtime IDs;
- primary and development case IDs;
- runner Git SHA/tree and standalone source digest;
- standalone `uv.lock` and SDK wheel/version digests;
- generic prompt digest;
- model, reasoning effort, service tier, timeout, one-turn limit, concurrency;
- preparation timestamp and record digest.

`run` rejects a dirty checkout or any mismatch before the first SDK call.

### RawProcessResult

The standalone process reports thread/turn identity, timestamps, status, final
response, usage, and paths/digests for streamed event and stderr logs. It does
not report a trusted patch or score.

### FrozenRawSubmission

After process exit, Eval records:

- case and prepared-manifest digests;
- real base SHA and current worktree identity;
- process-result and raw-log digests;
- process completion/timeout status;
- Git patch, patch digest, and changed paths;
- source-policy result;
- model and prompt identities;
- freeze timestamp and record digest.

The patch is then applied to a different clean checkout for scoring. Eval
recomputes worktree and artifact digests afterward and writes a noninterference
receipt.

## Direct SDK Runtime

The standalone runner uses the pinned public API:

```python
Codex(CodexConfig(...)).thread_start(
    cwd=worktree,
    model="gpt-5.4-mini",
    sandbox=Sandbox.workspace_write,
    approval_mode=ApprovalMode.auto_review,
    developer_instructions=FROZEN_BASELINE_INSTRUCTIONS,
).turn(prompt, ...)
```

It consumes the public notification stream and writes every event as JSONL.
The SDK's internal CLI transport is allowed; shelling out to `codex exec` is
not. A single thread and single turn are created for each case.

The host creates a private per-case `CODEX_HOME` containing only required auth
bridge files and a generated config with `hooks = false`, `multi_agent = false`,
the frozen reasoning effort/service tier, and the target worktree trust entry.
No user/project skill directory is copied.

## Process Sandbox

On Linux/WSL the formal run requires Bubblewrap. The sandbox:

- mounts the target worktree read-write;
- mounts the case bundle and standalone runtime read-only;
- mounts the raw-log directory read-write;
- hides the Autobugfix checkout, trusted roots, active Memory, host home,
  Docker socket and installation paths, other case worktrees, and previous
  outputs;
- leaves network available only because the SDK must reach the model service;
- starts in the target worktree;
- kills the whole process group on the fixed deadline.

The SDK's own `workspace-write` sandbox remains enabled inside this outer
boundary. Formal execution fails closed when Bubblewrap is unavailable.

## Prompt And Treatment Semantics

The generic prompt asks Codex to resolve the supplied issue in the current
repository, inspect before editing, modify implementation rather than tests or
benchmark metadata, run relevant commands that are actually available, and
finish without requesting human input. The prompt contains no Autobugfix role
skills, repository-specific strategy, gold information, or per-case advice.

Raw Codex receives initial visible failure evidence but no managed verifier
callback. This is intentional: the comparator is one direct SDK coding turn,
whereas H0 is the complete loop/harness treatment. Runtime and token usage are
reported so the result is not misrepresented as a compute-matched causal
ablation.

## Formal Protocol

1. Implement and unit-test without production calls.
2. Run one real pilot on an already exposed development case.
3. Fix only generic runner/harness defects discovered by the pilot.
4. Freeze source, lockfile, prompt, SDK/model/budget, case cohort, and H0 report
   into a prepared Raw manifest.
5. Run all 16 cases serially, each in a fresh process, thread, `CODEX_HOME`, and
   worktree.
6. Freeze and officially score each case exactly once.
7. Abort and invalidate the entire run on a harness defect. A later corrected
   run must use a new manifest digest and run ID.
8. Generate the deterministic comparison report without invoking SDK or
   scorer again.

The primary comparison uses the 13 cases not touched during H0 development.
The three exposed cases and all-16 aggregate are secondary diagnostics.

## CLI

Trusted control-plane commands:

```text
autobugfix eval baseline prepare-raw-codex
  --manifest <H0-prepared-manifest>
  --h0-report <evaluation-report.yaml>

autobugfix eval baseline pilot-raw-codex
  --manifest <prepared-raw-manifest> --case <development-case>
  --out <runtime-root> --run-id <id>

autobugfix eval baseline run-raw-codex
  --manifest <prepared-raw-manifest>
  --out <runtime-root> --run-id <id>

autobugfix eval baseline report-raw-codex
  --run-dir <completed-run> --h0-report <evaluation-report.yaml>
```

Standalone treatment command, launched only by the trusted service:

```text
uv run --project baselines/raw_codex_sdk raw-codex-sdk-baseline run
  --case-bundle <visible.json>
  --worktree <case-worktree>
  --artifacts <raw-output>
```

CLI handlers delegate to services and never author manifests, submissions, or
scores directly.

## Rollback And Reproducibility

- The branch is independent and may be deleted without changing H0.
- Runtime state is gitignored and every formal run is append-only by run ID.
- A failed pilot does not alter the formal manifest.
- A formal harness defect invalidates the entire run rather than permitting a
  selective rerun.
- The H0 report is read-only and bound by digest; Raw results cannot modify it.
