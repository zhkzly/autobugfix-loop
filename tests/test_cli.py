from __future__ import annotations

from autobugfix.cli import main
from tests.helpers import make_service_project


def test_cli_doctor_uses_real_config(tmp_path, monkeypatch, capsys):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.chdir(project_root)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "repo toy_repo" in output
    assert ".autobugfix/worktrees/toy_repo" in output
