from __future__ import annotations

import json
import subprocess
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from autobugfix.eval.benchmarks.authority import GuardCodeIdentity
from autobugfix.eval.benchmarks.models import record_with_digest
from autobugfix.eval.benchmarks.service import (
    EvalBenchmarkService,
    EvalBenchmarkServiceError,
)
from autobugfix.eval.benchmarks.swe_guard import SWEGuardStore, SWEGuardStoreError
from autobugfix.eval.benchmarks.swe_materialize import SWEMaterializedRepository
from autobugfix.eval.benchmarks.swe_runtime import (
    SWEDockerAuthority,
    SWERuntime,
    SWERuntimeError,
)
from autobugfix.eval.benchmarks.swe_models import (
    SWEInstance,
    SWEOptimizationSelection,
    SWESubjectTreatmentRuntime,
)
from tests.helpers import make_service_project


SECRET = "guard-test-secret-with-32-bytes"
PROTOCOL = "a" * 64
RUNTIME = "sha256:" + "b" * 64
TREATMENT = SWESubjectTreatmentRuntime(
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
SUBJECT_RUNTIME = record_with_digest(
    {
        "schema": "autobugfix-swe-subject-runtime-v1",
        "treatment_contract_digest": TREATMENT.contract_digest,
        "treatment": TREATMENT.to_dict(),
    }
)


def guard_store(tmp_path: Path) -> SWEGuardStore:
    project = tmp_path / "project"
    project.mkdir()
    return SWEGuardStore(
        tmp_path / "external-guard",
        forbidden_roots=(project,),
    )


def qualification(canary: str) -> dict[str, object]:
    return record_with_digest(
        {
            "schema": "autobugfix-swe-qualification-v4",
            "qualification_contract_digest": PROTOCOL,
            "evaluator_runtime_id": RUNTIME,
            "adapter": "swebench_live",
            "instance_id": canary,
            "eligible": True,
            "qualified_at": "2026-07-13T00:00:00Z",
        }
    )


def test_guard_store_keeps_holdout_record_and_artifacts_encrypted(
    tmp_path: Path,
) -> None:
    store = guard_store(tmp_path)
    artifacts = tmp_path / "private-artifacts"
    artifacts.mkdir()
    canary = "sealed-owner__repo-12345"
    (artifacts / "gold.patch").write_text(
        f"private patch for {canary}\n", encoding="utf-8"
    )

    result = store.write_qualification(
        qualification(canary),
        artifacts,
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )

    assert result["eligible"] is True
    records = store.qualification_records(
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )
    assert records[0]["instance_id"] == canary
    for path in store.root.rglob("*"):
        if path.is_file():
            assert canary.encode("utf-8") not in path.read_bytes()
            assert b"private patch" not in path.read_bytes()


def test_guard_exposure_ledger_is_encrypted_and_append_only(tmp_path: Path) -> None:
    store = guard_store(tmp_path)
    revision = "live-revision-1"
    instance = "sealed-owner__repo-123"
    repository = "sealed-owner/repo"

    store.write_exposure_ledger(
        instance_ids={instance},
        repositories={repository},
        secret=SECRET,
        dataset_revision=revision,
    )
    assert store.load_exposure_ledger(
        secret=SECRET,
        dataset_revision=revision,
    ) == ({instance}, {repository})
    encrypted = next((store.root / "exposure-ledgers").glob("*.abfg"))
    assert instance.encode() not in encrypted.read_bytes()
    assert repository.encode() not in encrypted.read_bytes()

    with pytest.raises(SWEGuardStoreError, match="cannot remove"):
        store.write_exposure_ledger(
            instance_ids=set(),
            repositories=set(),
            secret=SECRET,
            dataset_revision=revision,
        )


def test_guard_store_detects_encrypted_qualification_artifact_tampering(
    tmp_path: Path,
) -> None:
    store = guard_store(tmp_path)
    artifacts = tmp_path / "private-artifacts"
    artifacts.mkdir()
    (artifacts / "raw.log").write_text("private\n", encoding="utf-8")
    store.write_qualification(
        qualification("sealed-owner__repo-1"),
        artifacts,
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )
    encrypted = next((store.root / "qualification-artifacts").glob("*.abfg"))
    encrypted.write_bytes(encrypted.read_bytes() + b"tampered")

    with pytest.raises(SWEGuardStoreError, match="missing or changed"):
        store.qualification_records(
            secret=SECRET,
            protocol_digest=PROTOCOL,
            runtime_id=RUNTIME,
        )


def test_guard_store_preparation_round_trip_has_no_plaintext(
    tmp_path: Path,
) -> None:
    store = guard_store(tmp_path)
    canary = "sealed-holdout-identity"
    payload = record_with_digest(
        {
            "schema": "autobugfix-swe-private-cohort-v1",
            "cases": [{"instance_id": canary, "gold_patch": "secret patch"}],
        }
    )
    _, digest = store.write_preparation(
        "swe-prep-test",
        payload,
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )

    observed = store.load_preparation(
        "swe-prep-test",
        expected_sha256=digest,
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )

    assert observed == payload
    ciphertext = (store.root / "preparations/swe-prep-test.abfg").read_bytes()
    assert canary.encode("utf-8") not in ciphertext
    assert b"secret patch" not in ciphertext


def test_guard_store_rejects_operator_visible_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(SWEGuardStoreError, match="must be disjoint"):
        SWEGuardStore(project / ".autobugfix/guard", forbidden_roots=(project,))


def test_service_guard_store_rejects_raw_baseline_runtime(tmp_path: Path) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)

    with pytest.raises(SWEGuardStoreError, match="must be disjoint"):
        service._swe_guard_store(
            service.config.eval.benchmarks.raw_codex.runtime_root / "guard"
        )


