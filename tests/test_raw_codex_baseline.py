from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from autobugfix.config import load_config
from autobugfix.eval.baselines.isolation import (
    RawCodexIsolationError,
    RawCodexProcessSandbox,
    RawProcessRun,
    RunnerMetadata,
    _resolver_mount,
    _python_runtime_mounts,
)
from autobugfix.eval.baselines.models import (
    PreparedRawBaselineManifest,
    RawBaselineCase,
    RawBaselineSeedManifest,
)
from autobugfix.eval.baselines.raw_codex import (
    RawCodexBaselineError,
    RawCodexBaselineHarnessError,
    RawCodexBaselineService,
)
from autobugfix.eval.baselines.reporting import _cohort_metrics
from autobugfix.eval.baselines.reporting import (
    RawBaselineReportError,
    write_raw_baseline_report,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    EligibilityReceipt,
    PreparedEvaluationCase,
    PreparedEvaluationManifest,
    digest_file,
    record_with_digest,
    verify_record,
)
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.models import (
    DEFECTS4J_FRAMEWORK_REVISION,
    RawCodexBaselineConfig,
    VerifierResult,
    utc_now,
)
from tests.helpers import make_service_project, run


def raw_cases() -> tuple[RawBaselineCase, ...]:
    return tuple(
        RawBaselineCase(
            case_id=f"case-{index}",
            project="Project",
            bug_id=index,
            receipt_digest=f"{index:064x}",
            cohort="development" if index <= 3 else "primary",
        )
        for index in range(1, 17)
    )


def prepared_raw_manifest() -> PreparedRawBaselineManifest:
    return PreparedRawBaselineManifest(
        manifest_id="raw-baseline",
        seed_manifest_digest="0" * 64,
        source_evaluation_manifest_digest="1" * 64,
        h0_report_digest="2" * 64,
        benchmark="defects4j",
        framework_revision="framework",
        dataset_revision="defects4j-v3.0.1",
        runtime_id="sha256:" + "3" * 64,
        verifier_runtime_id="sha256:" + "4" * 64,
        runner_git_sha="5" * 40,
        runner_git_tree="6" * 40,
        runner_source_digest="7" * 64,
        runner_install_digest="f" * 64,
        runner_lock_digest="8" * 64,
        sdk_version="0.144.4",
        prompt_template_digest="9" * 64,
        config_digest="a" * 64,
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        service_tier=None,
        approval_mode="deny_all",
        sandbox="workspace-write",
        network_access=False,
        timeout_seconds=500,
        turns_per_case=1,
        concurrency=1,
        cases=raw_cases(),
        prepared_at=utc_now(),
    )


def test_raw_baseline_protocol_preregisters_primary_and_development() -> None:
    seed = RawBaselineSeedManifest.from_yaml(
        Path(__file__).parents[1]
        / "benchmarks/defects4j-v3.0.1-raw-codex-baseline.yaml"
    )
    assert seed.expected_case_count == 16
    assert seed.development_case_ids == (
        "d4j-jsoup-2",
        "d4j-gson-2",
        "d4j-jacksoncore-2",
    )
    assert seed.turns_per_case == 1
    assert seed.concurrency == 1
    assert seed.model == "gpt-5.4-mini"
    assert seed.sdk_version == "0.144.4"
    assert seed.approval_mode == "deny_all"
    assert seed.sandbox == "workspace-write"
    assert seed.network_access is False


def test_prepared_raw_manifest_is_digest_bound_and_enforces_13_3_split() -> None:
    prepared = prepared_raw_manifest()
    encoded = prepared.to_dict()
    assert PreparedRawBaselineManifest.from_dict(encoded) == prepared

    encoded["timeout_seconds"] = 900
    with pytest.raises(BenchmarkContractError, match="digest mismatch"):
        PreparedRawBaselineManifest.from_dict(encoded)

    with pytest.raises(BenchmarkContractError, match="13 primary"):
        replace(
            prepared,
            cases=tuple(
                replace(case, cohort="development")
                if case.case_id == "case-4"
                else case
                for case in prepared.cases
            ),
        )


