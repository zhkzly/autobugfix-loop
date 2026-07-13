from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from openai_codex.models import Notification, UnknownNotification

from raw_codex_sdk_baseline.models import (
    CaseBundle,
    ContractError,
    package_source_digest,
    record_with_digest,
)
from raw_codex_sdk_baseline.prompt import (
    DEVELOPER_INSTRUCTIONS,
    PROMPT_TEMPLATE_DIGEST,
    render_prompt,
)
from raw_codex_sdk_baseline import runner


def case_record() -> dict[str, object]:
    return record_with_digest(
        {
            "schema": "raw-codex-sdk-case-v1",
            "case_id": "case-1",
            "benchmark": "defects4j",
            "dataset_revision": "v3.0.1",
            "base_commit": "a" * 40,
            "problem_statement": "Parsing the empty value raises an error.",
            "expected_behavior": "The parser accepts the empty value.",
            "visible_evidence": ["ExampleTest::testEmpty fails"],
            "attachments": [],
        }
    )


def test_case_bundle_verifies_digest(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case_record()), encoding="utf-8")
    case = CaseBundle.from_json(path)
    assert case.case_id == "case-1"
    assert case.visible_evidence == ("ExampleTest::testEmpty fails",)

    changed = case_record()
    changed["problem_statement"] = "tampered"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ContractError, match="digest mismatch"):
        CaseBundle.from_json(path)


def test_prompt_is_generic_and_deterministic() -> None:
    case = CaseBundle.from_dict(case_record())
    rendered = render_prompt(case)
    assert case.problem_statement in rendered
    assert case.visible_evidence[0] in rendered
    assert len(PROMPT_TEMPLATE_DIGEST) == 64
    combined = DEVELOPER_INSTRUCTIONS + rendered
    for forbidden in (
        "gold patch",
        "fixed revision",
        "AutobugfixService",
        "memory proposal",
    ):
        assert forbidden not in combined
    assert len(package_source_digest()) == 64


def test_real_sdk_notification_dataclass_serializes_structurally() -> None:
    event = Notification(
        method="turn/completed",
        payload=UnknownNotification(params={"turn_id": "turn-1"}),
    )
    encoded = runner._jsonable(event)
    assert encoded == {
        "method": "turn/completed",
        "payload": {"params": {"turn_id": "turn-1"}},
    }
    assert runner._event_fields(encoded)[0] == "turn/completed"


def test_event_fields_unwraps_real_thread_item_shape() -> None:
    encoded = {
        "method": "item/completed",
        "payload": {
            "item": {
                "root": {
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "fixed",
                }
            }
        },
    }
    assert runner._event_fields(encoded)[2] == "fixed"


def test_standalone_source_has_no_autobugfix_import() -> None:
    source_root = Path(__file__).parents[1] / "src"
    imports = []
    for path in source_root.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import autobugfix") or stripped.startswith(
                "from autobugfix"
            ):
                imports.append(f"{path}:{line}")
    assert imports == []


def test_runner_uses_one_direct_thread_and_streams_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(worktree)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.name", "Test"],
        check=True,
    )
    (worktree / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "base"],
        check=True,
        capture_output=True,
        text=True,
    )
    base = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    record = case_record()
    record["base_commit"] = base
    unsigned = {key: value for key, value in record.items() if key != "record_digest"}
    record = record_with_digest(unsigned)
    case = CaseBundle.from_dict(record)
    calls = {"threads": 0, "turns": 0}

    class Event:
        def __init__(self, value):
            self.value = value

        def model_dump(self, by_alias=False):
            del by_alias
            return self.value

    class Turn:
        id = "turn-1"

        def stream(self):
            calls["turns"] += 1
            (worktree / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
            return iter(
                (
                    Event(
                        {
                            "method": "item/completed",
                            "payload": {
                                "item": {
                                    "type": "agentMessage",
                                    "phase": "finalAnswer",
                                    "text": "fixed",
                                }
                            },
                        }
                    ),
                    Event(
                        {
                            "method": "thread/tokenUsage/updated",
                            "payload": {"tokenUsage": {"total_tokens": 42}},
                        }
                    ),
                    Event(
                        {
                            "method": "turn/completed",
                            "payload": {"turn": {"status": "completed"}},
                        }
                    ),
                )
            )

    class Thread:
        id = "thread-1"

        def turn(self, prompt, **kwargs):
            assert case.problem_statement in prompt
            assert kwargs["model"] == "gpt-5.4-mini"
            assert kwargs["approval_mode"] is runner.ApprovalMode.deny_all
            assert kwargs["sandbox"] is runner.Sandbox.workspace_write
            return Turn()

    class Codex:
        def __init__(self, config):
            self.config = config

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def thread_start(self, **kwargs):
            calls["threads"] += 1
            assert kwargs["model"] == "gpt-5.4-mini"
            assert kwargs["approval_mode"] is runner.ApprovalMode.deny_all
            assert kwargs["sandbox"] is runner.Sandbox.workspace_write
            return Thread()

    monkeypatch.setattr(runner, "Codex", Codex)
    monkeypatch.setattr(runner, "CodexConfig", lambda **kwargs: kwargs)
    result = runner.run_case(
        case,
        worktree,
        tmp_path / "artifacts",
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        service_tier=None,
    )
    assert calls == {"threads": 1, "turns": 1}
    assert result["status"] == "completed"
    assert result["final_response"] == "fixed"
    assert result["usage"] == {"total_tokens": 42}
    assert result["approval_mode"] == "deny_all"
    assert result["sandbox"] == "workspace-write"
    assert result["network_access"] is False
    assert (tmp_path / "artifacts/request.json").is_file()
    assert len(result["request_sha256"]) == 64
    assert (tmp_path / "artifacts/events.jsonl").read_text().count("\n") == 3
