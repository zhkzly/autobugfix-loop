from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autobugfix.eval.benchmarks.subject_broker import (
    SWESubjectBroker,
    SWESubjectBrokerError,
)
from autobugfix.eval.benchmarks.swe_codex import (
    SWECodexServer,
    SWEExecutionLedger,
)
from autobugfix.eval.benchmarks.swe_materialize import SWEMaterializedRepository
from autobugfix.eval.benchmarks.swe_models import SWESubjectTreatmentRuntime
from autobugfix.eval.benchmarks.swe_verifier import (
    SWEDockerVisibleVerifier,
    SWEVerifierServer,
    VISIBLE_VERIFIER_COMMAND_ID,
    visible_command,
)
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime, SWERuntimeError
from autobugfix.models import VerifierResult
from autobugfix.operator.service import OperatorGovernanceService
from tests.helpers import FakeCodexBackend, make_service_project


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    return result.stdout.strip()


def make_repo(root: Path, name: str) -> tuple[Path, str, str]:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    git(repo, "config", "user.email", "broker@example.com")
    git(repo, "config", "user.name", "Subject Broker")
    (repo / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", "source.py")
    git(repo, "commit", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")


def broker(project: Path) -> SWESubjectBroker:
    runtime = cast(
        SWERuntime,
        SimpleNamespace(
            cache_root=project / ".autobugfix/cache",
            project_root=project,
        ),
    )
    return SWESubjectBroker(project, runtime)


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


def test_subject_broker_materializes_exact_clean_sha_and_rejects_drift(
    tmp_path: Path,
) -> None:
    project, head, _ = make_repo(tmp_path, "control")
    subject_broker = broker(project)
    checkout = subject_broker._subject_checkout(head, tmp_path / "artifacts")

    assert git(checkout, "rev-parse", "HEAD") == head
    assert git(checkout, "status", "--porcelain=v1", "--untracked-files=all") == ""

    (checkout / "forged.txt").write_text("forged\n", encoding="utf-8")
    with pytest.raises(SWESubjectBrokerError, match="identity drift"):
        subject_broker._subject_checkout(head, tmp_path / "unused-artifacts")


def test_subject_broker_builds_independent_target_remote_and_main(
    tmp_path: Path,
) -> None:
    project, _, _ = make_repo(tmp_path, "control")
    source, base, tree = make_repo(tmp_path, "source")
    subject_broker = broker(project)
    materialized = SWEMaterializedRepository(
        instance_id="owner__repo-1",
        repository="owner/repo",
        base_commit=base,
        source_path=str(source),
        source_tree=tree,
        source_digest="a" * 64,
        image="official/image",
        image_id="sha256:" + "b" * 64,
    )

    remote, main = subject_broker._prepare_target(
        materialized,
        tmp_path / "target",
        tmp_path / "target-artifacts",
    )

    assert git(main, "rev-parse", "HEAD") == base
    assert git(main, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert git(source, "rev-parse", "HEAD") == base
    assert git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == base


class FakeVerifier:
    command_id = VISIBLE_VERIFIER_COMMAND_ID

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def run(self, worktree: Path, artifact_dir: Path, timeout_seconds: int) -> VerifierResult:
        del artifact_dir, timeout_seconds
        self.calls.append(worktree)
        return VerifierResult(
            command=self.command_id,
            exit_code=0,
            stdout="passed",
            stderr="",
            started_at="2026-07-12T00:00:00Z",
            finished_at="2026-07-12T00:00:01Z",
            outcome="passed",
        )


def request(socket_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        stream = connection.makefile("rwb")
        stream.write(json.dumps(data).encode("utf-8") + b"\n")
        stream.flush()
        return json.loads(stream.readline())


def test_verifier_server_rejects_authority_mismatch_before_backend(
    tmp_path: Path,
) -> None:
    backend = FakeVerifier()
    socket_path = tmp_path / "verifier.sock"
    with SWEVerifierServer(socket_path, "trusted-token", cast(Any, backend)):
        denied = request(
            socket_path,
            {
                "token": "forged",
                "command_id": VISIBLE_VERIFIER_COMMAND_ID,
                "worktree": str(tmp_path),
                "timeout_seconds": 30,
            },
        )
        allowed = request(
            socket_path,
            {
                "token": "trusted-token",
                "command_id": VISIBLE_VERIFIER_COMMAND_ID,
                "worktree": str(tmp_path),
                "timeout_seconds": 30,
            },
        )

    assert "token is invalid" in denied["error"]
    assert allowed["result"]["outcome"] == "passed"
    assert backend.calls == [tmp_path]


def test_visible_verifier_profiles_are_static_and_language_specific() -> None:
    assert visible_command("py") == "python -m compileall -q ."
    assert visible_command("go") == "go test ./..."
    with pytest.raises(SWERuntimeError, match="unsupported"):
        visible_command("brainfuck")


def test_subject_skill_projection_rejects_symlink_exfiltration(tmp_path: Path) -> None:
    subject = tmp_path / "subject"
    control = tmp_path / "control"
    roles = subject / ".agents/role-skills"
    for relative in ("base", "execution/writer", "execution/evaluator"):
        root = roles / relative
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(f"# {relative}\n", encoding="utf-8")
    secret = tmp_path / "private.txt"
    secret.write_text("not role data\n", encoding="utf-8")
    (roles / "execution/writer/leak").symlink_to(secret)

    with pytest.raises(SWESubjectBrokerError, match="cannot contain symlinks"):
        SWESubjectBroker._copy_subject_skills(subject, control)


def test_execution_ledger_enforces_writer_verifier_evaluator_order() -> None:
    ledger = SWEExecutionLedger(2)
    empty_patch = hashlib.sha256(b"").hexdigest()
    writer = ledger.begin_codex("writer", empty_patch)
    ledger.finish_codex(
        "writer", writer, passed=True, patch_sha256=empty_patch
    )
    with pytest.raises(SWERuntimeError, match="Evaluator call is invalid"):
        ledger.begin_codex("evaluator", empty_patch)
    verifier = ledger.begin_verifier(empty_patch)
    ledger.finish_verifier(verifier, "passed", empty_patch)
    evaluator = ledger.begin_codex("evaluator", empty_patch)
    ledger.finish_codex(
        "evaluator", evaluator, passed=True, patch_sha256=empty_patch
    )

    snapshot = ledger.validate_terminal(empty_patch)
    assert snapshot["writer_calls"] == 1
    assert snapshot["verifier_calls"] == 1
    assert snapshot["evaluator_calls"] == 1
    assert snapshot["phase"] == "evaluator_completed"


def test_execution_ledger_rejects_patch_changes_outside_writer_transition() -> None:
    ledger = SWEExecutionLedger(2)
    empty_patch = hashlib.sha256(b"").hexdigest()
    writer_patch = hashlib.sha256(b"writer patch").hexdigest()
    writer = ledger.begin_codex("writer", empty_patch)
    ledger.finish_codex(
        "writer", writer, passed=True, patch_sha256=writer_patch
    )

    with pytest.raises(SWERuntimeError, match="outside the trusted Writer"):
        ledger.begin_verifier(hashlib.sha256(b"candidate edit").hexdigest())
    with pytest.raises(SWERuntimeError, match="no valid terminal"):
        ledger.validate_terminal(writer_patch)


def test_subject_broker_detects_target_git_control_mutation(tmp_path: Path) -> None:
    project, main = make_service_project(tmp_path)
    subject_broker = broker(project)
    before = subject_broker._git_control_identity(main)

    with (main / ".git/config").open("a", encoding="utf-8") as handle:
        handle.write("\n[core]\n\tfsmonitor = /tmp/untrusted-monitor\n")

    assert subject_broker._git_control_identity(main) != before


def test_subject_broker_freezes_failure_evidence_for_postprocess_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = make_service_project(tmp_path)
    subject_broker = broker(project)
    artifact_root = (
        subject_broker.trusted_root / "swe/test-failure-evidence"
    )

    def fail_after_raw_artifacts(**kwargs):
        root = Path(kwargs["artifact_root"])
        (root / "subject-process").mkdir(parents=True)
        (root / "subject-process/stdout.log").write_text(
            "raw process evidence\n", encoding="utf-8"
        )
        raise SWESubjectBrokerError("postprocess failed")

    monkeypatch.setattr(subject_broker, "_run_impl", fail_after_raw_artifacts)
    with pytest.raises(SWESubjectBrokerError, match="postprocess"):
        subject_broker.run(
            subject_sha="a" * 40,
            expected_subject_tree="b" * 40,
            visible_case=cast(Any, object()),
            instance=cast(Any, object()),
            materialized=cast(Any, object()),
            image_id="sha256:" + "c" * 64,
            artifact_root=artifact_root,
            protocol_digest="d" * 64,
            treatment=treatment(),
            subject_runtime=cast(Any, {}),
        )

    assert (artifact_root / "broker-failure.yaml").is_file()
    assert (
        artifact_root
        / "failed-execution-evidence/subject-process/stdout.log"
    ).read_text(encoding="utf-8") == "raw process evidence\n"


def test_codex_broker_rebuilds_trusted_role_request_and_rejects_extra_authority(
    tmp_path: Path,
) -> None:
    project, main = make_service_project(tmp_path)
    subject_broker = broker(project)
    control = tmp_path / "subject-control"
    control.mkdir()
    worktrees = tmp_path / "worktrees"
    worktree = worktrees / "task"
    worktrees.mkdir()
    # The fixture remote may retain the Git installation's default HEAD name.
    # Pin the local checkout to the pushed main ref before adding a worktree.
    subprocess.run(
        ["git", "-C", str(main), "checkout", "-B", "main", "origin/main"],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "--detach", str(worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    subject_broker._write_control_config(
        project,
        control,
        main,
        worktrees,
        "swe_target",
        treatment(),
    )
    for relative in (
        "base/autobugfix-runtime-base/SKILL.md",
        "execution/writer/autobugfix-writer/SKILL.md",
        "execution/evaluator/autobugfix-evaluator/SKILL.md",
    ):
        role_path = control / ".agents/role-skills" / relative
        role_path.parent.mkdir(parents=True)
        role_path.write_text(
            "Autobugfix trusted test role.\n", encoding="utf-8"
        )
    backend = FakeCodexBackend(edit=False)
    factory_calls: list[tuple[str, int, int]] = []

    def backend_factory(role: str, attempt: int, sequence: int):
        factory_calls.append((role, attempt, sequence))
        return backend

    ledger = SWEExecutionLedger(2)
    socket_path = tmp_path / "codex.sock"
    with SWECodexServer(
        socket_path,
        "trusted-token",
        control_root=control,
        repo_id="swe_target",
        main_checkout=main,
        worktree_root=worktrees,
        artifact_root=tmp_path / "codex-artifacts",
        hidden_paths=(tmp_path / "hidden",),
        treatment=treatment(),
        ledger=ledger,
        backend=cast(Any, backend),
        backend_factory=backend_factory,
    ):
        denied = request(
            socket_path,
            {
                "token": "trusted-token",
                "role": "writer",
                "prompt": "repair the visible issue",
                "cwd": str(worktree),
                "model": "attacker-model",
            },
        )
        allowed = request(
            socket_path,
            {
                "token": "trusted-token",
                "role": "writer",
                "prompt": "repair the visible issue",
                "cwd": str(worktree),
            },
        )

    assert "unauthorized fields" in denied["error"]
    assert allowed["result"]["text"] == "NO_CHANGE\n"
    assert len(backend.calls) == 1
    assert factory_calls == [("writer", 1, 1)]
    trusted = backend.calls[0]
    assert trusted.model == "gpt-5.4-mini"
    assert trusted.sandbox == "workspace-write"
    assert trusted.approval_mode == "auto_review"
    assert trusted.raw_log_path.is_relative_to(tmp_path / "codex-artifacts")


def test_subject_process_environment_excludes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("CODEX_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://secret-proxy")
    monkeypatch.setenv("LANG", "C.UTF-8")

    environment = SWESubjectBroker._subject_environment()

    assert environment["LANG"] == "C.UTF-8"
    assert "OPENAI_API_KEY" not in environment
    assert "CODEX_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="Bubblewrap is unavailable")
def test_subject_outer_sandbox_masks_git_credentials_network_and_main_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = make_service_project(tmp_path)
    subject_broker = broker(project)
    external_guard = tmp_path / "external-guard"
    external_guard.mkdir()
    guard_canary = external_guard / "sealed-case-id.txt"
    guard_canary.write_text("private-case\n", encoding="utf-8")
    subject_broker.trusted_root = external_guard.resolve()
    subject = tmp_path / "subject"
    control = tmp_path / "isolated-control"
    target = tmp_path / "isolated-target"
    capability = tmp_path / "capability"
    for path in (
        subject / "src",
        subject / ".git",
        control / ".autobugfix",
        control / ".agents",
        control / ".autobugfix-memory",
        target / "main/.git",
        target / "main/.git/worktrees",
        target / "remote.git",
        capability,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (subject / ".git/HEAD").write_text("secret Git metadata\n", encoding="utf-8")
    (target / "main/source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (target / "remote.git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    project_canary = project / "trusted-adapter-canary.txt"
    project_canary.write_text("trusted source\n", encoding="utf-8")
    config_path = control / ".autobugfix/config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    request_path = control / "subject-request.json"
    result_path = control / "subject-result.json"
    socket_path = capability / "probe.sock"
    request_path.write_text(
        json.dumps(
            {
                "subject_git_head": str(subject / ".git/HEAD"),
                "main_source": str(target / "main/source.py"),
                "main_git_probe": str(target / "main/.git/probe"),
                "main_worktrees_probe": str(target / "main/.git/worktrees/probe"),
                "remote_head": str(target / "remote.git/HEAD"),
                "socket": str(socket_path),
                "project_canary": str(project_canary),
                "guard_canary": str(guard_canary),
            }
        ),
        encoding="utf-8",
    )
    worker = control / "run_subject.py"
    worker.write_text(
        """
import argparse
import json
import os
import socket
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--result", required=True)
args = parser.parse_args()
data = json.loads(Path(args.request).read_text())

def writable(path):
    try:
        Path(path).write_text("changed\\n")
        return True
    except OSError:
        return False

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.connect(data["socket"])
    capability_reply = connection.recv(16).decode()
Path(args.result).write_text(json.dumps({
    "subject_git_visible": Path(data["subject_git_head"]).exists(),
    "main_writable": writable(data["main_source"]),
    "main_git_writable": writable(data["main_git_probe"]),
    "main_worktrees_writable": writable(data["main_worktrees_probe"]),
    "remote_writable": writable(data["remote_head"]),
    "openai_key_visible": "OPENAI_API_KEY" in os.environ,
    "codex_key_visible": "CODEX_API_KEY" in os.environ,
    "trusted_project_visible": Path(data["project_canary"]).exists(),
    "guard_visible": Path(data["guard_canary"]).exists(),
    "wsl_docker_visible": Path("/mnt/wsl/docker-desktop-bind-mounts").exists(),
    "capability_reply": capability_reply,
}))
""".lstrip(),
        encoding="utf-8",
    )
    capability_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    capability_server.bind(str(socket_path))
    capability_server.listen(1)

    def serve() -> None:
        connection, _ = capability_server.accept()
        with connection:
            connection.sendall(b"ok")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("CODEX_API_KEY", "secret")
    try:
        completed = subprocess.run(
            subject_broker._sandbox_argv(
                subject,
                control,
                target,
                request_path,
                result_path,
                capability,
            ),
            cwd=project,
            env=subject_broker._subject_environment(),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    finally:
        capability_server.close()
        thread.join(timeout=5)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result == {
        "subject_git_visible": False,
        "main_writable": False,
        "main_git_writable": False,
        "main_worktrees_writable": True,
        "remote_writable": False,
        "openai_key_visible": False,
        "codex_key_visible": False,
        "trusted_project_visible": False,
        "guard_visible": False,
        "wsl_docker_visible": False,
        "capability_reply": "ok",
    }


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="Bubblewrap is unavailable")
def test_subject_outer_sandbox_allows_only_verifier_worktree_bookkeeping(
    tmp_path: Path,
) -> None:
    project, _ = make_service_project(tmp_path)
    subject_broker = broker(project)
    subject = tmp_path / "subject"
    control = tmp_path / "isolated-control"
    target = tmp_path / "isolated-target"
    capability = tmp_path / "capability"
    main, _, _ = make_repo(target, "main")
    for path in (
        subject / "src",
        subject / ".git",
        control / ".autobugfix",
        control / ".agents",
        control / ".autobugfix-memory",
        target / "remote.git",
        capability,
        main / ".git/worktrees",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (control / ".autobugfix/config.yaml").write_text("{}\n", encoding="utf-8")
    request_path = control / "subject-request.json"
    result_path = control / "subject-result.json"
    verification_worktree = control / "verification-worktree"
    request_path.write_text(
        json.dumps(
            {
                "main": str(main),
                "destination": str(verification_worktree),
            }
        ),
        encoding="utf-8",
    )
    worker = control / "run_subject.py"
    worker.write_text(
        """
import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--result", required=True)
args = parser.parse_args()
data = json.loads(Path(args.request).read_text())
added = subprocess.run(
    ["git", "-C", data["main"], "worktree", "add", "--detach", data["destination"], "HEAD"],
    text=True,
    capture_output=True,
)
removed = subprocess.run(
    ["git", "-C", data["main"], "worktree", "remove", "--force", data["destination"]],
    text=True,
    capture_output=True,
) if added.returncode == 0 else None
try:
    (Path(data["main"]) / ".git/probe").write_text("forbidden" + chr(10))
    main_git_writable = True
except OSError:
    main_git_writable = False
Path(args.result).write_text(json.dumps({
    "added": added.returncode,
    "removed": None if removed is None else removed.returncode,
    "main_git_writable": main_git_writable,
}), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        subject_broker._sandbox_argv(
            subject,
            control,
            target,
            request_path,
            result_path,
            capability,
        ),
        cwd=project,
        env=subject_broker._subject_environment(),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "added": 0,
        "removed": 0,
        "main_git_writable": False,
    }
    assert not verification_worktree.exists()
    assert git(main, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_subject_broker_copies_only_active_reviewed_memory(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    active = snapshot / "active/user-preferences.md"
    approved = snapshot / "skills/approved/fix/SKILL.md"
    pending = snapshot / "proposals/pending/patch.md"
    for path, content in (
        (active, "accepted memory\n"),
        (approved, "# Accepted Skill\n"),
        (pending, "unreviewed proposal\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    control = tmp_path / "control"
    control.mkdir()

    copied = SWESubjectBroker._copy_study_memory(snapshot, control)

    assert (copied / "active/user-preferences.md").read_text() == "accepted memory\n"
    assert (copied / "skills/approved/fix/SKILL.md").is_file()
    assert not (copied / "proposals").exists()


def test_development_memory_binding_digest_distinguishes_snapshot_source(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    for directory in (
        fixture,
        fixture / "active",
        fixture / "skills",
        fixture / "skills/approved",
    ):
        directory.mkdir()
    fabricated = tmp_path / "fabricated"
    fabricated.mkdir()

    snapshot_digest = SWESubjectBroker.development_memory_binding_digest(
        fixture, "unused"
    )

    assert snapshot_digest == OperatorGovernanceService.exp2_empty_memory_digest()
    assert snapshot_digest != SWESubjectBroker.development_memory_binding_digest(
        fabricated, "unused"
    )
    assert (
        SWESubjectBroker.development_memory_binding_digest(
            None, "copy-digest"
        )
        == "copy-digest"
    )


def _docker_evidence(root: Path, name: str, *, passed: bool, timed_out: bool = False):
    step = root / name
    step.mkdir(parents=True, exist_ok=True)
    stdout = step / "stdout.log"
    stderr = step / "stderr.log"
    stdout.write_text("", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    return SimpleNamespace(
        passed=passed,
        timed_out=timed_out,
        exit_code=0 if passed else None if timed_out else 1,
        stdout_path=str(stdout),
        stderr_path=str(stderr),
    )


def _visible_verifier(tmp_path: Path) -> SWEDockerVisibleVerifier:
    verifier = object.__new__(SWEDockerVisibleVerifier)
    verifier.runtime = SimpleNamespace(
        project_root=tmp_path,
        config=SimpleNamespace(
            platform="linux/amd64",
            memory_limit="1g",
            cpu_limit=1,
            pids_limit=128,
        ),
    )
    verifier.instance = SimpleNamespace(language="python")
    verifier.repo = SimpleNamespace()
    verifier.artifact_root = tmp_path / "verifier-artifacts"
    verifier.image_id = "sha256:" + "1" * 64
    verifier._sequence = 0
    verifier._validate_worktree = lambda path: path.resolve()
    return verifier


def test_visible_verifier_timeout_is_harness_error_not_writer_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _visible_verifier(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_verifier.diff_for_task",
        lambda *args: "",
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_step(argv, root, name, timeout_seconds):
        del argv, timeout_seconds
        evidence = _docker_evidence(
            root,
            name,
            passed=name != "visible-command",
            timed_out=name == "visible-command",
        )
        if name == "image-inspect":
            Path(evidence.stdout_path).write_text(verifier.image_id + "\n", encoding="utf-8")
        return evidence

    verifier._docker_step = fake_step
    result = verifier.run(worktree, tmp_path / "ignored", 30)

    assert result.outcome == "harness_error"
    assert result.exit_code == 124


def test_visible_verifier_cleanup_failure_invalidates_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _visible_verifier(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_verifier.diff_for_task",
        lambda *args: "",
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_step(argv, root, name, timeout_seconds):
        del argv, timeout_seconds
        evidence = _docker_evidence(root, name, passed=name != "container-remove")
        if name == "image-inspect":
            Path(evidence.stdout_path).write_text(verifier.image_id + "\n", encoding="utf-8")
        return evidence

    verifier._docker_step = fake_step
    with pytest.raises(SWERuntimeError, match="run is not isolated"):
        verifier.run(worktree, tmp_path / "ignored", 30)
