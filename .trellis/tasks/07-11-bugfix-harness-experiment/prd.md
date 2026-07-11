# Run bugfix harness experiment

## Goal

Run the real independent `H0 -> H_bug` controlled experiment on the sealed
Defects4J manifest and measure whether governed Operator changes improve the
bugfix-specialized Autobugfix harness.

## Requirements

- Depend on completed experiment-line control and Defects4J adapter children.
- Freeze H0 subject SHA, trusted harness/policy SHA, model, role config, skills,
  Memory snapshot, manifest, scorer, and initial budget.
- Initialize `experiment/bugfix-main` exactly at H0.
- Use only `gpt-5.4-mini`, the production Python SDK, one case at a time, and at
  most two Writer attempts per case under each paired budget.
- Run 3, inspect trusted cost/harness evidence, obtain human grant for 8, then
  obtain human grant for 16. No unused budget transfers to Experiment 2.
- Expose only the 10 Optimization cases and their allowed artifacts to
  Operator. Six Holdout cases remain sealed.
- Let Operator diagnose the failing layer before requesting a candidate;
  integrate only through the trusted Guard and retain every failed attempt.
- Build Regression from solved Optimization cases and freeze H_bug only after
  full visible validation.
- Run H0/H_bug on the same six Holdout cases and report paired outcomes, cost,
  artifact completeness, and governance violations.

## Acceptance Criteria

- [ ] H0 and H_bug are immutable, digest-complete checkpoints with H0 as the
      treatment parent.
- [ ] All production role calls use `gpt-5.4-mini`; no fake/Spark fallback is
      present.
- [ ] Each 3/8/16 expansion has a distinct human budget grant and usage ledger.
- [ ] Operator never receives Holdout identity, prompt, patch, log, or
      case-level diagnosis before permanent study closure.
- [ ] The report separates rescue, regression, preserved, unresolved, harness
      errors, Optimization, Regression, and Holdout.
- [ ] Any Holdout regression or governance violation blocks promotion while
      preserving the result as evidence.
- [ ] Real Execution logs/events/artifacts and official oracle outputs are
      complete for every launched case.

## Notes

- Parent contract: `07-11-experiment-main-benchmarks`.
- Experiment 2 may be scheduled later for quota reasons but is not descended
  from this treatment.
