Active task: .trellis/tasks/08-10-exp2-initial-calibration

Dataset opposition round, READ-ONLY. Do not edit files, download images, or run
benchmark cases.

Argue the strongest evidence-based case for incorporating SWE-bench Pro into
Exp2 because the project is meant to improve a real bugfix harness, not merely
exercise old easy Python issues. Also identify why doing so now could invalidate
the experiment.

Official facts available as of 2026-08-17:

- ScaleAI/SWE-bench_Pro public metadata has 731 tasks from 11 repos, four
  languages, about 7.82 MB; tasks are long-horizon and often multi-file.
- The official evaluation repo provides a per-instance `dockerhub_tag`, so
  selected images can be pulled individually; local Docker is described as
  beta and the repo reports recent leaderboard/test corrections.
- The current Autobugfix adapter accepts only `swebench_verified` for
  calibration and uses a separate SWE-bench-Live Guard for sealed cases.
- Changing dataset and scorer after H0 would confound the treatment, but a new
  adapter frozen before H0 is apparatus, not H1.

Answer:

1. Should Pro replace Verified public Optimization, replace Live Holdout, be a
   small post-lock stress cohort, or remain a later replication?
2. What is the smallest repository-disjoint Pro subset that adds real value?
3. How should official scorer/version corrections be pinned?
4. Does Pro's difficulty create a floor effect under gpt-5.4-mini, two attempts,
   and 900 seconds?
5. Which claims become stronger and which remain invalid?

End with RETAIN, AUGMENT, or REPLACE and a minimum experiment design.
