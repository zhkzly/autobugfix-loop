from __future__ import annotations

import importlib
import json
import os
import shutil
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from autobugfix.codex_backend import CodexBackend
from autobugfix.config import load_config
from autobugfix.models import CodexRequest, CodexResult, utc_now


class CodexSDKError(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _extract_text(result: Any) -> str:
    final_response = getattr(result, "final_response", None)
    if final_response is not None:
        text = _extract_text(final_response)
        if text:
            return text
    for attr in ("output_text", "text", "content", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    if isinstance(result, dict):
        for key in ("output_text", "text", "content", "message"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    items = getattr(result, "items", None)
    if isinstance(items, list):
        parts = [_extract_text(item) for item in items]
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    return str(result)


class CodexSDKBackend(CodexBackend):
    """Production adapter for the local preview Python Codex SDK.

    The current preview package is `openai-codex` and imports as
    `openai_codex`. It exposes `Codex(CodexConfig(...)).thread_start(...).run`.
    This adapter never invokes `codex exec` and never falls back to a fake
    backend for production CLI paths.
    """

    module_names = ("openai_codex", "codex")

    def __init__(self, module_name: str | None = None) -> None:
        self.module_name = module_name

    def _load_module(self) -> Any:
        names = (self.module_name,) if self.module_name else self.module_names
        errors: list[str] = []
        for name in names:
            if not name:
                continue
            try:
                return importlib.import_module(name)
            except ImportError as exc:
                errors.append(f"{name}: {exc}")
        joined = "; ".join(errors) or "no module names configured"
        raise CodexSDKError(
            "Python Codex SDK is not installed or importable. Install the preview "
            f"`openai-codex` package in this uv environment. Tried: {joined}"
        )

    def _project_root_for(self, request: CodexRequest) -> Path | None:
        candidates = [request.cwd, request.raw_log_path, *request.raw_log_path.parents]
        for candidate in candidates:
            path = candidate if candidate.is_dir() else candidate.parent
            for parent in [path, *path.parents]:
                if (parent / ".autobugfix/config.yaml").exists():
                    return parent.resolve()
        return None

    def _runtime_env(self, request: CodexRequest) -> dict[str, str] | None:
        project_root = self._project_root_for(request)
        if project_root is None:
            return None
        cfg = load_config(project_root)
        runtime = cfg.codex.role_runtime
        if not runtime.enabled or not runtime.bridge_auth:
            return None
        runtime_root = runtime.runtime_root
        if not runtime_root.is_absolute():
            runtime_root = project_root / runtime_root
        codex_home = runtime_root / "home"
        codex_home.mkdir(parents=True, exist_ok=True)
        source_home = Path.home() / ".codex"
        for name in ("auth.json", "config.toml", "version.json", "installation_id", ".personality_migration"):
            source = source_home / name
            dest = codex_home / name
            if source.exists() and not dest.exists():
                shutil.copy2(source, dest)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        return env

    def _call_preview_sdk(self, module: Any, request: CodexRequest) -> Any:
        try:
            config = module.CodexConfig(cwd=str(request.cwd), env=self._runtime_env(request))
        except TypeError:
            config = module.CodexConfig(cwd=str(request.cwd))
        client = module.Codex(config)
        try:
            sandbox = module.Sandbox(request.sandbox)
            approval_name = request.approval_mode or ("auto_review" if request.sandbox == "workspace-write" else "deny_all")
            approval = getattr(module.ApprovalMode, approval_name, approval_name)
            thread = client.thread_start(
                approval_mode=approval,
                cwd=str(request.cwd),
                developer_instructions=request.developer_instructions,
                model=request.model,
                sandbox=sandbox,
            )
            return thread.run(
                request.prompt,
                approval_mode=approval,
                cwd=str(request.cwd),
                model=request.model,
                sandbox=sandbox,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _call_legacy_sdk(self, module: Any, request: CodexRequest) -> Any:
        kwargs = {
            "prompt": request.prompt,
            "cwd": str(request.cwd),
            "sandbox": request.sandbox,
            "model": request.model,
            "timeout_seconds": request.timeout_seconds,
            "developer_instructions": request.developer_instructions,
        }
        if hasattr(module, "Client"):
            client = module.Client()
            if hasattr(client, "run"):
                return client.run(**kwargs)
            if hasattr(client, "responses") and hasattr(client.responses, "run"):
                return client.responses.run(**kwargs)
        if hasattr(module, "Codex"):
            client = module.Codex()
            if hasattr(client, "run"):
                return client.run(**kwargs)
        if hasattr(module, "run"):
            return module.run(**kwargs)
        raise CodexSDKError(
            "Python Codex SDK import succeeded, but no supported run API was found. "
            "Expected openai_codex Codex.thread_start().run(...), module.run(...), "
            "Client().run(...), Client().responses.run(...), or Codex().run(...)."
        )

    def _call_sdk(self, module: Any, request: CodexRequest) -> Any:
        if all(hasattr(module, name) for name in ("Codex", "CodexConfig", "Sandbox", "ApprovalMode")):
            return self._call_preview_sdk(module, request)
        return self._call_legacy_sdk(module, request)

    def run(self, request: CodexRequest) -> CodexResult:
        request.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log_path.touch(exist_ok=True)
        with request.raw_log_path.open("a", encoding="utf-8") as raw:
            raw.write(json.dumps({"kind": "codex_request", "timestamp": utc_now(), "request": _jsonable(request)}, sort_keys=True) + "\n")
        try:
            module = self._load_module()
            result = self._call_sdk(module, request)
            text = _extract_text(result)
            raw_payload = _jsonable(result)
            module_name = getattr(module, "__name__", self.module_name or type(module).__name__)
            with request.raw_log_path.open("a", encoding="utf-8") as raw:
                raw.write(
                    json.dumps(
                        {
                            "kind": "codex_response",
                            "timestamp": utc_now(),
                            "module": module_name,
                            "response": raw_payload,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            return CodexResult(text=text, raw={"module": module_name, "response": raw_payload})
        except Exception as exc:
            request.stderr_log_path.write_text(traceback.format_exc(), encoding="utf-8")
            with request.raw_log_path.open("a", encoding="utf-8") as raw:
                raw.write(
                    json.dumps(
                        {
                            "kind": "codex_error",
                            "timestamp": utc_now(),
                            "error": repr(exc),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            raise
