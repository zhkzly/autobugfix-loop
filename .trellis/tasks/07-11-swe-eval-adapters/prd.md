# Implement official SWE benchmark Eval adapters

## Goal

Build the trusted, production-grade measurement control required by Experiment
2. The control must materialize real repository-level SWE tasks, run the real
Autobugfix Execution loop against an exact H0 or candidate subject, freeze the
generated patch and trace, and only then invoke the official benchmark scorer.

This child does not optimize Autobugfix and does not create `H_general`. It
provides the external Eval/Guard authority that will measure both H0 and the
later governed H_general experiment line under one frozen protocol.

## Project Boundary

Autobugfix remains a local, repo-agnostic loop-engineering and
harness-engineering control system:

- Execution repairs the configured target repository only in an isolated task
  worktree.
- Memory compiles accepted Execution evidence into reviewed wiki/skills and
  cannot consume benchmark oracle feedback.
- Eval owns benchmark manifests, materialization, frozen submissions, official
  scoring, noninterference receipts, and comparison reports.
- Operator later diagnoses visible Optimization evidence and requests governed
  non-main candidate changes; it does not own Eval state.

The LLM is a bounded node. Git facts, Docker, official scorers, deterministic
checks, digest-bound artifacts, Guard signatures, and human budget grants own
truth.

## Frozen Upstream Inputs

