# Governance V4 Sequential Review

No subagents were available. The main agent performed the required reviewer
passes sequentially and did not represent them as independent agents.

## Execution Reviewer

```yaml
reviewer: execution
purpose_restatement: Autobugfix uses Execution to repair configured repositories in isolated worktrees, Memory to compile accepted evidence into reviewed knowledge, Eval to measure real Execution, and Operator to improve Autobugfix without owning those loops.
scope_read:
  - src/autobugfix/config.py
  - src/autobugfix/models.py
  - existing execution service/runner/worktree/verifier contracts
  - tests/test_config_task_store.py
  - full pytest suite
contracts_confirmed:
  - No Execution task/store/state transition was moved into Operator.
  - Target main checkout protection, real worktrees, verifier commands, and raw artifacts remain unchanged.
  - New Operator roots are pairwise non-overlapping and cannot contain candidate worktrees.
risks: []
required_changes: []
acceptance_evidence:
  - 108 unit/integration tests passed.
  - /tmp/autobugfix-real-repository-v4-e2e/ retained the real ItsDangerous execution artifacts.
decision: pass
```

## Memory Reviewer

```yaml
reviewer: memory
purpose_restatement: Execution owns repairs, Memory reads only accepted Execution evidence and proposes wiki/skills, Eval measures Execution, and Operator diagnoses/improves the harness without approving Memory.
scope_read:
  - src/autobugfix/memory/store.py
  - src/autobugfix/memory/service.py
  - src/autobugfix/operator/service.py
  - tests/test_operator_budget.py
contracts_confirmed:
  - Memory collect/digest/proposal/approval state was not changed.
  - Study creation freezes a separate read-only H0 Memory snapshot and digest.
  - Tampered Study Memory prevents a line-bound request before Writer starts.
  - Real acceptance left the maintainer proposal pending rather than self-approved.
risks:
  - Future benchmark adapters must consume Study Memory snapshots rather than global active Memory.
required_changes: []
acceptance_evidence:
  - /tmp/autobugfix-real-repository-v4-e2e/control/.autobugfix-memory/proposals/20260711-20260711-restore-url-safe-base64-padding-cont-324e0f57
decision: pass
```

## Eval Reviewer

```yaml
reviewer: eval
purpose_restatement: Execution performs repairs, Memory compiles accepted evidence, Eval runs isolated real Execution and scores tests/oracles, and Operator improves the system without turning Eval into task or approval state.
scope_read:
  - src/autobugfix/eval/models.py
  - src/autobugfix/eval/scorers.py
  - src/autobugfix/operator/models.py
  - src/autobugfix/operator/service.py
  - tests/test_operator_checkpoint.py
contracts_confirmed:
  - Tests/oracles remain score authority; exact diff equality is diagnostic.
  - Study metrics require content-addressed Guard registration and aggregate scalar payloads.
  - Visible Optimization manifests are frozen; sealed Holdout identities and case-level results remain external Guard state.
risks:
  - Defects4J, SWE-bench Verified, and SWE-bench-Live adapters are separate planned child tasks and no official benchmark claim is made here.
required_changes: []
acceptance_evidence:
  - /tmp/autobugfix-real-repository-v4-e2e/eval-runs/itsdangerous-real-e2e
decision: pass
```

## Codex Runtime Reviewer

```yaml
reviewer: codex_runtime
purpose_restatement: Execution and Operator use bounded Codex nodes, Memory and Eval retain their own roles, and services/Git/checks rather than model prose own all four loops' truth.
scope_read:
  - src/autobugfix/codex_sdk.py
  - src/autobugfix/operator/metering.py
  - src/autobugfix/operator/service.py
  - src/autobugfix/operator/store.py
  - scripts/validate_role_skills.py
contracts_confirmed:
  - Production defaults to preview Python CodexSDKBackend and never fake/codex exec.
  - Line-bound Supervisor/Writer/Verifier calls reserve Mini usage before SDK launch.
  - Wrong model, replayed revision, concurrency, expiry, and exhaustion fail before backend invocation.
  - SDK roles disable project hooks; operator_host alone receives those hooks.
risks: []
required_changes: []
acceptance_evidence:
  - Role skill validator passed after installing the authorized Governance V4 role protocols.
  - /tmp/autobugfix-real-operator-v4-final-e2e/.autobugfix/operator-artifacts/studies/operator-acceptance-study/usage
decision: pass
```

## Portability Reviewer

```yaml
reviewer: portability
purpose_restatement: All four loops remain local and repo-agnostic: target details come from config, while Memory, Eval, and Operator consume portable paths and preserve private runtime state outside Git.
scope_read:
  - README.md
  - examples/config.yaml
  - .gitignore
  - src/autobugfix/config.py
  - privacy/internal-name scans
contracts_confirmed:
  - No internal repository, company command, username, /Users path, or workspace absolute path is committed.
  - Target repo, test commands, branches, remotes, and roots remain configuration-driven.
  - Runtime state, logs, worktrees, checkpoints, and active releases are gitignored.
  - PPE remains disabled unless a repo profile explicitly enables it.
risks:
  - Authoritative process isolation currently requires Linux Bubblewrap, which is documented and fails closed.
required_changes: []
acceptance_evidence:
  - Autobugfix doctor reported openai-codex 0.1.0b3 and SDK-bundled runtime.
decision: pass
```

## Acceptance Reviewer

```yaml
reviewer: acceptance
purpose_restatement: Real Execution repairs a repo, Memory proposes reviewed learning, Eval independently measures the repair loop, and Operator improves Autobugfix through metered non-main candidates and trusted promotion state.
scope_read:
  - full pytest/compileall/diff checks
  - scripts/real_repository_acceptance.py
  - scripts/real_operator_acceptance.py
  - scripts/validate_operator_policy.py
  - scripts/validate_role_skills.py
contracts_confirmed:
  - Real ItsDangerous E2E passed with Mini, real Git worktree, pytest, evaluator, pending Memory proposal, and independent Eval.
  - Governance V4 E2E reached CLOSED through Mini grant, three metered roles, trusted integration, and H_bug checkpoint.
  - Standalone trusted Operator policy validation passed in Bubblewrap.
risks:
  - Official benchmark adapters remain separate child tasks; this review makes no Defects4J or SWE-bench score claim.
required_changes: []
acceptance_evidence:
  - /tmp/autobugfix-real-repository-v4-e2e
  - /tmp/autobugfix-real-operator-v4-final-e2e
  - standalone validation_id validation-4dbced33e58a
  - 108 tests, compileall, diff check, and role skill validation passed.
decision: pass
```
