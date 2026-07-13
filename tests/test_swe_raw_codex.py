from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autobugfix.cli import build_parser
from autobugfix.eval.baselines.isolation import RawProcessRun, RunnerMetadata
from autobugfix.eval.baselines.swe_raw_codex import (
    SWERawCodexBaselineError,
    SWERawCodexBaselineService,
)
from autobugfix.eval.baselines.swe_raw_models import (
    PreparedSWERawCase,
    PreparedSWERawManifest,
    SWERawTreatmentProtocol,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
)
from autobugfix.eval.benchmarks.swe_materialize import SWEMaterializedRepository
from autobugfix.eval.benchmarks.swe_models import (
    SWEAttachment,
    SWEExperimentProtocol,
    SWEInstance,
    SWEOfficialResult,
    SWEVisibleCase,
)
from autobugfix.models import utc_now
from tests.helpers import make_service_project, run


ROOT = Path(__file__).parents[1]
SOURCE_PROTOCOL = ROOT / "benchmarks/swe-experiment-2.yaml"
RAW_PROTOCOL = ROOT / "benchmarks/swe-experiment-2-raw-codex.yaml"


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def make_sanitized_source(root: Path) -> tuple[Path, str, str]:
    source = root / "source"
    run(["git", "init", "-b", "main", str(source)])
    git(source, "config", "user.email", "benchmark@example.com")
    git(source, "config", "user.name", "Benchmark")
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "module.py")
    git(source, "commit", "-m", "buggy base")
    base = git(source, "rev-parse", "HEAD")
    tree = git(source, "rev-parse", "HEAD^{tree}")
    git(source, "checkout", "--detach", base)
    git(source, "branch", "-D", "main")
    (source / ".git" / "autobugfix-sanitized-v1").write_text(
        base + "\n", encoding="utf-8"
    )
    return source, base, tree


def visible_case(base: str, *, token: str = "opt-visible-case") -> SWEVisibleCase:
    return SWEVisibleCase(
        case_token=token,
        benchmark="swebench_verified",
        dataset_revision="a" * 40,
        harness_commit="b" * 40,
        repository="owner/repo",
        base_commit=base,
        language="python",
        task_type="bugfix",
        problem_statement="Fix VALUE so it equals 2.",
        public_hints=("The failure is in module.py.",),
        attachments=(
            SWEAttachment(
                kind="upstream-reference",
                uri="https://example.invalid/public.png",
                sha256=hashlib.sha256(
                    b"https://example.invalid/public.png"
                ).hexdigest(),
                media_type="image/png",
            ),
        ),
        first_wave=3,
        source_snapshot_digest="c" * 64,
        verifier_profile="swe-visible-v1",
    )


def prepared_case(source: Path, base: str, tree: str) -> tuple[
    PreparedSWERawCase,
    SWEInstance,
    SWEMaterializedRepository,
]:
    visible = visible_case(base)
    case = PreparedSWERawCase(
        instance_id="owner__repo-1",
        qualification_digest="d" * 64,
        image_id="sha256:" + "e" * 64,
        source_tree=tree,
        source_digest=visible.source_snapshot_digest,
        visible_case=visible,
    )
    instance = SWEInstance(
        adapter="swebench_verified",
        instance_id=case.instance_id,
        repository=visible.repository,
        base_commit=base,
        language="python",
        problem_statement=visible.problem_statement,
        hints_text="The failure is in module.py.",
        created_at="2026-07-13T00:00:00Z",
        docker_image="sweb.eval.x86_64.owner__repo-1:latest",
        gold_patch="diff --git a/module.py b/module.py\n",
        test_patch="diff --git a/test_module.py b/test_module.py\n",
        fail_to_pass=("test_module.py::test_value",),
        pass_to_pass=(),
        official={"version": "1"},
    )
    materialized = SWEMaterializedRepository(
        instance_id=case.instance_id,
        repository=visible.repository,
        base_commit=base,
        source_path=str(source),
        source_tree=tree,
        source_digest=case.source_digest,
        image=instance.docker_image,
        image_id=case.image_id,
    )
    return case, instance, materialized


def treatment() -> SWERawTreatmentProtocol:
    return SWERawTreatmentProtocol.from_yaml(RAW_PROTOCOL)


def runner_metadata(tmp_path: Path) -> RunnerMetadata:
    return RunnerMetadata(
        sdk_version="0.1.0b3",
        prompt_template_digest=treatment().prompt_template_digest,
        source_digest="1" * 64,
        package_digest="2" * 64,
        environment=tmp_path / "runner-env",
    )


