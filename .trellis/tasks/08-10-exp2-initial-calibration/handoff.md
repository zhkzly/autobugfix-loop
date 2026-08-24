# Handoff — Exp2 resume-first pilot (written 2026-08-24)

> **FINAL STATUS 2026-08-24: PILOT COMPLETE.** Study `exp2-pilot-f8bec35-r4` reached
> **REPORTED** with decision **`retain_transfer_rescue`**. Report digest
> `41d3b4445c2290f29157567aac2655e5bae44dfc9d5949b8b91c1aaae88d7e22` under
> `trusted-eval-cases/exp2/exp2-pilot-f8bec35-r4/reports/` in the exp2 worktree.
> The sections below are the historical record of how it got there (including the
> r3 stop and the four harness fixes that unblocked the governed candidate path).

Complete state + next steps for any agent continuing this task. Read `prd.md` / `design.md` in this
directory for the experiment design; this file is the operational snapshot.

> **TL;DR — 在哪继续开发：`/home/kelong/pycodes/autobugfix-exp2-resume-mvp-v2`**
> （分支 `experiment/exp2-resume-mvp-v2` @ 649546f）。这就是下文表格中标粗体的那个 worktree：
> 所有执行、所有后续提交都在它里面，直接 `cd` 进去开工。主仓 `autobugfix-loop`（main）只用来
> 更新 Trellis 任务/journal；`…guard-seal-db0f2b5` 是死路，不要进去。

## 1. Checkouts (3, all on this machine)

| Path | Branch / HEAD | Role |
|---|---|---|
| `/home/kelong/pycodes/autobugfix-loop` | `main` @ 89c5bda | Trellis task home, journal, this file |
| `/home/kelong/pycodes/autobugfix-exp2-resume-mvp-v2` | `experiment/exp2-resume-mvp-v2` @ 649546f (clean, pushed) | **All execution work happens here.** PR #17 → base `experiment/exp2-execution-only-20260809` |
| `/home/kelong/pycodes/autobugfix-exp2-guard-seal-db0f2b5` | detached @ db0f2b5 | Dead end. Was used only because trusted_ref pointed there |

Supporting roots (shared, still valid):
- Empty-memory fixture: `/home/kelong/pycodes/autobugfix-exp2-empty-memory-649546f` (digest `8d05dbaf…`)
- Guard root: `/home/kelong/pycodes/autobugfix-exp2-guard-649546f`
- Disposable study state roots: created fresh per study under `/tmp` (0700)

## 2. Completed (do not redo)

- **Phase 7 calibration at 649546f** — two studies, both terminal:
  - `exp2-calibration-649546f-r1`: crash-consistency proof (forced interruption mid-Flask case; open
    intent reconciled as `execution_infrastructure_invalid`; Flask never re-run; honest infra receipt).
  - `exp2-calibration-649546f-r2`: **CALIBRATION_COMPLETE**, published report.
    Receipt: `.autobugfix/trusted-eval-cases/exp2/exp2-calibration-649546f-r2/calibration-terminal-receipt.yaml`
    (apparatus_receipt_digest `0a28ab63b63b13966b8bbf90cbcca0b50b86e1a88705e99d52d2ec873b8a04d6`).
