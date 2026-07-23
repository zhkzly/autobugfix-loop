from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autobugfix.eval.benchmarks.swe_materialize import SWEImageMaterializer
from autobugfix.eval.benchmarks.models import CommandEvidence, digest_file
from autobugfix.eval.benchmarks.swe_models import SWEInstance, SWESubmission
from autobugfix.eval.benchmarks.swe_official import SWEOfficialRunner
from autobugfix.eval.benchmarks.swe_runtime import SWEDatasetSnapshot
from autobugfix.eval.benchmarks.swe_runtime import SWERuntimeError


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    return result.stdout.strip()


def make_image_repository(root: Path) -> tuple[Path, str, str]:
    source = root / "image-testbed"
    source.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(source)],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark Builder")
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "module.py")
    git(source, "commit", "-m", "buggy dataset base")
    base = git(source, "rev-parse", "HEAD")

    (source / ".swebench_setup").write_text("installed\n", encoding="utf-8")
    git(source, "add", ".swebench_setup")
    git(source, "commit", "-m", "official image setup")
    return source, base, git(source, "rev-parse", "HEAD")


def instance(base_commit: str) -> SWEInstance:
    return SWEInstance(
        adapter="swebench_verified",
        instance_id="owner__repo-1",
        repository="owner/repo",
        base_commit=base_commit,
        language="python",
        problem_statement="Repair the bug.",
        hints_text="",
        created_at="2026-07-12T00:00:00Z",
        docker_image="sweb.eval.x86_64.owner__repo-1:latest",
        gold_patch="diff --git a/module.py b/module.py\n",
        test_patch="diff --git a/test_module.py b/test_module.py\n",
        fail_to_pass=("test_module.py::test_value",),
        pass_to_pass=(),
        official={"version": "1"},
    )


def materializer(tmp_path: Path) -> SWEImageMaterializer:
    runtime = SimpleNamespace(
        project_root=tmp_path,
        benchmark_config=SimpleNamespace(command_timeout_seconds=30),
    )
    runner = cast(SWEOfficialRunner, SimpleNamespace(runtime=runtime))
    return SWEImageMaterializer(runner)


def test_materializer_fails_closed_when_container_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = SimpleNamespace(
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
        config=SimpleNamespace(platform="linux"),
        benchmark_config=SimpleNamespace(command_timeout_seconds=30),
        command_env=lambda: {},
    )
    runner = cast(
        SWEOfficialRunner,
        SimpleNamespace(
            runtime=runtime,
            image_id=lambda *args, **kwargs: "sha256:" + "1" * 64,
        ),
    )
    subject = SWEImageMaterializer(runner)
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_materialize.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )

    def fake_run(*args, name: str, **kwargs):
        del args, kwargs
        return SimpleNamespace(passed=name != "docker-remove-materializer")

    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_materialize.run_command", fake_run
    )
    monkeypatch.setattr(
        subject,
        "_repo_root",
        lambda root: (_ for _ in ()).throw(SWERuntimeError("copy unavailable")),
    )

    with pytest.raises(SWERuntimeError, match="failed to remove"):
        subject.materialize(instance("a" * 40), tmp_path / "artifacts")


class OfficialRuntime:
    def __init__(self, root: Path) -> None:
        self.project_root = root
        self.config = SimpleNamespace(
            harness_project=root / "harness",
            verified_namespace="official",
            verified_build_network_mode="default",
            scorer_timeout_seconds=30,
        )
        self.benchmark_config = SimpleNamespace(command_timeout_seconds=30)
        self.live_checkout = root / "live"
        self.official_network_access: list[bool] = []
        self.snapshot_path = root / "verified.jsonl"
        self.snapshot_path.write_text("{}\n", encoding="utf-8")

    def read_dataset_snapshot(self, adapter: str) -> SWEDatasetSnapshot:
        return SWEDatasetSnapshot(
            adapter=adapter,
            dataset="verified",
            revision="a" * 40,
            split="test",
            path=str(self.snapshot_path),
            sha256=digest_file(self.snapshot_path),
            row_count=1,
        )

    def command_env(self, writable_state_root: Path | None = None) -> dict[str, str]:
        if writable_state_root is not None:
            assert writable_state_root.is_dir()
        return {}

    def live_command_env(
        self,
        writable_state_root: Path | None = None,
    ) -> dict[str, str]:
        return self.command_env(writable_state_root)

    def isolated_official_argv(
        self,
        argv: list[str],
        *,
        cwd: Path,
        writable_roots: tuple[Path, ...],
        readable_roots: tuple[Path, ...] = (),
        allow_network: bool = False,
    ) -> list[str]:
        assert cwd in writable_roots
        for path in readable_roots:
            assert path.is_file()
        self.official_network_access.append(allow_network)
        return argv


