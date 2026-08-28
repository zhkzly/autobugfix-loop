# Exp2 evolution ledger — resume-first execution-harness pilot

Iterative evolution of a bug-fixing agent system under a hard regression
gate, per the evolution / regression / held-out three-split protocol.

- Benchmark: SWE-bench Verified, 10 frozen optimization cases (astropy,
  django, matplotlib, xarray, sympy, seaborn, requests, pytest,
  scikit-learn, sphinx), two writer attempts per case, official scorer.
- Model: gpt-5.4-mini, reasoning effort pinned per lineage (low, then a
  controlled low->medium switch in v3r6).
- Every revision goes through a governed chain: triage -> budget ->
  Writer edits exactly one skill file -> fast/full checks -> candidate
  commit -> subject-gate experiment -> integrate -> export; every report
  is content-addressed and reproducible.

## Rounds

| Round | Revision hypothesis | Decision | Key outcome |
| --- | --- | --- | --- |
| r4 | Writer empty-patch prohibition | retain_transfer_rescue | first rescue; subject f529f09d -> fe0f2fea |
| c6 | derive acceptance tests + verify locally | rollback | xarray rescued but matplotlib regressed; gate fired |
| c7 | requirement checklist | retain_no_gain | no case movement |
| c8 | same checklist @ medium compute | retain_transfer_rescue | guidance x compute interaction: no-gain at low, rescue at medium |
| v3r3 | requirement enumeration (full-regression protocol v3) | blocked_invalid | seaborn observed-regression + timeout invalid arm |
| v3r4 | type-scoped guidance (feature checklists / bugfix minimal-diff) | retain_transfer_rescue | matplotlib rescued, regression 4/4 green |
| v3r5b | structured attempt-2 retry discipline | blocked_invalid | retry lever never fires for official_eval failures (structural); sympy infra race (fixed by c969818) |
| v3r6 | concrete example execution @ medium | rollback | pytest rescued for the first time, seaborn regressed; gate fired |

Decision distribution: 3 retain / 2 rollback / 2 blocked / 1 no-gain.
Regression gate fired correctly 3/3 times (c6, v3r3, v3r6) with zero
leaked regressions; every retained candidate kept the complete
H0-resolved set green on the next round's H0.

## Resolution trajectory (H0 on the frozen subject)

4/10 (r4 baseline) -> 5/10 steady state, 6/10 peak at the medium
lineage. Chronic core (astropy, sympy, sphinx) unmoved by seven guidance
revisions at low or medium; borderline cases (seaborn, matplotlib,
pytest) oscillate with every revision — the seesaw effect that defines
the plateau.

## Held-out final (SWE-bench-Live MultiLang, plain run)

Subject b9c86b0 @ medium. Six cases, six repositories, five languages,
deterministic snapshot-order selection, excluding the one protocol-listed
instance. mongoose (JavaScript) resolved; blocky (Go), AntennaPod (Java),
automq (Java), harper (C++) unresolved at the visible verifier; Avalonia
(C#) infrastructure-invalid (MSBuild daemon keeps the worktree unstable).
Effective resolution 1/5.

## Findings

1. Guidance x compute interaction: identical verification guidance is
   ineffective at low reasoning and produces rescues at medium; compute
   alone moves nothing.
2. Seesaw effect: writer-skill revisions rescue one borderline case while
   perturbing another, so prompt-level evolution cannot accumulate
   conquests on a fixed small model.
3. Retry-lever unreachability: the loop stops at the first
   visible-verifier pass, so attempt-2 guidance can never engage for
   cases that fail only the hidden official suite — a runtime policy
   property, not a skill property.

## Milestone tags

- exp2/retain-r4 (fe0f2fea) — first retained candidate
- exp2/retain-v3r4 (d2811289) — type-scoped guidance retention
- exp2/final-subject-medium (b9c86b0) — final frozen subject used for
  the held-out final

Per-round decision reports: `round-reports/`. Held-out per-case reports:
`heldout-reports/`. Full trusted state (events, receipts, runs) remains
in the local `.autobugfix/` store outside git.
