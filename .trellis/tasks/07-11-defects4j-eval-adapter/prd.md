# Implement Defects4J Eval adapter

## Goal

Implement a production Defects4J adapter that materializes real Java
repositories for the existing Execution loop, runs official tests as hidden
authority, and seals an eligible 16-case Experiment 1 manifest.

## Requirements

- Pin Defects4J tag `v3.0.1` and commit
  `6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09` plus every selected project
  revision and runtime dependency.
- Add benchmark doctor output for the Docker daemon, immutable image ID,
  platform, Java 11, timezone, cache space, and framework initialization.
- Materialize the complete buggy repository as a real Git target compatible
  with task worktrees without exposing fixed code or modified-class hints.
- Generate target `.autobugfix/config.yaml` through the existing Eval/service
  boundary and run real Defects4J triggering/full tests.
- Preserve issue text, failure output, stack trace, reproduction command, and
  available attachments as visible evidence.
- Keep fixed revision, gold patch, hidden metadata, and Holdout case artifacts
  in the trusted Eval root.
- Preflight every selected case without an LLM: the fixed-revision failure set
  must be stable, the buggy failure set must equal that baseline plus official
  triggering tests, and unstable cases are rejected/replaced.
- Curate 16 cases: 10 Optimization plus 6 Holdout cases whose repositories are
  absent from Optimization, with nested 3/8/16 wave projections.
- Official tests determine correctness; diff equality remains diagnostic.
- Retain setup, framework, compile, test, oracle, diff, timing, and eligibility
  artifacts.
- Use one pinned Dockerfile with separate materializer and verifier images for
  checkout/oracle work and Writer-facing official tests. Host Java, Defects4J,
  Perl, SVN, cpanm, and library paths are forbidden configuration surfaces.
- Add typed benchmark configuration for image, platform, framework revision,
  cache, trusted-case, visible-projection, timeout, timezone, and repetitions.
- Keep the 10 Optimization case identities visible, but let the trusted Guard
  choose and store the 6 Holdout identities outside Operator roots. Operator
  records may contain only opaque Holdout budget tokens and aggregate metrics.
- Bind every encrypted Guard bundle and signed aggregate to a clean configured
  trusted Git ref, source tree, machine constitution, and benchmark harness.
- Import Guard aggregates into Operator Study state only through an
  interactive signature-verifying service transition bound to current
  Study/line/budget facts.
- Repackage every buggy checkout as a one-snapshot Git repository so the
  Writer cannot inspect fixed history, tags, source patches, or modified-class
  metadata.
- Reject generated changes to tests, benchmark metadata, and build harness
  files before running official tests; production-source changes remain
  eligible.
- Fetch and cache upstream issue title/body and attachment metadata where the
  tracker exposes them. Tracker failure is retained but official triggering
  tests remain sufficient visible problem evidence.
- Expose a service-owned `run-case` command that directly materializes one
  case, calls the production Execution loop, retries only with real verifier
  feedback, and independently runs the official oracle.
- Expose benchmark operations through CLI adapters over an Eval benchmark
  service. CLI code must not write receipts, manifests, or trusted case state
  directly.

## Acceptance Criteria

- [ ] Doctor fails before mutation/SDK use when any required runtime is absent.
- [ ] At least one pinned case completes buggy-fail and gold-pass preflight
      using official Defects4J commands.
- [ ] Materialized target main remains unchanged while Execution edits only its
      task worktree.
- [ ] Writer/Operator views cannot read fixed revision, gold patch,
      modified-class hints, or sealed case artifacts.
- [ ] All final 16 cases have immutable eligibility receipts and valid
      repository-group splits.
- [ ] A generated alternative patch passes when official tests pass even when
      it differs from the gold diff.
- [ ] Harness/setup failures are distinct from model repair failures.
- [ ] Adapter, schema, CLI, isolation, and real-case acceptance tests pass.
- [ ] Sealed projections contain no Holdout case ID, repository, issue text,
      gold identity, or case-level result, while the Guard can still run and
      score the case through an opaque token.
- [ ] Existing `local-git` Eval datasets remain compatible.

## Out Of Scope

- This child does not optimize Autobugfix skills or create `H_bug`.
- Building/pulling the pinned image and providing Docker are operator
  prerequisites. The adapter never invokes `sudo` or installs host packages.
- Full governed H0/H_bug optimization remains a separate study. This child may
  run bounded production SDK acceptance cases to prove its direct path.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This child affects Eval and benchmark-facing Execution configuration. Eval
  state owner remains `EvalRunner`; target task state owner remains
  `AutobugfixService`.
- Governance V4 is implemented by the archived
  `07-11-experiment-integration-lines` child but must be admitted to protected
  `origin/main` before it can be the experiment trust root.
- Real acceptance uses `gpt-5.4-mini`; it never approves PPE or archives the
  Eval-owned Execution task.
