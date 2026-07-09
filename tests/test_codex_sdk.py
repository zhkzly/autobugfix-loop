from __future__ import annotations

import sys
import types

from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.models import CodexRequest


def test_codex_sdk_preview_api_parameter_passing(tmp_path, monkeypatch):
    calls = {}

    class Sandbox(str):
        def __new__(cls, value):
            return str.__new__(cls, value)

    class ApprovalMode:
        auto_review = "auto_review"
        deny_all = "deny_all"

    class CodexConfig:
        def __init__(self, cwd=None):
            calls["config_cwd"] = cwd

    class Thread:
        def run(self, prompt, **kwargs):
            calls["run"] = {"prompt": prompt, **kwargs}
            return types.SimpleNamespace(final_response="done")

    class Codex:
        def __init__(self, config):
            calls["client"] = config

        def thread_start(self, **kwargs):
            calls["thread_start"] = kwargs
            return Thread()

        def close(self):
            calls["closed"] = True

    module = types.SimpleNamespace(Codex=Codex, CodexConfig=CodexConfig, Sandbox=Sandbox, ApprovalMode=ApprovalMode)
    monkeypatch.setitem(sys.modules, "fake_codex", module)
    request = CodexRequest(
        role="writer",
        prompt="hello",
        cwd=tmp_path,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=3,
        developer_instructions="dev",
        raw_log_path=tmp_path / "raw.jsonl",
        stderr_log_path=tmp_path / "err.log",
        approval_mode="auto_review",
    )
    result = CodexSDKBackend("fake_codex").run(request)
    assert result.text == "done"
    assert calls["thread_start"]["developer_instructions"] == "dev"
    assert calls["thread_start"]["approval_mode"] == "auto_review"
    assert calls["run"]["sandbox"] == "workspace-write"
