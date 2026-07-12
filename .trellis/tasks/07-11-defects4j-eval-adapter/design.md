# Design: common benchmark protocol with a Defects4J adapter

## Scope And Ownership

Autobugfix benchmark evaluation has one protocol:

```text
Case(repo@buggy_revision, issue/evidence, environment)
-> existing complete Execution loop
-> Submission(frozen patch, trace, subject/config digests)
-> dataset adapter's official evaluator
-> Result(score, diagnosis, artifacts)
```

The Defects4J adapter is responsible only for translating Defects4J into that
protocol. It materializes a real buggy Java repository, constructs visible
problem evidence, supplies an Execution-visible verifier contract, and runs
the official evaluator after submission freeze.

State ownership remains strict:

- `AutobugfixService` owns inner Execution task, worktree, attempts, feedback,
  and human-gate state.
- `EvalRunner` owns Case, Submission, official evaluator invocation, score,
  diagnosis, and benchmark artifacts.
- Memory is a frozen Execution input where configured; benchmark results do
  not maintain or approve Memory.
- Operator is absent from Experiment 1 and cannot optimize H0 from its results.

The adapter does not create a second task state machine.

## Runtime Configuration

Defects4J has two roles built from one pinned Dockerfile. The materializer has
the framework metadata needed for official checkout, private qualification,
and final scoring. The verifier removes gold patches and localization hints
while retaining only metadata required to run the visible triggering tests.

```yaml
eval:
  benchmarks:
    cache_root: .autobugfix/benchmark-cache
    trusted_case_root: .autobugfix/trusted-eval-cases
    visible_manifest_root: .autobugfix/eval-manifests
    command_timeout_seconds: 1800
    defects4j:
      image: autobugfix/defects4j:3.0.1
      verifier_image: autobugfix/defects4j-verifier:3.0.1
      platform: linux/amd64
      framework_revision: 6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09
      timezone: America/Los_Angeles
      preflight_repetitions: 2
```

Host Java, Defects4J, Perl, SVN, cpanm, `PERL5LIB`, and library paths are not
configuration surfaces. The host requires Python/uv, Git, the Codex Python
SDK, and a Docker client/daemon. `AUTOBUGFIX_DOCKER_BIN` may select an installed
Docker executable for one process without committing a host path.

Doctor resolves image tags to immutable image IDs and checks the daemon,
platform, labels, framework revision, Java 11, verifier sanitization,
`defects4j info`, and disk capacity. Every case command uses those IDs.

## Case Qualification And Materialization

Before model execution, trusted Eval may privately checkout `<id>b` and
`<id>f`, repeat official tests, and reject an unstable or unrunnable case.
Qualification proves benchmark validity; it is not Writer feedback. Fixed
source, developer patch, fixed-revision failures, hidden metadata, and private
raw output stay under the trusted Eval root.

For Execution, Eval creates a fresh `<id>b` checkout, removes its VCS history,
and creates a one-snapshot Git repository. The visible Case contains:

- issue title/body when available;
- official triggering-test names and visible reproduction command;
- visible buggy failure evidence;
- production source roots;
- immutable runtime identity and a digest-bound visible verifier contract.

It contains no fixed revision, developer patch, modified-class hint, private
baseline, trusted receipt path, official score, or scorer diagnosis.

## Execution-Visible Verification

`autobugfix eval benchmark run-case` converts the visible benchmark Case to the
canonical `EvalCase`, then invokes the existing `run_eval` and
`AutobugfixService` surfaces. Production uses the Python Codex SDK and
`gpt-5.4-mini`.

For each bounded Writer attempt, the managed verifier:

1. derives all tracked and untracked changes;
2. rejects changes outside declared production source roots;
3. copies the task worktree to an isolated verification directory;
4. injects only digest-bound build metadata required by Defects4J;
5. runs the predeclared official triggering tests in the immutable verifier
   image;
6. stores raw stdout, stderr, exit status, and failing-test evidence outside
   the worktree;
