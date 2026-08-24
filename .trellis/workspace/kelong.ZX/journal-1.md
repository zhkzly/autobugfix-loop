# Journal - kelong.ZX (Part 1)

> AI development session journal
> Started: 2026-07-09

---


## Session 1: Operator governance V3 and real E2E

**Date**: 2026-07-11
**Task**: Operator governance V3 and real E2E
**Branch**: `agent/operator-governance-policy`

### Summary

Rebuilt Operator as a trusted transition harness, assigned Codex hooks only to operator_host, enforced isolated SDK roles, added real ItsDangerous Execution-Memory-Eval acceptance, and passed real Operator promotion preparation.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `9c45301` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 2: Harden Operator and Eval benchmark foundations

**Date**: 2026-07-11
**Task**: Harden Operator and Eval benchmark foundations
**Branch**: `agent/operator-governance-policy`

### Summary

Added deterministic Operator scope ownership, trusted baseline receipts and remote admission, versioned Eval adapters, tests-first scoring, observable harness failures, and real Operator/public-repository acceptance coverage.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `01b40b6` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 3: Exp2 execution-only coordinator

**Date**: 2026-08-09
**Task**: Exp2 execution-only coordinator
**Branch**: `main`

### Summary

Implemented the frozen Exp2 execution-only apparatus on the isolated experiment branch: required study references, empty-Memory fixture identity, workspace-only fail-closed preflight, direct SDK/worktree receipts, bounded coordinator transitions, public/sealed projections, and CLI wiring. Verified with 341 passed and 1 skipped, role skills, compileall, diff check, and a passing benchmark doctor in the Docker-enabled host; formal direct execution remains blocked until a disposable read-only authority environment is supplied.

### Git Commits

| Hash | Message |
|------|---------|
| `ebb994f` | (see git log) |

### Status

[OK] **Completed**


## Session 4: Exp2 initial calibration executed (crash-consistency + full run)

**Date**: 2026-08-20
**Task**: Exp2 initial calibration executed (crash-consistency + full run)
**Branch**: `experiment/exp2-resume-mvp-v2`

### Summary

Closed the four-round Memory-binding review cycle (PROCEED at 649546f), rebuilt the trusted identity chain, and executed the two-case calibration pilot for real: study r1 proved crash-consistency via forced SIGKILL mid-case (Flask receipt untouched, interrupted attempt honestly reconciled as execution_infrastructure_invalid, CALIBRATION_BLOCKED), study r2 ran clean to CALIBRATION_COMPLETE and published the v2 report (Flask resolved twice, Pylint official-terminal repair_failure; official_coverage/evidence_completeness/noninterference_validity all 1.0).

### Main Changes

- exp2_resume: removed the coordinator Memory relabel primitive; executor dict results carrying memory_digest are rejected; receipts bind the official report value
- operator/service + subject_broker: uniform frozen-fixture digest binding (8d05dbaf) across all three report producers via directory-inclusive snapshot digest; adoption-time live Memory root revalidation
- Real execution evidence: exp2-calibration-649546f-r1 (crash study, terminal receipts 2, blocked-by-design) and exp2-calibration-649546f-r2 (CALIBRATION_COMPLETE -> REPORTED, report digest f7e85ab0)

### Git Commits

| Hash | Message |
|------|---------|
| `6db7d00` | (see git log) |
| `649546f` | (see git log) |

### Testing

- [OK] full gate at 649546f: 408 passed / 1 skipped; compileall, role-skills, diff-check, benchmark doctor all green
- [OK] forced-interruption validation: SIGKILL after pylint case_attempt_started; readiness reported reconciliation_required with the exact open intent; recovery did not rerun Flask
- [OK] r2 report metrics: official_coverage 1.0, evidence_completeness 1.0, noninterference_validity 1.0, first_attempt_resolved_rate 0.5, 0 infrastructure failures, 4 model calls, ~448s model time

### Status

[OK] **Completed**

### Next Steps

- Proceed to the resume_pilot phases: H0 ten-repository baseline, one governed Execution-harness revision, paired source/transfer outcomes
- Open PR for experiment/exp2-resume-mvp-v2 into experiment/exp2-execution-only-20260809

## Session 5: Trust anchor advanced (Path A) and r3 pilot H0 launched

**Date**: 2026-08-24
**Task**: Exp2 initial calibration (pilot phase)
**Branch**: `experiment/exp2-resume-mvp-v2`

### Summary