def test_guard_docker_authority_rejects_same_daemon_identity(
    tmp_path: Path, monkeypatch
) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)
    service.config.eval.benchmarks.guard.docker_host = "unix:///tmp/guard.sock"
    store = service._swe_guard_store(tmp_path / "external-guard")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr("autobugfix.eval.benchmarks.service.shutil.which", lambda name: "/usr/bin/docker")

    def fake_run(argv, *, artifact_dir, **kwargs):
        del kwargs
        artifact_dir.mkdir(parents=True)
        output = artifact_dir / "stdout.log"
        if "{{json .}}" in argv:
            output.write_text(
                json.dumps(
                    {
                        "ID": "same-daemon-id",
                        "Labels": [
                            "autobugfix.guard.isolation=dedicated-vm-v1"
                        ],
                    }
                ),
                encoding="utf-8",
            )
        else:
            output.write_text("same-daemon-id\n", encoding="utf-8")
        return SimpleNamespace(passed=True, stdout_path=str(output))

    monkeypatch.setattr("autobugfix.eval.benchmarks.service.run_command", fake_run)

    with pytest.raises(EvalBenchmarkServiceError, match="regular Eval daemon"):
        service._verify_swe_guard_daemon(store)


def test_guard_docker_authority_pins_private_socket_and_daemon(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with tempfile.TemporaryDirectory(prefix="abfg-", dir="/tmp") as guard_dir:
        store = SWEGuardStore(Path(guard_dir), forbidden_roots=(project,))
        socket_path = store.root / "docker" / "d.sock"
        socket_path.parent.mkdir(mode=0o700)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        socket_path.chmod(0o600)
        authority = SWEDockerAuthority.capture(
            endpoint=f"unix://{socket_path}",
            guard_root=store.root,
            daemon_id="guard-daemon-id",
            docker_executable="/usr/bin/docker",
            isolation_label="autobugfix.guard.isolation=dedicated-vm-v1",
            daemon_profile_digest="a" * 64,
        )

        monkeypatch.setattr(
            "autobugfix.eval.benchmarks.swe_runtime.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="guard-daemon-id\n",
                stderr="",
            ),
        )
        authority.assert_current(
            {"DOCKER_HOST": authority.endpoint, "PATH": "/usr/bin"}
        )
        server.close()
        socket_path.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement.bind(str(socket_path))
        socket_path.chmod(0o600)
        try:
            with pytest.raises(SWERuntimeError, match="authority changed"):
                authority.assert_current(
                    {"DOCKER_HOST": authority.endpoint, "PATH": "/usr/bin"}
                )
        finally:
            replacement.close()


