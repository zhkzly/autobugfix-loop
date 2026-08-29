# Raw experiment evidence (per-study ledgers and formal case reports)

This directory publishes the machine-generated raw result layer of the eight
Exp2 evolution rounds, extracted verbatim from the trusted Eval store
(`.autobugfix/trusted-eval-cases/exp2/`, which stays outside git). Nothing here
is hand-edited.

Per study directory (e.g. `exp2-pilot-c4562b5-v3r6b/`):

- `events.jsonl` — the append-only event ledger: initialization, per-case
  `case_attempt_started` / `case_attempt_terminal` receipts (terminal status,
  resolved flag, digests), stage transitions, and the final decision event.
  Every event carries `predecessor_event_digest` + `record_digest`, so the
  chain is tamper-evident: altering one line breaks every later digest.
- `formal-case-reports/exp2v2-<stage>-<nn>-<digest>.yaml` — the official
  per-case reports as produced by the Eval plane (stage `h0`,
  `h1_evolution`, `h1_regression`; `nn` is the case position in the frozen
  composition). These carry the official scorer result, submission and
  noninterference digests.
- `plan.yaml` / `protocol.yaml` — the frozen plan and protocol records the
  study executed under.
- `source-projection-bundle.yaml` — the minimal failure-set projection that
  the Operator (revision author) was allowed to see.

The full trusted state (18 GB: broker execution evidence trees, image gates,
frozen submissions, raw SDK logs, governance sqlite) remains in the local
`.autobugfix/` store; every artifact above carries digests that bind into it.

Held-out final per-case reports: `../heldout-reports/`. Round decision
reports: `../round-reports/`. Human-readable summary: `../EVOLUTION-LEDGER.md`.