def test_config_resolves_raw_baseline_without_activating_it(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    config = load_config(project_root).eval.benchmarks.raw_codex
    assert config.runner_project == project_root / "baselines/raw_codex_sdk"
    assert config.runtime_root == project_root / ".autobugfix/raw-codex-baseline"
    assert config.model == "gpt-5.4-mini"
    assert config.sdk_version == "0.144.4"
    assert config.cli_version == "0.144.4"
    assert config.timeout_seconds == 500
    assert config.swe_timeout_seconds == 900
    assert config.approval_mode == "deny_all"
    assert config.sandbox == "workspace-write"
    assert config.network_access is False
    assert config.require_process_sandbox


def test_raw_run_output_cannot_escape_configured_runtime(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    service = RawCodexBaselineService(project_root)
    with pytest.raises(RawCodexBaselineError, match="configured runtime root"):
        service._run_directory(tmp_path / "outside", "escaped-run")


def test_prepare_freezes_h0_runner_runtime_and_cohorts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = make_service_project(tmp_path)
    runner_project = project_root / "baselines/raw_codex_sdk"
    runner_project.mkdir(parents=True)
    (runner_project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    service = RawCodexBaselineService(project_root)
    case_ids = [
        "d4j-jsoup-2",
        "d4j-gson-2",
        "d4j-jacksoncore-2",
        *(f"case-{index}" for index in range(4, 17)),
    ]
    source = PreparedEvaluationManifest(
        manifest_id="defects4j-v3.0.1-h0-16",
        seed_manifest_digest="0" * 64,
        benchmark="defects4j",
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        dataset_revision="defects4j-v3.0.1",
        runtime_id="sha256:" + "1" * 64,
        verifier_runtime_id="sha256:" + "2" * 64,
        subject_sha="3" * 40,
        subject_tree="4" * 40,
        config_digest="5" * 64,
        roles_digest="6" * 64,
        skills_digest="7" * 64,
        memory_digest="8" * 64,
        model="gpt-5.4-mini",
        max_attempts=2,
        expected_case_count=16,
        cases=tuple(
            PreparedEvaluationCase(
                case_id=case_id,
                project="Project",
                bug_id=index,
                receipt_digest=f"{index:064x}",
            )
            for index, case_id in enumerate(case_ids, start=1)
        ),
        prepared_at=utc_now(),
    )
    source_data = source.to_dict()
    source_path = service.store.write_trusted_manifest(
        source.manifest_id,
        f"evaluation-{source_data['record_digest']}.yaml",
        source_data,
    )
    h0 = record_with_digest(
        {
            "schema": "autobugfix-formal-evaluation-report-v1",
            "prepared_manifest_digest": source_data["record_digest"],
            "subject_sha": source.subject_sha,
            "case_count": 16,
            "cases": [
                {"case_id": case_id, "decision": "pass"}
                for case_id in case_ids
            ],
        }
    )
    h0_path = tmp_path / "h0-report.yaml"
    h0_path.write_text(yaml.safe_dump(h0, sort_keys=False), encoding="utf-8")
    runner = RunnerMetadata(
        sdk_version="0.144.4",
        prompt_template_digest="9" * 64,
        source_digest="a" * 64,
        package_digest="b" * 64,
        environment=tmp_path / "runner-env",
    )
    monkeypatch.setattr(service.store, "read_receipt", lambda path: path)
    monkeypatch.setattr(
        EvalBenchmarkService,
        "doctor",
        lambda self, adapter: {
            "passed": True,
            "runtime_id": source.runtime_id,
            "verifier_runtime_id": source.verifier_runtime_id,
        },
    )
    monkeypatch.setattr(service.sandbox, "ensure_runner_environment", lambda: runner)
    monkeypatch.setattr(
        service,
        "_git_identity",
        lambda *, require_clean: ("c" * 40, "d" * 40, ""),
    )
    monkeypatch.setattr(service, "_runner_source_digest", lambda: "e" * 64)

    prepared_result = service.prepare(
        Path(__file__).parents[1]
        / "benchmarks/defects4j-v3.0.1-raw-codex-baseline.yaml",
        source_path,
        h0_path,
    )
    prepared = service._read_prepared_manifest(
        Path(prepared_result["prepared_manifest"])
    )
    assert prepared.source_evaluation_manifest_digest == source_data["record_digest"]
    assert prepared.h0_report_digest == h0["record_digest"]
    assert prepared.runner_install_digest == runner.package_digest
    assert prepared.runtime_id == source.runtime_id
    assert sum(case.cohort == "primary" for case in prepared.cases) == 13
    assert sum(case.cohort == "development" for case in prepared.cases) == 3


def test_formal_run_aborts_after_first_harness_error_and_writes_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = make_service_project(tmp_path)
    service = RawCodexBaselineService(project_root)
    prepared = prepared_raw_manifest()
    runner = RunnerMetadata(
        sdk_version="0.144.4",
        prompt_template_digest=prepared.prompt_template_digest,
        source_digest="a" * 64,
        package_digest=prepared.runner_install_digest,
        environment=tmp_path / "runner-env",
    )
    monkeypatch.setattr(service, "_read_prepared_manifest", lambda path: prepared)
    monkeypatch.setattr(service, "_validate_prepared_runtime", lambda value: runner)
    monkeypatch.setattr(service, "_receipt", lambda case: case)
    called: list[str] = []

    def run_case(case, receipt, **kwargs):
        del receipt, kwargs
        called.append(case.case_id)
        if len(called) == 2:
            raise RawCodexBaselineHarnessError("scorer transport failed")
        return record_with_digest(
            {
                "schema": "autobugfix-raw-codex-case-report-v1",
                "case_id": case.case_id,
                "decision": "pass",
            }
        )

    monkeypatch.setattr(service, "_run_case", run_case)
    result = service.run_formal(
        tmp_path / "prepared.yaml",
        out_root=project_root / ".autobugfix/raw-codex-baseline/formal-runs",
        run_id="formal-abort",
    )
    summary = result["summary"]
    assert called == ["case-1", "case-2"]
    assert summary["status"] == "invalid"
    assert summary["completed_case_count"] == 1
    assert summary["harness_error_count"] == 1
    assert "scorer transport failed" in summary["harness_errors"][0]
    binding = yaml.safe_load(
        (Path(result["run_dir"]) / "run-binding.yaml").read_text(encoding="utf-8")
    )
    verify_record(binding)
    assert binding["summary_digest"] == summary["record_digest"]
    assert binding["h0_report_digest"] == prepared.h0_report_digest


def test_codex_home_disables_hooks_skills_and_multi_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "control"
    runner = project / "baselines/raw_codex_sdk"
    runtime = project / ".autobugfix/raw-codex-baseline"
    worktree = runtime / "case/worktree"
    worktree.mkdir(parents=True)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex/auth.json").write_text("{}\n", encoding="utf-8")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=runner,
            runtime_root=runtime,
        ),
        host_home=tmp_path,
    )
    codex_home = sandbox.create_codex_home(
        runtime / "case/codex-home",
        worktree=worktree,
        reasoning_effort="medium",
        service_tier=None,
    )
    config = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert "hooks = false" in config
    assert "multi_agent = false" in config
    assert 'approval_policy = "never"' in config
    assert 'sandbox_mode = "workspace-write"' in config
    assert "network_access = false" in config
    assert "skills" not in config
    assert str(worktree) in config
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == "{}\n"


def test_raw_process_preserves_sdk_transport_but_not_tool_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "control"
    runtime = project / ".autobugfix/raw-codex-baseline"
    runner_environment = runtime / "runner"
    worktree = runtime / "case/worktree"
    input_root = runtime / "case/input"
    output_root = runtime / "case/process"
    codex_home = output_root / "codex-home"
    for path in (
        runner_environment / "bin",
        worktree,
        input_root,
        output_root,
        codex_home,
    ):
        path.mkdir(parents=True, exist_ok=True)
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}\n", encoding="utf-8")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=runtime,
        ),
        host_home=tmp_path / "home",
    )
    monkeypatch.setattr(
        "autobugfix.eval.baselines.isolation.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    argv = sandbox._sandbox_argv(
        runner_environment=runner_environment,
        runtime_mounts=(),
        worktree=worktree,
        input_root=input_root,
        sdk_output_parent=output_root,
        codex_home=codex_home,
        case_bundle=case_bundle,
        model="gpt-5.4-mini",
        reasoning_effort="low",
        service_tier=None,
    )

    assert "--unshare-pid" in argv
    assert "--unshare-ipc" in argv
    assert "--unshare-uts" in argv
    assert "--unshare-net" not in argv


