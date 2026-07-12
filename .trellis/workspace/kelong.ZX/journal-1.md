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


## Session 3: Governed experiment integration lines

**Date**: 2026-07-11
**Task**: Governed experiment integration lines
**Branch**: `agent/experiment-integration-lines`

### Summary

Implemented Governance V4 studies, independent H0 experiment lines, human-gated Mini budgets, metered SDK roles, trusted Git/SQLite CAS integration, immutable checkpoints, history-preserving rollback, role skills, tests, and real Operator E2E.

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `36c29b8` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 4: Raw Codex SDK baseline

**Date**: 2026-07-12
**Task**: Raw Codex SDK baseline
**Branch**: `experiment/raw-codex-sdk-baseline`

### Summary

Built and froze an isolated direct openai-codex 0.1.0b3 comparator, ran 16 Defects4J cases once with gpt-5.4-mini, and produced a digest-bound H0 comparison: primary H0 11/13 versus Raw 10/13, zero harness errors.

### Main Changes

- Added a separately locked direct `openai_codex` baseline and an Eval-owned
  Bubblewrap control plane that freezes real Git diffs before official scoring.
- Froze commit `9b144bb`, manifest digest `7dc74860255269adda3b3176bbece5f17c477c5802c4ddd9015d2e7005c99bd9`,
  model `gpt-5.4-mini`, SDK `0.1.0b3`, prompt, timeout, case order, Docker IDs,
  and the H0 report before formal generation.
- Ran all 16 cases exactly once: primary H0 `11/13` versus Raw `10/13`;
  all-case H0 `14/16` versus Raw `12/16`; zero harness errors, two timeouts,
  and no oracle-to-generator feedback.
- Retained the digest-bound local evidence under
  `.autobugfix/raw-codex-baseline/formal-runs/raw-codex-formal-16-9b144bb`.

### Git Commits

| Hash | Message |
|------|---------|
| `9b144bb` | (see git log) |
| `e2605d8` | (see git log) |

### Testing

- [OK] Root test suite: `194 passed in 30.91s`
- [OK] Standalone baseline suite: `6 passed in 0.35s`
- [OK] Root and standalone `compileall`
- [OK] `git diff --check`, role-skill validation, and Trellis task validation
- [OK] 16/16 noninterference receipts passed; temporary auth bridges removed

### Status

[OK] **Completed**

### Next Steps

- Keep this result immutable. Any H0 optimization or broader experiment must
  use a separate branch and a newly frozen protocol rather than rerunning an
  unfavorable case from this baseline.
