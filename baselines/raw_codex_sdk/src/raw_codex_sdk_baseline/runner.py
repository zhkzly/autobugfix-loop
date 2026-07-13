from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import openai_codex
from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort

from raw_codex_sdk_baseline.models import (
    CaseBundle,
    digest_file,
    record_with_digest,
)
from raw_codex_sdk_baseline.prompt import (
    DEVELOPER_INSTRUCTIONS,
    PROMPT_TEMPLATE_DIGEST,
    render_prompt,
)


class RunnerError(RuntimeError):
    pass


APPROVAL_MODE = "deny_all"
SANDBOX_MODE = "workspace-write"
NETWORK_ACCESS = False


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=False, mode="json")
        except TypeError:
            dumped = model_dump(by_alias=False)
        return _jsonable(dumped)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RunnerError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _validate_worktree(worktree: Path, expected_base: str) -> None:
    if not worktree.is_dir():
        raise RunnerError("worktree does not exist")
    if _git(worktree, "rev-parse", "HEAD") != expected_base:
        raise RunnerError("worktree HEAD differs from visible case base_commit")
    if _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RunnerError("Raw Codex worktree must be clean before the SDK turn")


def _event_fields(value: Mapping[str, Any]) -> tuple[str | None, Any, str | None]:
    method = str(value.get("method") or "")
    payload = value.get("payload")
    final_text: str | None = None
    if method == "item/completed" and isinstance(payload, Mapping):
        item = payload.get("item")
        if isinstance(item, Mapping):
            root = item.get("root")
            if isinstance(root, Mapping):
                item = root
            item_type = str(item.get("type") or "")
            phase = str(item.get("phase") or "")
            text = item.get("text")
            if item_type in {"agentMessage", "agent_message"} and isinstance(text, str):
                if phase in {"finalAnswer", "final_answer", ""}:
                    final_text = text
    usage = None
    if method == "thread/tokenUsage/updated" and isinstance(payload, Mapping):
        usage = payload.get("tokenUsage") or payload.get("token_usage")
    return method or None, usage, final_text


def run_case(
    case: CaseBundle,
    worktree: Path,
    artifacts: Path,
    *,
    model: str,
    reasoning_effort: str,
    service_tier: str | None,
) -> dict[str, Any]:
    _validate_worktree(worktree, case.base_commit)
    artifacts.mkdir(parents=True, exist_ok=False)
    artifacts.chmod(0o700)
    events_path = artifacts / "events.jsonl"
    stderr_path = artifacts / "stderr.log"
    request_path = artifacts / "request.json"
    result_path = artifacts / "process-result.json"
    started_wall = time.time()
    started_monotonic = time.monotonic()
    thread_id: str | None = None
    turn_id: str | None = None
    status = "sdk_error"
    error = ""
    final_response = ""
    usage: Any = None
    event_count = 0
    stderr_path.write_text("", encoding="utf-8")
    prompt = render_prompt(case)
    request = record_with_digest(
        {
            "schema": "raw-codex-sdk-request-v1",
            "case_id": case.case_id,
            "case_digest": case.record_digest,
            "sdk_package": "openai-codex",
            "sdk_version": openai_codex.__version__,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "service_tier": service_tier,
            "approval_mode": APPROVAL_MODE,
            "sandbox": SANDBOX_MODE,
            "network_access": NETWORK_ACCESS,
            "prompt_template_digest": PROMPT_TEMPLATE_DIGEST,
            "developer_instructions": DEVELOPER_INSTRUCTIONS,
            "prompt": prompt,
        }
    )
    _write_json_atomic(request_path, request)
    try:
        config = CodexConfig(cwd=str(worktree), env=dict(os.environ))
        with Codex(config) as client:
            thread = client.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(worktree),
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                ephemeral=True,
                model=model,
                sandbox=Sandbox.workspace_write,
                service_tier=service_tier,
            )
            thread_id = thread.id
            turn = thread.turn(
                prompt,
                approval_mode=ApprovalMode.deny_all,
                cwd=str(worktree),
                effort=ReasoningEffort(reasoning_effort),
                model=model,
                sandbox=Sandbox.workspace_write,
                service_tier=service_tier,
            )
            turn_id = turn.id
            descriptor = os.open(
                events_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                for event in turn.stream():
                    event_value = _jsonable(event)
                    if not isinstance(event_value, Mapping):
                        event_value = {"value": event_value}
                    stream.write(
                        json.dumps(event_value, ensure_ascii=True, sort_keys=True)
                        + "\n"
                    )
                    stream.flush()
                    event_count += 1
                    method, observed_usage, observed_final = _event_fields(event_value)
                    if observed_usage is not None:
                        usage = observed_usage
                    if observed_final is not None:
                        final_response = observed_final
                    if method == "turn/completed":
                        payload = event_value.get("payload")
                        turn_value = (
                            payload.get("turn")
                            if isinstance(payload, Mapping)
                            else None
                        )
                        turn_status = (
                            str(turn_value.get("status") or "")
                            if isinstance(turn_value, Mapping)
                            else ""
                        )
                        if turn_status != "completed":
                            raise RunnerError(
                                "SDK turn/completed event has non-completed status: "
                                + (turn_status or "missing")
                            )
                        status = "completed"
        if status != "completed":
            raise RunnerError("SDK stream ended without turn/completed")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        stderr_path.write_text(traceback.format_exc(), encoding="utf-8")
    if not events_path.exists():
        events_path.write_text("", encoding="utf-8")
    record = record_with_digest(
        {
            "schema": "raw-codex-sdk-process-result-v1",
            "case_id": case.case_id,
            "case_digest": case.record_digest,
            "sdk_package": "openai-codex",
            "sdk_version": openai_codex.__version__,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "service_tier": service_tier,
            "approval_mode": APPROVAL_MODE,
            "sandbox": SANDBOX_MODE,
            "network_access": NETWORK_ACCESS,
            "prompt_template_digest": PROMPT_TEMPLATE_DIGEST,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": status,
            "error": error,
            "final_response": final_response,
            "usage": _jsonable(usage),
            "event_count": event_count,
            "request_sha256": digest_file(request_path),
            "events_sha256": digest_file(events_path),
            "stderr_sha256": digest_file(stderr_path),
            "started_unix": started_wall,
            "finished_unix": time.time(),
            "duration_seconds": time.monotonic() - started_monotonic,
        }
    )
    _write_json_atomic(result_path, record)
    return record
