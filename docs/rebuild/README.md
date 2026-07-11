Autobugfix Rebuild Dossier
This directory is the source-of-truth dossier for recreating the Autobugfix control project in a clean repository with a different target application repo.
The rebuilt project must preserve the same product purpose, loop boundaries, state ownership, file layout, CLI surface, role isolation, and real end-to-end acceptance behavior. The only expected change is the configured target repo profile in .autobugfix/config.yaml.
How To Use This Dossier
Use this dossier when asking an AI coding agent to rebuild the project from scratch or when extracting the project into a public repository.
Recommended flow:
Create a fresh Git repository.
Give the AI agent this full docs/rebuild/ directory.
Use 04-ai-generation-task.md as the primary Trellis task or PRD.
Require the AI agent to implement production code, not a mock harness.
Require the acceptance checks in 05-real-acceptance.md before merge.
Do not give the AI only .trellis/ and expect a rebuild. Trellis contains workflow memory and history. It is useful context, but this dossier is the compiled rebuild contract.
Documents
01-project-purpose.md
Defines the product purpose and the four loop boundaries.
02-system-architecture.md
Defines state machines, service/projection boundaries, Codex roles, and data
flows.
03-file-structure-contract.md
Defines the repository tree and the runtime state directories the rebuilt
project must create.
04-ai-generation-task.md
Paste-ready task spec for an AI agent or Trellis task. It is strict about
real code and real acceptance.
05-real-acceptance.md
End-to-end acceptance plan using a pinned public Git repository, real worktrees,
real Codex SDK writer/evaluator calls, memory loop, and eval loop.
06-non-mock-guardrails.md
Explicit constraints that prevent the AI from replacing the system with
mock-only code.
07-config-and-portability.md
Repo-agnostic configuration rules and privacy/publication constraints.
08-review-protocol.md
Multi-review and purpose-checkpoint protocol for rebuilds, major changes, and
final acceptance.
Rebuild Principle
The rebuilt repository is not a demo. It must be a working local control system:
human bug report
  -> deterministic task record
  -> deterministic Git worktree
  -> real Codex writer
  -> real verifier command
  -> real Codex evaluator
  -> human gate
  -> memory extraction
  -> eval experiment scoring

Tests may use fake backends for unit determinism, but the shipped CLI and the acceptance flow must run the real production paths.
