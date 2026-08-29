# Terra Max empty-Memory isolation recheck — 2026-08-17

Reviewer configuration: `gpt-5.6-terra`, reasoning effort `max`.

## Scope

Independent review of the dedicated empty-Memory remediation across the real
CLI plan builder, Eval authority/recovery and broker path, Operator Study
snapshot path, tests, and Trellis contracts.

## Findings fixed during review

- Empty-tree validation now rejects unexpected regular or special entries,
  symlinks, redirected roots, ownership drift, and group/world permissions
  before computing the frozen digest.
- Operator empty-fixture Study creation requires the explicit Guard root,
  proves Memory/Guard disjointness, and rechecks the immutable snapshot digest
  without reading or mutating canonical `.autobugfix-memory`.
- Eval rejects redirected Guard paths both when building and reopening a plan.
- Real calibration/formal dispatch passes both dedicated Memory and Guard as
  additional hidden paths. Evidence adoption requires the exact broker list,
  outer Bubblewrap mask coverage for both, SDK hidden-path evidence, and exact
  task-worktree cwd.
- `build-plan-v2` requires `--memory-root` and refuses an output path inside
  that fixture.
- No model, scorer, budget, role-skill, Memory-evolution, or candidate-scope
  drift was found. Execution remains the only treatment.

## Final verification

- Focused: `51 passed`.
- Full suite: `388 passed, 1 skipped`.
- `python -m compileall -q src tests scripts`: passed.
- `git diff --check`: passed.
- `scripts/validate_role_skills.py`: passed.
- Dedicated Ruff/type-check commands are not installed/configured in this
  project environment and were not claimed as passing.

## Verdict

`PROCEED` — the review's temporary `BLOCK` was solely for missing post-patch
verification; the exact final tree passed the checks above with no subsequent
source changes.