7. removes the verification copy without changing the Writer worktree.

This result is ordinary Execution feedback. A visible compile or triggering
test failure may drive another Writer attempt within the frozen attempt
budget. It does not know the fixed revision or final official score.

## Submission Freeze And Official Evaluation

When the complete Execution loop terminates, Eval computes and stores:

- final generated patch and SHA-256;
- task YAML and event-stream digests;
- Execution task ID, state, and iteration count;
- measured Autobugfix subject SHA;
- resolved model/config/skill/Memory identities;
- freeze timestamp.

Only then does the Defects4J adapter create a fresh clean buggy checkout, apply
the frozen patch, and invoke the official full-suite evaluator in the trusted
materializer image. The official evaluator may use private fixed-revision
baseline semantics required by the pinned benchmark. Its output is Eval-only.

After scoring, Eval recomputes the patch, task, events, and iteration count. A
change is a harness violation, not a repair result. The
`oracle-noninterference.yaml` receipt records this comparison. No official
result can call Writer, append Execution feedback, or advance Execution state.

Official tests are the correctness score. Gold-diff equality is diagnostic
only; an alternative patch is valid when the official evaluator accepts it.

## Storage Boundary

```text
.autobugfix/benchmark-cache/
  cases/                 buggy-only history-free repositories
  verifier-contracts/    visible-safe contracts
  verifier-evidence/     per-attempt visible command evidence

.autobugfix/trusted-eval-cases/
  doctor/                runtime reports
  preflight-runs/        private fixed/buggy qualification evidence
  receipts/              immutable eligibility authority

.autobugfix/eval-runs/<run>/
  control/               inner Execution service state
  cases/<case>/
    raw/                 SDK and adapter logs
    generated.patch      frozen final patch
    submission.yaml      pre-oracle submission identity
    oracle/              independent official evaluation checkout/output
    oracle-noninterference.yaml
    report.yaml
  summary.yaml
```

Runtime roots are gitignored. Candidate-authored files cannot substitute for
service-generated receipts.

## CLI

```text
autobugfix eval benchmark doctor --adapter defects4j
autobugfix eval benchmark preflight --manifest <evaluation-seed> --case <id>
autobugfix eval benchmark run-case --manifest <evaluation-seed> --case <id>
  --run-id <id> --model gpt-5.4-mini --max-attempts 2
autobugfix eval benchmark prepare-evaluation --manifest <evaluation-seed>
autobugfix eval benchmark run-evaluation --manifest <prepared-manifest>
  --run-id <id>
autobugfix eval benchmark report-evaluation --run-dir <completed-run>
```

CLI handlers parse arguments and call the Eval benchmark service. They do not
write task, receipt, submission, or score files directly.

`prepare-evaluation` qualifies every pre-registered case before model use and
freezes H0 Git/tree, config, roles, skills, Memory, model, budget, and receipt
digests. `run-evaluation` rejects drift, runs the complete suite serially, and
writes subject-level noninterference evidence. The repository retains generic
sealed-Holdout and Operator Study commands for future treatment experiments.
Experiment 1 does not call them: its 16 case identities are pre-registered as
`evaluation`, and H0 is never changed.

`report-evaluation` reads only completed case artifacts. It verifies submission
and noninterference digests, reconciles case decisions with `summary.yaml`, and
writes a digest-bound aggregate. It cannot rerun Execution or a scorer.

## Failure Semantics

- Docker/import/materialization/patch-apply/noninterference failure: harness
  error; no capability score is inferred.
- Execution completes but official evaluator rejects the frozen patch: valid
  unsuccessful repair; retain and count it once.
- Official evaluator accepts an alternative patch: successful repair even when
  it differs from the developer patch.
- Valid official failure never grants an extra attempt and never triggers H0
  modification in Experiment 1.

Legacy `local-git` Eval remains compatible and follows the same
generate-freeze-score ordering with its own adapter/scorer.
