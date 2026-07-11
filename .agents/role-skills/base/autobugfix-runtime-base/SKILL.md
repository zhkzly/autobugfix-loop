---
name: autobugfix-runtime-base
description: Shared runtime boundaries for Autobugfix Codex roles.
---

# Autobugfix Runtime Base

You are running inside Autobugfix, a local control system for bugfix work.

Core boundaries:
- The target application repository is not Autobugfix.
- Do not modify the target repo main checkout.
- Respect the current role's cwd and sandbox.
- Preserve evidence in the files requested by the caller.
- Do not approve PPE, final acceptance, memory proposals, or archive tasks.
- Do not use private repo names, local usernames, or organization-specific commands.
- Do not rely on project Codex hooks. Isolated SDK roles run with hooks disabled;
  service, sandbox, worktree, verifier, and approval boundaries remain binding.

If you cannot complete the role, explain the blocker clearly.
