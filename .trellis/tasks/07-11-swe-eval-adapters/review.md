# Review: official SWE Eval adapters

## Constitutional scope

Autobugfix remains a repo-agnostic loop-engineering and harness-engineering
control system. Execution owns real target-repository repair in isolated task
worktrees. Memory compiles accepted Execution evidence into human-reviewed
wiki/skills and cannot mutate Execution state or approve itself. Eval owns
dataset adaptation, isolated generation, frozen submissions, and independent
official scoring. Operator diagnoses and improves Autobugfix on governed
non-main experiment lines; trusted services, Git facts, deterministic checks,
external Guard authority, and humans own state transitions.

This candidate changes Eval, Memory, Operator governance, Codex runtime
isolation, and shared configuration. It does not change the four loop purposes.
Benchmark oracle/gold/hidden results remain downstream of frozen generation and
cannot become Execution Writer feedback.

## Final hardening decisions

- Raw is a standalone direct `openai-codex` SDK comparator. Its process imports
  no Autobugfix Execution, Memory, Evaluator, or production backend. A private
  one-call `CODEX_HOME` is removed after publication of allowlisted evidence.
- Production Codex roles use private broker/call homes. Host code publishes
  logs after worker exit and scrubs credentials even when publication fails.
  The shared Guard fingerprints the complete auth document and secret-bearing
  fields while treating ordinary account metadata as non-secret. Both the
  standard SDK worker and cancellable Operator Writer scan changed worktree
  files and private output before publication, redact matches, and fail closed.
- Memory packet/proposal provenance is digest-bound. Approval uses a
  cross-process lock, crash-recoverable activation journal, atomic replacement,
  and a final approved status; rejection shares the same lock. Proposal IDs
  cannot escape their authority root, and activation hashes and consumes one
  immutable patch byte sequence rather than reopening the path.
- Candidate Holdout binding, not later metric import, closes the experiment
  line with CAS before scoring. Failed, abandoned, or passing metrics therefore
  cannot become feedback for another Writer attempt. Passing metrics may create
  the immutable H_bug/H_general checkpoint; rollback preserves Git history.
- Public Optimization feedback enters line-bound triage only through a trusted,
  content-addressed Study evidence record bound to Study, cohort, treatment,
  subject SHA, binding digest, and source-record digest. Arbitrary evidence
  paths and cross-treatment transfer are rejected and WriterView revalidates
  the artifact. The registry accepts either an official SWE formal case or a
  repo-agnostic formal Optimization case containing a real command exit status,
  stdout/stderr digests, and noninterference proof.
- Human Guard Docker authority is a persistent mode-0600 Unix socket under the
  external mode-0700 Guard root. Its independently administered VM daemon must
  publish `autobugfix.guard.isolation=dedicated-vm-v1`; socket fingerprint,
  daemon ID/profile, and authority digest are pinned across all phases. The
  daemon must differ from regular Eval.
- Official SWE scorer code runs in Bubblewrap with host home, runtime state,
  Memory, Trellis, Codex config, and external Guard roots hidden. It receives
  only read-only source/runtime/cache mounts, a per-run output/client-state
  root, and the pinned dedicated-VM Docker socket; scorer network is unshared.
- Operator-visible Live identity audit scans instance and repository identities
  in path names and every regular file
  as bytes, including SQLite and opaque artifacts, across Eval, Raw, Operator
  state/worktrees/checkpoints/active releases/promotions, and Trellis roots.
- A formal unresolved case is a valid measurement. CLI failure status is
  reserved for harness failure; official verdicts are not execution control
  signals.

## Independent reviews

Earlier independent security reviewer `Averroes` and acceptance reviewer
`Aristotle` found the candidate-metric feedback window, mutable Docker authority,
credential-cleanup gaps, Raw backend coupling, incomplete binary contamination
audit, non-atomic Memory activation, and invalid formal CLI exit semantics.
Final acceptance reviewer `Jason` then found that candidate Holdout output was
still visible while the line remained open and that arbitrary triage paths
could transfer evidence between H_bug/H_general. Final security reviewer
`Erdos` independently found the same Holdout window plus cross-phase Docker
authority replacement, unsafe socket trust without a VM contract, writable
benchmark cache, repository-level Holdout contamination, Memory patch TOCTOU
and proposal path escape, and Raw cleanup-failure residue. All concrete findings
are addressed in source and focused tests; these reviewer passes are findings,
not merge approval.

