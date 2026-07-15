from __future__ import annotations

import subprocess

import pytest
import yaml

from autobugfix.config import ConfigError, load_config
from autobugfix.models import TaskRecord
from autobugfix.role_config import resolve_role
from autobugfix.task_store import TaskStore
from tests.helpers import make_service_project


def test_config_defaults_and_task_store_round_trip(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    cfg = load_config(project_root)
    assert cfg.repo("toy_repo").worktree_root == project_root / ".autobugfix/worktrees/toy_repo"
    assert cfg.codex.default_model is None
    assert cfg.operator.experiments.default_profile == "real-e2e"
    real_profile = cfg.operator.experiments.profiles["real-e2e"]
    assert real_profile["network_access"] is True
    assert "scripts/real_repository_acceptance.py" in real_profile["commands"][0]["argv"]
    assert cfg.operator.experiment_lines.root == (
        project_root / ".autobugfix/operator-line-worktrees"
    )
    assert cfg.operator.experiment_lines.branch_template == "experiment/{study_id}-main"
    assert cfg.operator.budgets.allowed_waves == (3, 8, 16)
    assert cfg.operator.budgets.allowed_primary_models == ("gpt-5.4-mini",)
    assert cfg.operator.budgets.max_calls_by_wave == {3: 30, 8: 80, 16: 160}
    assert cfg.operator.budgets.default_case_concurrency == 1
    assert not cfg.operator.budgets.allow_model_fallback
    assert cfg.eval.benchmarks.cache_root == project_root / ".autobugfix/benchmark-cache"
    assert cfg.eval.benchmarks.trusted_case_root == (
        project_root / ".autobugfix/trusted-eval-cases"
    )
    assert cfg.eval.benchmarks.visible_manifest_root == (
        project_root / ".autobugfix/eval-manifests"
    )
    assert cfg.eval.benchmarks.defects4j.image == "autobugfix/defects4j:3.0.1"
    assert (
        cfg.eval.benchmarks.defects4j.verifier_image
        == "autobugfix/defects4j-verifier:3.0.1"
    )
    assert cfg.eval.benchmarks.defects4j.platform == "linux/amd64"
    assert cfg.eval.benchmarks.defects4j.preflight_repetitions == 2
    assert cfg.eval.benchmarks.defects4j.memory_limit == "8g"
    assert cfg.eval.benchmarks.defects4j.cpu_limit == 4.0
    assert cfg.eval.benchmarks.defects4j.pids_limit == 1024
    assert cfg.eval.benchmarks.guard.trusted_ref == "origin/main"
    store = TaskStore(project_root, cfg.task_root)
    record = TaskRecord(task_id="t1", repo_id="toy_repo", title="title", body="body", state="ready")
    store.create(record)
    store.add_context("t1", "log", "evidence")
    assert store.load("t1").state == "ready"
    assert store.events("t1")[0].kind == "task_created"


def test_role_config_defaults_legacy_models_and_repo_override(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["codex"]["writer_model"] = "legacy-writer"
    data["codex"]["roles"] = {
        "evaluator": {
            "model": "global-evaluator",
            "timeout_seconds": 77,
        }
    }
    data["repos"]["toy_repo"]["codex"] = {
        "roles": {
            "writer": {
                "model": "repo-writer",
                "timeout_seconds": 33,
            }
        }
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    cfg = load_config(project_root)
    global_writer = resolve_role(cfg, "writer")
    writer = resolve_role(cfg, "writer", repo_id="toy_repo")
    evaluator = resolve_role(cfg, "evaluator", repo_id="toy_repo")
    assert global_writer.model == "legacy-writer"
    assert writer.model == "repo-writer"
    assert writer.sandbox == "workspace-write"
    assert writer.approval_mode == "auto_review"
    assert writer.timeout_seconds == 33
    assert any("autobugfix-writer" in str(path) for path in writer.skill_paths)
    assert evaluator.model == "global-evaluator"
    assert evaluator.sandbox == "read-only"
    assert evaluator.timeout_seconds == 77


def test_config_rejects_disabling_isolated_codex_role_runtime(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["codex"]["role_runtime"]["enabled"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="isolated CODEX_HOME"):
        load_config(project_root)


def test_config_rejects_remote_guard_docker_endpoint(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("eval", {}).setdefault("benchmarks", {}).setdefault(
        "guard", {}
    )["docker_host"] = "tcp://guard.example.test:2376"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="absolute local unix"):
        load_config(project_root)


def test_config_rejects_benchmark_roots_in_source_or_target_repo(tmp_path):
    project_root, main = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("eval", {}).setdefault("benchmarks", {})["cache_root"] = (
        "src/bench-cache"
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="gitignored .autobugfix"):
        load_config(project_root)

    data["eval"]["benchmarks"]["cache_root"] = str(main)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="repo.toy_repo.main_checkout"):
        load_config(project_root)


@pytest.mark.parametrize(
    ("section", "value", "message"),
    [
        ("allowed_waves", [3, 16], "exactly"),
        ("allowed_primary_models", [], "must be exactly"),
        (
            "allowed_primary_models",
            ["gpt-5.4-mini", "gpt-5.3-codex-spark"],
            "must be exactly",
        ),
        ("max_calls_by_wave", {3: 30, 8: 0, 16: 160}, "positive limits"),
        ("default_case_concurrency", 2, "must remain 1"),
        ("max_case_concurrency", 2, "must remain 1"),
        ("allow_model_fallback", True, "must remain false"),
    ],
)
def test_config_rejects_weakened_experiment_budget_contract(
    tmp_path,
    section,
    value,
    message,
):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("operator", {}).setdefault("budgets", {})[section] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(project_root)


def test_config_rejects_experiment_authority_root_inside_candidate_worktrees(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    operator = data.setdefault("operator", {})
    operator.setdefault("worktrees", {})["root"] = ".autobugfix/operator-worktrees"
    operator.setdefault("experiment_lines", {})["checkpoint_root"] = (
        ".autobugfix/operator-worktrees/checkpoints"
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="runtime roots must not overlap"):
        load_config(project_root)


def test_config_rejects_nested_operator_authority_roots(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    operator = data.setdefault("operator", {})
    operator.setdefault("state", {})["root"] = ".autobugfix/operator-control"
    operator.setdefault("artifacts", {})["root"] = (
        ".autobugfix/operator-control/artifacts"
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="runtime roots must not overlap"):
        load_config(project_root)


@pytest.mark.parametrize(
    ("section", "root_value"),
    [
        ("state", ".autobugfix-memory/operator-state"),
        ("worktrees", ".autobugfix/tasks/operator-worktrees"),
    ],
)
def test_config_rejects_operator_roots_overlapping_memory_or_execution(
    tmp_path, section, root_value
):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("operator", {}).setdefault(section, {})["root"] = root_value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="protected state or data plane"):
        load_config(project_root)


def test_config_protects_real_git_common_dir_from_linked_worktree(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "."], cwd=project_root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=project_root, check=True)
    linked = tmp_path / "linked-control"
    subprocess.run(
        ["git", "worktree", "add", "-b", "linked", str(linked), "HEAD"],
        cwd=project_root,
        check=True,
    )
    config_path = linked / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data.setdefault("operator", {}).setdefault("state", {})["root"] = str(
        project_root / ".git/operator-state"
    )
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="project_git"):
        load_config(linked)


def test_config_rejects_experiment_line_template_without_study_identity(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("operator", {}).setdefault("experiment_lines", {})[
        "branch_template"
    ] = "experiment/shared-main"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="must contain.*study_id"):
        load_config(project_root)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (
            ("defects4j", "framework_revision"),
            "floating-main",
            "framework_revision must remain pinned",
        ),
        (
            ("defects4j", "timezone"),
            "UTC",
            "timezone must remain America/Los_Angeles",
        ),
        ((None, "command_timeout_seconds"), 0, "command_timeout_seconds must be positive"),
        (("defects4j", "preflight_repetitions"), 0, "preflight_repetitions must be positive"),
    ],
)
def test_config_rejects_weakened_defects4j_contract(tmp_path, path, value, message):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    benchmarks = data.setdefault("eval", {}).setdefault("benchmarks", {})
    section, key = path
    target = benchmarks.setdefault(section, {}) if section else benchmarks
    target[key] = value
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(project_root)


def test_config_rejects_legacy_defects4j_host_runtime_and_authority_overlap(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    benchmarks = data.setdefault("eval", {}).setdefault("benchmarks", {})
    defects4j = benchmarks.setdefault("defects4j", {})
    defects4j["java_home"] = ".autobugfix/host-tools/java"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="host runtime fields are unsupported"):
        load_config(project_root)

    defects4j.pop("java_home")
    data["eval"]["benchmarks"]["trusted_case_root"] = (
        ".autobugfix/operator-worktrees/trusted-cases"
    )
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="must not overlap Operator runtime roots"):
        load_config(project_root)


def test_config_rejects_unpinned_defects4j_platform(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    config_path = project_root / ".autobugfix/config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data.setdefault("eval", {}).setdefault("benchmarks", {}).setdefault(
        "defects4j", {}
    )["platform"] = "linux/arm64"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="platform must remain linux/amd64"):
        load_config(project_root)
