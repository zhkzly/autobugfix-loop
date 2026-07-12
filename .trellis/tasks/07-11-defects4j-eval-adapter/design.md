# Design: direct Docker-backed Defects4J Eval

## Scope And Ownership

This child adds a Defects4J Case source to the existing Eval loop. Eval owns
case materialization, the immutable Docker runtime, official verification,
eligibility evidence, and scoring. `AutobugfixService` still owns Execution
task/worktree state. The Writer sees one ordinary buggy Git worktree and never
receives Docker authority, a fixed revision, or a developer patch.

The adapter does not create a second task state machine. Its direct path is:

```text
project + bug ID -> Docker checkout <id>b -> history-free Git snapshot
-> real Execution loop -> Docker defects4j test -> independent Eval oracle
-> tests-first report
```

## Runtime Configuration

Defects4J has exactly one production runtime family: two roles built from one
pinned Dockerfile. The materializer contains checkout/oracle metadata. The
verifier removes gold patches and localization hints but retains
`active-bugs.csv`, commit databases, layout/build metadata, and other files
required by the official `defects4j test` implementation. It removes every
project repository entry while retaining only `project_repos/README`, which
the pinned Defects4J bootstrap requires before any command can run.

```yaml
eval:
  benchmarks:
    cache_root: .autobugfix/benchmark-cache
    trusted_case_root: .autobugfix/trusted-eval-cases
    visible_manifest_root: .autobugfix/eval-manifests
    command_timeout_seconds: 1800
    guard:
      trusted_ref: origin/main
    defects4j:
      image: autobugfix/defects4j:3.0.1
      verifier_image: autobugfix/defects4j-verifier:3.0.1
      platform: linux/amd64
      framework_revision: 6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09
      timezone: America/Los_Angeles
      preflight_repetitions: 2
```

Host Java, Defects4J, Perl, SVN, cpanm, `PERL5LIB`, and library paths are not
configuration surfaces. Legacy host-runtime keys fail config loading. The host
requires only Python/uv, Git, the Codex Python SDK, and a Docker client/daemon.
`AUTOBUGFIX_DOCKER_BIN` may select an already-installed Docker executable for
the current process without committing a host path.

Doctor resolves the configured image tag to an immutable image ID and verifies
the Docker daemon, both roles, platform, image revision/role labels,
in-container framework HEAD, Java 11, verifier sanitization, `defects4j info`,
and available disk. Every case command uses the resolved immutable role ID.

## Case Materialization

For a visible seed case, `EvalBenchmarkService` performs:

1. Read `active-bugs.csv` from the pinned image.
2. Optionally enrich visible input from the upstream issue; tracker failure
   does not replace or invalidate official triggering-test evidence.
3. Checkout `<id>b`, `<id>f`, and a fresh `<id>b` snapshot through the official
   CLI. The container runs as the current host UID/GID so bind-mounted output
   is writable without a privileged ownership-repair phase. An ephemeral
   container-only Git config marks checkout repositories safe; it never
   changes the host Git configuration.
4. Export `tests.trigger` and `dir.src.classes`.
5. Repeat official full tests. Fixed failures must be identical across runs;
   buggy failures must be exactly that stable baseline plus every trigger.
6. Store fixed checkout, developer patch, command evidence, and receipt below
   the trusted root.
7. Strip VCS history from the fresh buggy checkout and create a deterministic
   one-commit Git repository below the benchmark cache data plane.

The visible Eval row contains only the buggy repository, issue/failure input,
triggering tests, production source roots, immutable runtime identity, and a
digest-bound verifier contract. It contains no fixed revision, gold path,
trusted receipt path, or trusted-root-adjacent path.

## Execution And Verification

`autobugfix eval benchmark run-case` writes one visible canonical `EvalCase`,
then calls the existing `run_eval` and `AutobugfixService` surfaces. Production
uses only the Python Codex SDK and defaults the experiment model to
`gpt-5.4-mini`.

The deterministic verifier:

