# Run general-agent experiment

## Goal

Independently test whether SWE-driven Operator evolution can turn the frozen
bugfix-specialized H0 Autobugfix subject into a broader issue-resolution agent
that outperforms the original main baseline.

## Requirements

- Depend on completed experiment-line control and SWE adapter children, not on
  H_bug or the Experiment 1 treatment.
- Initialize `experiment/general-main` at the exact same H0 subject SHA used by
  Experiment 1, with separate config, Memory, artifacts, cases, and budget.
- Prove H_bug commits, skills, Memory, configuration, and artifacts are absent
  before the first model call.
- Use only `gpt-5.4-mini`, the production Python SDK, one case at a time, and at
  most two Writer attempts per paired case budget.
- Run independent 3/8/16 human budget gates.
- Expose only 10 Verified Optimization cases spanning bugfix, feature, and
  maintenance to Operator. Keep six SWE-bench-Live Holdout cases sealed.
- Let Operator evolve the subject only through diagnosed, scoped, verified
  candidate changes on `experiment/general-main`.
- Freeze H_general and compare it directly with H0 on the same official SWE
  evaluations.
- Treat Defects4J non-regression as a secondary trusted report only; it may not
  feed Operator optimization.

## Acceptance Criteria

- [ ] H_general names H0, not H_bug, as its parent and passes an ancestry/input
      contamination audit.
- [ ] All production role calls use `gpt-5.4-mini`; no fake/Spark fallback is
      present.
- [ ] Each 3/8/16 expansion has its own human grant and usage ledger.
- [ ] H_general has positive visible net improvement over H0.
- [ ] H_general rescues at least one sealed SWE-bench-Live case and regresses
      none of the six Holdout cases.
- [ ] The evaluated set covers bugfix, feature, and maintenance task types.
- [ ] Operator never receives Holdout case-level evidence before permanent
      study closure.
- [ ] The report is separate from Experiment 1 and includes complete official
      harness and SDK artifacts.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- This experiment may run after Experiment 1 only as a scheduling decision; it
  remains an independent treatment of H0.