def test_guard_docker_authority_is_persistent_across_runtime_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = make_service_project(tmp_path)
    external = tmp_path / "external-guard"
    service = EvalBenchmarkService(project)
    store = service._swe_guard_store(external)
    socket_path = store.root / "docker/d.sock"
    socket_path.parent.mkdir(mode=0o700)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    socket_path.chmod(0o600)
    service.config.eval.benchmarks.guard.docker_host = f"unix://{socket_path}"
    daemon_id = {"value": "guard-daemon-1"}
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.service.shutil.which",
        lambda name: "/usr/bin/docker",
    )

    def fake_run(argv, *, artifact_dir, **kwargs):
        del kwargs
        artifact_dir.mkdir(parents=True)
        output = artifact_dir / "stdout.log"
        if "{{json .}}" in argv:
            output.write_text(
                json.dumps(
                    {
                        "ID": daemon_id["value"],
                        "Name": "guard-vm",
                        "OperatingSystem": "Guard VM",
                        "OSType": "linux",
                        "Architecture": "x86_64",
                        "DockerRootDir": "/var/lib/docker",
                        "Driver": "overlay2",
                        "SecurityOptions": [],
                        "Labels": [
                            "autobugfix.guard.isolation=dedicated-vm-v1"
                        ],
                    }
                ),
                encoding="utf-8",
            )
        else:
            output.write_text("regular-daemon\n", encoding="utf-8")
        return SimpleNamespace(passed=True, stdout_path=str(output))

    monkeypatch.setattr("autobugfix.eval.benchmarks.service.run_command", fake_run)
    try:
        _, first = service._verify_swe_guard_daemon(store)
        assert (store.root / "docker-authority/authority.yaml").is_file()
        _, second = service._verify_swe_guard_daemon(store)
        assert first.authority_digest == second.authority_digest

        daemon_id["value"] = "guard-daemon-2"
        with pytest.raises(EvalBenchmarkServiceError, match="changed after"):
            service._verify_swe_guard_daemon(store)
    finally:
        server.close()


def test_guard_docker_authority_requires_dedicated_vm_attestation(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "docker-info.json"
    evidence.write_text(json.dumps({"ID": "guard-daemon", "Labels": []}))

    with pytest.raises(EvalBenchmarkServiceError, match="independently administered VM"):
        EvalBenchmarkService._docker_daemon_profile(evidence)


def test_swe_runtime_uses_private_client_home(tmp_path: Path, monkeypatch) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)
    monkeypatch.setenv("HOME", "/host/home/with-codex-auth")
    monkeypatch.setenv("DOCKER_CONFIG", "/host/docker-config")

    environment = SWERuntime(project, service.config.eval.benchmarks).command_env()

    assert environment["HOME"].endswith("/client-home")
    assert environment["DOCKER_CONFIG"].endswith("/client-home/.docker")
    assert environment["HOME"] != "/host/home/with-codex-auth"
    assert environment["DOCKER_CONFIG"] != "/host/docker-config"


