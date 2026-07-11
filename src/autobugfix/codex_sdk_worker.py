from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from autobugfix.codex_sdk import CodexSDKBackend
from autobugfix.models import CodexRequest


def build_backend() -> CodexSDKBackend:
    return CodexSDKBackend()


def _request(data: dict[str, Any]) -> CodexRequest:
    return CodexRequest(
        role=str(data["role"]),
        prompt=str(data["prompt"]),
        cwd=Path(data["cwd"]),
        sandbox=str(data["sandbox"]),
        model=data.get("model"),
        timeout_seconds=data.get("timeout_seconds"),
        developer_instructions=str(data["developer_instructions"]),
        raw_log_path=Path(data["raw_log_path"]),
        stderr_log_path=Path(data["stderr_log_path"]),
        approval_mode=data.get("approval_mode"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Codex Python SDK request in an isolated host process.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    request_data = json.loads(Path(args.request).read_text(encoding="utf-8"))
    result = build_backend().run(_request(request_data))
    Path(args.result).write_text(
        json.dumps({"text": result.text, "raw": result.raw, "exit_code": result.exit_code}, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