def test_resolver_mount_restores_only_resolved_file_under_masked_run(
    tmp_path: Path,
) -> None:
    reset_root = tmp_path / "run"
    source = reset_root / "systemd/resolve/stub-resolv.conf"
    source.parent.mkdir(parents=True)
    source.write_text("nameserver 127.0.0.53\n", encoding="utf-8")
    resolver = tmp_path / "etc/resolv.conf"
    resolver.parent.mkdir()
    resolver.symlink_to("../run/systemd/resolve/stub-resolv.conf")

    assert _resolver_mount(resolver_path=resolver, reset_root=reset_root) == (
        source,
        source,
    )


def test_raw_process_restores_only_resolver_file_after_masking_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "control"
    runtime = project / ".autobugfix/raw-codex-baseline"
    runner_environment = runtime / "runner"
    worktree = runtime / "case/worktree"
    input_root = runtime / "case/input"
    output_root = runtime / "case/process"
    codex_home = output_root / "codex-home"
    for path in (
        runner_environment / "bin",
        worktree,
        input_root,
        output_root,
        codex_home,
    ):
        path.mkdir(parents=True, exist_ok=True)
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}\n", encoding="utf-8")
    source = tmp_path / "host-resolver.conf"
    source.write_text("nameserver 127.0.0.53\n", encoding="utf-8")
    destination = Path("/run/systemd/resolve/stub-resolv.conf")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=runtime,
        ),
        host_home=tmp_path / "home",
    )
    monkeypatch.setattr(
        "autobugfix.eval.baselines.isolation.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )
    monkeypatch.setattr(
        "autobugfix.eval.baselines.isolation._resolver_mount",
        lambda: (source, destination),
    )

    argv = sandbox._sandbox_argv(
        runner_environment=runner_environment,
        runtime_mounts=(),
        worktree=worktree,
        input_root=input_root,
        sdk_output_parent=output_root,
        codex_home=codex_home,
        case_bundle=case_bundle,
        model="gpt-5.4-mini",
        reasoning_effort="low",
        service_tier=None,
    )

    mount_index = argv.index("--ro-bind", argv.index("--tmpfs") + 1)
    assert argv[mount_index : mount_index + 3] == [
        "--ro-bind",
        str(source),
        str(destination),
    ]
    assert ["--ro-bind", "/run", "/run"] not in [
        argv[index : index + 3] for index in range(len(argv) - 2)
    ]
    assert "--dir" in argv
    assert str(destination.parent) in argv


def test_raw_worker_scrubs_auth_before_recursive_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "control"
    runtime = project / ".autobugfix/raw"
    runner_environment = runtime / "runner"
    (runner_environment / "bin").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/auth.json").write_text(
        '{"token":"test-only"}\n',
        encoding="utf-8",
    )
    worktree = runtime / "case/worktree"
    input_root = runtime / "case/input"
    artifact_root = runtime / "case/process"
    worktree.mkdir(parents=True)
    input_root.mkdir(parents=True)
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}\n", encoding="utf-8")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=runtime,
        ),
        host_home=home,
    )
    monkeypatch.setattr(
        sandbox,
        "_sandbox_argv",
        lambda **kwargs: [
            "/bin/sh",
            "-c",
            "mkdir -p \"$CODEX_HOME/nested\"; "
            "cp \"$CODEX_HOME/auth.json\" \"$CODEX_HOME/nested/auth.json\"",
        ],
    )
    real_rmtree = shutil.rmtree
    codex_home = artifact_root / "codex-home"

    def fail_codex_home_cleanup(path, *args, **kwargs):
        if Path(path) == codex_home:
            raise OSError("simulated recursive cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "autobugfix.eval.baselines.isolation.shutil.rmtree",
        fail_codex_home_cleanup,
    )
    with pytest.raises(OSError, match="recursive cleanup failure"):
        sandbox.run(
            runner_metadata=RunnerMetadata(
                sdk_version="0.1.0b3",
                prompt_template_digest="b" * 64,
                source_digest="c" * 64,
                package_digest="d" * 64,
                environment=runner_environment,
            ),
            worktree=worktree,
            input_root=input_root,
            case_bundle=case_bundle,
            artifact_root=artifact_root,
            model="gpt-5.4-mini",
            reasoning_effort="medium",
            service_tier=None,
            timeout_seconds=30,
        )

    assert codex_home.is_dir()
    assert not list(codex_home.rglob("auth.json"))


