Active task: .trellis/tasks/08-10-exp2-initial-calibration

Second-round opposition review. Remain READ-ONLY. Reconsider your verdict using
these implementation facts found by the main reviewer:

1. The current task PRD explicitly makes H1 candidate creation, attribution,
   harness edits, regression waves, and Holdout execution out of scope. Do not
   assess the archived full-loop design as though it were the current task.
2. `Exp2Coordinator.record_attribution()` validates a caller-supplied record
   and advances directly to `H1B_LOCKED`/`H1C_LOCKED`; it does not invoke
   `OperatorGovernanceService`, obtain a budget grant, create a Request, run a
   Writer, verify a candidate, integrate it, or bind a candidate-transition
   receipt.
3. `Exp2StudyPlan` has one fixed `candidate_binding_path`; the CLI later reads
   whatever valid binding is at that path. The coordinator checks that H1B has
   a different SHA/digest, but not that a trusted Operator transition produced
   it.
4. `exp2 init` validates cohort/policy/apparatus/fixture records but does not
   load and freeze the H0/candidate bindings or public/calibration manifests.
5. `report` is read-only and there is no coordinator rollback/report transition
   to the declared `REPORTED`/`ROLLED_BACK` terminal states.
6. The code contract requires exactly 2 or 3 calibration cases. A one-case
   smoke cannot be proposed as an executable use of this coordinator.

Challenge each fact against source. Then answer:

- Which are true blockers versus intentionally manual boundaries?
- Can the current task be ALLOWed as a narrow H0 child without pretending the
  project self-improvement goal is met?
- What exact parent/child gates or code changes are required before claiming a
  real result -> attribution -> governed harness change -> regression loop?

Return a corrected verdict and explicitly retract any first-round statement
that these facts invalidate.
