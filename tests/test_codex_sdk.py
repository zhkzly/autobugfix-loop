from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from autobugfix.codex_sdk import CodexSDKBackend, CodexSDKError
from autobugfix.git_utils import git_common_dir, git_dir
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
        control_root=tmp_path,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=3,
        developer_instructions="dev",
        raw_log_path=tmp_path / "raw.jsonl",
        stderr_log_path=tmp_path / "err.log",
        approval_mode="auto_review",
    )
    result = CodexSDKBackend("fake_codex", in_process=True).run(request)
    assert result.text == "done"
    assert calls["thread_start"]["developer_instructions"] == "dev"
    assert calls["thread_start"]["approval_mode"] == "auto_review"
    assert calls["run"]["sandbox"] == "workspace-write"


def test_production_sdk_backend_runs_in_bounded_worker_process(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    worktree = tmp_path / "worktree"
    logs = tmp_path / "logs"
    worktree.mkdir()
    (tmp_path / "fake_worker_codex.py").write_text(
        """
import os
import time
import types

class Sandbox(str):
    pass

class ApprovalMode:
    auto_review = "auto_review"
    deny_all = "deny_all"

class CodexConfig:
    def __init__(self, cwd=None, env=None, codex_bin=None):
        self.cwd = cwd

class Thread:
    def run(self, prompt, **kwargs):
        if prompt == "sleep":
            time.sleep(5)
        if prompt == "environment":
            return types.SimpleNamespace(
                final_response=os.environ.get("UNRELATED_TOKEN", "absent")
            )
        return types.SimpleNamespace(final_response="worker-done")

class Codex:
    def __init__(self, config):
        self.config = config

    def thread_start(self, **kwargs):
        return Thread()

    def close(self):
        pass
""".lstrip(),
        encoding="utf-8",
    )

    def request(prompt: str, name: str, timeout: int) -> CodexRequest:
        return CodexRequest(
            role="writer",
            prompt=prompt,
            cwd=worktree,
            control_root=tmp_path,
            sandbox="workspace-write",
            model="gpt-test",
            timeout_seconds=timeout,
            developer_instructions="dev",
            raw_log_path=logs / f"{name}.raw.jsonl",
            stderr_log_path=logs / f"{name}.stderr.log",
            approval_mode="auto_review",
        )

    monkeypatch.setenv("UNRELATED_TOKEN", "must-not-reach-worker")
    backend = CodexSDKBackend("fake_worker_codex")
    result = backend.run(request("complete", "complete", 5))
    assert result.text == "worker-done"
    assert (logs / "complete.raw.jsonl.sdk-request.json").is_file()
    assert (logs / "complete.raw.jsonl.sdk-result.json").is_file()
    assert (logs / "complete.raw.jsonl").stat().st_mode & 0o077 == 0
    assert (logs / "complete.raw.jsonl.worker.stderr.log").stat().st_mode & 0o077 == 0
    request_payload = json.loads(
        (logs / "complete.raw.jsonl.sdk-request.json").read_text(encoding="utf-8")
    )
    assert request_payload["control_root"] == str(tmp_path)
    for artifact in (
        logs / "complete.raw.jsonl.sdk-request.json",
        logs / "complete.raw.jsonl.sdk-result.json",
        logs / "complete.raw.jsonl.worker.stdout.log",
        logs / "complete.raw.jsonl.worker.stderr.log",
    ):
        assert artifact.stat().st_mode & 0o077 == 0
    assert backend.run(request("environment", "environment", 5)).text == "absent"

    with pytest.raises(CodexSDKError, match="timed out after 1 seconds"):
        backend.run(request("sleep", "timeout", 1))
    raw = (logs / "timeout.raw.jsonl").read_text(encoding="utf-8")
    assert '"kind": "codex_timeout"' in raw


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
        control_root=tmp_path,
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
        control_root=tmp_path,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=3,
        developer_instructions="dev",
        raw_log_path=tmp_path / "raw.jsonl",
        stderr_log_path=tmp_path / "err.log",
        approval_mode="auto_review",
    )

    with pytest.raises(CodexSDKError, match="trusted Codex control root"):
        CodexSDKBackend()._runtime_env(request)


def test_runtime_bridge_sanitizes_user_config_and_legacy_effort(tmp_path, monkeypatch):
    home = tmp_path / "home"
    source_home = home / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "model-x"\nmodel_reasoning_effort = "max"\nservice_tier = "fast"\n'
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
        control_root=project,
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
    assert 'model_reasoning_effort = "medium"' in text
    assert "hooks = false" in text
    assert "multi_agent = false" in text
    assert "mcp_servers" not in text
    assert "service_tier" not in text
    assert "secret-value" not in text
    assert f"[projects.{json.dumps(str(project.resolve()))}]" in text
    second_environment = CodexSDKBackend()._runtime_env(request)
    assert second_environment["CODEX_HOME"] != environment["CODEX_HOME"]