def submission(patch: str = "") -> SWESubmission:
    return SWESubmission(
        case_token="visible-1",
        subject_sha="a" * 40,
        subject_tree="b" * 40,
        base_commit="c" * 40,
        patch=patch,
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        events_sha256="d" * 64,
        task_sha256="e" * 64,
        subject_request_digest="1" * 64,
        visible_case_digest="2" * 64,
        source_snapshot_digest="3" * 64,
        config_digest="4" * 64,
        skills_digest="5" * 64,
        execution_ledger_digest="6" * 64,
        evidence_manifest_digest="7" * 64,
        frozen_at="2026-07-12T00:00:00Z",
    )


def command_evidence(artifact_dir: Path, argv: list[str]) -> CommandEvidence:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout = artifact_dir / "stdout.log"
    stderr = artifact_dir / "stderr.log"
    stdout.write_text("official scorer completed\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    return CommandEvidence(
        name="official-swe-score",
        argv=tuple(argv),
        cwd=str(artifact_dir.parent),
        started_at="2026-07-12T00:00:00Z",
        finished_at="2026-07-12T00:00:01Z",
        duration_seconds=1.0,
        exit_code=0,
        timed_out=False,
        stdout_path=str(stdout),
        stderr_path=str(stderr),
        stdout_sha256=digest_file(stdout),
        stderr_sha256=digest_file(stderr),
        environment_digest="f" * 64,
    )


def test_verified_command_binds_explicit_build_network_mode(tmp_path: Path) -> None:
    runtime = OfficialRuntime(tmp_path)
    runtime.config.verified_build_network_mode = "host"
    runner = SWEOfficialRunner(cast(Any, runtime), "swebench_verified")

    argv = runner._verified_command(
        instance("c" * 40),
        "gold",
        "network-mode",
        tmp_path / "official",
    )

    assert str(runtime.config.harness_project / "scripts/run_official.py") in argv
    assert argv[argv.index("--build-network-mode") + 1] == "host"
    assert argv[argv.index("--module") + 1] == "swebench.harness.run_evaluation"


def test_official_bridge_pins_host_network_mode() -> None:
    bridge_path = (
        Path(__file__).parents[1]
        / "harnesses/swebench/scripts/run_official.py"
    )
    spec = importlib.util.spec_from_file_location("test_official_bridge", bridge_path)
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    calls: list[dict[str, Any]] = []

    class FakeAPIClient:
        def build(self, *args: Any, **kwargs: Any) -> str:
            del args
            calls.append(kwargs)
            return "built"

    bridge.install_build_network_mode(FakeAPIClient, "host")

    assert FakeAPIClient().build(path="context") == "built"
    assert calls == [{"path": "context", "network_mode": "host"}]
    with pytest.raises(RuntimeError, match="conflicting"):
        FakeAPIClient().build(path="context", network_mode="none")


def test_official_bridge_dispatches_only_the_pinned_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_path = (
        Path(__file__).parents[1]
        / "harnesses/swebench/scripts/run_official.py"
    )
    spec = importlib.util.spec_from_file_location("test_official_bridge_main", bridge_path)
    assert spec is not None and spec.loader is not None
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        bridge.runpy,
        "run_module",
        lambda module, *, run_name: calls.append((module, run_name)),
    )
    monkeypatch.setattr(bridge.sys, "argv", ["pytest"])

    assert bridge.main(
        [
            "--build-network-mode",
            "default",
            "--module",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            "verified.jsonl",
            "--instance_ids",
            "sympy__sympy-12481",
        ]
    ) == 0

    assert calls == [("swebench.harness.run_evaluation", "__main__")]
    assert bridge.sys.argv == [
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "verified.jsonl",
        "--instance_ids",
        "sympy__sympy-12481",
    ]


