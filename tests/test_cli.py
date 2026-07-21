from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autobugfix.cli import build_parser, main
from autobugfix.models import CodexResult
from tests.helpers import make_service_project


def test_cli_doctor_uses_real_config(tmp_path, monkeypatch, capsys):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.chdir(project_root)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "repo toy_repo" in output
    assert ".autobugfix/worktrees/toy_repo" in output
    assert "roles:" in output
    assert "writer:" in output
    assert "sandbox: workspace-write" in output


def test_eval_cli_returns_nonzero_when_case_or_harness_fails(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.yaml").write_text(
        yaml.safe_dump({"failed_count": 1, "harness_error_count": 0}), encoding="utf-8"
    )

    def fake_run_eval(*args, **kwargs):
        del args, kwargs
        return Path(run_dir)

    monkeypatch.setattr("autobugfix.cli.run_eval", fake_run_eval)
    assert main(["eval", "run", "--dataset", "cases.jsonl", "--out", str(tmp_path)]) == 1


def test_eval_cli_does_not_expose_a_production_fake_backend():
    with pytest.raises(SystemExit):
        main(
            [
                "eval",
                "run",
                "--dataset",
                "cases.jsonl",
                "--out",
                ".autobugfix-evals",
                "--model-mode",
                "fake",
            ]
        )


def test_memory_approve_skill_cli_requires_human_review_binding() -> None:
    args = build_parser().parse_args(
        [
            "memory",
            "approve-skill",
            "proposal-1",
            "--skill-name",
            "preserve-verifier-evidence",
            "--description",
            "Preserve verifier evidence before accepting a repair.",
            "--note",
            "reviewed",
            "--confirm-review-digest",
            "a" * 64,
        ]
    )

    assert args.memory_action == "approve-skill"
    assert args.skill_name == "preserve-verifier-evidence"
    assert args.confirm_review_digest == "a" * 64


def test_codex_probe_uses_a_private_log_leaf_below_hidden_controller(
    tmp_path, monkeypatch
):
    project_root, _ = make_service_project(tmp_path)
    captured = {}

    def fake_run(self, request):
        del self
        captured["request"] = request
        return CodexResult(text="ready", raw={"module": "test"}, exit_code=0)

    monkeypatch.setattr("autobugfix.cli.CodexSDKBackend.run", fake_run)
    monkeypatch.chdir(project_root)

    assert main(["codex", "probe-role", "--role", "evaluator", "--execute"]) == 0

    request = captured["request"]
    controller = project_root / ".autobugfix/controller"
    assert request.raw_log_path.parent.is_relative_to(controller / "probes")
    assert controller in request.hidden_paths
    assert request.raw_log_path.parent not in request.hidden_paths


def test_defects4j_run_case_cli_forwards_bounded_production_options(
    tmp_path, monkeypatch, capsys
):
    project_root, _ = make_service_project(tmp_path)
    manifest = project_root / "seed.yaml"
    manifest.write_text("schema_version: 1\n", encoding="utf-8")
    captured = {}

    def fake_run_case(self, manifest_path, **kwargs):
        del self
        captured["manifest"] = manifest_path
        captured.update(kwargs)
        return {"report": {"decision": "pass"}, "run_dir": "/tmp/eval-run"}

    monkeypatch.setattr(
        "autobugfix.cli.EvalBenchmarkService.run_case",
        fake_run_case,
    )
    monkeypatch.chdir(project_root)

    exit_code = main(
        [
            "eval",
            "benchmark",
            "run-case",
            "--manifest",
            str(manifest),
            "--case",
            "d4j-jsoup-2",
            "--out",
            ".autobugfix/eval-runs",
            "--run-id",
            "h0-jsoup-2",
            "--model",
            "gpt-5.4-mini",
            "--max-attempts",
            "2",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "manifest": manifest,
        "case_selector": "d4j-jsoup-2",
        "out_root": Path(".autobugfix/eval-runs"),
        "run_id": "h0-jsoup-2",
        "model": "gpt-5.4-mini",
        "max_attempts": 2,
    }
    assert "decision: pass" in capsys.readouterr().out


def test_formal_swe_optimization_unresolved_is_a_valid_measurement(
    tmp_path, monkeypatch, capsys
):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(
        "autobugfix.cli.EvalBenchmarkService.run_swe_optimization_case",
        lambda *args, **kwargs: {
            "resolved": False,
            "harness_error": False,
            "record_digest": "a" * 64,
        },
    )

    exit_code = main(
        [
            "eval",
            "benchmark",
            "run-swe-optimization",
            "--manifest",
            "manifest.yaml",
            "--case",
            "public-case",
            "--study-binding",
            "binding.yaml",
            "--run-id",
            "h-general-public-case",
        ]
    )

    assert exit_code == 0
    assert "resolved: false" in capsys.readouterr().out


def test_raw_codex_baseline_cli_delegates_all_state_changes_to_service(
    tmp_path, monkeypatch, capsys
):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.chdir(project_root)
    calls = []

    def fake_prepare(self, protocol, source_manifest, h0_report):
        del self
        calls.append(("prepare", protocol, source_manifest, h0_report))
        return {"prepared_manifest_digest": "a" * 64}

    def fake_pilot(self, protocol, source_manifest, **kwargs):
        del self
        calls.append(("pilot", protocol, source_manifest, kwargs))
        return {"summary": {"harness_error_count": 0}}

    def fake_run(self, manifest, **kwargs):
        del self
        calls.append(("run", manifest, kwargs))
        return {"summary": {"status": "completed"}}

    def fake_report(run_dir, h0_report):
        calls.append(("report", run_dir, h0_report))
        return {"record_digest": "b" * 64}

    monkeypatch.setattr(
        "autobugfix.cli.RawCodexBaselineService.prepare", fake_prepare
    )
    monkeypatch.setattr(
        "autobugfix.cli.RawCodexBaselineService.pilot", fake_pilot
    )
    monkeypatch.setattr(
        "autobugfix.cli.RawCodexBaselineService.run_formal", fake_run
    )
    monkeypatch.setattr(
        "autobugfix.cli.RawCodexBaselineService.report",
        staticmethod(fake_report),
    )

    assert main(
        [
            "eval",
            "baseline",
            "prepare-raw-codex",
            "--protocol",
            "protocol.yaml",
            "--source-manifest",
            "source.yaml",
            "--h0-report",
            "h0.yaml",
        ]
    ) == 0
    assert main(
        [
            "eval",
            "baseline",
            "pilot-raw-codex",
            "--protocol",
            "protocol.yaml",
            "--source-manifest",
            "source.yaml",
            "--case",
            "d4j-gson-2",
            "--run-id",
            "pilot-1",
        ]
    ) == 0
    assert main(
        [
            "eval",
            "baseline",
            "run-raw-codex",
            "--manifest",
            "prepared.yaml",
            "--run-id",
            "formal-1",
        ]
    ) == 0
    assert main(
        [
            "eval",
            "baseline",
            "report-raw-codex",
            "--run-dir",
            "formal-1",
            "--h0-report",
            "h0.yaml",
        ]
    ) == 0
    assert [call[0] for call in calls] == ["prepare", "pilot", "run", "report"]
    capsys.readouterr()