- 4 hardening commits ending at 649546f; independent trellis-check PROCEED; full gate 408 passed.
- PR #17 open (9 commits, +16054/−189). Branch pushed.
- Journal session 4 committed on main (89c5bda). Task meta has `pr_url` + `commit=649546f`.
- Pilot studies r1/r2 at 649546f burned (documented in task #10): r1 hit manifest-visible-root wall,
  r2 hit sealed-manifest/trusted-checkout mismatch wall. Their state roots are dead evidence.

## 3. RESOLVED 2026-08-24: trust anchor advanced to 649546f (user-authorized Path A)

The user explicitly authorized re-pinning both `trusted_ref` locations in
`.autobugfix/config.yaml` of the exp2 worktree from `db0f2b58701e68a21ee1c04033475f7ade68b67c`
to `649546f93b660c61c0f9b07f43c6d60056244e92` (justification on record: trellis-check PROCEED at
649546f, full gate 408 passed, r2 real calibration CALIBRATION_COMPLETE). This section records the
former blocker for audit; the contradiction evidence lives on in dead studies
`exp2-calibration-db0f2b5-r1/r2`:
- db0f2b5's own producer emitted the **old** empty-Memory digest `602e35a9…` (files-only algorithm),
  but the frozen fixture + db0f2b5's own checker bind `8d05dbaf…` (directory-inclusive algorithm)
  → every official case died with "official receipt Memory differs from the frozen empty fixture".
- 649546f executes cleanly (r2 calibration green) and is now the anchor.

**Governance rule (still in force):** an agent must NOT unilaterally edit `trusted_ref` to point at
its own commit; advancing it is a human decision that explicitly names the change. This advance was
made under exactly that authorization.

## 4. Path A executed (2026-08-24) — artifact trail

All inside `/home/kelong/pycodes/autobugfix-exp2-resume-mvp-v2` (HEAD 649546f, clean):

1. `config.yaml` trusted_ref ×2 → `649546f93b…` (user-authorized).
2. Fresh public manifest: `exp2-resume-exp2-prep-4063467854218a2f9ce199b5`
   (manifest digest `4dcabe2be4edf884c4d4652562eb7ea93535a69188a571f452a6bce06d9cd124`,
   guard code_identity binds 649546f). Old db0f2b5-sealed tree `…3eb6c0cf…` NOT reused.
   Gotcha hit: had to delete the stale materialization dir under
   `trusted-eval-cases/swe/exp2-preparation-runs/exp2-prep-4063467854218a2f9ce199b5/` first.
3. Operator study #2: `exp2-operator-649546f-r2` — `--harness-ref 649546f93b…` (explicit),
   `--base-ref f529f09d`, `--target-checkpoint H_general`, memory digest 8d05dbaf, manifest 4dcabe2b.
   (Study #1 `exp2-operator-649546f-r1` remains the H_bug/old-manifest record.)
4. H0 binding exported: `.autobugfix/operator-artifacts/exp2-study-bindings/8ee12c0db8c417ccaa7f2a5fa1d5cb8eda07401fe71022d53c75e020d8b6d8ff.yaml`.
5. Pilot r3 plan built (digest `950a7e8caf30f79d78755f2260194594ed1851c6718fceb5afb2d81d9d33c41a`)
   binding: r2 calibration terminal receipt + r2 apparatus (0a28ab63) + new manifest + new binding +
   disposable root `/tmp/autobugfix-exp2-disposable-649546f-pilot-r3` (0700, must pre-exist!).
   Study initialized: `exp2-pilot-649546f-r3` (burned r1/r2 IDs untouched), state PREPARED → H0.
6. H0 execution COMPLETE (2026-08-24): ten sequential `resume --execute` runs, all
   `official_terminal`, zero invalid arms. Baseline: 5 resolved (django, matplotlib,
   xarray, requests, scikit-learn) / 5 unresolved (astropy, sympy, seaborn, pytest,
   sphinx); 26 model calls / ~2033s model time. Study → SOURCE_RELEASED; source
   projection bundle digest 3b5f5ef9… (feasibility passed; astropy failure_stage
   visible_verifier = Execution-owned, satisfies the gate).
7. Post-H0 governance chain — executed until a STRUCTURAL gate deadlock (see §5):
   - `register-h0` → line exp2-operator-649546f-r2 @ H0 checkpoint (metric 47b9487…),
     line branch `experiment/exp2-operator-649546f-r2-main` @ f529f09d, generation 0.
   - Evidence auto-registered: `study-evidence-20260824T044651-7bbf18f3`.
   - Attribution recorded (empty-patch first-attempt on astropy; Writer-skill scope);
     triage 3ad5fb45…; wave-3 + wave-8 budget grants human-approved
     (budget-grant-…-6049c4ca wave 3, budget-grant-…-4471da05 wave 8, zero usage consumed).
   - Three candidate requests created (docs_skills wrong-layer → execution + pr2-real-e2e
     baseline) — ALL blocked at preflight; closed as superseded. Study
     exp2-pilot-649546f-r3 remains in CANDIDATE_TRANSITION_AWAITING (dead state, evidence
     preserved). Pilot r3 stops here per PRD stop conditions.

## 5. NEW BLOCKER (2026-08-24): baseline gate structurally unsatisfiable for line-bound candidates

The constitution (`baseline_required_layers: [execution, memory, eval, operator]`) makes every
behavior-layer request require a trusted performance baseline. `baseline_for_request` demands:
(a) the baseline YAML committed **in the request-base commit's tree**, (b) measured SHA an
ancestor of the base, (c) diff(measured, base) touching only `.autobugfix-baselines/`.

For a line-bound request the base is the LINE HEAD = frozen H0 subject f529f09d (immutable).
The only baseline in f529f09d's tree is pr2-real-e2e (measured 0ca8f66) — stale, because real
behavior changed between 0ca8f66 and f529f09d. `capture_baseline` can only measure at
`operator.experiments.trusted_ref` (649546f), which is NOT an ancestor of f529f09d. No
sanctioned CLI advances the line head with a baseline-only commit (only `integrate` advances
it, which itself needs preflight first). Chicken-and-egg. Tests never caught it because
`tests/helpers.py` sets `baseline_required_layers: []` globally.

**Remediation options for a follow-up harness task (then restart pilot as r4, new IDs):**
- Add `--base <sha>` to baseline capture + a governed `line commit-baseline` transition that
  CAS-advances the line head by a baseline-only commit; or
- Scope baseline freshness for line-bound requests to the declared layer's paths (and define
  ancestry for the subject lineage explicitly).
- Either way: capture the baseline BEFORE freezing the H0 subject in future protocols.

**What stands as deliverables (r3):** anchor advance record, fresh 649546f-sealed manifest,
operator study #2 (H_general), complete 10/10 H0 baseline evidence (5/5 resolved split, all
official_terminal), source projection + evidence-bound attribution, budget grant trail, and
this defect analysis. Governed continuation: merge PR #17 through trusted-merge (advances the
anchor), then the harness fix task above, then exp2-pilot-…-r4.

