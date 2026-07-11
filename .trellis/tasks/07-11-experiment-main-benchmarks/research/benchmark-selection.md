# Benchmark selection and feasibility

## Research question

Which repository-level benchmarks have sufficiently strong publication,
reproducibility, and executable-oracle support for the two Autobugfix
experiments?

## Selection

### Experiment 1: Defects4J 3.0.1

- Paper: *Defects4J: A Database of Existing Faults to Enable Controlled
  Testing Studies for Java Programs*, ISSTA 2014.
- Venue: ISSTA is CCF A in software engineering. The paper received the ISSTA
  2024 Impact Paper Award.
- Framework pin: tag `v3.0.1`, commit
  `6d54320e0db5a357f9ab38a8e4d2e5aead7e1c09`.
- License: MIT for the framework; checked-out projects retain their upstream
  licenses.
- Current corpus: 854 active bugs plus 10 deprecated bugs in 17 real Java
  projects.
- Useful properties: issue tracker identity, buggy/fixed revisions, one fixing
  commit, manually minimized source patch, and deterministic triggering tests.
- Official commands support checkout, compile, test, metadata query, and
  version-specific property export.
- Official reproducibility environment requires Java 11, Git 1.9 or newer,
  Subversion 1.8 or newer, Perl 5.0.12 or newer, `cpanm`, and timezone
  `America/Los_Angeles`.

Primary sources:

- https://github.com/rjust/defects4j
- https://defects4j.org/
- https://homes.cs.washington.edu/~mernst/pubs/bug-database-issta2014-abstract.html
- https://www.issta.org/
- https://www.ccf.org.cn/Academic_Evaluation/TCSE_SS_PDL/

### Experiment 2 optimization: SWE-bench Verified

- Parent paper: *SWE-bench: Can Language Models Resolve Real-World GitHub
  Issues?*, ICLR 2024 Oral.
- Verified is a later human-reviewed 500-case subset, not a separate top-venue
  paper.
- It supplies full Python repositories, issue text, base revisions, gold
  patches, fail-to-pass tests, pass-to-pass tests, and an official Docker
  evaluation harness.
- It remains useful as visible optimization and compatibility data.
- It is not the final scientific holdout because a 2026 audit reports
  contamination and residual test/specification problems.

Primary sources:

- https://github.com/SWE-bench/SWE-bench
- https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf
- https://openai.com/index/introducing-swe-bench-verified/
- https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/

### Experiment 2 holdout: SWE-bench-Live

- Paper: *SWE-bench Goes Live!*, NeurIPS 2025 Datasets and Benchmarks Track.
- Venue: NeurIPS is CCF A. The Datasets and Benchmarks track is distinct from
  the main track but is peer reviewed and published in the NeurIPS
  proceedings.
- Repository license: MIT.
- Stable framework pin available for the multi-language/multi-OS harness: tag
  `v1.0-multi-language-multi-os-benchmarking`, commit
  `c5ea7e48b7b8bb0f4bcbbceb182a09dadfabfc2c`.
- The continuously updated corpus provides recent real GitHub issue-resolution
  tasks and dedicated execution environments, making it better suited to
  unseen-repository holdout evaluation than static Verified cases.

Primary sources:

- https://github.com/microsoft/SWE-bench-Live
- https://papers.nips.cc/paper_files/paper/2025/hash/d83c4a745789690f82e86d0ef752ae7c-Abstract-Datasets_and_Benchmarks_Track.html
- https://neurips.cc/Conferences/2025/CallForDatasetsBenchmarks
- https://www.ccf.org.cn/Academic_Evaluation/AI/

## Rejected as primary sources

- BugSwarm is an ICSE 2019 Technical Track benchmark with strong provenance,
  but fail-pass artifacts include build, dependency, configuration, and flaky
  failures. It is suitable only for a filtered robustness supplement.
- ManyBugs is a TSE benchmark of real C defects, but its old toolchains, weak
  issue context, and known test adequacy limitations make it a secondary
  replication set.
- BugsInPy appeared in the ESEC/FSE 2020 Tool Demo track, not the research
  track. It is a useful Python supplement but not the strongest primary
  publication claim.
- BugsJS, PyBugHive, GitBug-Java, and RepairBench have weaker venue, maturity,
  adoption, or framework fit than the selected sources.

## Case program

- Experiment 1: 16 unique Defects4J cases, 10 visible Optimization and 6
  sealed Holdout cases from repositories absent from Optimization.
- Experiment 2: 16 unique SWE cases, 10 visible SWE-bench Verified
  Optimization and 6 SWE-bench-Live sealed Holdout cases from repositories
  absent from Optimization.
- Both experiments independently branch from the same frozen `H0` subject.
- Model execution expands only through manually approved `3 -> 8 -> 16`
  gates.
- All selected cases must first pass their official buggy/gold deterministic
  eligibility oracle.