| Input | Frozen identity |
| --- | --- |
| H0 subject | `f529f09de53183d7ddbf9e05b31a9d3b3fbde008` |
| SWE-bench harness | `v4.1.0`, commit `726c5461e2ef52d83cf1ea2107870a8bb3328d57` |
| SWE-bench harness tree | `f178530b37202c549b1b2b3300db2da90da648db` |
| Verified dataset | `princeton-nlp/SWE-bench_Verified`, revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` |
| SWE-bench-Live harness | tag `v1.0-multi-language-multi-os-benchmarking`, commit `c5ea7e48b7b8bb0f4bcbbceb182a09dadfabfc2c` |
| Live harness tree | `aaa2c4a59dab49c54ef6576d1190dfb590c2fd1d` |
| Live dataset | `SWE-bench-Live/MultiLang`, revision `608f7ae9ab8ea1f9f0d030fe04562cf6bd1a0c8b` |
| Primary model | `gpt-5.4-mini`; no fallback |
| Execution concurrency | one case at a time |

Any upstream revision change requires a new prepared manifest and a new formal
run identity. Formal execution must not resolve `main`, `latest`, or an
unpinned Hugging Face revision.

## Requirements

### Official Runtime

- R1. Add a separately locked uv harness environment for
  `swebench==4.1.0`. The production adapter must invoke the official
  `swebench.harness.run_evaluation` module; it must not reimplement grading.
- R2. Materialize the pinned SWE-bench-Live checkout in the runtime cache,
  verify its commit and tree, and invoke its official
  `evaluation.evaluation` entrypoint from that checkout. Its Python dependency
  environment must be lockfile controlled.
- R3. Doctor must verify Linux/amd64, Docker client/server access, API
  compatibility, CPU, memory, disk, harness revisions, dataset revisions,
  cache writability, and required image access before any SDK call.
- R4. Preserve official stdout, stderr, per-instance logs, reports, image IDs,
  commands, durations, and exit codes outside all target worktrees.

### Case Translation And Materialization

- R5. Translate Verified and Live records into a versioned common SWE case
  contract containing public issue evidence, repository/base identity,
  language, task type, attachment references, visible verifier contract, and
  official runtime identity.
- R6. Preserve issue text, comments or hints explicitly declared public,
  attachments, and screenshots. A text-only benchmark case does not imply that
  normal Autobugfix tasks should discard richer evidence.
- R7. Keep gold/developer patches, test patches, FAIL_TO_PASS/PASS_TO_PASS
  identities, fixed truth, hidden images, official commands, and sealed case
  identity outside Writer, Execution Evaluator, Memory, and Operator views.
- R8. Materialize the exact buggy repository from the official instance image
  into a sanitized Git source snapshot. Every model edit must occur in an
  Autobugfix task worktree cloned from that snapshot; target main checkouts and
  image source are read-only.
- R9. Generate real `.autobugfix/config.yaml` verifier commands for the target
  language/repository. Execution-visible checks may provide bounded Writer
  feedback only when declared before the case and available without oracle
  fields.

### Generate, Freeze, Score

- R10. Execute the complete production Execution loop with the local Codex
  Python SDK. Production commands may not use `codex exec`, fake output, a fake
  backend, or scorer-driven retries.
- R11. Freeze final Git diff, changed paths, base SHA, task trace, role logs,
  verifier artifacts, model usage, timeout status, and a submission digest
  before the official scorer starts.
- R12. Score the frozen patch in a clean official container. Official verdict,
  hidden test output, gold patch, and scorer diagnosis must never become
  Execution feedback or trigger another attempt of the same formal case.
- R13. Distinguish valid unresolved submissions from harness failures. Patch
  rejection or hidden-test failure is a model/system result; missing image,
  corrupt dataset, broken checkout, Docker failure, scorer crash, or evidence
  mutation is a harness error.
- R14. Recompute patch and artifact digests after official scoring and emit a
  noninterference receipt. Any mutation of the frozen submission is a harness
  error.

### Subject Broker

- R15. Add a trusted isolated subject broker capable of running the exact
  Autobugfix Git SHA named by a Study binding. The broker must materialize a
  clean detached subject worktree, use trusted dependencies, import candidate
  source explicitly, and bind every run to the observed subject SHA/tree.
- R16. Candidate code may provide Execution behavior, roles, config, and
  skills but cannot supply the benchmark manifest, hidden state, scorer,
  success contract, policy authority, or claimed metric.
- R17. The broker must hide control checkout source, Operator SQLite state,
  Holdout bundles, other case artifacts, Docker authority from SDK roles, and
  H_bug/Experiment-1 artifacts. SDK roles run with hooks disabled.
- R18. H0 and H_general must be evaluated through the same broker and official
  scorer. A Guard metric is invalid when `executed_subject_sha` differs from
  the Study binding.

### Qualification And Experiment-2 Split

- R19. Before sealing, run every candidate case's official gold patch in the
  pinned harness and retain its logs. Cases with image, setup, test,
  patch-application, flakiness, or parser failures are ineligible.
- R20. Curate exactly ten visible SWE-bench Verified Optimization cases that
  cover bugfix, feature, and maintenance tasks and multiple repositories.
- R21. Curate exactly six SWE-bench-Live MultiLang Holdout cases from six
  repositories absent from the Optimization set. Holdout case IDs, records,
  patches, logs, and per-case results remain in authenticated encrypted Guard
  storage and outside Operator roots.
- R22. Produce nested 3/8/16 budget projections without changing the final
  10+6 cohort. Operator may see Optimization case-level evidence only; it may
  receive only signed Holdout aggregates after permanent study closure.
- R23. The public Study manifest contains only visible Optimization inputs and
  digests for external Holdout authority. It cannot reveal Holdout IDs by file
  name, ordering, logs, cache path, or error message.

## Acceptance Criteria

- [ ] A locked official runtime reports SWE-bench `4.1.0` and verifies both
      upstream checkout tree digests and both dataset revisions.
- [ ] `autobugfix eval benchmark doctor` passes on the current Docker engine
      without starting an SDK role.
- [ ] At least one Verified and one Live gold patch pass their official pinned
      Docker scorers with complete retained logs.
- [ ] A real production `gpt-5.4-mini` case runs the complete Execution loop,
      edits only its task worktree, freezes a non-empty patch, and receives an
      independent official result.
- [ ] Official scoring cannot modify the frozen patch or trigger a Writer
      retry; oracle leakage tests fail closed.
- [ ] The subject broker proves the executed SHA/tree and rejects forged,
      dirty, stale, or control-root candidate subjects.
- [ ] Writer and Operator isolation tests prove they cannot read gold patches,
      hidden tests, sealed case IDs/results, scorer authority, or sibling case
      artifacts.
- [ ] Exactly ten eligible Verified Optimization and six eligible Live
      unseen-repository Holdout cases are sealed with 3/8/16 projections.
- [ ] Generated patches are scored by official tests, never exact-diff
      equality, and harness errors remain distinct from unresolved issues.
- [ ] Root tests, harness-project tests, both compile checks, diff hygiene,
      role-skill validation, and a real official-container acceptance pass.

## Out Of Scope

- Operator optimization, budget-wave execution, or creating `H_general`.
- Reading Holdout results to improve H0 or candidate skills.
- A full 500-case leaderboard run.
- Windows Holdout execution in this Linux/WSL experiment.
- Combining Experiment 1, Raw SDK baseline, H_bug, or Defects4J case feedback
  with Experiment 2.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- Implementation branch: `agent/swe-eval-adapters`, rooted directly at the
  frozen H0 commit rather than the Raw SDK comparator branch.
- Official references are recorded in `research.md`.