def test_bubblewrap_hides_control_and_sibling_state_but_allows_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    home = tmp_path / "home"
    project = tmp_path / "control-outside-home"
    runner_environment = project / ".autobugfix/raw/runner"
    executable = runner_environment / "bin/raw-codex-sdk-baseline"
    worktree = project / ".autobugfix/raw/case/worktree"
    input_root = project / ".autobugfix/raw/case/input"
    sibling = project / ".autobugfix/raw/sibling/secret.txt"
    secret = project / "src/secret.txt"
    executable.parent.mkdir(parents=True)
    worktree.mkdir(parents=True)
    input_root.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)
    secret.parent.mkdir(parents=True)
    sibling.write_text("hidden", encoding="utf-8")
    secret.write_text("hidden", encoding="utf-8")
    codex_source = home / ".codex"
    codex_source.mkdir(parents=True)
    (codex_source / "auth.json").write_text(
        '{"tokens":"test-only"}', encoding="utf-8"
    )
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}", encoding="utf-8")
    script = "\n".join(
        (
            "#!/bin/sh",
            "set -eu",
            f"test ! -e {shlex.quote(str(secret))}",
            f"test ! -e {shlex.quote(str(sibling))}",
            f"test -r {shlex.quote(str(case_bundle))}",
            f"if touch {shlex.quote(str(input_root / 'forbidden'))} 2>/dev/null; then exit 41; fi",
            f"touch {shlex.quote(str(worktree / 'written'))}",
            "test -r \"$CODEX_HOME/config.toml\"",
            "test -r \"$CODEX_HOME/auth.json\"",
            "test -z \"${OPENAI_API_KEY:-}\"",
            "test -z \"${CODEX_API_KEY:-}\"",
            "echo isolated",
        )
    )
    executable.write_text(script + "\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=project / ".autobugfix/raw",
        ),
        host_home=home,
    )
    result = sandbox.run(
        runner_metadata=RunnerMetadata(
            sdk_version="0.144.4",
            prompt_template_digest="b" * 64,
            source_digest="c" * 64,
            package_digest="d" * 64,
            environment=runner_environment,
        ),
        worktree=worktree,
        input_root=input_root,
        case_bundle=case_bundle,
        artifact_root=project / ".autobugfix/raw/case/process",
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        service_tier=None,
        timeout_seconds=30,
    )
    assert result.return_code == 0, result.stderr
    assert result.stdout.strip() == "isolated"
    assert (worktree / "written").is_file()
    assert not (input_root / "forbidden").exists()
    assert not (
        project / ".autobugfix/raw/case/process/codex-home/auth.json"
    ).exists()
    assert not (
        project / ".autobugfix/raw/case/process/codex-home"
    ).exists()
    retained_config = (
        project / ".autobugfix/raw/case/process/codex-config.toml"
    )
    assert retained_config.is_file()
    assert "hooks = false" in retained_config.read_text(encoding="utf-8")