def test_official_scorer_process_hides_host_authority_roots(
    tmp_path: Path, monkeypatch
) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)
    runtime = SWERuntime(project, service.config.eval.benchmarks)
    runtime.command_env()
    official_root = project / ".autobugfix/official-case"
    official_root.mkdir(parents=True)
    prediction = official_root.parent / "prediction.jsonl"
    prediction.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_runtime.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    argv = runtime.isolated_official_argv(
        ["python", "-m", "swebench.harness.run_evaluation"],
        cwd=official_root,
        writable_roots=(official_root,),
        readable_roots=(prediction,),
    )
    encoded = "\n".join(argv)

    assert "--unshare-net" in argv
    assert "/home" in argv
    assert "/srv" in argv
    assert str(project / ".autobugfix") in argv
    assert argv[-4:] == [
        "--",
        "python",
        "-m",
        "swebench.harness.run_evaluation",
    ]
    assert any(
        argv[index : index + 3]
        == ["--ro-bind", str(project.resolve()), str(project.resolve())]
        for index in range(len(argv) - 2)
    )
    cache_root = str(runtime.cache_root)
    assert any(
        argv[index : index + 3] == ["--ro-bind", cache_root, cache_root]
        for index in range(len(argv) - 2)
    )
    assert not any(
        argv[index : index + 3] == ["--bind", cache_root, cache_root]
        for index in range(len(argv) - 2)
    )
    assert any(
        argv[index : index + 3]
        == ["--ro-bind", str(prediction), str(prediction)]
        for index in range(len(argv) - 2)
    )


def test_official_scorer_public_network_does_not_reopen_authority_roots(
    tmp_path: Path, monkeypatch
) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)
    runtime = SWERuntime(project, service.config.eval.benchmarks)
    runtime.command_env()
    official_root = project / ".autobugfix/official-case"
    official_root.mkdir(parents=True)
    (project / ".autobugfix-memory").mkdir()
    (project / ".codex").mkdir()
    prediction = official_root.parent / "prediction.jsonl"
    prediction.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_runtime.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    argv = runtime.isolated_official_argv(
        ["python", "-m", "swebench.harness.run_evaluation"],
        cwd=official_root,
        writable_roots=(official_root,),
        readable_roots=(prediction,),
        allow_network=True,
    )

    assert "--unshare-net" not in argv
    assert any(
        argv[index : index + 2]
        == ["--tmpfs", str(project / ".autobugfix-memory")]
        for index in range(len(argv) - 1)
    )
    assert any(
        argv[index : index + 2] == ["--tmpfs", str(project / ".codex")]
        for index in range(len(argv) - 1)
    )
    assert any(
        argv[index : index + 3]
        == ["--ro-bind", str(project.resolve()), str(project.resolve())]
        for index in range(len(argv) - 2)
    )
    assert any(
        argv[index : index + 3]
        == ["--ro-bind", str(prediction), str(prediction)]
        for index in range(len(argv) - 2)
    )


def test_official_scorer_reuses_runtime_already_mounted_from_cache(
    tmp_path: Path, monkeypatch
) -> None:
    project, _ = make_service_project(tmp_path)
    service = EvalBenchmarkService(project)
    runtime = SWERuntime(project, service.config.eval.benchmarks)
    cache_python = (
        runtime.cache_root
        / "client-home/.local/share/uv/python"
    )
    resolved_runtime = cache_python / "cpython-3.13.14-linux-x86_64-gnu"
    (resolved_runtime / "bin").mkdir(parents=True)
    (resolved_runtime / "bin/python3.13").write_text("", encoding="utf-8")
    compatibility_runtime = cache_python / "cpython-3.13-linux-x86_64-gnu"
    compatibility_runtime.symlink_to(resolved_runtime, target_is_directory=True)
    harness_python = project / "harnesses/swebench/.venv/bin/python"
    harness_python.parent.mkdir(parents=True)
    harness_python.symlink_to(compatibility_runtime / "bin/python3.13")
    official_root = project / ".autobugfix/official-case"
    official_root.mkdir(parents=True)
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.swe_runtime.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    argv = runtime.isolated_official_argv(
        ["python", "-m", "swebench.harness.run_evaluation"],
        cwd=official_root,
        writable_roots=(official_root,),
    )

    assert any(
        argv[index : index + 3]
        == ["--ro-bind", str(runtime.cache_root), str(runtime.cache_root)]
        for index in range(len(argv) - 2)
    )
    assert not any(
        argv[index : index + 3]
        == ["--ro-bind", str(resolved_runtime), str(compatibility_runtime)]
        for index in range(len(argv) - 2)
    )