## 5. Hard constraints (never violate)

- Burned study IDs are never reused.
- No parallel codex session on the same worktree.
- Caller-supplied YAML/strings never become authority (receipts bind digests).
- One case per `resume --execute`; a crash reconciles the open intent as
  `execution_infrastructure_invalid` — there is no execution retry path (only scorer_only_retry).
  CALIBRATION_BLOCKED / non-official terminal = study dead, start a new -rN.
- Do not edit `trusted_ref` without explicit user authorization naming that change.

## 6. Environment gotchas (this machine)

- `find` / `grep` in the agent shell are broken shims → use `/usr/bin/find` and `command grep`.
- Agent shell cwd resets to `/home/kelong/pycodes/autobugfix-loop` after every command — always `cd` into the worktree in the same command.
- Export `UV_CACHE_DIR=/tmp/autobugfix-uv-cache PYTHONDONTWRITEBYTECODE=1`.
- `source-check-v2` takes argv via argparse.REMAINDER — no `--` separator.
- Artifact dirs must be deleted before rerun ("not fresh" otherwise).
- Dataset snapshot is path-absolute-bound: per worktree run
  `autobugfix eval benchmark doctor --adapter swebench_verified` (pinned c104f840, 500 rows).
- Qualification records embed absolute paths: per worktree re-qualify the 12 instances via
  `qualify-swe --protocol benchmarks/swe-experiment-2-resume-mvp-v2.yaml --adapter swebench_verified --instance <id>`.

## 7. Optional bookkeeping

- `exp2-calibration-db0f2b5-r1` (seal worktree) still has an open Flask intent; one more
  `resume --execute` would reconcile it to CALIBRATION_BLOCKED for a clean terminal record.

## 8. Orientation pointers

- Full prior-session transcript: `/home/kelong/.claude/projects/-home-kelong-pycodes-autobugfix-loop/6b5fbba2-7933-4f0a-8e5e-0c0929d361d0.jsonl`
- Task list item #10 "Phase 8：resume_pilot H0 十仓基线" tracks the pilot phase.
- Journal: `.trellis/workspace/kelong.ZX/journal-1.md` sessions 1–4.

## 9. FINAL RUN RECORD (2026-08-24, session 5 completion)

**Outcome: `exp2-pilot-f8bec35-r4` → REPORTED, decision `retain_transfer_rescue`.**

