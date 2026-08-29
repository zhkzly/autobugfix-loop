Active task: .trellis/tasks/08-10-exp2-initial-calibration

You are an independent experimental-design and causal-validity reviewer. This
is a READ-ONLY review. Do not edit files, start the task, run cases, or create
Operator state.

Review the proposed Exp2 calibration/full-loop staging using repository
evidence. The treatment surface is Execution harness only; Memory, Eval,
scorer, model, protocol, Operator policy/skills, and dataset identities are
frozen. The formal public cohort has ten SWE-bench Verified cases clustered in
six repositories, with a separately guarded six-case SWE-bench-Live holdout.

Read the current PRD and the archived Exp2 PRD/design/research. Inspect the
protocol and implementation contracts when needed. Challenge, in particular:

1. Whether 2-3 calibration cases are serving apparatus validation or being
   misused as evidence of improvement.
2. Whether calibration, optimization, public replay, and sealed holdout roles
   are cleanly separated and repository leakage is prevented.
3. Whether results from one case may trigger a same-case retry or overfit.
4. Whether H0 and H1 share one frozen apparatus and comparable budgets.
5. Whether the proposed two-stage plan can actually attribute improvement, or
   only produce an engineering case study.
6. Whether the dataset is too small for any stated gate or claim.

Return severity-ranked findings, rejected assumptions, and a corrected
minimum experiment sequence with denominators. End with ALLOW, REVISE, or
BLOCK and the required pre-start changes.