def test_guard_metric_subject_is_derived_from_case_reports() -> None:
    subject = "a" * 40
    assert (
        EvalBenchmarkService._swe_executed_subject_sha(
            [
                {"executed_subject_sha": subject},
                {"executed_subject_sha": subject},
            ],
            subject,
        )
        == subject
    )
    with pytest.raises(EvalBenchmarkServiceError, match="do not prove"):
        EvalBenchmarkService._swe_executed_subject_sha(
            [
                {"executed_subject_sha": subject},
                {"executed_subject_sha": "b" * 40},
            ],
            subject,
        )


def test_swe_seal_publishes_no_holdout_plaintext_or_wave_tokens(
    tmp_path: Path,
) -> None:
    project, _ = make_service_project(tmp_path)
    identity = GuardCodeIdentity(
        trusted_ref="origin/main",
        trusted_commit="a" * 40,
        source_tree="b" * 40,
        machine_constitution_digest="c" * 64,
        harness_digest="d" * 64,
    )
    service = EvalBenchmarkService(
        project,
        guard_authority_resolver=lambda root, trusted_ref: identity,
    )
    external = tmp_path / "external-guard"
    store = service._swe_guard_store(external)
    preparation_id = "swe-prep-seal-test"
    canary = "sealed-owner__private-repo-999"
    private = record_with_digest(
        {
            "schema": "autobugfix-swe-private-cohort-v2",
            "preparation_id": preparation_id,
            "protocol_digest": PROTOCOL,
            "runtime_id": RUNTIME,
            "guard_runtime_id": RUNTIME,
            "docker_authority_digest": "e" * 64,
            "qualification_contract_digest": PROTOCOL,
            "evaluator_runtime_id": RUNTIME,
            "codex_runtime": TREATMENT.to_dict(),
            "subject_runtime": SUBJECT_RUNTIME,
            "h0_subject": "a" * 40,
            "cases": [
                {"role": "optimization", "instance_id": f"visible-{index}"}
                for index in range(10)
            ]
            + [
                {
                    "role": "sealed_holdout",
                    "instance_id": canary if index == 0 else f"private-{index}",
                }
                for index in range(6)
            ],
            "created_at": "2026-07-13T00:00:00Z",
        }
    )
    _, encrypted_sha = store.write_preparation(
        preparation_id,
        private,
        secret=SECRET,
        protocol_digest=PROTOCOL,
        runtime_id=RUNTIME,
    )
    prepared = record_with_digest(
        {
            "schema": "autobugfix-swe-preparation-v2",
            "preparation_id": preparation_id,
            "protocol_digest": PROTOCOL,
            "runtime_id": RUNTIME,
            "guard_runtime_id": RUNTIME,
            "docker_authority_digest": "e" * 64,
            "qualification_contract_digest": PROTOCOL,
            "evaluator_runtime_id": RUNTIME,
            "codex_runtime": TREATMENT.to_dict(),
            "subject_runtime": SUBJECT_RUNTIME,
            "h0_subject": "a" * 40,
            "h0_tree": "b" * 40,
            "optimization_cases": [
                {"case_token": f"visible-{index}"} for index in range(10)
            ],
            "optimization_count": 10,
            "holdout_count": 6,
            "private_bundle_digest": private["record_digest"],
            "encrypted_preparation_sha256": encrypted_sha,
            "waves": {"3": 3, "8": 8, "16": 16},
            "created_at": "2026-07-13T00:00:00Z",
        }
    )
    prepared_path = service.store.write_swe_record(
        "prepared", "swe_experiment_2", preparation_id, prepared
    )

    result = service.seal_swe(
        prepared_path,
        guard_root=external,
        guard_secret=SECRET,
    )

    public_text = Path(result["visible_manifest"]).read_text(encoding="utf-8")
    public = yaml.safe_load(public_text)
    assert canary not in public_text
    assert all(token not in public_text for token in result["wave_tokens"].values())
    assert public["guard"]["holdout_count"] == 6
    assert not list(project.rglob("*prepared-private*"))
    for path in external.rglob("*.abfg"):
        assert canary.encode("utf-8") not in path.read_bytes()