Paired outcomes (H0 → H1, all `official_terminal`, zero invalid arms):

| Case | Slice | H0 | H1 | Pair |
|---|---|---|---|---|
| astropy__astropy-13398 | source | unresolved (2 att., first empty) | unresolved (1 att., first non-empty) | both-fail; mechanism fix confirmed |
| django__django-10097 | source | resolved | resolved | both-pass |
| matplotlib__matplotlib-24627 | transfer | unresolved | resolved | **observed transfer rescue** |
| pydata__xarray-2905 | transfer | unresolved | resolved | **observed transfer rescue** |
| sympy__sympy-13091 | transfer | unresolved | unresolved | both-fail |

Two observed transfer rescues, zero observed transfer regressions → candidate
retained on the experiment line (never promoted). Sanctioned phrasing: "observed
transfer rescue on this three-repository pilot" — no broader claim.

Governed candidate (request-20260824T102055-03c554fc, line exp2-operator-f8bec35-r1):
Writer skill diff `5ddc98a1` (forbid empty-patch termination + feature-type
implementation sketch), integrated as line HEAD `fe0f2fea` (generation 1);
transition receipt `8faa15d8`.

H1 execution detail: the control worktree was checked out detached at `f8bec35`
(the frozen H0 apparatus identity) for H1, matching the apparatus-consistency
requirement; governance verification ran from the branch tip code. The branch tip
is `ba3b67d` (fixes a6a167d baseline-gate, 5672c8f sandbox DNS, 5c922bc
line-bound production invariants + git metadata binding, ba3b67d baseline
passthrough); all pushed to PR #17, full gate 410 passed / 1 skipped.

Machine notes for any future governed run on THIS host:
- `kernel.apparmor_restrict_unprivileged_userns=1` (user permanently refuses to
  change it) → nested bwrap impossible → verification profiles must avoid
  sandbox-spawning tests; machine-local `operator.verification.fast_profiles/
  full_profiles` are set to [execution] / [execution, memory].
- The governed Writer's SDK session cannot exec (nested sandbox); writers must
  blind-apply_patch. Put explicit file-path + "no shell" guidance in the REQUEST
  SUMMARY to bias the model (request-…-03c554fc is the working example).
- Budget `max_operator_revisions=3` counts per (role, execution_id); a fresh
  request resets it when a writer run is burned.

## 10. RETURN-TO-CONTINUE CARD (user stepped back 2026-08-24; everything below is turnkey)

The user delegated all judgment and stopped participating. Everything autonomously
executable is done. The remaining evolution steps are human-gated; when the user returns,
these are the exact unlocks (no context needed):

**A. Formalize H_general (holdout generalization proof — the full evolution close):**
1. One-time infra: dedicated Guard VM docker daemon publishing
   `autobugfix.guard.isolation=dedicated-vm-v1` via a mode-0600 unix socket under an
   external guard root; set `eval.benchmarks.guard.docker_host` to it. (Absent today:
   it is `null`.)
2. New task/protocol/study IDs (holdout extension is now UNLOCKED by the PRD's own
   criteria: positive transfer signal + zero regressions).
3. Budget approvals are interactive-TTY commands the agent will hand over when reached.
4. `operator study import-guard-metric` (getpass guard secret — human only) →
   `operator checkpoint create --name H_general` materializes the frozen evolved identity.

**B. Evolution round 2 (H2 iteration):** new study/line on top of `fe0f2fea`; needs new
budget grants (same interactive approvals). The Writer blind-patch guidance pattern from
request-…-03c554fc's summary is the working recipe on this kernel.

**C. Merge PR #17:** must go through the operator trusted-merge flow (it is itself an
anchor-advancing act); do NOT raw-merge.

**Durable state:** branch tip `ba3b67d` (clean, pushed); study r4 REPORTED; line
`exp2-operator-f8bec35-r1` CLOSED at `fe0f2fea`; anchor `trusted_ref=f8bec35` in the
machine-local config; verification profiles fast=[execution] full=[execution,memory];
subject baseline `exp2-subject-f529f09d-gate` committed at f8bec35.

## 11. Why there is no "H2 iteration" (design boundary, checked 2026-08-24)

