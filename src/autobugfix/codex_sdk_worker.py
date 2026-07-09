from __future__ import annotations

from autobugfix.codex_sdk import CodexSDKBackend


def build_backend() -> CodexSDKBackend:
    return CodexSDKBackend()
