from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from autobugfix.config import load_config
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime, SWERuntimeError
from tests.helpers import make_service_project


def test_swe_runtime_rejects_mutable_or_drifted_upstream(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    config = load_config(project_root)
    config.eval.benchmarks.swe = replace(
        config.eval.benchmarks.swe,
        swebench_commit="main",
    )

    with pytest.raises(SWERuntimeError, match="configuration drift"):
        SWERuntime(project_root, config.eval.benchmarks)


def test_swe_runtime_rejects_repolaunch_submodule_drift(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    config = load_config(project_root)
    config.eval.benchmarks.swe = replace(
        config.eval.benchmarks.swe,
        live_launch_commit="0" * 40,
    )

    with pytest.raises(SWERuntimeError, match="live_launch_commit"):
        SWERuntime(project_root, config.eval.benchmarks)


def test_swe_runtime_rejects_mutable_verified_image_namespace(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    config = load_config(project_root)
    config.eval.benchmarks.swe = replace(
        config.eval.benchmarks.swe,
        verified_namespace="mutable-remote",
    )

    with pytest.raises(SWERuntimeError, match="local-build images"):
        SWERuntime(project_root, config.eval.benchmarks)


def test_swe_runtime_id_binds_lockfile(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    harness = project_root / "harnesses/swebench"
    harness.mkdir(parents=True)
    (harness / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (harness / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (project_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (harness / "scripts").mkdir()
    (harness / "scripts/runner.py").write_text("VERSION = 1\n", encoding="utf-8")
    adapter_source = project_root / "src/autobugfix/eval/benchmarks"
    adapter_source.mkdir(parents=True)
    (adapter_source / "adapter.py").write_text("VERSION = 1\n", encoding="utf-8")
    config = load_config(project_root)
    runtime = SWERuntime(project_root, config.eval.benchmarks)
    first = runtime.runtime_id

    (harness / "uv.lock").write_text("version = 2\n", encoding="utf-8")

    assert first.startswith("sha256:")
    assert runtime.runtime_id != first


def test_dataset_snapshot_reader_rejects_digest_drift(tmp_path: Path) -> None:
    project_root, _ = make_service_project(tmp_path)
    harness = project_root / "harnesses/swebench"
    harness.mkdir(parents=True)
    (harness / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (harness / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    config = load_config(project_root)
    runtime = SWERuntime(project_root, config.eval.benchmarks)
    root = runtime._snapshot_root(
        "swebench_verified",
        config.eval.benchmarks.swe.verified_dataset_revision,
    )
    root.mkdir(parents=True)
    data = root / "test.jsonl"
    data.write_text('{"instance_id":"one"}\n', encoding="utf-8")
    from autobugfix.eval.benchmarks.models import digest_file
    from autobugfix.eval.benchmarks.swe_runtime import SWEDatasetSnapshot
    import yaml

    snapshot = SWEDatasetSnapshot(
        adapter="swebench_verified",
        dataset=config.eval.benchmarks.swe.verified_dataset,
        revision=config.eval.benchmarks.swe.verified_dataset_revision,
        split="test",
        path=str(data.resolve()),
        sha256=digest_file(data),
        row_count=1,
    )
    (root / "snapshot.yaml").write_text(
        yaml.safe_dump(snapshot.to_dict(), sort_keys=False),
        encoding="utf-8",
    )
    assert runtime.read_dataset_snapshot("swebench_verified").row_count == 1

    data.write_text('{"instance_id":"forged"}\n', encoding="utf-8")
    with pytest.raises(SWERuntimeError, match="digest drift"):
        runtime.read_dataset_snapshot("swebench_verified")
