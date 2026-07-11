# Review: Operator and Eval benchmark foundations

The current platform is in inline mode, so no reviewer subagents were used or
claimed. The main agent completed the required reviewer passes sequentially.

## Execution reviewer

- Eval still creates tasks through `AutobugfixService` and lets Execution own
  task state, Writer worktrees, verifier events, and human gates.
- The pinned ItsDangerous acceptance changed only the task worktree, ran 9 real
  pytest cases, and left the configured main checkout at the original SHA and
  clean.
- No Execution or target-main mutation semantic was changed.

## Memory reviewer

- Memory code and approval semantics were not changed.
- Real accepted Execution evidence produced raw packet, digest, lint result,
  maintainer output, and a proposal that remained `pending`.
- Eval and Operator still cannot approve Memory proposals.

## Eval reviewer

- Case schema is versioned and records adapter/source identity, task type,
  attachments, environment, execution command, and hidden-oracle metadata.
- `LocalGitAdapter` performs real Git isolation and fails closed when asked to
  emulate a container environment it does not support.
- Independent command-oracle results are authoritative; oracle diff equality
  is diagnostic only. Alternative-correct and identical-but-failing patches
  have explicit regression tests.
- Schema/setup/oracle failures produce separate harness reports, non-zero CLI
  results, raw logs, summaries, and diagnosis.

## Codex runtime reviewer

- Production CLI exposes only the real Codex Eval mode. Fake backends remain
  injectable only in unit tests.
- Real Operator and public-repository acceptances ran the preview Python SDK
  with `gpt-5.4-mini`; isolated role runtimes kept project hooks disabled.
- One external stream disconnect was retained as SDK evidence; a clean retry
  completed successfully without a fake fallback.

## Portability and privacy reviewer

- No internal repository name, username, company command, `/Users/...`, or
  `/home/...` literal was introduced in production code/config.
- Committed experiment values reject sensitive key names, multiline values,
  home-relative values, and POSIX/Windows absolute local paths.
- Baseline profile/input/patch digests are recomputed by trusted code. GitHub
  admission fetches full Git history and uploads Guard logs from the trusted
  checkout.

## Acceptance reviewer

- `65 passed` in the final pre-review full unit suite.
- Compileall, diff check, and role-skill validation passed.
- Independent `validate_operator_policy.py` reran two Bubblewrap checks and the
  candidate experiment, then accepted the patch-bound receipt.
- Real Operator E2E reached `VERIFIED` and produced a promotion receipt.
- Real public-repository E2E completed Execution, human gate/archive, Memory,
  and a second Eval Execution with decision `pass`.

## Findings fixed during review

1. Remote bundle validation ignored performance baselines. It now loads the
   baseline from the trusted PR base and reruns its embedded contract.
2. A baseline could not be both committed for CI and exactly equal to request
   base. The Guard now permits only intervening baseline-metadata commits and
   rejects later behavior changes.
3. Scope expansion accepted an unrelated path for a new layer. Added layers
   now require a path classified to each layer; broad patterns that may touch a
   protected path elevate authority before Writer launch.
4. `pass_rate: 1.0` was incorrectly mandatory for all benchmarks. The metric
   now enforces zero absolute regression from the trusted baseline, allowing
   measurable partial improvements.
5. The real Operator acceptance ran an independent behavior assertion only
   after `VERIFIED`. That assertion is now a Guard-owned fast/full/experiment
   command, with a bounded feedback/retry path before verification.

## Residual risks and next task

- Official SWE-bench download, Docker image construction, hidden official
  scorer, and reviewed bug-only manifest remain intentionally unimplemented.
- Generic receipts currently measure command-level pass rate. The official
  adapter must add case-level repair metrics before optimization claims.
- A credentialed network experiment executes candidate code. Before unattended
  remote benchmark optimization, run it in an ephemeral least-privilege runner
  or a credential-isolating proxy; Bubblewrap prevents state writes but is not
  a hostile-code secret boundary when network and model credentials coexist.
- `actionlint` is not installed locally; workflow structure is covered by unit
  assertions, but the GitHub-hosted workflow itself runs only after push.