class FakeRawSandbox:
    def __init__(self, *, timed_out: bool = False) -> None:
        self.timed_out = timed_out
        self.calls = 0

    def run(self, **kwargs: Any) -> RawProcessRun:
        self.calls += 1
        worktree = Path(kwargs["worktree"])
        artifact_root = Path(kwargs["artifact_root"])
        case_path = Path(kwargs["case_bundle"])
        case = json.loads(case_path.read_text(encoding="utf-8"))
        (worktree / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        sdk = artifact_root / "untrusted-sdk-output" / "sdk"
        sdk.mkdir(parents=True)
        stdout = artifact_root / "worker.stdout.log"
        stderr = artifact_root / "worker.stderr.log"
        request = sdk / "request.json"
        events = sdk / "events.jsonl"
        sdk_stderr = sdk / "stderr.log"
        result = sdk / "process-result.json"
        codex_home = artifact_root / "codex-home"
        codex_home.mkdir()
        (codex_home / "untrusted-cache.bin").write_bytes(b"x" * 1024)
        (artifact_root / "codex-config.toml").write_text(
            "[features]\nhooks = false\nmulti_agent = false\n",
            encoding="utf-8",
        )
        stdout.write_text("worker completed\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        request.write_text("{}\n", encoding="utf-8")
        events.write_text('{"method":"turn/completed"}\n', encoding="utf-8")
        sdk_stderr.write_text("", encoding="utf-8")
        if not self.timed_out:
            process_record = record_with_digest(
                {
                    "schema": "raw-codex-sdk-process-result-v1",
                    "case_id": case["case_id"],
                    "case_digest": case["record_digest"],
                    "sdk_package": "openai-codex",
                    "sdk_version": kwargs["runner_metadata"].sdk_version,
                    "model": kwargs["model"],
                    "reasoning_effort": kwargs["reasoning_effort"],
                    "service_tier": kwargs["service_tier"],
                    "approval_mode": kwargs["runner_metadata"].approval_mode,
                    "sandbox": kwargs["runner_metadata"].sandbox,
                    "network_access": kwargs["runner_metadata"].network_access,
                    "prompt_template_digest": kwargs[
                        "runner_metadata"
                    ].prompt_template_digest,
                    "thread_id": "thread-1",
                    "turn_id": "turn-1",
                    "status": "completed",
                    "error": "",
                    "final_response": "Fixed module.py",
                    "usage": {"total_tokens": 100},
                    "event_count": 1,
                    "request_sha256": digest_file(request),
                    "events_sha256": digest_file(events),
                    "stderr_sha256": digest_file(sdk_stderr),
                    "started_unix": 1.0,
                    "finished_unix": 2.0,
                    "duration_seconds": 1.0,
                }
            )
            result.write_text(
                json.dumps(process_record, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return RawProcessRun(
            return_code=None if self.timed_out else 0,
            timed_out=self.timed_out,
            duration_seconds=1.0,
            stdout=stdout.read_text(encoding="utf-8"),
            stderr="",
            stdout_path=stdout,
            stderr_path=stderr,
            sdk_artifact_root=sdk,
            process_result_path=result,
        )


class FakeOfficialRunner:
    def __init__(self, trusted_root: Path, *, resolved: bool = True) -> None:
        self.trusted_root = trusted_root
        self.resolved = resolved
        self.calls = 0

    def score(self, instance: SWEInstance, artifact_root: Path, **kwargs: Any) -> SWEOfficialResult:
        self.calls += 1
        submission = kwargs["submission"]
        digest = submission.record["record_digest"]
        frozen = (
            self.trusted_root
            / "swe/raw-submissions"
            / submission.case_token
            / digest
        )
        assert (frozen / "submission.yaml").is_file()
        assert (frozen / "patch.diff").read_text(encoding="utf-8") == submission.patch
        artifact_root.mkdir(parents=True)
        return SWEOfficialResult(
            adapter="swebench_verified",
            instance_id=instance.instance_id,
            run_id=str(kwargs["run_id"]),
            resolved=self.resolved,
            harness_error="",
            image=instance.docker_image,
            image_id=str(kwargs["expected_image_id"]),
            command={"argv": ["official-swe-scorer"]},
            report_path="fake-report.json",
            report_sha256="f" * 64,
            output_root=str(artifact_root),
            started_at=utc_now(),
            finished_at=utc_now(),
        )


def test_swe_raw_treatment_is_bound_to_shared_protocol() -> None:
    source = SWEExperimentProtocol.from_yaml(SOURCE_PROTOCOL)
    raw = treatment()
    assert raw.source_protocol_digest == source.protocol_digest
    assert raw.model == source.model == "gpt-5.4-mini"
    assert raw.timeout_seconds == source.timeout_seconds == 900
    assert raw.turns_per_case == 1
    assert raw.expected_case_count == 10
    assert raw.approval_mode == "deny_all"
    assert raw.sandbox == "workspace-write"
    assert raw.network_access is False

    with pytest.raises(BenchmarkContractError, match="source_protocol_digest"):
        replace(raw, source_protocol_digest="not-a-digest")
    with pytest.raises(BenchmarkContractError, match="deny approvals"):
        replace(raw, network_access=True)


def test_prepared_swe_raw_manifest_round_trip_requires_ten_cases() -> None:
    visible = visible_case("0" * 40)
    cases = tuple(
        PreparedSWERawCase(
            instance_id=f"owner__repo-{index}",
            qualification_digest=f"{index:064x}",
            image_id="sha256:" + f"{index:064x}",
            source_tree=f"{index:040x}",
            source_digest=f"{index + 10:064x}",
            visible_case=replace(
                visible,
                case_token=f"case-{index}",
                source_snapshot_digest=f"{index + 10:064x}",
            ),
        )
        for index in range(1, 11)
    )
    manifest = PreparedSWERawManifest(
        manifest_id="swe-raw-prepared",
        source_protocol_digest=treatment().source_protocol_digest,
        treatment=treatment(),
        runtime_id="sha256:" + "a" * 64,
        control_sha="b" * 40,
        control_tree="c" * 40,
        runner_source_digest="d" * 64,
        runner_install_digest="e" * 64,
        runner_lock_digest="f" * 64,
        config_digest="1" * 64,
        cases=cases,
        prepared_at=utc_now(),
    )
    assert PreparedSWERawManifest.from_dict(manifest.to_dict()) == manifest
    with pytest.raises(BenchmarkContractError, match="case count"):
        replace(manifest, cases=cases[:-1])


def test_raw_case_bundle_contains_only_visible_evidence() -> None:
    bundle = SWERawCodexBaselineService._case_bundle(visible_case("0" * 40))
    encoded = json.dumps(bundle, sort_keys=True)
    assert "Fix VALUE" in encoded
    assert "public.png" in encoded
    assert "gold_patch" not in encoded
    assert "test_patch" not in encoded
    assert "FAIL_TO_PASS" not in encoded


@pytest.mark.parametrize(("timed_out", "resolved"), [(False, True), (True, False)])
def test_raw_sdk_freezes_before_official_score_and_never_retries(
    tmp_path: Path,
    timed_out: bool,
    resolved: bool,
) -> None:
    project_root, _ = make_service_project(tmp_path)
    service = SWERawCodexBaselineService(project_root)
    source, base, tree = make_sanitized_source(tmp_path / "benchmark")
    case, instance, materialized = prepared_case(source, base, tree)
    sandbox = FakeRawSandbox(timed_out=timed_out)
    service.sandbox = sandbox  # type: ignore[assignment]
    official = FakeOfficialRunner(
        service.config.eval.benchmarks.trusted_case_root,
        resolved=resolved,
    )
    run_dir = service._run_directory(
        service.config.eval.benchmarks.raw_codex.runtime_root / "swe/tests",
        f"run-{int(timed_out)}",
    )

    report = service._run_case(
        case,
        instance,
        materialized,
        run_dir=run_dir,
        manifest_digest="9" * 64,
        treatment=treatment(),
        runner_metadata=runner_metadata(tmp_path),
        official_runner=official,  # type: ignore[arg-type]
        official_run_id="official-1",
    )

    assert sandbox.calls == 1
    assert official.calls == 1
    assert report["decision"] == ("pass" if resolved else "fail")
    assert report["timed_out"] is timed_out
    assert (run_dir / case.instance_id / "noninterference.yaml").is_file()
    frozen_evidence = next(
        (
            service.config.eval.benchmarks.trusted_case_root
            / "swe/raw-submissions"
            / case.visible_case.case_token
        ).glob("*/evidence")
    )
    assert (frozen_evidence / "process/sdk/events.jsonl").is_file()
    assert (frozen_evidence / "process/codex-config.toml").is_file()
    assert not (frozen_evidence / "process/codex-home").exists()
    assert not (frozen_evidence / "process/untrusted-cache.bin").exists()
    assert git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_raw_worktree_hides_source_remote_and_preserves_base(tmp_path: Path) -> None:
    source, base, _ = make_sanitized_source(tmp_path)
    worktree = SWERawCodexBaselineService._clone_source(
        source, base, tmp_path / "worktree"
    )
    assert git(worktree, "rev-parse", "HEAD") == base
    assert git(worktree, "remote") == ""
    assert git(worktree, "for-each-ref", "--format=%(refname)") == ""
    assert not (worktree / ".git/FETCH_HEAD").exists()


def test_swe_raw_output_cannot_escape_runtime_root(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    service = SWERawCodexBaselineService(project_root)
    with pytest.raises(SWERawCodexBaselineError, match="configured runtime root"):
        service._run_directory(tmp_path / "outside", "escaped")


def test_cli_exposes_real_swe_raw_commands() -> None:
    parser = build_parser()
    prepared = parser.parse_args(
        [
            "eval",
            "baseline",
            "prepare-swe-raw-codex",
            "--source-protocol",
            "benchmarks/swe-experiment-2.yaml",
            "--treatment",
            "benchmarks/swe-experiment-2-raw-codex.yaml",
        ]
    )
    assert prepared.baseline_action == "prepare-swe-raw-codex"
    formal = parser.parse_args(
        [
            "eval",
            "baseline",
            "run-swe-raw-codex",
            "--manifest",
            "prepared.yaml",
            "--run-id",
            "formal-1",
        ]
    )
    assert formal.baseline_action == "run-swe-raw-codex"
