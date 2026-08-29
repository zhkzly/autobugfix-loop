# Exp2 resume-first MVP metric contract

## Reporting populations

| Population | Scheduled denominator | Formal role |
| --- | ---: | --- |
| Calibration | 2 | apparatus integrity only; no effect rate |
| H0 baseline | 10 | fixed repo-unique feasibility baseline |
| H1 source | 2 | selection-exposed development evidence |
| H1 transfer | 3 | optimizer-unexposed repository pilot |
| Reserve | 0 executed in MVP | report `not_run`, never 0% |
| Live / Pro | 0 executed in MVP | report `not_run`, never 0% |

Every metric is reported separately by population. Source and transfer are
never pooled into a five-case effectiveness rate.

## A. Run integrity

### A1. Coverage

For each stage:

- `started_coverage = unique_case_attempt_started / scheduled_cases`;
- `terminal_coverage = unique_terminal_case_receipts / scheduled_cases`;
- `official_coverage = official_terminal_receipts / scheduled_cases`;
- `invalid_coverage = invalid_terminal_receipts / scheduled_cases`.

Terminal statuses partition every scheduled case exactly once. Counts and
denominators are always shown alongside rates.

Authority: append-only attempt intent and terminal receipt ledger.

### A2. Preflight safety

- `sdk_after_rejected_preflight = SDK calls whose case preflight was rejected`;
- required value: zero;
- report raw `0 / preflight_rejections`, including `0 / 0`.

Authority: protected broker command plus isolated SDK-worker call receipts.

### A3. Resume safety

- `completed_case_reexecution_count`: normal Execution attempts started for a
  case/stage/arm that already had a valid terminal receipt;
- required value: zero;
- denominator: completed receipts considered during each resume.

Scorer-only retry from the same frozen submission is reported separately and
is not a Writer re-execution.

Authority: reconciliation events, run IDs, frozen submissions, SDK ledger.

### A4. Evidence validity

- `evidence_completeness = complete_terminal_receipts / terminal_receipts`;
- `noninterference_validity = valid_noninterference_receipts /
  official_terminal_receipts`;
- frozen-input drift, event-chain failure, protected-root mutation, and
  credential exposure are raw counts with required value zero.

Invalid cases are excluded only from the noninterference denominator when no
official result/submission exists; they remain in scheduled/terminal counts.

Authority: terminal receipts and referenced trusted artifacts.

## B. Repair effectiveness

### B1. H0 baseline

- `h0_resolved = resolved official H0 reports`;
- `h0_resolved_rate = h0_resolved / 10` only when all ten H0 arms are
  apparatus-valid;
- otherwise rate is `null` and invalid arms are reported separately.

Authority: H0 official-result receipts.

### B2. Paired outcome cells

Produce separate source (`N=2`) and transfer (`N=3`) tables:

- both-pass;
- both-fail;
- rescue: H0 fail, H1 pass;
- observed-regression: H0 pass, H1 fail;
- invalid-H0-only;
- invalid-H1-only;
- both-invalid.

Authority: matched H0/H1 official terminal receipts and the trusted reducer.

### B3. Net paired gain

For each valid population:

```text
net_paired_gain = (rescues - observed_regressions) / fixed_population_N
```

Compute only when `invalid_any = 0`; otherwise report `null`, not zero.
`H1_resolved - H0_resolved` is a consistency check, not an additional result.

### B4. Candidate decision

Exactly one terminal value:

- `no_signal`;
- `blocked_invalid`;
- `rollback`;
- `retain_transfer_rescue`;
- `retain_no_gain`.

Rollback requires a trusted Operator rollback receipt. A source-only rescue
cannot produce `retain_transfer_rescue`.

## C. Loop behavior

### C1. Attempts

- first-attempt resolved rate:
  cases with official resolution and exactly one Writer attempt /
  official-terminal cases;
- loop-rescue rate:
  cases whose first visible-verifier attempt failed and whose genuine second
  Writer attempt produced the final officially resolved submission /
  cases reaching a second Writer attempt.

Scorer-only retries are not Writer attempts. Report both numerator and
conditional denominator.

### C2. Failure and patch shape

- one primary terminal failure-stage count per terminal case;
- empty-patch rate = zero-diff frozen submissions / frozen submissions;
- changed lines/files from the frozen submission diff;
- visible verifier, fast check, and full check outcomes as raw counts.

Authority: Execution ledger, frozen submission, trusted check records.

## D. Governance and information flow

- transition completeness =
  valid complete candidate-transition receipts / accepted candidate locks;
  report `N/A` when no candidate exists;
- requested/allowed/actual changed-path sets and out-of-scope file count;
- forged, stale, out-of-scope, or audience-leaking input rejection counts;
- governed revision count: exactly zero or one;
- future/private field delivery count: required zero;
- rollback receipt and post-rollback line/head/tree identity when applicable.

Zero leakage is claimable only when every projection dispatch has an
audience-delivery audit record.

Authority: Operator store, check/integration records, transition receipt,
projection/audience ledger, Git.

## E. Efficiency

- case wall time: attempt-intent timestamp to terminal receipt;
- stage/study wall time: first stage intent to terminal stage/report event,
  never the sum of case durations;
- model calls and attempts;
- service-observed input, cached-input, output, and reasoning tokens;
- model time, verifier time, scorer time, Docker/materialization time;
- API cost from observed token categories and a pinned pricing snapshot;
- cost per terminal case = stage model cost / terminal cases;
- incremental H1 evaluation cost per observed transfer rescue =
  H1 source+transfer model cost / transfer rescues, only when transfer rescues
  are nonzero.

Unknown usage/pricing remains `null`; it is never inferred from logs owned by
the candidate.

## F. Claim discipline

Required labels:

- calibration: apparatus evidence, no capability rate;
- source: selection-exposed development evidence;
- transfer: three-repository optimizer-unexposed pilot evidence;
- reserve/Live/Pro: `not_run`.

Run claim lint over every public report artifact. Required violations: zero.

Do not report:

- a combined five-case H1 effectiveness rate;
- statistical significance or population confidence;
- broad repository/language/benchmark generalization;
- “zero regression risk”;
- production safety/readiness;
- reserve/Live/Pro as 0%;
- cost per rescue when rescue count is zero;
- paper or leaderboard claims.

## Resume-safe result sentence

After real results, fill only observed values:

> Built a governed, resumable coding-agent harness pilot over ten repo-unique
> SWE-bench Verified H0 cases; produced one evidence-bound Execution-harness
> revision and measured [source cells] on two development repositories and
> [transfer cells] on three optimizer-unexposed repositories, with [invalid
> count], [rollback/retain outcome], [calls/tokens/cost], and a complete
> immutable evidence chain.
