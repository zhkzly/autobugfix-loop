Active task: .trellis/tasks/08-10-exp2-initial-calibration

You are already the Trellis implementation sub-agent. Implement directly in
the current worktree. Do not spawn another implement/check agent. Do not
commit, push, merge, create real study/operator state, pull/build images, call
the real benchmark SDK, run a benchmark case, or enter human approval text.

Model requirement: gpt-5.6-terra with max reasoning (already configured by the
dispatcher).

Implement source-hardening Phases 1-4 from implement.md for the approved
resume-first MVP:

1. v2 plan/record/metric contracts and versioned MVP benchmark manifests;
2. per-case attempt intent/terminal receipts, reconciliation, independent
   calibration terminal states, and no fallthrough;
3. H0 source-only visibility and one service-owned candidate-transition
   receipt/content-addressed binding;
4. source/transfer paired decisions, trusted rollback/report terminal events,
   reproducibility/metrics output;
5. focused tests and required fault/tamper/interruption cases.

Preserve v1 read/audit compatibility and the frozen scorer, dataset revision,
Memory behavior, model/budget, Operator constitution/skills, Guard semantics,
and main protection. Reuse existing services/reducers rather than creating a
parallel state authority. Caller YAML/author/approver strings must not become
authority.

Read every implement.jsonl entry, PRD, design, implement plan, metrics.md, and
the independent audit before coding. Inspect existing contracts before adding
helpers. Keep the diff within the approved source/tests/benchmark manifest
scope.

Verification before reporting:

- run focused Exp2/Operator tests;
- run full pytest if time/resources permit;
- compileall, role-skill validation, and git diff --check;
- report exact failures or remaining blockers without weakening requirements.