def test_target_worktree_config_cannot_override_trusted_control_runtime(tmp_path, monkeypatch):
    control_root = tmp_path / "control"
    target = tmp_path / "target"
    control_root.mkdir()
    target.mkdir()
    write_project_config(control_root)
    (control_root / ".autobugfix/config.yaml").write_text(
        "codex:\n  role_runtime:\n    codex_bin: ./trusted-codex\n",
        encoding="utf-8",
    )
    write_project_config(target)
    (target / ".autobugfix/config.yaml").write_text(
        "codex:\n  role_runtime:\n    codex_bin: ./attacker-codex\n",
        encoding="utf-8",
    )
    calls: dict[str, object] = {}

    class Sandbox(str):
        pass

    class ApprovalMode:
        auto_review = "auto_review"

    class CodexConfig:
        def __init__(self, cwd=None, env=None, codex_bin=None):
            calls["codex_bin"] = codex_bin

    class Thread:
        def run(self, prompt, **kwargs):
            return types.SimpleNamespace(final_response="done")

    class Codex:
        def __init__(self, config):
            self.config = config

        def thread_start(self, **kwargs):
            return Thread()

    module = types.SimpleNamespace(
        Codex=Codex,
        CodexConfig=CodexConfig,
        Sandbox=Sandbox,
        ApprovalMode=ApprovalMode,
    )
    monkeypatch.setitem(sys.modules, "trusted_root_codex", module)
    request = CodexRequest(
        role="writer",
        prompt="hello",
        cwd=target,
        control_root=control_root,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=3,
        developer_instructions="dev",
        raw_log_path=control_root / "raw.jsonl",
        stderr_log_path=control_root / "err.log",
        approval_mode="auto_review",
    )
    CodexSDKBackend("trusted_root_codex", in_process=True).run(request)
    assert calls["codex_bin"] == str((control_root / "trusted-codex").resolve())


