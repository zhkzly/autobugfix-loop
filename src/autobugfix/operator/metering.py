from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from autobugfix.codex_backend import CodexBackend
from autobugfix.models import CodexRequest, CodexResult
from autobugfix.operator.models import UsageEntryRecord


class MeteringError(RuntimeError):
    pass


class UsageAuthority(Protocol):
    def reserve_usage(
        self,
        grant_id: str,
        *,
        call_key: str,
        execution_id: str,
        role: str,
        model: str,
        case_id: str | None = None,
        attempt: int = 0,
        revision: int = 0,
    ) -> UsageEntryRecord: ...

    def finalize_usage(
        self,
        usage_id: str,
        *,
        status: str,
        raw_log_path: Path | None = None,
        stderr_log_path: Path | None = None,
        result_id: str | None = None,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_seconds: float | None = None,
        error: str | None = None,
    ) -> UsageEntryRecord: ...


@dataclass(slots=True, frozen=True)
class StudyCallContext:
    grant_id: str
    call_key: str
    execution_id: str
    case_id: str | None = None
    attempt: int = 0
    revision: int = 0


class CallbackCodexBackend(CodexBackend):
    def __init__(self, callback: Callable[[CodexRequest], CodexResult]) -> None:
        self.callback = callback

    def run(self, request: CodexRequest) -> CodexResult:
        return self.callback(request)


def _find_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in names:
                return item
        for item in value.values():
            found = _find_value(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_value(item, names)
            if found is not None:
                return found
    return None


def _optional_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


class MeteredCodexBackend(CodexBackend):
    """Reserve trusted study budget before delegating to a real Codex backend."""

    def __init__(
        self,
        backend: CodexBackend,
        authority: UsageAuthority,
        context: StudyCallContext,
    ) -> None:
        self.backend = backend
        self.authority = authority
        self.context = context

    def run(self, request: CodexRequest) -> CodexResult:
        if not request.model:
            raise MeteringError("metered study calls require an explicit model")
        usage = self.authority.reserve_usage(
            self.context.grant_id,
            call_key=self.context.call_key,
            execution_id=self.context.execution_id,
            role=request.role,
            model=request.model,
            case_id=self.context.case_id,
            attempt=self.context.attempt,
            revision=self.context.revision,
        )
        started = time.monotonic()
        try:
            result = self.backend.run(request)
        except BaseException as exc:
            duration = time.monotonic() - started
            try:
                self.authority.finalize_usage(
                    usage.usage_id,
                    status="INDETERMINATE",
                    raw_log_path=request.raw_log_path,
                    stderr_log_path=request.stderr_log_path,
                    duration_seconds=duration,
                    error=repr(exc),
                )
            except Exception as finalize_error:
                raise MeteringError(
                    "Codex call failed and trusted usage finalization also failed"
                ) from finalize_error
            raise
        duration = time.monotonic() - started
        response = result.raw.get("response") if isinstance(result.raw, dict) else None
        input_tokens = _optional_nonnegative_int(
            _find_value(response, {"input_tokens", "inputTokens"})
        )
        cached_input_tokens = _optional_nonnegative_int(
            _find_value(response, {"cached_input_tokens", "cachedInputTokens"})
        )
        output_tokens = _optional_nonnegative_int(
            _find_value(response, {"output_tokens", "outputTokens"})
        )
        result_id = _find_value(response, {"thread_id", "threadId", "response_id", "id"})
        self.authority.finalize_usage(
            usage.usage_id,
            status="COMPLETED",
            raw_log_path=request.raw_log_path,
            stderr_log_path=request.stderr_log_path,
            result_id=str(result_id) if result_id is not None else None,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration,
        )
        return result
