# Terra Max qualification audit — 2026-08-17

Model: `gpt-5.6-terra`, reasoning effort `max`; independent read-only review.

## Verdict

`BLOCK` before protocol/apparatus freeze or calibration.

The first Matplotlib qualification applied the gold patch and passed its F2P
test, but the official scorer observed 316 P2P failures in both runs. The
review correctly classified this as a qualification/build-apparatus failure,
not a model or benchmark-task failure. It required preserving the ineligible
receipt, fixing reproducible image authority, and rerunning affected
qualification without weakening the scorer or replacing the agreed case.

The review also found two source-contract gaps:

- the implementation ran repeated gold only although the plan requires a
  null/base negative probe; and
- the candidate validator froze the H0 Execution role-skill digest even though
  the design permits an allowlisted Execution skill revision.

It rejected the proposed four-file allowlist as mixing Execution, shared
runtime, protected runner behavior, and two independent skill mechanisms. The
accepted remediation uses one treatment path only:
`.agents/role-skills/execution/writer/autobugfix-writer/SKILL.md`.

## Resolution criteria

Proceed only after a clean source commit and checks prove:

1. selected images come from immutable official OCI manifest digests;
2. every selected case passes two gold runs and an unresolved null/base run on
   one image, then materializes;
3. pre-remediation qualification receipts cannot enter the pool; and
4. candidate skill-digest drift is accepted iff the actual allowlisted skill
   changed.

This review does not authorize H0 budget, a candidate Writer, or any paper-scale
claim.
