from __future__ import annotations

import json
import socket
import sys
import threading
import types
from pathlib import Path

import pytest

from autobugfix.codex_sdk import (
    CODEX_BROKER_SOCKET_ENV,
    CODEX_BROKER_TOKEN_ENV,
    CodexSDKBackend,
    CodexSDKError,
)
from autobugfix.models import CodexRequest


def write_project_config(root: Path) -> None:
    (root / ".autobugfix").mkdir(exist_ok=True)
    (root / ".autobugfix/config.yaml").write_text("{}\n", encoding="utf-8")


def test_codex_sdk_preview_api_parameter_passing(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    calls = {}

    class Sandbox(str):
        def __new__(cls, value):
            return str.__new__(cls, value)

    class ApprovalMode:
        auto_review = "auto_review"
        deny_all = "deny_all"

    class CodexConfig:
        def __init__(self, cwd=None, env=None, codex_bin=None):
            calls["config_cwd"] = cwd
            calls["config_env"] = env
            calls["config_codex_bin"] = codex_bin

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


def test_preview_sdk_without_isolated_runtime_config_fails_closed(tmp_path, monkeypatch):
    write_project_config(tmp_path)

    class CodexConfig:
        def __init__(self, cwd=None):
            self.cwd = cwd

    module = types.SimpleNamespace(CodexConfig=CodexConfig)
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

    with pytest.raises(CodexSDKError, match="required isolated env/codex_bin"):
        CodexSDKBackend()._call_preview_sdk(module, request)


def test_sdk_role_without_project_config_refuses_global_runtime(tmp_path):
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

    with pytest.raises(CodexSDKError, match="refusing to inherit global Codex hooks"):
        CodexSDKBackend()._runtime_env(request)


def test_runtime_bridge_sanitizes_user_config_and_legacy_effort(tmp_path, monkeypatch):
    home = tmp_path / "home"
    source_home = home / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "model-x"\nmodel_reasoning_effort = "max"\n'
        '[mcp_servers.private]\nurl = "https://example.invalid"\n'
        '[mcp_servers.private.http_headers]\nAuthorization = "secret-value"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    write_project_config(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("UNRELATED_TOKEN", "must-not-reach-sdk")
    request = CodexRequest(
        role="writer",
        prompt="prompt",
        cwd=project,
        sandbox="workspace-write",
        model=None,
        timeout_seconds=30,
        developer_instructions="instructions",
        raw_log_path=project / "raw.jsonl",
        stderr_log_path=project / "stderr.log",
        approval_mode="auto_review",
    )
    environment = CodexSDKBackend()._runtime_env(request)
    assert environment is not None
    assert "UNRELATED_TOKEN" not in environment
    runtime_config = Path(environment["CODEX_HOME"]) / "config.toml"
    text = runtime_config.read_text(encoding="utf-8")
    assert 'model_reasoning_effort = "xhigh"' in text
    assert "hooks = false" in text
    assert "multi_agent = false" in text
    assert "mcp_servers" not in text
    assert "secret-value" not in text
    assert f"[projects.{json.dumps(str(project.resolve()))}]" in text


def test_sdk_uses_trusted_broker_without_importing_candidate_sdk(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    write_project_config(project)
    socket_path = tmp_path / "broker.sock"
    token = "ephemeral-capability-token"
    captured = {}
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _ = server.accept()
            with connection, connection.makefile("rwb") as stream:
                captured.update(json.loads(stream.readline()))
                stream.write(
                    json.dumps(
                        {
                            "result": {
                                "text": "brokered result",
                                "exit_code": 7,
                                "receipt": {"sequence": 1},
                            }
                        }
                    ).encode("utf-8")
                    + b"\n"
                )
                stream.flush()

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=5)
    monkeypatch.setenv(CODEX_BROKER_SOCKET_ENV, str(socket_path))
    monkeypatch.setenv(CODEX_BROKER_TOKEN_ENV, token)
    request = CodexRequest(
        role="writer",
        prompt="repair the repository",
        cwd=project,
        sandbox="workspace-write",
        model="gpt-5.4-mini",
        timeout_seconds=30,
        developer_instructions="follow the writer contract",
        raw_log_path=project / "raw.jsonl",
        stderr_log_path=project / "stderr.log",
        approval_mode="auto_review",
    )

    result = CodexSDKBackend("candidate_sdk_must_not_be_imported").run(request)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result.text == "brokered result"
    assert result.exit_code == 7
    assert captured["token"] == token
    assert captured["control_root"] == str(project)
    raw_log = request.raw_log_path.read_text(encoding="utf-8")
    assert "trusted_operator_codex_broker" in raw_log
    assert token not in raw_log


def test_sdk_rejects_partial_broker_environment(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    monkeypatch.setenv(CODEX_BROKER_SOCKET_ENV, str(tmp_path / "missing.sock"))
    monkeypatch.delenv(CODEX_BROKER_TOKEN_ENV, raising=False)
    request = CodexRequest(
        role="writer",
        prompt="repair",
        cwd=tmp_path,
        sandbox="workspace-write",
        model="gpt-5.4-mini",
        timeout_seconds=30,
        developer_instructions="writer contract",
        raw_log_path=tmp_path / "raw.jsonl",
        stderr_log_path=tmp_path / "stderr.log",
        approval_mode="auto_review",
    )

    with pytest.raises(CodexSDKError, match="configuration is incomplete"):
        CodexSDKBackend().run(request)