def test_worker_bubblewrap_hides_host_tmp_authority_but_mounts_worktree(tmp_path):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    control = tmp_path / "control"
    worktree = control / "worktree"
    authority = control / "trusted-authority"
    worktree.mkdir(parents=True)
    authority.mkdir()
    (authority / "gold.patch").write_text("secret\n", encoding="utf-8")
    write_project_config(control)
    request = CodexRequest(
        role="writer",
        prompt="inspect isolation",
        cwd=worktree,
        control_root=control,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=control / "logs/raw.jsonl",
        stderr_log_path=control / "logs/stderr.log",
        approval_mode="auto_review",
        hidden_paths=(authority,),
    )
    backend = CodexSDKBackend()
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"print(Path({str(authority / 'gold.patch')!r}).exists()); "
            "print(Path('/run/docker.sock').exists()); "
            "print(Path('/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe').exists()); "
            f"Path({str(worktree / 'writer.txt')!r}).write_text('ok')"
        ),
    ]
    result = subprocess.run(
        backend.worker_launch_argv(request, command),
        cwd=control,
        env=backend.worker_environment(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["False", "False", "False"]
    assert (worktree / "writer.txt").read_text(encoding="utf-8") == "ok"


def test_worker_bubblewrap_enforces_read_only_cwd_and_hides_home(tmp_path):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    control = tmp_path / "control"
    worktree = control / "worktree"
    worktree.mkdir(parents=True)
    write_project_config(control)
    request = CodexRequest(
        role="evaluator",
        prompt="inspect read-only isolation",
        cwd=worktree,
        control_root=control,
        sandbox="read-only",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=control / "logs/raw.jsonl",
        stderr_log_path=control / "logs/stderr.log",
        approval_mode="deny_all",
    )
    backend = CodexSDKBackend()
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path\n"
            f"target = Path({str(worktree / 'forbidden.txt')!r})\n"
            "try:\n"
            "    target.write_text('forbidden')\n"
            "except OSError:\n"
            "    print('read-only')\n"
            "print((Path.home() / '.ssh').exists())"
        ),
    ]
    result = subprocess.run(
        backend.worker_launch_argv(request, command),
        cwd=control,
        env=backend.worker_environment(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["read-only", "False"]
    assert not (worktree / "forbidden.txt").exists()


def test_worker_bubblewrap_reopens_only_current_log_dir_under_hidden_authority(
    tmp_path,
):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    control = tmp_path / "control"
    authority = control / ".autobugfix/operator-artifacts"
    log_root = authority / "request-1/supervisor/run-1"
    sibling_secret = authority / "request-2/private.yaml"
    log_root.mkdir(parents=True)
    sibling_secret.parent.mkdir(parents=True)
    sibling_secret.write_text("secret\n", encoding="utf-8")
    write_project_config(control)
    request = CodexRequest(
        role="operator_supervisor",
        prompt="inspect log isolation",
        cwd=control,
        control_root=control,
        sandbox="read-only",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=log_root / "raw.jsonl",
        stderr_log_path=log_root / "stderr.log",
        approval_mode="deny_all",
        hidden_paths=(authority,),
    )
    backend = CodexSDKBackend()
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"log = Path({str(log_root / 'worker.txt')!r}); "
            f"secret = Path({str(sibling_secret)!r}); "
            "log.write_text('visible'); print(log.exists()); print(secret.exists())"
        ),
    ]
    result = subprocess.run(
        backend.worker_launch_argv(request, command),
        cwd=control,
        env=backend.worker_environment(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["True", "False"]
    assert (log_root / "worker.txt").read_text(encoding="utf-8") == "visible"


def test_worker_rejects_workspace_write_at_trusted_control_root(tmp_path):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    write_project_config(tmp_path)
    request = CodexRequest(
        role="writer",
        prompt="must use a task worktree",
        cwd=tmp_path,
        control_root=tmp_path,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=tmp_path / "logs/raw.jsonl",
        stderr_log_path=tmp_path / "logs/stderr.log",
        approval_mode="auto_review",
    )

    with pytest.raises(
        CodexSDKError,
        match="workspace-write Codex role cannot use the trusted control root",
    ):
        CodexSDKBackend().worker_launch_argv(request, [sys.executable, "-V"])


def test_worker_bubblewrap_restores_only_linked_worktree_git_metadata(tmp_path):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    control = tmp_path / "control"
    cache_root = control / ".autobugfix/benchmark-cache"
    main = cache_root / "cases/case-1/repo"
    authority = cache_root / "trusted/case-1/gold.patch"
    worktree = control / ".autobugfix/eval-runs/run-1/task-worktree"
    main.mkdir(parents=True)
    authority.parent.mkdir(parents=True)
    worktree.parent.mkdir(parents=True)
    write_project_config(control)
    subprocess.run(
        ["git", "init", "-b", "main", str(main)],
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "config", "user.name", "Test User"],
        check=True,
    )
    (main / "source.py").write_text("VALUE = 'buggy'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "source.py"], check=True)
    subprocess.run(
        ["git", "-C", str(main), "commit", "-m", "buggy snapshot"],
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(main),
            "worktree",
            "add",
            "-b",
            "fix/case-1",
            str(worktree),
            "HEAD",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    authority.write_text("secret gold\n", encoding="utf-8")

    request = CodexRequest(
        role="writer",
        prompt="inspect linked worktree isolation",
        cwd=worktree,
        control_root=control,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=control / "logs/raw.jsonl",
        stderr_log_path=control / "logs/stderr.log",
        approval_mode="auto_review",
        hidden_paths=(cache_root,),
        readable_paths=(git_common_dir(main),),
        writable_paths=(git_dir(worktree),),
    )
    backend = CodexSDKBackend()
    payload = backend.worker_request_payload(request)
    assert payload["readable_paths"] == [str(git_common_dir(main))]
    assert payload["writable_paths"] == [str(git_dir(worktree))]
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess; from pathlib import Path; "
            f"worktree = Path({str(worktree)!r}); "
            f"gold = Path({str(authority)!r}); "
            "before = subprocess.run(['git', '-C', str(worktree), 'status', "
            "'--porcelain'], text=True, capture_output=True); "
            "(worktree / 'source.py').write_text(\"VALUE = 'fixed'\\n\"); "
            "after = subprocess.run(['git', '-C', str(worktree), 'status', "
            "'--porcelain'], text=True, capture_output=True); "
            "diff = subprocess.run(['git', '-C', str(worktree), 'diff', "
            "'--name-only'], text=True, capture_output=True); "
            "print(before.returncode); print(after.returncode); "
            "print(diff.stdout.strip()); print(gold.exists())"
        ),
    ]
    result = subprocess.run(
        backend.worker_launch_argv(request, command),
        cwd=control,
        env=backend.worker_environment(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["0", "0", "source.py", "False"]
    assert subprocess.run(
        ["git", "-C", str(main), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout == ""


def test_production_worker_fails_closed_without_bubblewrap(tmp_path, monkeypatch):
    write_project_config(tmp_path)
    request = CodexRequest(
        role="writer",
        prompt="isolate",
        cwd=tmp_path,
        control_root=tmp_path,
        sandbox="workspace-write",
        model="m",
        timeout_seconds=10,
        developer_instructions="dev",
        raw_log_path=tmp_path / "raw.jsonl",
        stderr_log_path=tmp_path / "stderr.log",
        approval_mode="auto_review",
    )
    monkeypatch.setattr("autobugfix.codex_sdk.shutil.which", lambda name: None)

    with pytest.raises(CodexSDKError, match="requires Bubblewrap"):
        CodexSDKBackend().worker_launch_argv(request, [sys.executable, "-V"])
