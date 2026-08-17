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

Initial v2 apparatus checks at commit `393fc29` on 2026-08-17:

- full pytest: `360 passed, 1 skipped`;
- compileall: passed;
- fatal Ruff rules: passed;
- role-skill validation: passed;
- `git diff --check`: passed;
- SWE-bench Verified doctor: passed;
- independent Terra Max review: all code blockers closed; only clean commit and
  apparatus receipt remained before source freeze.

## Qualification remediation

The first real selected-image qualification correctly stopped before protocol
freeze: Matplotlib gold resolved its F2P test but failed 316 P2P tests twice in
a locally rebuilt environment. The first causal deviation was 2026 dependency
resolution of a historical open-ended environment, not the model, patch, or
scorer. A pinned official image with manifest `526b4c5c...` passed the complete
gold boundary.

The remediation now:

- binds all twelve selected official amd64 image manifests in
  `benchmarks/swe-exp2-resume-selected-images-v1.yaml`;
- imports and verifies each digest before qualification;
- records registry config/blob-layer digests separately from the local Docker
  image ID and rootfs diff IDs;
- requires two gold passes plus an unresolved null/base score and stable image;
- ignores pre-v5 qualification receipts;
- narrows the candidate treatment to the Execution Writer skill only; and
- permits an Execution skill digest change only when that skill is in the
  actual candidate diff.
- holds a process-exclusive study lease across dispatch/reconciliation so a
  second resume cannot invalidate a live attempt.

All twelve v5 qualifications now pass. The latest full source run completed
with `378 passed, 1 skipped`; compileall, role-skill validation, fatal Ruff,
diff-check, and SWE doctor pass. Protocol construction was also hardened to
replay the current qualification metadata rather than refetching historical
requirements through `make_test_spec`; a real twelve-image protocol build
succeeded.

A fresh independent `gpt-5.6-terra`/`max` recheck returned `PROCEED` for the
source gate after verifying exact per-case pins, null error handling, the
single Writer-skill treatment, process-exclusive resume, and the separated OCI
registry/local identity fields.

## Empty-Memory isolation remediation

The first real calibration initialization at apparatus `c472e5d` stopped
before any model call because the project's canonical `.autobugfix-memory`
contains an approved user-preference entry. That stop is correct: Exp2's
frozen input is empty, but deleting or moving user Memory would violate the
project boundary. Study `exp2-calibration-c472e5d` is retained as a blocked
pre-dispatch audit artifact and will not be reused.

The current remediation adds an explicit dedicated empty-Memory capability:

- `build-plan-v2` requires an external `--memory-root`;
- the root must be absolute, unredirected, private, current-user owned,
  exactly empty in the frozen shape, and disjoint from protected/canonical
  state;
- Eval checks the root and digest at plan build and authority reopen;
- Operator accepts it only with `empty_memory_fixture=True` and the same
  explicit Guard root, then rechecks disjointness and the immutable snapshot
  digest; and
- tests prove canonical Memory remains unchanged and reject nonempty,
  permissive, redirected, or overlapping trees.

Focused tests pass `51/51`; the full source suite passes
`388 passed, 1 skipped`. Compileall, role-skill validation, and diff-check
pass. An independent `gpt-5.6-terra`/`max` recheck returned `PROCEED` after
its Guard-path, special-entry, and broker-proof findings were fixed and
retested. The remediation is not yet frozen into a clean apparatus commit, so
no replacement calibration study has been initialized yet.

## Remaining execution sequence

1. Independently review and commit the empty-Memory isolation remediation,
   then rerun structured source checks on that clean commit.
2. Rebuild/freeze the final v2 protocol and apparatus receipt and materialize
   a dedicated private empty-Memory root.
3. Build/init/run a new-ID two-case calibration, including forced resume proof.
4. If calibration succeeds, prepare the ten-case visible manifest and H0 Study.
5. Stop at the human budget approval gate before any governed candidate Writer
   call; never synthesize approval.