def test_bubblewrap_mounts_external_uv_python_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap is unavailable")
    home = tmp_path / "home"
    project = home / "control"
    runtime = project / ".autobugfix/raw"
    environment = runtime / "runner"
    versioned = home / ".local/share/uv/python/cpython-test-versioned"
    alias = home / ".local/share/uv/python/cpython-test"
    (versioned / "bin").mkdir(parents=True)
    shutil.copy2("/bin/sh", versioned / "bin/python")
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(versioned)
    (environment / "bin").mkdir(parents=True)
    (environment / "bin/python").symlink_to(alias / "bin/python")
    executable = environment / "bin/raw-codex-sdk-baseline"
    executable.write_text(
        f"#!{environment / 'bin/python'}\n"
        "set -eu\n"
        "echo python-prefix-mounted\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    worktree = runtime / "case/worktree"
    input_root = runtime / "case/input"
    worktree.mkdir(parents=True)
    input_root.mkdir(parents=True)
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}", encoding="utf-8")
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    mounts = _python_runtime_mounts(environment, home)
    assert mounts == ((versioned.resolve(), alias),)
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=runtime,
        ),
        host_home=home,
    )
    result = sandbox.run(
        runner_metadata=RunnerMetadata(
            sdk_version="0.144.4",
            prompt_template_digest="b" * 64,
            source_digest="c" * 64,
            package_digest="d" * 64,
            environment=environment,
            runtime_mounts=mounts,
        ),
        worktree=worktree,
        input_root=input_root,
        case_bundle=case_bundle,
        artifact_root=runtime / "case/process",
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        service_tier=None,
        timeout_seconds=30,
    )
    assert result.return_code == 0, result.stderr
    assert result.stdout.strip() == "python-prefix-mounted"


def test_raw_worker_cannot_forge_retained_codex_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "control"
    runtime = project / ".autobugfix/raw"
    worktree = runtime / "case/worktree"
    input_root = runtime / "case/input"
    runner_environment = runtime / "runner"
    worktree.mkdir(parents=True)
    input_root.mkdir(parents=True)
    runner_environment.mkdir(parents=True)
    case_bundle = input_root / "case.json"
    case_bundle.write_text("{}\n", encoding="utf-8")
    raw_home = tmp_path / "home"
    (raw_home / ".codex").mkdir(parents=True)
    (raw_home / ".codex/auth.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    sandbox = RawCodexProcessSandbox(
        project,
        RawCodexBaselineConfig(
            runner_project=project / "baselines/raw_codex_sdk",
            runtime_root=runtime,
        ),
        host_home=raw_home,
    )
    monkeypatch.setattr(
        sandbox,
        "_sandbox_argv",
        lambda **kwargs: [
            "/bin/sh",
            "-c",
            'printf "forged = true\\n" > "$CODEX_HOME/config.toml"',
        ],
    )

    with pytest.raises(RawCodexIsolationError, match="launch configuration"):
        sandbox.run(
            runner_metadata=RunnerMetadata(
                sdk_version="0.144.4",
                prompt_template_digest="b" * 64,
                source_digest="c" * 64,
                package_digest="d" * 64,
                environment=runner_environment,
            ),
            worktree=worktree,
            input_root=input_root,
            case_bundle=case_bundle,
            artifact_root=runtime / "case/process",
            model="gpt-5.4-mini",
            reasoning_effort="medium",
            service_tier=None,
            timeout_seconds=30,
        )
    process_root = runtime / "case/process"
    assert not (process_root / "codex-home").exists()
    retained = (process_root / "codex-config.toml").read_text(encoding="utf-8")
    assert "hooks = false" in retained
    assert "forged" not in retained


