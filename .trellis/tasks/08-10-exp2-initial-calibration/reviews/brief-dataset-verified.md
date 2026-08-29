Active task: .trellis/tasks/08-10-exp2-initial-calibration

Dataset opposition round, READ-ONLY. Do not edit files, download images, or run
benchmark cases.

Argue the strongest evidence-based case for retaining the current frozen
SWE-bench Verified + SWE-bench-Live Exp2 design. Then identify concrete
conditions under which that choice would fail the actual Autobugfix objective:
showing that frozen Eval evidence can drive a governed Execution-harness
improvement that survives later unseen cases.

Facts to challenge:

- Current formal public set: 10 Verified cases, six repository clusters,
  difficulty mix 4 `<15 min`, 5 `15 min-1 hour`, 1 `1-4 hours`, Python only.
- Current calibration can use two easy cases from external Verified repos.
- Verified is a 500-case human-validated static set but is now relatively old
  and may be saturated/contaminated for frontier models.
- The planned final set is six guarded SWE-bench-Live MultiLang cases.
- The result is only a process pilot, not a benchmark superiority claim.

Answer:

1. Is 10+6 enough to demonstrate the project loop, not general capability?
2. What H0 outcome distribution would make adaptation impossible or
   uninterpretable?
3. Must the formal cohort be changed before H0, or can a preregistered stop
   rule handle saturation/all-failure?
4. What exact dataset roles and denominators should be frozen?

End with RETAIN, AUGMENT, or REPLACE and a minimum experiment design.