def test_materializer_fetches_sanitized_dataset_base_below_synthetic_image_head(
    tmp_path: Path,
) -> None:
    source, base, synthetic_head = make_image_repository(tmp_path)
    destination = tmp_path / "snapshot"
    artifacts = tmp_path / "artifacts"

    materializer(tmp_path)._clone_base_snapshot(
        instance(base), source, destination, artifacts
    )

    assert git(source, "rev-parse", "HEAD") == synthetic_head
    assert git(destination, "rev-parse", "HEAD") == base
    assert git(destination, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not (destination / ".swebench_setup").exists()
    assert git(destination, "for-each-ref", "--format=%(refname)") == ""
    assert git(destination, "remote") == ""
    assert git(destination, "rev-list", "--count", "HEAD") == "1"
    assert not (destination / ".git/FETCH_HEAD").exists()
    assert (artifacts / "git-fetch-base/stdout.log").is_file()
    assert (artifacts / "git-checkout-base/stderr.log").is_file()


def test_materializer_rejects_base_absent_from_image_repository(
    tmp_path: Path,
) -> None:
    source, _, _ = make_image_repository(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(foreign)],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    git(foreign, "config", "user.email", "benchmark@example.com")
    git(foreign, "config", "user.name", "Benchmark Builder")
    (foreign / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    git(foreign, "add", "foreign.txt")
    git(foreign, "commit", "-m", "foreign")
    foreign_commit = git(foreign, "rev-parse", "HEAD")

    with pytest.raises(SWERuntimeError, match="base commit is absent"):
        materializer(tmp_path)._clone_base_snapshot(
            instance(foreign_commit),
            source,
            tmp_path / "snapshot",
            tmp_path / "artifacts",
        )


def test_materializer_ignores_dirty_image_worktree_and_fetches_exact_base(
    tmp_path: Path,
) -> None:
    source, base, synthetic_head = make_image_repository(tmp_path)
    (source / "module.py").write_text("VALUE = 999\n", encoding="utf-8")
    (source / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    source_status = git(
        source,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    destination = tmp_path / "snapshot"

    materializer(tmp_path)._clone_base_snapshot(
        instance(base),
        source,
        destination,
        tmp_path / "artifacts",
    )

    assert git(source, "rev-parse", "HEAD") == synthetic_head
    assert (
        git(source, "status", "--porcelain=v1", "--untracked-files=all")
        == source_status
    )
    assert (source / "module.py").read_text(encoding="utf-8") == "VALUE = 999\n"
    assert (source / "untracked.txt").read_text(encoding="utf-8") == "dirty\n"
    assert git(destination, "rev-parse", "HEAD") == base
    assert git(destination, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (destination / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (destination / ".swebench_setup").exists()
    assert not (destination / "untracked.txt").exists()


def test_verified_official_empty_patch_is_valid_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SWEOfficialRunner(cast(Any, OfficialRuntime(tmp_path)), "swebench_verified")
    monkeypatch.setattr(runner, "image_id", lambda *args, **kwargs: "sha256:" + "1" * 64)

    def fake_run(argv, *, artifact_dir, **kwargs):
        report_root = Path(argv[argv.index("--report_dir") + 1])
        run_id = argv[argv.index("--run_id") + 1]
        aggregate = {
            "completed_ids": [],
            "empty_patch_ids": ["owner__repo-1"],
            "error_ids": [],
        }
        (report_root / f"autobugfix-subject.{run_id}.json").write_text(
            json.dumps(aggregate), encoding="utf-8"
        )
        return command_evidence(artifact_dir, list(argv))

    monkeypatch.setattr("autobugfix.eval.benchmarks.swe_official.run_command", fake_run)
    result = runner.score(
        instance("c" * 40),
        tmp_path / "score",
        run_id="empty-run",
        submission=submission(),
    )

    assert result.resolved is False
    assert result.harness_error == ""
    assert runner.runtime.official_network_access == [True]
    assert result.report_path == "missing"


def test_verified_official_rejects_unclassified_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SWEOfficialRunner(cast(Any, OfficialRuntime(tmp_path)), "swebench_verified")
    monkeypatch.setattr(runner, "image_id", lambda *args, **kwargs: "sha256:" + "2" * 64)

    def fake_run(argv, *, artifact_dir, **kwargs):
        report_root = Path(argv[argv.index("--report_dir") + 1])
        run_id = argv[argv.index("--run_id") + 1]
        (report_root / f"autobugfix-subject.{run_id}.json").write_text(
            json.dumps(
                {"completed_ids": [], "empty_patch_ids": [], "error_ids": []}
            ),
            encoding="utf-8",
        )
        return command_evidence(artifact_dir, list(argv))

    monkeypatch.setattr("autobugfix.eval.benchmarks.swe_official.run_command", fake_run)
    result = runner.score(
        instance("c" * 40),
        tmp_path / "score",
        run_id="unclassified-run",
        submission=submission("diff --git a/a b/a\n"),
    )

    assert "did not classify" in result.harness_error


def test_verified_patch_rejection_is_valid_unresolved_not_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SWEOfficialRunner(cast(Any, OfficialRuntime(tmp_path)), "swebench_verified")
    monkeypatch.setattr(runner, "image_id", lambda *args, **kwargs: "sha256:" + "2" * 64)

    def fake_run(argv, *, artifact_dir, **kwargs):
        report_root = Path(argv[argv.index("--report_dir") + 1])
        run_id = argv[argv.index("--run_id") + 1]
        log_root = (
            report_root
            / "logs/run_evaluation"
            / run_id
            / "autobugfix-subject"
            / "owner__repo-1"
        )
        log_root.mkdir(parents=True)
        (log_root / "run_instance.log").write_text(
            ">>>>> Patch Apply Failed\n", encoding="utf-8"
        )
        (report_root / f"autobugfix-subject.{run_id}.json").write_text(
            json.dumps(
                {
                    "completed_ids": [],
                    "empty_patch_ids": [],
                    "error_ids": ["owner__repo-1"],
                }
            ),
            encoding="utf-8",
        )
        return command_evidence(artifact_dir, list(argv))

    monkeypatch.setattr("autobugfix.eval.benchmarks.swe_official.run_command", fake_run)
    result = runner.score(
        instance("c" * 40),
        tmp_path / "score",
        run_id="patch-rejected",
        submission=submission("not a valid patch"),
    )

    assert result.resolved is False
    assert result.harness_error == ""


def test_verified_report_rejects_string_resolved_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SWEOfficialRunner(cast(Any, OfficialRuntime(tmp_path)), "swebench_verified")
    monkeypatch.setattr(runner, "image_id", lambda *args, **kwargs: "sha256:" + "2" * 64)

    def fake_run(argv, *, artifact_dir, **kwargs):
        report_root = Path(argv[argv.index("--report_dir") + 1])
        run_id = argv[argv.index("--run_id") + 1]
        report_path = (
            report_root
            / "logs/run_evaluation"
            / run_id
            / "autobugfix-subject"
            / "owner__repo-1"
            / "report.json"
        )
        report_path.parent.mkdir(parents=True)
        report_path.write_text(
            json.dumps({"owner__repo-1": {"resolved": "false"}}),
            encoding="utf-8",
        )
        (report_root / f"autobugfix-subject.{run_id}.json").write_text(
            json.dumps(
                {
                    "completed_ids": ["owner__repo-1"],
                    "empty_patch_ids": [],
                    "error_ids": [],
                }
            ),
            encoding="utf-8",
        )
        return command_evidence(artifact_dir, list(argv))

    monkeypatch.setattr("autobugfix.eval.benchmarks.swe_official.run_command", fake_run)
    result = runner.score(
        instance("c" * 40),
        tmp_path / "score",
        run_id="invalid-bool",
        submission=submission("diff --git a/a b/a\n"),
    )

    assert "not boolean" in result.harness_error