User explicitly authorized Path A: both `trusted_ref` locations in the exp2 worktree's `.autobugfix/config.yaml` advanced from db0f2b58 to 649546f93b (the justification already on record: trellis-check PROCEED, 408-test full gate, r2 real calibration CALIBRATION_COMPLETE). Re-sealed the public manifest against the new anchor (exp2-resume-exp2-prep-4063467854218a2f9ce199b5, digest 4dcabe2b), created operator study #2 exp2-operator-649546f-r2 with explicit harness 649546f / base f529f09d / target H_general, exported H0 binding 8ee12c0d, built the r3 pilot plan (950a7e8c) binding the r2 calibration receipt + apparatus 0a28ab63 + new manifest + new binding, and initialized study exp2-pilot-649546f-r3 (burned r1/r2 IDs never reused). H0 execution started: case 1 astropy terminal `official_terminal` (resolved=false, honest measured failure, 2 writer attempts / 4 model calls / ~241s).

### Main Changes

- `.autobugfix/config.yaml` (exp2 worktree, gitignored): eval.benchmarks.guard.trusted_ref and operator.experiments.trusted_ref → 649546f93b (user-authorized change, named explicitly)
- Fresh sealed artifacts: manifest 4dcabe2b, operator study record 9847c833, H0 binding 8ee12c0d, protocol build 649546f-pilot-r3 (31a3e1bf), plan 950a7e8c, study state exp2-pilot-649546f-r3
- handoff.md sections 3–4 rewritten as the Path A decision + artifact trail

### Testing

- [OK] benchmark doctor green in the exp2 worktree (snapshot c104f840, 500 rows)
- [OK] prepare-manifest-v2 guard authority resolves at 649546f (HEAD == trusted_ref, clean tree)
- [OK] H0: ten sequential resume --execute runs, all official_terminal, zero invalid arms
- [BLOCKED] governed candidate chain: preflight baseline gate structurally unsatisfiable (see below)

### H0 baseline result (exp2-pilot-649546f-r3)

| Case | Slice | Terminal | Resolved |
|---|---|---|---|
| astropy__astropy-13398 | source | official_terminal | no |
| django__django-10097 | source | official_terminal | yes |
| matplotlib__matplotlib-24627 | transfer | official_terminal | yes |
| pydata__xarray-2905 | transfer | official_terminal | yes |
| sympy__sympy-13091 | transfer | official_terminal | no |
| mwaskom__seaborn-3187 | reserve | official_terminal | no |
| psf__requests-6028 | reserve | official_terminal | yes |
| pytest-dev__pytest-10051 | reserve | official_terminal | no |
| scikit-learn__scikit-learn-13439 | reserve | official_terminal | yes |
| sphinx-doc__sphinx-9229 | reserve | official_terminal | no |

5 resolved / 5 unresolved, 26 model calls, ~2033s model time. Feasibility gate passed
(astropy failure_stage=visible_verifier is Execution-owned).

### Blocker found (harness defect, first real contact)

The constitution requires a trusted performance baseline for every behavior-layer request.
For a line-bound candidate the request base is the frozen H0 subject f529f09d; the baseline
must be committed in that commit's tree and be behavior-fresh, but capture_baseline can only
measure at operator.experiments.trusted_ref (649546f, not an ancestor of f529f09d), and no
sanctioned path advances the line head with a baseline-only commit before the candidate.
Tests never caught this because tests/helpers.py sets baseline_required_layers=[]. Three
requests (docs_skills wrong-layer; execution without baseline; execution + pr2-real-e2e
stale baseline) were closed as superseded; study r3 stays in CANDIDATE_TRANSITION_AWAITING
with all evidence preserved. Full analysis + remediation options in handoff.md §5.

### Resolution (same session, later)

Four governed-quality harness fixes landed on the branch (a6a167d line-bound baseline
gate; 5672c8f sandbox DNS for systemd-resolved hosts; 5c922ba→5c922bc line-bound
production invariants + candidate git metadata binding; ba3b67d baseline fallback
passthrough), each passing the full gate (409→410 passed). Anchor advanced to f8bec35
(user-authorized), fresh calibration exp2-calibration-f8bec35-r1 CALIBRATION_COMPLETE,
study exp2-pilot-f8bec35-r4 executed end to end: H0 10/10 official_terminal (2 resolved /
8 unresolved), governed candidate (Writer skill: forbid empty-patch termination + feature
sketch; in-scope blind patch after retry lottery caused by the kernel's nested-userns ban),
verify fast/full green against a subject-measured baseline, integrated, exported, and H1
run on the frozen apparatus checkout.

**FINAL: PILOT_COMPLETE → REPORTED, decision `retain_transfer_rescue`.**
matplotlib and xarray were observed transfer rescues with zero transfer regressions;
django both-pass; astropy both-fail with the attributed mechanism confirmed (first-attempt
patch non-empty under H1). Report digest 41d3b444. No broader claim is made.

### Status

[OK] **Completed** — REPORTED, retain_transfer_rescue

### Next Steps

- PR #17 now carries the four harness fixes + pilot evidence; merge through trusted-merge
- Optional follow-up task: streamline the wave-3→wave-8 double approval (user friction)
- Optional: refresh the two durable memory notes (r3 blocker → resolved)
