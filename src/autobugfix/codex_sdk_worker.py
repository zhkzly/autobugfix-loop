from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

from autobugfix.codex_sdk import CodexSDKBackend, write_private_text
from autobugfix.models import CodexRequest


def build_backend(module_name: str | None = None) -> CodexSDKBackend:
    return CodexSDKBackend(module_name, in_process=True)


def _request(data: dict[str, Any]) -> CodexRequest:
    return CodexRequest(
        role=str(data["role"]),
        prompt=str(data["prompt"]),
        cwd=Path(data["cwd"]),
        control_root=Path(data["control_root"]),
        sandbox=str(data["sandbox"]),
        model=data.get("model"),
        timeout_seconds=data.get("timeout_seconds"),
        developer_instructions=str(data["developer_instructions"]),
        raw_log_path=Path(data["raw_log_path"]),
        stderr_log_path=Path(data["stderr_log_path"]),
        approval_mode=data.get("approval_mode"),
        hidden_paths=tuple(Path(item) for item in data.get("hidden_paths") or ()),
        readable_paths=tuple(Path(item) for item in data.get("readable_paths") or ()),
        writable_paths=tuple(Path(item) for item in data.get("writable_paths") or ()),
        require_process_isolation=bool(data.get("require_process_isolation", True)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Codex Python SDK request in an isolated host process.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--module-name")
    args = parser.parse_args(argv)
    request_data = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result = build_backend(args.module_name).run(_request(request_data))
    result_path = Path(args.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_name(
        f".{result_path.name}.{uuid.uuid4().hex}.tmp"
    )
    write_private_text(
        temporary,
        json.dumps(
            {"text": result.text, "raw": result.raw, "exit_code": result.exit_code},
            sort_keys=True,
        ),
        exclusive=True,
    )
    temporary.replace(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
