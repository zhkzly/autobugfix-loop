# Review: official SWE Eval adapters

## Constitutional scope

Autobugfix remains a repo-agnostic loop/harness control system. Execution owns
real target-repository repair in isolated worktrees. Memory only compiles
accepted Execution evidence into reviewed wiki/skills. Eval owns benchmark
materialization, complete Execution invocation, frozen submission, and
independent official scoring. Operator owns governed Autobugfix improvement on
non-main branches and cannot mutate Eval, Memory, or Execution state directly.

This task changes Eval and shared runtime, with protected Operator policy
updates needed to classify and validate the new harness. Eval service and the
external Guard own benchmark state. Execution service owns each inner repair
task. No benchmark oracle result is an Execution transition input.

## Independent reviews actually performed

- Eval/acceptance reviewer `Singer` reviewed official scoring, experiment
  separation, and artifact requirements. Findings were addressed in the exact
  subject broker, official image binding, patch freeze, encrypted Guard, and
  formal runner.
- Codex runtime/privacy reviewer `Kant` reviewed SDK and sandbox boundaries.
  Findings were addressed by one-call credential cleanup, trusted worker-source
  snapshots, Docker socket masking, project-root masking, Git authority
  fingerprints, and failure-evidence freezing.

No additional subagents were used after inline implementation mode was
required. The main agent performed the remaining passes below sequentially.

## Main-agent sequential passes

### Execution

Passed. The production acceptance ledger
`autobugfix-swe-execution-ledger-v2` records one Writer call, one configured
Docker visible-verifier call, and one read-only Evaluator call. Every transition
is bound to patch SHA
`372fc6522c35a89fbaa8605910b7784e4e72d31dbff77ac866d2dbf31b167c9f`;
the terminal phase is `evaluator_completed`. The target main checkout remains
clean and all edits occur in the task worktree.

### Memory

Passed. SWE Eval reads only frozen Memory/skill digests for subject identity.
It does not propose, approve, or activate Memory and does not write Execution
task state. Official scorer output is absent from Writer/Evaluator input.

### Eval

Passed with one finding fixed. Guard aggregate metrics previously copied the
requested subject SHA after case execution. They now derive one actual subject
SHA from all case reports and reject missing, mixed, or unexpected subjects.
The current runtime was requalified after this change.

### Operator governance

Code boundaries pass unit validation, but publication has an external authority
gate. `origin/main` does not yet contain the trusted `pr2-real-e2e` baseline;
the candidate copy cannot authorize itself. A human or trusted CI must seed or
approve that baseline before trusted-base PR admission can succeed. No bundle
or baseline authority will be fabricated in this task.

### Portability and privacy

Passed. No target repository, user home, company command, or internal path is
hardcoded. Generic Windows path examples in the Trellis session hook are not
runtime identities. Runtime roots are config-derived. Holdout records and
case-level artifacts are AES-GCM encrypted under a disjoint mode-0700 external
Guard root; public manifests contain only counts, digests, Optimization cases,
and opaque wave authority.

### Acceptance

Passed for public infrastructure and one real production case:

- Root tests: `218 passed`.
- Nested official harness tests: `1 passed`.
- Verified and Live doctors pass on Docker Engine 29.6.1.
- Current runtime:
  `sha256:e83ab8521188fd47492b443a504116ff8d3bcfe77fba4c2990c8e1b8e87533cc`.
- Protocol digest:
  `ededcd5303e279b9e8b318428b4af255efc7227503243bda6120a17c0a391892`.
- All ten public Optimization cases passed two official gold qualification
  runs and source materialization.
- Real `gpt-5.4-mini` development acceptance on
  `astropy__astropy-12907` completed the full Execution loop, froze submission
  `929bb7b3b547f83c00c9048cac6532ee6a1c9afb1f26994066323c90a6f81e71`,
  and independently scored `resolved=true` with no harness error.
- Noninterference digest:
  `99bbb3b407e3879d1d60574637caf0185ea385ee06d361f21d2369b12c64a585`.

The six private Live qualifications, encrypted 10+6 preparation, sealing, and
formal H0/H_general study remain pending human-held Guard authority and are not
claimed complete.