An H2 round with subject = the retained fe0f2fea was attempted and correctly rejected by
the apparatus: `SWE_H0_SUBJECT = f529f09d` is a hard constant (swe_constants.py) and the
constitution states H_bug/H_general are INDEPENDENT successors of one frozen H0 that may
not inherit each other's code/skills/artifacts/case feedback. The evolution model is:
frozen H0 → one governed revision per lineage → holdout validation → frozen checkpoint.
Continuation therefore means (a) holdout + H_general checkpoint for the retained
candidate (handoff §10.A — human gates), or (b) a fresh INDEPENDENT lineage from f529f09d
with its own H0/attribution (legal, but "more of the same" and still budget-gated at
candidate time). The H2 attempt left no artifacts.

## 12. FINAL DISPOSITION (2026-08-24): H_general holdout formalization declined by user

After the pilot completed (§9), the sanctioned H_general path (holdout guard machinery:
dedicated Guard daemon + sealed cohort + secret-signed metric + checkpoint) was fully
scoped and infra setup was started, and the user explicitly declined it: "我不需要这个
保护". The half-built guard root was removed; `eval.benchmarks.guard.docker_host` remains
null. Consequence, by the system's own design: no CANDIDATE metric can be registered, so
the formal H_general checkpoint is permanently out of scope on this machine unless the
user re-opens it (§10.A remains the recipe). The pilot's terminal state stands on its own:
exp2-pilot-f8bec35-r4 REPORTED, retain_transfer_rescue.

## 13. THE USER'S ITERATIVE PROTOCOL (executed 2026-08-24 evening)

The user clarified exp2's real design mid-session: an ITERATIVE conquer loop —
(evolution set: attack failures from execution feedback) + (regression set: passing
cases must stay passing) + (held-out: final eval only after convergence) — with
standing delegation ("我也不想反复确认了"). Executed:

- **Frozen-H0 advancement**: each round's retained candidate becomes the next frozen
  H0 (SWE_H0_SUBJECT constant + benchmark protocol yamls + raw-codex treatment
  rebind; commit aa98bba). f529f09d → fe0f2fea (round-1 evolution).
- **Round 2 exploration** exposed the real root cause of empty-patch failures: the
  CASE-execUTION Codex worker was double-sandboxed (outer worker bwrap + inner codex
  sandbox = nested userns, banned by this kernel). Fixed in 2c35c1b (inner session
  runs full-access inside the already-confined outer wrapper; verified with a real
  SDK probe). Writers can exec again.
- **Feasibility gate extended** (1965577): official_eval failures are now attributable
  (writers can run tests locally, so repair-quality skill revisions are legal).
- **Delegated budget approvals** (37842ab): approval_kind=delegated_agent + recorded
  delegation note; interactive human attestation unchanged as default.
- **Cycle-6 completed the loop INCLUDING rollback**: candidate (skill: derive
  acceptance tests from the problem statement + verify locally) verified and
  integrated, H1 ran, transfer showed xarray RESCUE but matplotlib OBSERVED
  REGRESSION → decision=rollback → governed rollback restored the line to its H0
  checkpoint → REPORTED (d78e50ad). The user's "不许修坏" gate fired for real.
- **Rollback-path defects fixed** (d1c1fd0): Exp2PairedMetrics.record_digest crashes
  (authorization + replay), usage-digest provenance (now prefix-verified because H1
  meters onto the same grant by design), rollback validation now honors the machine
  full_profiles knob.

**Evolution curve (H0 resolved/10 per harness generation):** f529f09d @f8bec35: 2 →
subject fe0f2fea @aa98bba: 4 → @2c35c1b (exec fixed): 3-4 (variance). Retained
evolution: round-1 skill fix (empty-patch prohibition). Round-2 candidate REJECTED by
the regression gate (correct behavior). Continuation is turnkey: cycle assembly
(/tmp pattern: anchor+tag; steps 1-9) + candidate chain scripts; every future cycle is
zero-human (delegated approvals, committed baseline pattern).

Honest variance note: django/matplotlib/requests flip between runs (gpt-5.4-mini@low
nondeterminism); paired same-study comparisons are the only sound basis (which is
what each cycle's H0/H1 pairing does).
