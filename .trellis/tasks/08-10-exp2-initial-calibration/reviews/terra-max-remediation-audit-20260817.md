# Terra Max remediation audit — 2026-08-17

## Prior blockers

All seven blockers from `terra-max-final-source-audit-20260817.md` are
`CLOSED` in the current implementation:

1. canonical manifest-verified broker evidence tree;
2. protected-only formal protocol with outer/SDK Bubblewrap and exact cwd;
3. trusted state root plus locked/CAS/fsync/torn-tail journal;
4. bounded Eval H0 handoff and Operator-rederived candidate provenance;
5. Eval rollback authorization before Operator mutation;
6. reopened apparatus/check/fixture/policy/skill/runtime authorities;
7. ledger/submission-derived usage, loop, patch, and report evidence.

## New release-critical findings

1. `guard_root` is checked for disposable overlap but is not passed into the
   outer Bubblewrap masks or inner SDK hidden paths, and receipt validation
   does not require it.
2. Runtime checks apparatus `HEAD` and committed tree but does not require a
   clean worktree immediately before dispatch. Dirty source could execute
   under a frozen SHA.

## Decision

- Source freeze: `BLOCK`.
- Real two-case calibration: `BLOCK`.

Required closure: mask/prove `guard_root`, enforce clean checkout immediately
before dispatch, rerun checks, commit, and issue a clean apparatus receipt.

The reviewer used `gpt-5.6-terra` at reasoning effort `max`, read-only. It made
no edits, model calls, image operations, budget changes, approvals, or commits.