The final main-agent sequential pass covered Execution, Memory, Eval, Codex
runtime, portability/privacy, and acceptance after those independent reviews.
No new subagent tool was available for this last delta, so no additional
independent reviewer is claimed. The Codex runtime pass found one remaining
custom-path defect: the cancellable Operator Writer cleaned its private call
home but did not scan output before publication. The shared Guard and a real
Bubblewrap worker regression now cover that path. The portability pass found
only deliberately forbidden examples in rebuild guardrail docs; production
source contains no internal repository, user, company, or local absolute path.

## Current real evidence

- Root suite: `315 passed` after the final production-service and acceptance
  fixes.
- Standalone Raw SDK suite: `6 passed`.
- Locked SWE harness suite: `1 passed`.
- Root and nested compileall: passed.
- `git diff --check`: passed on the candidate and is rerun after documentation
  changes before commit.
- Role-skill validator: `role skills valid`.
- Trellis task context validator: all entries valid.
- Verified doctor: passed against SWE-bench 4.1.0, dataset revision
  `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`, 500 rows, Docker 29.6.1,
  and Bubblewrap.
- Live doctor: passed against revision
  `608f7ae9ab8ea1f9f0d030fe04562cf6bd1a0c8b`, 743 rows, pinned Live/launch
  commits, Docker 29.6.1, and Bubblewrap.
- Current runtime:
  `sha256:3f6445541a9490719b70b37dba9b47d21333c0b61923c713462156ac603cc8f7`.
- Current protocol:
  `a2f8e4d30a1b9d542b802367fd7c25cd6ecd0f5d2fbf7d3dc00855d36b66bd40`.
- `astropy__astropy-12907` passed two isolated official gold scorer runs and
  source materialization. Qualification receipt:
  `4abcab1192e3f76e3fb10065008960a46cdaffe8baf824d761993f81f1ff8bc9`.
- Real public-repository acceptance passed against pinned ItsDangerous commit
  `672971d66a2ef9f85151e53283113f33d642dabd`: production `gpt-5.4-mini`
  repaired only `src/itsdangerous/encoding.py` in one Execution iteration,
  the configured pytest command reported `9 passed`, the target main checkout
  stayed clean at the injected fixture SHA, Memory produced a pending proposal,
  and the frozen isolated Eval decision was `pass`.
- Real Operator acceptance passed with production `gpt-5.4-mini`: request
  `operator-real`, Writer run `writer-20260715T110226-a2f61d33`, integration
  `integration-20260715T110357-694f0f4b`, terminal `CLOSED` line, and immutable
  checkpoint `operator-acceptance-study-H_bug`. The protected main branch stayed
  at H0.

The first sandboxed qualification attempt failed because the hidden home also
hid the locked harness venv's uv-managed CPython runtime. That run was correctly
recorded as `eligible: false` with a harness error. The harness was fixed by
read-only mounting only that pinned CPython runtime, and a fresh two-attempt
qualification then passed. No model or Writer consumed either result.

The Operator acceptance initially failed closed before Writer because its
expected-failing task tests were incorrectly reused as a passing performance
baseline and because its line-bound triage used an unregistered path. A later
full check rejected a candidate experiment whose profile did not match the
performance baseline. The harness now separates the always-pass H0/candidate
performance profile from the task-success validation profile, registers the
real pre-repair command result as digest-bound generic Study evidence, and
terminalizes the line before candidate scoring. These were deterministic
Operator harness corrections; no oracle result or Writer patch was converted
into task feedback or a skill answer.

A later production acceptance exposed a credential-Guard false positive:
preview SDK events legitimately repeat `account_id`, while the first scanner
classified every auth JSON string as a secret. The scanner now classifies the
whole auth document and secret-bearing fields instead; token and whole-file
leaks remain blocked. The final runtime review then found that Operator's
custom cancellable Writer publisher had not called the scanner at all. A real
isolated worker test proves that path now redacts a deliberately copied token,
records a failed WriterRun, and emits trusted feedback without credential
content. A fresh normal Operator acceptance passed after both corrections.

## Claims not made

- The other nine current-runtime Verified qualifications have not run.
- The six Human Guard Live cases have not been selected or qualified.
- The encrypted 10+6 preparation and sealing have not run.
- Formal H0, Raw, or H_general experiments have not run.
- Historical Raw pilots used superseded wiring and are not acceptance evidence
  for the current direct-SDK comparator.
- PR 10 is not merge-authorized by candidate-authored state. Trusted-base
  bootstrap approval and the base/main validator remain external human/CI
  gates.

## Remaining acceptance

1. Re-run current Verified/Live doctors if benchmark runtime source changes.
2. Qualify the remaining nine public cases and run the Human Guard Live cohort.
3. Prepare/seal the 10+6 manifest, then run frozen H0, independent Raw, and the
   separately governed H_general experiment without oracle feedback.
4. Obtain trusted-base authorization before merging PR 10.