def _make_sanitized_repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    run(["git", "init", "--initial-branch=main", str(path)])
    run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"])
    run(["git", "-C", str(path), "config", "user.name", "Test"])
    source = path / "src"
    source.mkdir()
    (source / "Value.java").write_text("class Value { int get() { return 1; } }\n")
    run(["git", "-C", str(path), "add", "."])
    run(["git", "-C", str(path), "commit", "-m", "buggy"])
    base = run(["git", "-C", str(path), "rev-parse", "HEAD"]).stdout.strip()
    return path, base


class FakeRawSandbox:
    def run(self, **kwargs):
        worktree = Path(kwargs["worktree"])
        artifact_root = Path(kwargs["artifact_root"])
        bundle = json.loads(Path(kwargs["case_bundle"]).read_text(encoding="utf-8"))
        value = worktree / "src/Value.java"
        value.write_text("class Value { int get() { return 2; } }\n")
        sdk = artifact_root / "untrusted-sdk-output/sdk"
        sdk.mkdir(parents=True)
        events = sdk / "events.jsonl"
        stderr = sdk / "stderr.log"
        request = sdk / "request.json"
        events.write_text('{"method": "turn/completed"}\n', encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        request.write_text('{"request": "test"}\n', encoding="utf-8")
        process_result = record_with_digest(
            {
                "schema": "raw-codex-sdk-process-result-v1",
                "case_id": bundle["case_id"],
                "case_digest": bundle["record_digest"],
                "sdk_package": "openai-codex",
                "sdk_version": "0.144.4",
                "model": "gpt-5.4-mini",
                "reasoning_effort": "medium",
                "service_tier": None,
                "approval_mode": "deny_all",
                "sandbox": "workspace-write",
                "network_access": False,
                "prompt_template_digest": "b" * 64,
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "status": "completed",
                "error": "",
                "final_response": "fixed",
                "usage": {"total_tokens": 100},
                "event_count": 1,
                "request_sha256": digest_file(request),
                "events_sha256": digest_file(events),
                "stderr_sha256": digest_file(stderr),
                "started_unix": 1.0,
                "finished_unix": 2.0,
                "duration_seconds": 1.0,
            }
        )
        result_path = sdk / "process-result.json"
        result_path.write_text(json.dumps(process_result), encoding="utf-8")
        worker_stdout = artifact_root / "worker.stdout.log"
        worker_stderr = artifact_root / "worker.stderr.log"
        worker_stdout.write_text("", encoding="utf-8")
        worker_stderr.write_text("", encoding="utf-8")
        return RawProcessRun(
            return_code=0,
            timed_out=False,
            duration_seconds=1.0,
            stdout="",
            stderr="",
            stdout_path=worker_stdout,
            stderr_path=worker_stderr,
            sdk_artifact_root=sdk,
            process_result_path=result_path,
        )


def test_trusted_host_classifies_malformed_process_result_as_harness_error(
    tmp_path: Path,
) -> None:
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    events = sdk / "events.jsonl"
    stderr = sdk / "stderr.log"
    events.write_text('{"method":"turn/completed"}\n', encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    malformed = record_with_digest(
        {
            "schema": "raw-codex-sdk-process-result-v1",
            "case_id": "case-1",
        }
    )
    result_path = sdk / "process-result.json"
    result_path.write_text(json.dumps(malformed), encoding="utf-8")
    stdout = tmp_path / "stdout.log"
    worker_stderr = tmp_path / "worker-stderr.log"
    stdout.write_text("", encoding="utf-8")
    worker_stderr.write_text("", encoding="utf-8")
    process = RawProcessRun(
        return_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="",
        stderr="",
        stdout_path=stdout,
        stderr_path=worker_stderr,
        sdk_artifact_root=sdk,
        process_result_path=result_path,
    )
    case = raw_cases()[0]
    bundle = record_with_digest({"case_id": case.case_id})
    runner = RunnerMetadata(
        sdk_version="0.144.4",
        prompt_template_digest="b" * 64,
        source_digest="c" * 64,
        package_digest="d" * 64,
        environment=tmp_path / "runner",
    )
    with pytest.raises(
        RawCodexBaselineHarnessError,
        match="violates its contract",
    ):
        RawCodexBaselineService._validate_observation(
            process,
            bundle,
            case,
            model="gpt-5.4-mini",
            reasoning_effort="medium",
            service_tier=None,
            runner=runner,
        )


def test_trusted_service_derives_patch_and_scores_fresh_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, _ = make_service_project(tmp_path)
    source, base = _make_sanitized_repo(tmp_path / "sanitized")
    receipt = replace(
        EligibilityReceipt.pending(
            receipt_id="case-1-receipt",
            manifest_digest="a" * 64,
            case_id="case-1",
            project="Project",
            bug_id=1,
            role="evaluation",
            first_wave=16,
            framework_revision="framework",
            dataset_revision="defects4j-v3.0.1",
            status="eligible",
            reason="",
        ),
        runtime_id="sha256:" + "1" * 64,
        verifier_runtime_id="sha256:" + "2" * 64,
        issue_evidence_digest="3" * 64,
        triggering_tests=("ValueTest::returnsTwo",),
        baseline_failing_tests=("ValueTest::returnsTwo",),
        source_roots=("src",),
        sanitized_repo_path=str(source),
        sanitized_base_sha=base,
    )
    case = RawBaselineCase(
        case_id="case-1",
        project="Project",
        bug_id=1,
        receipt_digest=str(receipt.to_dict()["record_digest"]),
        cohort="development",
    )
    observed_candidates: list[Path] = []

    class FakeOracle:
        def run(self, candidate, artifact_dir, *, timeout_seconds):
            del timeout_seconds
            observed_candidates.append(candidate)
            artifact_dir.mkdir(parents=True)
            assert "return 2" in (candidate / "src/Value.java").read_text()
            return VerifierResult(
                command="official:test",
                exit_code=0,
                stdout="passed",
                stderr="",
                started_at=utc_now(),
                finished_at=utc_now(),
                outcome="passed",
            )

    monkeypatch.setattr(
        "autobugfix.eval.baselines.raw_codex.official_oracle_for_receipt",
        lambda receipt, config: FakeOracle(),
    )
    service = RawCodexBaselineService(project_root)
    service.sandbox = FakeRawSandbox()  # type: ignore[assignment]
    runner = RunnerMetadata(
        sdk_version="0.144.4",
        prompt_template_digest="b" * 64,
        source_digest="c" * 64,
        package_digest="d" * 64,
        environment=tmp_path / "runner",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_before = run(["git", "-C", str(source), "status", "--porcelain"]).stdout

    report = service._run_case(
        case,
        receipt,
        run_dir=run_dir,
        manifest_digest="c" * 64,
        runner=runner,
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        service_tier=None,
        timeout_seconds=500,
    )

    assert report["decision"] == "pass"
    assert observed_candidates[0] != run_dir / "case-1/worktree"
    assert "return 2" in (run_dir / "case-1/generated.diff").read_text()
    assert run(["git", "-C", str(source), "status", "--porcelain"]).stdout == source_before
    assert (
        json.loads((run_dir / "case-1/visible-input/case.json").read_text())["problem_statement"]
        .find("ValueTest::returnsTwo")
        >= 0
    )


def test_paired_metrics_keep_development_out_of_primary() -> None:
    raw = {
        "a": {"decision": "pass"},
        "b": {"decision": "fail"},
        "c": {"decision": "pass"},
    }
    h0 = {
        "a": {"decision": "pass"},
        "b": {"decision": "pass"},
        "c": {"decision": "fail"},
    }
    metrics = _cohort_metrics(["a", "b", "c"], raw, h0)
    assert metrics["raw_rescue_count"] == 1
    assert metrics["raw_regression_count"] == 1
    assert metrics["raw_minus_h0_absolute"] == 0.0
    assert metrics["mcnemar_exact_two_sided_p"] == 1.0


def test_comparison_report_revalidates_all_frozen_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "formal"
    run_dir.mkdir()
    raw_reports = []
    h0_cases = []
    for index in range(1, 17):
        case_id = f"case-{index}"
        case_dir = run_dir / case_id
        (case_dir / "visible-input").mkdir(parents=True)
        sdk_dir = case_dir / "process/untrusted-sdk-output/sdk"
        sdk_dir.mkdir(parents=True)
        case_bundle = record_with_digest(
            {
                "schema": "raw-codex-sdk-case-v1",
                "case_id": case_id,
            }
        )
        case_bundle_path = case_dir / "visible-input/case.json"
        case_bundle_path.write_text(json.dumps(case_bundle) + "\n")
        process_paths = {
            "worker_stdout": case_dir / "process/worker.stdout.log",
            "worker_stderr": case_dir / "process/worker.stderr.log",
            "sdk_request": sdk_dir / "request.json",
            "sdk_events": sdk_dir / "events.jsonl",
            "sdk_stderr": sdk_dir / "stderr.log",
            "sdk_result": sdk_dir / "process-result.json",
        }
        for name, path in process_paths.items():
            path.write_text(f"{name}-{index}\n")
        patch = case_dir / "generated.diff"
        patch.write_text(f"patch-{index}\n", encoding="utf-8")
        submission = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-submission-v1",
                "case_id": case_id,
                "manifest_digest": "a" * 64,
                "case_bundle_digest": case_bundle["record_digest"],
                "process_artifact_digests": {
                    name: digest_file(path) for name, path in process_paths.items()
                },
                "patch_sha256": digest_file(patch),
            }
        )
        (case_dir / "submission.yaml").write_text(
            yaml.safe_dump(submission, sort_keys=False)
        )
        oracle = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-oracle-result-v1",
                "case_id": case_id,
                "submission_digest": submission["record_digest"],
            }
        )
        (case_dir / "oracle-result.yaml").write_text(
            yaml.safe_dump(oracle, sort_keys=False)
        )
        noninterference = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-noninterference-v1",
                "case_id": case_id,
                "submission_digest": submission["record_digest"],
                "unchanged": True,
                "expected": {
                    "case_bundle_sha256": digest_file(case_bundle_path),
                },
            }
        )
        (case_dir / "oracle-noninterference.yaml").write_text(
            yaml.safe_dump(noninterference, sort_keys=False)
        )
        raw_pass = index % 3 != 0
        report = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-case-report-v1",
                "case_id": case_id,
                "cohort": "development" if index <= 3 else "primary",
                "decision": "pass" if raw_pass else "fail",
                "timed_out": False,
                "path_policy_passed": True,
                "usage": {"total_tokens": 100 + index},
                "runtime_seconds": float(index),
                "submission_digest": submission["record_digest"],
                "oracle_digest": oracle["record_digest"],
                "noninterference_digest": noninterference["record_digest"],
            }
        )
        (case_dir / "report.yaml").write_text(
            yaml.safe_dump(report, sort_keys=False)
        )
        raw_reports.append(report)
        h0_cases.append(
            {
                "case_id": case_id,
                "decision": "pass" if index <= 14 else "fail",
            }
        )
    summary = record_with_digest(
        {
            "schema": "autobugfix-raw-codex-run-summary-v1",
            "run_id": "raw-formal",
            "formal": True,
            "status": "completed",
            "expected_case_count": 16,
            "completed_case_count": 16,
            "harness_error_count": 0,
            "cases": raw_reports,
        }
    )
    (run_dir / "summary.yaml").write_text(
        yaml.safe_dump(summary, sort_keys=False)
    )
    h0 = record_with_digest(
        {
            "schema": "autobugfix-formal-evaluation-report-v1",
            "case_count": 16,
            "cases": h0_cases,
        }
    )
    h0_path = tmp_path / "h0.yaml"
    h0_path.write_text(yaml.safe_dump(h0, sort_keys=False))
    binding = record_with_digest(
        {
            "schema": "autobugfix-raw-codex-run-binding-v1",
            "summary_digest": summary["record_digest"],
            "prepared_manifest_digest": "a" * 64,
            "h0_report_digest": h0["record_digest"],
            "runner_git_sha": "b" * 40,
            "runner_source_digest": "c" * 64,
            "prompt_template_digest": "d" * 64,
            "model": "gpt-5.4-mini",
            "sdk_version": "0.144.4",
        }
    )
    (run_dir / "run-binding.yaml").write_text(
        yaml.safe_dump(binding, sort_keys=False)
    )

    report_path = write_raw_baseline_report(run_dir, h0_path)
    result = yaml.safe_load(report_path.read_text())
    assert result["primary"]["case_count"] == 13
    assert result["development"]["case_count"] == 3
    assert result["all_cases"]["case_count"] == 16
    assert result["artifact_completeness"] == 1.0
    assert result["raw_reported_total_tokens"] == sum(
        100 + index for index in range(1, 17)
    )
    assert write_raw_baseline_report(run_dir, h0_path) == report_path

    events_path = (
        run_dir
        / "case-1/process/untrusted-sdk-output/sdk/events.jsonl"
    )
    original_events = events_path.read_text(encoding="utf-8")
    events_path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RawBaselineReportError, match="artifact binding failed"):
        write_raw_baseline_report(run_dir, h0_path)
    events_path.write_text(original_events, encoding="utf-8")

    substituted_h0 = record_with_digest(
        {
            "schema": "autobugfix-formal-evaluation-report-v1",
            "case_count": 16,
            "cases": h0_cases,
            "substitution": True,
        }
    )
    h0_path.write_text(yaml.safe_dump(substituted_h0, sort_keys=False))
    with pytest.raises(RawBaselineReportError, match="frozen for this Raw run"):
        write_raw_baseline_report(run_dir, h0_path)
