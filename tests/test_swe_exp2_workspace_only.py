from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from autobugfix.eval.benchmarks.subject_broker import (
    SWESubjectBroker,
    SWESubjectBrokerError,
)
from autobugfix.eval.benchmarks.swe_codex import SWECodexServer, SWEExecutionLedger
from autobugfix.eval.benchmarks.swe_models import SWESubjectTreatmentRuntime
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime
from tests.helpers import make_service_project


def treatment() -> SWESubjectTreatmentRuntime:
    return SWESubjectTreatmentRuntime(
        model="gpt-5.4-mini",
        reasoning_effort="low",
        service_tier=None,
        sdk_package="openai-codex",
        sdk_version="0.144.4",
        cli_package="openai-codex-cli-bin",
        cli_version="0.144.4",
        max_attempts=2,
        timeout_seconds=900,
    )


def test_workspace_only_codex_server_selects_in_process_sdk(tmp_path: Path) -> None:
    project, main = make_service_project(tmp_path)
    server = SWECodexServer(
        tmp_path / "codex.sock",
        "token",
        control_root=project,
        repo_id="toy_repo",
        main_checkout=main,
        worktree_root=tmp_path / "worktrees",
        artifact_root=tmp_path / "artifacts",
        hidden_paths=(),
        treatment=treatment(),
        ledger=SWEExecutionLedger(2),
        execution_mode="workspace_only",
    )

    assert server.execution_mode == "workspace_only"
    assert server.backend.in_process is True


def test_workspace_only_preflight_is_disjoint_and_explicit(tmp_path: Path) -> None:
    project, _ = make_service_project(tmp_path)
    disposable = tmp_path / "disposable"
    disposable.mkdir()
    workspace = disposable / "run"
    workspace.mkdir()
    artifact = tmp_path / "trusted-artifact"
    artifact.mkdir()
    readonly_authority = tmp_path / "readonly-authority"
    readonly_authority.mkdir(mode=0o555)
    runtime = cast(
        SWERuntime,
        SimpleNamespace(
            cache_root=project / ".autobugfix/cache",
            project_root=project,
        ),
    )
    broker = SWESubjectBroker(project, runtime, authority_root=artifact)

    receipt = broker._workspace_only_preflight(
        workspace_root=workspace,
        disposable_root=disposable,
        artifact_root=artifact / "run",
        authority_roots=(readonly_authority,),
        environment={"PATH": "/usr/bin"},
    )

    assert receipt["direct_sdk_in_process"] is True
    assert receipt["outer_bubblewrap"] is False
    assert receipt["credential_keys"] == []


def test_workspace_only_preflight_rejects_credentials_and_authority_overlap(
    tmp_path: Path,
) -> None:
    project, _ = make_service_project(tmp_path)
    disposable = tmp_path / "disposable"
    disposable.mkdir()
    workspace = disposable / "run"
    workspace.mkdir()
    artifact = tmp_path / "trusted-artifact"
    artifact.mkdir()
    readonly_authority = tmp_path / "readonly-authority"
    readonly_authority.mkdir(mode=0o555)
    runtime = cast(
        SWERuntime,
        SimpleNamespace(
            cache_root=project / ".autobugfix/cache",
            project_root=project,
        ),
    )
    broker = SWESubjectBroker(project, runtime, authority_root=artifact)

    with pytest.raises(SWESubjectBrokerError, match="credentials"):
        broker._workspace_only_preflight(
            workspace_root=workspace,
            disposable_root=disposable,
            artifact_root=artifact / "run",
            authority_roots=(readonly_authority,),
            environment={"OPENAI_API_KEY": "must-not-be-forwarded"},
        )

    with pytest.raises(SWESubjectBrokerError, match="overlaps trusted authority"):
        broker._workspace_only_preflight(
            workspace_root=workspace,
            disposable_root=disposable,
            artifact_root=artifact / "run",
            authority_roots=(workspace,),
            environment={"PATH": "/usr/bin"},
        )
