Active task: .trellis/tasks/08-10-exp2-initial-calibration

You are the final independent executability and metrics reviewer. Run with
OpenAI model `gpt-5.6-terra` and inherited `model_reasoning_effort = "max"`.

READ-ONLY REVIEW. Do not edit any file, run `task.py start`, create or mutate
Trellis/Operator/Eval study state, download/pull/build images, invoke a real
benchmark model/SDK call, execute a benchmark case, approve a budget, or enter
human approval text. Deterministic source tests, CLI help, Git inspection,
Docker/image inventory, static parsing, and read-only doctor/preflight checks
are allowed.

Review the latest resume-first PRD, design, implementation plan, manifests,
independent audit, committed source at `ebb994f`, isolated worktree, current
main/worktree status, CLI surfaces, tests, cached dataset, Docker readiness,
and host isolation prerequisites.

## Questions

1. Is the latest planning package internally complete and implementable?
2. Can the experiment be executed now from existing source, or must source
   hardening happen first? Cite exact code paths and tests.
3. Are the 2 calibration, 10 H0, source-2, transfer-3, reserve-5 roles and
   result visibility coherent?
4. Are candidate provenance, one-revision binding, rollback, and report
   transitions executable rather than schema-only?
5. Which host/image/credential/isolation facts are proven, missing, or require
   human action?
6. Are the following final metrics well-defined, derivable from trusted
   artifacts, and honest for a resume pilot?

## Proposed final metrics

### A. Run integrity

- attempted/terminal/official/invalid case counts by stage;
- preflight rejection count and SDK-call-after-rejection count (must be zero);
- completed-case re-execution count after resume (must be zero);
- evidence completeness rate;
- valid noninterference receipt rate;
- frozen-input drift count;
- protected-root/credential exposure count.

### B. Repair effectiveness

- H0 resolved count/rate on fixed 10-case denominator;
- source and transfer 2x2 paired cells: both-pass, both-fail, rescue,
  observed-regression, invalid arm;
- source rescue count, transfer rescue count, transfer regression count;
- net paired gain = (rescues - regressions) / fixed paired denominator;
- candidate retain/rollback/no-signal outcome.

### C. Loop behavior

- first-attempt success count/rate;
- second-attempt loop-rescue count/rate, conditional denominator clearly
  reported;
- failure-stage distribution;
- empty-patch rate and patch lines/files changed;
- visible-verifier and deterministic/full-check outcomes.

### D. Governance/information flow

- attribution-to-transition binding completeness;
- requested versus actual changed-path scope;
- out-of-allowlist/stale/forged transition rejection count;
- number of governed revisions (exactly 0 or 1);
- future-case/private-field leakage count;
- trusted rollback receipt and post-rollback identity when applicable.

### E. Efficiency

- wall-clock duration per case/stage/study;
- model calls, attempts, input/cached/output/reasoning tokens;
- estimated API cost per case/stage/study;
- cost per terminal case and, only when nonzero, cost per observed rescue;
- verifier/scorer/Docker time separated from model time.

### F. Claim discipline

- source results labeled selection-exposed development evidence;
- transfer results labeled three-repository pilot evidence;
- reserve/Live/Pro denominator remains zero in this MVP;
- no statistical-significance, broad-generalization, production-safety, or
  paper claim.

Identify missing formulas, denominator errors, untrusted inputs, duplicated
metrics, and metrics that should not be reported.

## Required checks

At minimum:

- parse task JSON and both JSONL manifests;
- `git diff --check` for task documents;
- inspect current task status and worktrees;
- inspect relevant coordinator/CLI/service/Operator code;
- run focused deterministic tests if safe;
- inspect `autobugfix eval exp2 --help` and benchmark doctor readiness;
- inspect selected case presence and existing Docker image state without
  pulling anything.

## Output

Report:

1. severity-ranked findings;
2. corrected final metric dictionary with exact numerators/denominators and
   trusted source artifact for each metric family;
3. proven versus missing preconditions;
4. the exact safe next command/action;
5. one decision: `ALLOW`, `BLOCK`, or `NEEDS_HUMAN`.

`ALLOW` means source and environment can safely begin real calibration now.
`BLOCK` means implementation or deterministic prerequisites remain.
`NEEDS_HUMAN` means only an external authority/budget/credential choice blocks
otherwise executable source.
