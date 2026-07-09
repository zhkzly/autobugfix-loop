from __future__ import annotations

from typing import Protocol

from autobugfix.models import CodexRequest, CodexResult


class CodexBackend(Protocol):
    def run(self, request: CodexRequest) -> CodexResult:
        """Run a role-scoped Codex request and return captured text/raw output."""
