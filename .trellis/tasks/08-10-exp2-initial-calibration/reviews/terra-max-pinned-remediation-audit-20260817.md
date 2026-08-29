# Terra Max pinned-image remediation audit — 2026-08-17

Model: `gpt-5.6-terra`, reasoning effort `max`; independent and read-only.

## Audit verdict

The reviewed uncommitted snapshot was `BLOCK` before source freeze. The review
confirmed all twelve declared remote references resolved to their stated
`linux/amd64` OCI manifest digests, the selected-image manifest digest was
valid, and v4 qualifications could not enter the v5 pool. It found four
remaining transition weaknesses.

## Findings and resolution

1. **Case pin substitution:** prefix/format checks could accept another
   selected case's self-consistent image. Runtime identity now exports the
   ordered case-to-pin map; pool validation and protocol construction require
   exact equality with that case's `source_ref` and manifest digest.
2. **Null timeout misclassification:** the normal submission-failure branch
   could treat a timeout in `error_ids` as unresolved without a harness error.
   Null/base now treats every `error_ids` classification as a harness error;
   only explicit `empty_patch_ids` or a complete unresolved report is valid.
3. **Treatment scope was documentary only:** v2 protocol construction and
   parsing now require exactly
   `.agents/role-skills/execution/writer/autobugfix-writer/SKILL.md`; CLI choices
   and parameterized rejection tests enforce the same contract.
4. **Concurrent reconciliation race:** a non-blocking POSIX study lease now
   spans replay, intent, dispatch/reconcile, and terminal append. A second
   executing resume returns `in_progress`. Initialization uses the same lease
   and hard-link compare-and-create instead of replacement writes. Same-process
   and separate-process lock tests cover the boundary.

During remediation, a separate self-review also corrected OCI terminology:
registry config/compressed-layer digests are extracted from the immutable
manifest, while Docker local image IDs and rootfs diff IDs are recorded and
checked as distinct fields.

This file records findings and implemented responses; a fresh independent
recheck on the clean commit remains required before `PROCEED`.
