# Raw Codex SDK baseline result

## Experimental identity

- Date: 2026-07-12
- Treatment: one direct `openai_codex` thread and one turn per case
- Model: `gpt-5.4-mini`
- SDK: `openai-codex==0.1.0b3`
- Reasoning effort: `medium`
- Cases: Defects4J 3.0.1, 13 primary and three development cases
- Frozen runner commit:
  `9b144bb5fdcf994d80fcb42c5cc40ee452ab37f5`
- Frozen runner tree:
  `61716b0b9ac2073fb8da95846a697b46b0846f2a`
- Prepared manifest digest:
  `7dc74860255269adda3b3176bbece5f17c477c5802c4ddd9015d2e7005c99bd9`
- Frozen H0 report digest:
  `46bc91b819bf1eef300ff3c436fc83abb45b912bd8bbdf46d39811391186c89d`
- Formal run ID: `raw-codex-formal-16-9b144bb`

The formal run used the pre-registered case order and completed exactly one
SDK call for every case. No case was retried, no prompt or skill was changed,
and no official score or oracle diagnosis was returned to the SDK.

## Results

| Cohort | Autobugfix H0 | Raw SDK | Difference |
| --- | ---: | ---: | ---: |
| Primary | 11/13 (84.62%) | 10/13 (76.92%) | -7.69 pp |
| Development | 3/3 (100.00%) | 2/3 (66.67%) | -33.33 pp |
| All cases | 14/16 (87.50%) | 12/16 (75.00%) | -12.50 pp |

Primary paired outcomes were ten both-pass, two both-fail, zero Raw rescues,
and one Raw regression (`d4j-time-1`). The exact two-sided McNemar p-value is
`1.0`. Across all 16 cases there were two Raw regressions, zero rescues, and a
McNemar p-value of `0.5`.

The valid experimental observation is that H0 passed one more primary case
than Raw SDK in this run. The 13-case primary cohort has insufficient power to
claim a statistically significant difference or model-only causality.

## Runtime and integrity

- Formal wall time: 3,024.76 seconds
- SDK calls: 16
- Raw timeouts: two (`d4j-time-1`, `d4j-jacksoncore-2`)
- Harness errors: zero
- Production-source path-policy failures: zero
- Overall official result: 12 pass, four fail
- Raw summary digest:
  `98884fcc9ddc92289174927ffc15004c389c28ede8deeb1a7fd42f7a753d8ae9`
- Comparison report digest:
  `32df1996a961885ce766773341a6b5ed64afcbdeaf0a3e2c3b8493fafc445027`

The report leaves aggregate token usage unset because timeout termination
prevented trusted final usage records for two cases. Artifact completeness is
`191/192` (`0.9947916667`): `d4j-jacksoncore-2` did not produce the expected
final SDK result file before its hard deadline. Its request, event stream,
stderr, frozen Git diff, official score, and noninterference receipt remain
present, and the absence is explicitly recorded rather than synthesized. All
16 noninterference receipts passed, temporary SDK authentication bridges were
removed, and the source checkout remained unchanged.

## Runtime artifacts

Runtime evidence is intentionally gitignored and remains local:

```text
.autobugfix/raw-codex-baseline/formal-runs/raw-codex-formal-16-9b144bb/
  run-summary.yaml
  raw-codex-comparison-report.yaml
  <case-id>/
    visible-input/case.json
    process/
    generated.diff
    submission.yaml
    oracle-result.yaml
    oracle-noninterference.yaml
    report.yaml
```

The prepared manifest is:

```text
.autobugfix/trusted-eval-cases/manifests/
  defects4j-v3.0.1-raw-codex-sdk/
  raw-codex-7dc74860255269adda3b3176bbece5f17c477c5802c4ddd9015d2e7005c99bd9.yaml
```

## Interpretation boundary

This is a system-level comparison of a complete Autobugfix H0 harness against
one direct SDK coding turn. It is not compute matched: the treatments have
different feedback, verifier, evaluator, Memory, and retry structures. The
three development cases were previously exposed during H0 harness development
and are excluded from the primary claim. These results must not be used as
case-level feedback to mutate H0 or rerun this frozen experiment.
