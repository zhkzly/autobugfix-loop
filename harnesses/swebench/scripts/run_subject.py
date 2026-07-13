from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import tempfile
from pathlib import Path
from typing import Any

from autobugfix.eval.benchmarks.models import digest_file
from autobugfix.models import CodexRequest, CodexResult, VerifierResult
from autobugfix.service import AutobugfixService
from autobugfix.worktree import diff_for_task


def _rpc(socket_path: str, request: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_seconds)
        connection.connect(socket_path)
        stream = connection.makefile("rwb")
        stream.write(json.dumps(request, sort_keys=True).encode("utf-8") + b"\n")
        stream.flush()
        response_line = stream.readline()
    if not response_line:
        raise RuntimeError("trusted SWE capability broker returned no response")
    response = json.loads(response_line)
    if not isinstance(response, dict):
        raise RuntimeError("trusted SWE capability broker response is invalid")
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("trusted SWE capability broker omitted its result")
    return result


class CodexProxy:
    def __init__(self, socket_path: str, token: str, timeout_seconds: int):
        self.socket_path = socket_path
        self.token = token
        self.timeout_seconds = timeout_seconds

    def run(self, request: CodexRequest) -> CodexResult:
        result = _rpc(
            self.socket_path,
            {
                "token": self.token,
                "role": request.role,
                "prompt": request.prompt,
                "cwd": str(request.cwd.resolve()),
            },
            self.timeout_seconds + 60,
        )
        return CodexResult(
            text=str(result["text"]),
            exit_code=int(result.get("exit_code", 0)),
        )


class VerifierProxy:
    def __init__(self, socket_address: str, token: str, command_id: str):
        self.socket_address = socket_address
        self.token = token
        self.command_id = command_id

    def run(
        self,
        worktree: Path,
        artifact_dir: Path,
        timeout_seconds: int,
    ) -> VerifierResult:
        request = {
            "token": self.token,
            "command_id": self.command_id,
            "worktree": str(worktree.resolve()),
            "timeout_seconds": timeout_seconds,
        }
        del artifact_dir
        result = _rpc(self.socket_address, request, timeout_seconds + 60)
        return VerifierResult(
            command=str(result["command"]),
            exit_code=int(result["exit_code"]),
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            started_at=str(result["started_at"]),
            finished_at=str(result["finished_at"]),
            outcome=str(result["outcome"]),  # type: ignore[arg-type]
        )


def _write_json_once(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def run(request: dict[str, Any]) -> dict[str, Any]:
    control_root = Path(str(request["control_root"])).resolve()
    repo_id = str(request["repo_id"])
    max_attempts = int(request["max_attempts"])
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    verifier = VerifierProxy(
        str(request["verifier_socket"]),
        str(request["verifier_token"]),
        str(request["verifier_command_id"]),
    )
    codex = CodexProxy(
        str(request["codex_socket"]),
        str(request["codex_token"]),
        int(request["codex_timeout_seconds"]),
    )
    service = AutobugfixService(
        control_root,
        backend=codex,
        verifier_backend=verifier,
    )
    task = service.create_task(
        repo_id,
        f"SWE eval {request['case_token']}",
        str(request["problem_statement"]),
        metadata={
            "origin": "eval",
            "memory_eligible": False,
            "eval_case_token": str(request["case_token"]),
            "eval_adapter": str(request["adapter"]),
            "experiment_role": str(request["experiment_role"]),
        },
    )
    for context in request.get("context") or ():
        if not isinstance(context, dict):
            raise ValueError("subject context entries must be mappings")
        service.add_context(
            task.task_id,
            str(context["kind"]),
            str(context["content"]),
        )

    while True:
        service.run_task(task.task_id)
        current = service.store.load(task.task_id)
        if current.state != "writer_rework_required" or current.iterations >= max_attempts:
            break
        task_dir = service.store.find_task_dir(task.task_id)
        verifier_result = task_dir / "artifacts/test-result.md"
        feedback = [
            f"Attempt {current.iterations} did not satisfy the visible verifier.",
            f"State reason: {current.block_reason}",
        ]
        if verifier_result.is_file():
            feedback.extend(("", verifier_result.read_text(encoding="utf-8")))
        service.add_feedback(task.task_id, "visible-verifier-retry", "\n".join(feedback))

    current = service.store.load(task.task_id)
    if not current.worktree_path:
        raise RuntimeError("Execution task has no worktree")
    worktree = Path(current.worktree_path).resolve()
    repo = service.config.repo(repo_id)
    base_ref = str(current.metadata.get("base_commit") or f"{repo.remote}/{repo.main_branch}")
    patch = diff_for_task(repo, worktree, base_ref)
    task_dir = service.store.find_task_dir(task.task_id)
    events_path = task_dir / "events.jsonl"
    task_path = task_dir / "task.yaml"
    return {
        "schema": "autobugfix-swe-subject-result-v1",
        "case_token": str(request["case_token"]),
        "task_id": task.task_id,
        "execution_state": current.state,
        "iterations": current.iterations,
        "worktree": str(worktree),
        "base_commit": str(current.metadata["base_commit"]),
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "events_path": str(events_path.resolve()),
        "events_sha256": digest_file(events_path),
        "task_path": str(task_path.resolve()),
        "task_sha256": digest_file(task_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("subject request must be a JSON mapping")
    _write_json_once(Path(args.result), run(request))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