- derives complete tracked and untracked Git changes;
- rejects changes outside exported production source roots;
- mounts the task worktree into the immutable image;
- injects the digest-bound `defects4j.build.properties` retained under the
  trusted Eval root into an isolated verification copy only;
- runs `defects4j test` and reads the official `failing_tests` file;
- accepts only failures present in the digest-bound fixed-revision baseline;
- removes only build/test artifacts created by that invocation while
  preserving all pre-existing Writer changes, including untracked source, and
  removes the injected service metadata before returning;
- writes raw command logs to a persistent worktree-external artifact root;
- returns success only when the command executes and no failure outside the
  stable baseline remains.

Execution may retry a failed Writer attempt within an explicit maximum. The
real verifier result becomes the next feedback packet. Eval never approves
PPE, archives the task, or writes Memory. After the read-only evaluator reaches
the human gate, the Defects4J adapter independently repeats the official
verifier from a clean artifact state. Official tests are the score; gold diff
equality is diagnostic only.

## Storage Boundary

```text
.autobugfix/benchmark-cache/
  cases/                 buggy-only history-free repositories
  verifier-contracts/    visible-safe deterministic contracts
  verifier-evidence/     per-attempt official stdout/stderr/failing_tests

.autobugfix/trusted-eval-cases/
  doctor/                digest-bound runtime reports
  preflight-runs/        fixed checkout, gold, raw commands, issue raw data
  receipts/              immutable eligibility authority
  guard/<guard-id>/      AES-GCM Holdout bundle and preflight archive

.autobugfix/eval-manifests/
  <manifest>/            visible Optimization JSONL only

.autobugfix/eval-runs/
  <run>/                 Execution control root, worktree, raw SDK logs,
                         events, generated diff, oracle logs, report, summary
```

These roots are pairwise disjoint and outside Operator authority roots.

## Direct CLI

```text
autobugfix eval benchmark doctor --adapter defects4j
autobugfix eval benchmark preflight --manifest <seed> --case <visible-id>
autobugfix eval benchmark seal --manifest <seed>
autobugfix eval benchmark run-case --manifest <seed> --case <visible-id>
  --run-id <id> --model gpt-5.4-mini --max-attempts 2
```

`seal` writes an authenticated encrypted Holdout identity bundle and preflight
archive plus a deidentified public projection with cumulative 3/8/16 waves.
The encryption AAD binds the seed and trusted Guard code identity. Seal and
`guard-run` fail unless the control checkout is clean and exactly equals
`eval.benchmarks.guard.trusted_ref`. Holdout paths, issue data, gold data, and
case-level results never enter Operator roots.

For governed studies, Operator derives a digest-bound Study/line/budget
projection. Guard signs that projection together with aggregate metrics and
its code identity. An interactive Operator service transition verifies the
human-held Guard secret, frozen harness/policy, current binding, aggregate-only
schema, and numeric success contract before creating `StudyMetricRecord`.

Production Codex calls execute in `autobugfix.codex_sdk_worker`, still through
the Python SDK. The parent process owns the wall-clock timeout and terminates
the worker process group on expiry; request/result/stdout/stderr remain raw
artifacts. No path invokes `codex exec`.

Before Bubblewrap hides the host home, the trusted parent creates a private
per-call Codex home. The role then sees only read-only Autobugfix/Python
runtime mounts, its role-appropriate cwd, its own logs, and service-validated
linked-worktree Git metadata. Read-only roles receive a read-only cwd mount;
Writer roles cannot see Guard, benchmark authority, Operator state, Memory, or
other task roots.

The direct adapter executes the trusted checkout as the measured subject. A
future candidate-subject broker must report and isolate a distinct experiment
line SHA before candidate metrics can be authoritative; a Study binding alone
must never be treated as proof that candidate code executed.

## Rollback

The integration is additive to legacy `local-git` Eval. A Docker doctor,
preflight, verifier, SDK, or oracle failure is retained as a harness error or
repair failure and cannot produce a passing score. Runtime roots are
gitignored and may be discarded without mutating target main or Autobugfix Git
history.
