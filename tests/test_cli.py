from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autobugfix.cli import main
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