def test_prepare_swe_builds_10_plus_6_without_plaintext_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = make_service_project(tmp_path)
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "guard-test@example.com"],
        ["git", "config", "user.name", "Guard Test"],
        ["git", "add", "."],
        ["git", "commit", "-m", "freeze H0 subject"],
    ):
        subprocess.run(command, cwd=project, check=True, capture_output=True, text=True)
    head = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    service = EvalBenchmarkService(project)
    external = tmp_path / "external-guard"
    store = service._swe_guard_store(external)
    task_types = ["feature", "maintenance", *("bugfix" for _ in range(8))]
    selections = tuple(
        SWEOptimizationSelection(
            instance_id=f"public-repo{index}__case-{index}",
            task_type=task_type,  # type: ignore[arg-type]
        )
        for index, task_type in enumerate(task_types)
    )
    protocol = SimpleNamespace(
        protocol_digest=PROTOCOL,
        qualification_contract_digest=PROTOCOL,
        codex_runtime=TREATMENT,
        optimization_count=10,
        holdout_count=6,
        optimization_cases=selections,
        h0_subject=head,
    )
    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.service.SWEExperimentProtocol.from_yaml",
        lambda path: protocol,
    )

    def make_instance(adapter: str, instance_id: str, repository: str, language: str):
        return SWEInstance(
            adapter=adapter,  # type: ignore[arg-type]
            instance_id=instance_id,
            repository=repository,
            base_commit="a" * 40,
            language=language,
            problem_statement="Repair the repository behavior.",
            hints_text="",
            created_at="2026-07-13T00:00:00Z",
            docker_image=f"example/{instance_id}:pinned",
            gold_patch="diff --git a/a b/a\n",
            test_patch="diff --git a/t b/t\n",
            fail_to_pass=("test",),
            pass_to_pass=(),
            official={"version": "1"},
        )

    optimization_instances = {
        selection.instance_id: make_instance(
            "swebench_verified",
            selection.instance_id,
            f"public/repo-{index % 5}",
            "py",
        )
        for index, selection in enumerate(selections)
    }
    holdout_languages = ("go", "js", "java", "rust", "cpp", "c")
    holdout_instances = {
        f"private-owner{index}__repo-{index}": make_instance(
            "swebench_live",
            f"private-owner{index}__repo-{index}",
            f"private/repo-{index}",
            language,
        )
        for index, language in enumerate(holdout_languages)
    }

    class FakeRunner:
        def __init__(self, adapter: str, instances: dict[str, SWEInstance]) -> None:
            self.adapter = adapter
            self.instances = instances
            self.snapshot = SimpleNamespace(revision="dataset-revision")
            self.runtime = SimpleNamespace(
                runtime_id=RUNTIME,
                docker_authority_digest="e" * 64,
                evaluator_runtime_id=RUNTIME,
                cache_root=external / "runtime-cache",
                config=SimpleNamespace(
                    swebench_commit="swebench-commit",
                    live_commit="live-commit",
                ),
                subject_runtime_identity=lambda treatment: SUBJECT_RUNTIME,
            )

        def load_instance(self, instance_id: str, artifact_root: Path) -> SWEInstance:
            artifact_root.mkdir(parents=True, exist_ok=True)
            return self.instances[instance_id]

        def image_id(
            self, instance: SWEInstance, artifact_root: Path, allow_pull: bool = False
        ) -> str:
            del artifact_root, allow_pull
            return "sha256:" + instance.instance_id.encode().hex()[:64].ljust(64, "0")

    verified_runner = FakeRunner("swebench_verified", optimization_instances)
    live_runner = FakeRunner("swebench_live", holdout_instances)

    def qualification(instance: SWEInstance) -> dict[str, Any]:
        source_digest = (instance.instance_id.encode().hex()[:64]).ljust(64, "1")
        return record_with_digest(
            {
                "schema": "autobugfix-swe-qualification-v4",
                "qualification_contract_digest": PROTOCOL,
                "evaluator_runtime_id": RUNTIME,
                "adapter": instance.adapter,
                "instance_id": instance.instance_id,
                "recorded": True,
                "repository": instance.repository,
                "image_id": live_runner.image_id(instance, tmp_path),
                "source_path": str(tmp_path / "source" / instance.instance_id),
                "source_tree": "b" * 40,
                "source_digest": source_digest,
                "eligible": True,
                "qualified_at": "2026-07-13T00:00:00Z",
            }
        )

    optimization_records = [
        qualification(instance) for instance in optimization_instances.values()
    ]
    private_artifacts = tmp_path / "private-qualification-artifacts"
    private_artifacts.mkdir()
    for instance in holdout_instances.values():
        record = qualification(instance)
        store.write_qualification(
            record,
            private_artifacts,
            secret=SECRET,
            protocol_digest=PROTOCOL,
            runtime_id=RUNTIME,
        )

    monkeypatch.setattr(
        service,
        "_swe_adapter",
        lambda adapter: verified_runner if adapter == "swebench_verified" else live_runner,
    )
    monkeypatch.setattr(service, "_swe_guard_adapter", lambda adapter, root: live_runner)
    monkeypatch.setattr(
        service,
        "_swe_qualification_pool",
        lambda current_protocol, adapter: optimization_records,
    )

    def materialized(instance: SWEInstance, source_root: Path):
        source = source_root / instance.instance_id
        source.mkdir(parents=True, exist_ok=True)
        record = qualification(instance)
        return SWEMaterializedRepository(
            instance_id=instance.instance_id,
            repository=instance.repository,
            base_commit=instance.base_commit,
            source_path=str(source),
            source_tree=str(record["source_tree"]),
            source_digest=str(record["source_digest"]),
            image=instance.docker_image,
            image_id=str(record["image_id"]),
        )

    monkeypatch.setattr(
        service,
        "_validate_swe_qualification_source",
        lambda runner, instance, record, artifact_root: materialized(
            instance, tmp_path / "public-sources"
        ),
    )

    class FakeMaterializer:
        def __init__(self, runner):
            self.runner = runner

        def materialize(self, instance: SWEInstance, artifact_root: Path):
            del artifact_root
            return materialized(instance, external / "runtime-cache/sources")

    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.service.SWEImageMaterializer",
        FakeMaterializer,
    )

    result = service.prepare_swe(
        project / "protocol.yaml",
        guard_root=external,
        guard_secret=SECRET,
    )

    assert result["optimization_count"] == 10
    assert result["holdout_count"] == 6
    prepared = yaml.safe_load(Path(result["prepared_path"]).read_text(encoding="utf-8"))
    assert len(prepared["optimization_cases"]) == 10
    assert all(
        item["schema"] == "autobugfix-swe-optimization-case-v1"
        for item in prepared["optimization_cases"]
    )
    project_bytes = b"".join(
        path.read_bytes() for path in project.rglob("*") if path.is_file()
    )
    assert b"private-owner" not in project_bytes
    assert not list(project.rglob("*prepared-private*"))
