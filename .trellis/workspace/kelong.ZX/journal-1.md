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
