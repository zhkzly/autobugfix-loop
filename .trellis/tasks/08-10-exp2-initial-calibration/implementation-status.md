# Exp2 resume-first MVP implementation status

## Source gate

Current v2 source implements:

- protected-only formal execution through the existing SWE Eval service;
- one-case intent, canonical evidence adoption, scorer-only retry, and durable
  locked/CAS/fsync journal recovery;
- separate calibration and pilot state roots;
- qualified OCI identities and fixed 2+10 cohort;
- empty Memory spec/materialized-tree binding;
- source-only H0 Operator handoff and service-issued attribution;
- one governed candidate transition and Eval-authorized rollback;
- source/transfer paired metrics, usage, loop behavior, patch shape, and claim
  lint.

Final pre-commit checks on 2026-08-17:

- full pytest: `360 passed, 1 skipped`;
- compileall: passed;
- fatal Ruff rules: passed;
- role-skill validation: passed;
- `git diff --check`: passed;
- SWE-bench Verified doctor: passed;
- independent Terra Max review: all code blockers closed; only clean commit and
  apparatus receipt remained before source freeze.

## Remaining execution sequence

1. Commit this source snapshot.
2. Rerun structured source checks on the clean commit and issue the apparatus
   receipt.
3. Qualify only the selected 12 Verified cases and build the v2 protocol.
4. Build/init/run the two-case calibration, including forced resume proof.
5. If calibration succeeds, prepare the ten-case visible manifest and H0 Study.
6. Stop at the human budget approval gate before any governed candidate Writer
   call; never synthesize approval.
