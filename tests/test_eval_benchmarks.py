from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    BenchmarkSeedManifest,
    DoctorCheck,
    DoctorReport,
    EligibilityReceipt,
    SealedBenchmarkManifest,
    SealedCaseReference,
    digest_file,
    record_with_digest,
    verify_record,
)
from autobugfix.eval.benchmarks.issues import IssueEvidenceError, IssueEvidenceFetcher
from autobugfix.eval.benchmarks.guard import (
    decrypt_json,
    encrypt_json,
    signed_metric,
    verify_signed_metric,
)
from autobugfix.eval.benchmarks.authority import (
    GuardAuthorityError,
    GuardCodeIdentity,
    resolve_guard_code_identity,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.verify import (
    cleanup_test_artifacts,
    unexpected_failures,
    validate_changed_paths,
)
from autobugfix.eval.benchmarks.defects4j import Defects4JRuntime
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.eval.benchmarks.service import EvalBenchmarkServiceError
from autobugfix.eval.benchmarks.store import BenchmarkStore
from autobugfix.config import load_config
from autobugfix.eval.models import EvalCase
from tests.helpers import make_service_project, run
from autobugfix.models import DEFECTS4J_FRAMEWORK_REVISION, utc_now


def fake_guard_identity() -> GuardCodeIdentity:
    return GuardCodeIdentity(
        trusted_ref="origin/main",
        trusted_commit="a" * 40,
        source_tree="b" * 40,
        machine_constitution_digest="c" * 64,
        harness_digest="d" * 64,
    )


def seed_data() -> dict[str, object]:
    optimization_cases = []
    waves = [3, 3, 8, 8, 8, 16, 16, 16, 16, 16]
    for index, wave in enumerate(waves, start=1):
        optimization_cases.append(
            {
                "case_id": f"d4j-gson-{index}",
                "project": "Gson" if index <= 5 else "Jsoup",
                "bug_id": index,
                "first_wave": wave,
            }
        )
    return {
        "schema_version": 2,
        "manifest_id": "defects4j-v3-study",
        "benchmark": "defects4j",
        "framework_revision": DEFECTS4J_FRAMEWORK_REVISION,
        "dataset_revision": "defects4j-v3.0.1",
        "optimization_cases": optimization_cases,
        "holdout_count": 6,
    }


def test_benchmark_seed_enforces_split_and_nested_wave_contract():
    seed = BenchmarkSeedManifest.from_dict(seed_data())
    assert seed.manifest_digest
    assert len(seed.optimization_cases) == 10

    bad = seed_data()
    bad["holdout_project_pool"] = ["Gson", "JacksonCore", "JacksonXml"]
    with pytest.raises(BenchmarkContractError, match="forbids public"):
        BenchmarkSeedManifest.from_dict(bad)


def test_sealed_manifest_enforces_total_waves_and_hides_holdout_identity():
    optimization = tuple(
        SealedCaseReference(
            case_token=f"visible-{index}",
            case_id=f"visible-{index}",
            project="Gson" if index <= 5 else "Jsoup",
            bug_id=index,
            role="optimization",
            first_wave=3 if index <= 2 else 8 if index <= 5 else 16,
            receipt_path=f"/trusted/visible-{index}.yaml",
            receipt_digest=f"{index:064x}",
        )
        for index in range(1, 11)
    )
    holdout_waves = (3, 8, 8, 16, 16, 16)
    holdout = tuple(
        SealedCaseReference(
            case_token=f"opaque-{index}",
            case_id=f"opaque-{index}",
            project=("JacksonCore", "JacksonDatabind", "JacksonXml")[(index - 1) % 3],
            bug_id=index,
            role="sealed_holdout",
            first_wave=holdout_waves[index - 1],
            receipt_path=f"/trusted/opaque-{index}.yaml",
            receipt_digest=f"{index + 10:064x}",
        )
        for index in range(1, 7)
    )
    manifest = SealedBenchmarkManifest(
        manifest_id="sealed-study",
        seed_manifest_digest="seed-digest",
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        dataset_revision="defects4j-v3.0.1",
        runtime_id="sha256:" + "a" * 64,
        verifier_runtime_id="sha256:" + "b" * 64,
        optimization_cases=optimization,
        holdout_cases=holdout,
        created_at=utc_now(),
    )

    trusted = manifest.to_dict()
    visible = yaml.safe_dump(manifest.visible_projection(), sort_keys=False)
    assert SealedBenchmarkManifest.from_dict(trusted) == manifest
    assert "JacksonCore" not in visible
    assert "JacksonDatabind" not in visible
    assert "JacksonXml" not in visible
    assert "opaque-1" in visible
    assert "bug_id: 1" in visible  # Optimization identity remains public.

    trusted["runtime_id"] = "sha256:" + "b" * 64
    with pytest.raises(BenchmarkContractError, match="digest mismatch"):
        SealedBenchmarkManifest.from_dict(trusted)

    bad = seed_data()
    bad["optimization_cases"][2]["first_wave"] = 16  # type: ignore[index]
    with pytest.raises(BenchmarkContractError, match="cumulative counts"):
        BenchmarkSeedManifest.from_dict(bad)


def test_doctor_and_command_evidence_are_digest_bound(tmp_path):
    command = run_command(
        ["/bin/sh", "-c", "printf ok"],
        cwd=tmp_path,
        artifact_dir=tmp_path / "command",
        name="probe",
        timeout_seconds=10,
        env={"TZ": "America/Los_Angeles"},
    )
    assert command.passed
    assert Path(command.stdout_path).read_text(encoding="utf-8") == "ok"
    verify_record(command.to_dict())

    report = DoctorReport(
        adapter="defects4j",
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        started_at=utc_now(),
        finished_at=utc_now(),
        checks=(DoctorCheck("java", True, "11", "11.0.31"),),
    )
    assert report.passed
    verify_record(report.to_dict())


def test_guard_envelope_and_metric_reject_wrong_key_and_tampering(tmp_path):
    secret = "guard secret with enough entropy"
    aad = b"study:guard-1"
    envelope = tmp_path / "bundle.abfg"
    payload = record_with_digest({"sealed": ["private-case"]})
    encrypt_json(payload, envelope, secret=secret, aad=aad)

    assert decrypt_json(envelope, secret=secret, aad=aad) == payload
    assert envelope.stat().st_mode & 0o077 == 0
    with pytest.raises(BenchmarkContractError, match="authentication failed"):
        decrypt_json(envelope, secret="different secret value", aad=aad)
    with pytest.raises(BenchmarkContractError, match="authority binding"):
        decrypt_json(envelope, secret=secret, aad=b"another-study")

    metric = signed_metric({"guard_id": "guard-1", "passed_count": 2}, secret)
    verify_signed_metric(metric, secret)
    forged = dict(metric)
    forged["passed_count"] = 3
    forged = record_with_digest(
        {key: value for key, value in forged.items() if key != "record_digest"}
    )
    with pytest.raises(BenchmarkContractError, match="signature mismatch"):
        verify_signed_metric(forged, secret)


def test_guard_code_identity_requires_clean_checkout_at_trusted_ref(tmp_path):
    root = tmp_path / "control"
    root.mkdir()
    run(["git", "init", "-b", "main"], cwd=root)
    run(["git", "config", "user.email", "guard@example.invalid"], cwd=root)
    run(["git", "config", "user.name", "Guard"], cwd=root)
    constitution = root / "src/autobugfix/operator/constitution.yaml"
    constitution.parent.mkdir(parents=True)
    constitution.write_text("version: 4\nproject: {name: Autobugfix}\n", encoding="utf-8")
    harness = root / "src/autobugfix/eval/benchmarks/service.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("# trusted guard harness\n", encoding="utf-8")
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-m", "trusted guard"], cwd=root)

    identity = resolve_guard_code_identity(root, "main")
    assert identity.trusted_commit == run(
        ["git", "rev-parse", "HEAD"], cwd=root
    ).stdout.strip()
    assert identity.to_dict()["record_digest"] == identity.identity_digest

    harness.write_text("# dirty candidate guard\n", encoding="utf-8")
    with pytest.raises(GuardAuthorityError, match="uncommitted"):
        resolve_guard_code_identity(root, "main")


def test_benchmark_store_rejects_tampered_and_external_receipts(tmp_path):
    store = BenchmarkStore(tmp_path / "trusted", tmp_path / "visible")
    receipt = EligibilityReceipt.pending(
        receipt_id="receipt-1",
        manifest_digest="manifest",
        case_id="d4j-gson-1",
        project="Gson",
        bug_id=1,
        role="optimization",
        first_wave=3,
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        dataset_revision="v3.0.1",
        status="harness_error",
        reason="runtime unavailable",
    )
    path = store.write_receipt(receipt)
    assert store.read_receipt(path) == receipt

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["reason"] = "forged"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(BenchmarkContractError, match="digest mismatch"):
        store.read_receipt(path)

    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump(receipt.to_dict()), encoding="utf-8")
    with pytest.raises(BenchmarkContractError, match="outside trusted"):
        store.read_receipt(outside)

    with pytest.raises(BenchmarkContractError, match="safe path component"):
        store.write_visible_yaml("../escape", "manifest.yaml", {"value": 1})


def test_eligible_receipt_revalidates_gold_issue_snapshot_and_command_logs(tmp_path):
    trusted = tmp_path / "trusted"
    visible = tmp_path / "visible"
    cache = tmp_path / "cache"
    store = BenchmarkStore(trusted, visible, cache)
    issue = trusted / "preflight/issue.yaml"
    issue.parent.mkdir(parents=True)
    issue_data = record_with_digest({"title": "Real issue", "body": "Failure"})
    issue.write_text(yaml.safe_dump(issue_data, sort_keys=False), encoding="utf-8")
    gold = trusted / "preflight/gold.patch"
    gold.write_text("diff --git a/A.java b/A.java\n", encoding="utf-8")
    gold.chmod(0o600)
    command = run_command(
        ["/bin/sh", "-c", "printf command-evidence"],
        cwd=tmp_path,
        artifact_dir=trusted / "preflight/command",
        name="command",
        timeout_seconds=10,
    )
    snapshot = cache / "cases/receipt/source"
    snapshot.mkdir(parents=True)
    (snapshot / "src").mkdir()
    (snapshot / "src/Main.java").write_text("class Main {}\n", encoding="utf-8")
    run(["git", "init", "--initial-branch=main", str(snapshot)])
    run(["git", "-C", str(snapshot), "config", "user.email", "guard@example.invalid"])
    run(["git", "-C", str(snapshot), "config", "user.name", "Guard"])
    run(["git", "-C", str(snapshot), "add", "-A"])
    run(["git", "-C", str(snapshot), "commit", "-m", "snapshot"])
    head = run(["git", "-C", str(snapshot), "rev-parse", "HEAD"]).stdout.strip()
    receipt = EligibilityReceipt(
        receipt_id="receipt-eligible",
        manifest_digest="manifest",
        case_id="d4j-lang-1",
        project="Lang",
        bug_id=1,
        role="optimization",
        first_wave=3,
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        dataset_revision="v3.0.1",
        runtime_id="sha256:" + "a" * 64,
        verifier_runtime_id="sha256:" + "b" * 64,
        issue_evidence_digest=str(issue_data["record_digest"]),
        issue_evidence_path=str(issue),
        buggy_revision="buggy",
        fixed_revision="fixed",
        triggering_tests=("example.Test::fails",),
        baseline_failing_tests=(),
        source_roots=("src",),
        sanitized_repo_path=str(snapshot),
        sanitized_base_sha=head,
        gold_patch_path=str(gold),
        gold_patch_sha256=digest_file(gold),
        commands=(command.to_dict(),),
        status="eligible",
        reason="",
        created_at=utc_now(),
    )
    path = store.write_receipt(receipt)
    assert store.read_receipt(path) == receipt

    gold.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(BenchmarkContractError, match="gold patch digest mismatch"):
        store.read_receipt(path)


def test_defects4j_doctor_fails_closed_and_retains_report_when_docker_is_missing(
    tmp_path, monkeypatch
):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.delenv("AUTOBUGFIX_DOCKER_BIN", raising=False)
    monkeypatch.setattr("autobugfix.eval.benchmarks.defects4j.shutil.which", lambda _: None)
    service = EvalBenchmarkService(project_root)
    report = service.doctor("defects4j")

    assert report["passed"] is False
    assert Path(report["report_path"]).is_file()
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["docker"]["passed"] is False
    assert checks["framework_info"]["observed"] == "not run"


def test_defects4j_runtime_rejects_unsafe_project_names(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    runtime = Defects4JRuntime(load_config(project_root).eval.benchmarks)
    with pytest.raises(Exception, match="invalid Defects4J project"):
        runtime.active_bug("../Missing", 1)


def test_defects4j_container_contract_forces_utf8_locale(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    runtime = Defects4JRuntime(load_config(project_root).eval.benchmarks)
    runtime._docker_bin = "/usr/bin/docker"
    runtime._image_id = "sha256:" + "a" * 64

    argv = runtime._docker_run_argv(["java", "-version"])

    assert "LANG=C.UTF-8" in argv
    assert "LC_ALL=C.UTF-8" in argv
    assert "--network" in argv and "none" in argv
    assert "--memory" in argv and "8g" in argv
    assert "--cpus" in argv and "4.0" in argv
    assert "--pids-limit" in argv and "1024" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "no-new-privileges" in argv
    assert argv[-2:] == ["java", "-version"]


def test_verifier_image_removes_gold_hints_but_keeps_test_runtime_metadata():
    dockerfile = (
        Path(__file__).parents[1] / "containers/defects4j/Dockerfile"
    ).read_text(encoding="utf-8")

    for hidden in (
        "*/patches/*",
        "*/modified_classes/*",
        "*/loaded_classes/*",
        "*/relevant_tests/*",
        "*/trigger_tests/*",
    ):
        assert hidden in dockerfile
    for required in ("active-bugs.csv", "commit-db", "dir-layout.csv"):
        assert f"-name '{required}'" not in dockerfile


def test_issue_evidence_fetcher_normalizes_github_without_generated_text(tmp_path, monkeypatch):
    fetcher = IssueEvidenceFetcher(timeout_seconds=10)
    monkeypatch.setattr(
        fetcher,
        "_request_json",
        lambda url: {
            "title": "Parser drops a token",
            "body": "Reproducer attached: https://example.invalid/failure.png",
        },
    )
    evidence = fetcher.fetch(
        report_url="https://github.com/example/project/issues/17",
        report_id="17",
        artifact_dir=tmp_path / "issue",
    )
    assert evidence.tracker == "github"
    assert evidence.title == "Parser drops a token"
    assert evidence.attachment_uris == ("https://example.invalid/failure.png",)
    assert Path(evidence.raw_path).is_file()
    verify_record(evidence.to_dict())
    tracker, endpoint = fetcher._endpoint(
        "https://github.com/example/project/pull/17", "17"
    )
    assert tracker == "github"
    assert endpoint.endswith("/issues/17")


def test_issue_evidence_fetcher_rejects_unknown_tracker_and_missing_title(tmp_path, monkeypatch):
    fetcher = IssueEvidenceFetcher(timeout_seconds=10)
    with pytest.raises(IssueEvidenceError, match="unsupported issue tracker"):
        fetcher.fetch(
            report_url="https://example.invalid/issues/1",
            report_id="1",
            artifact_dir=tmp_path / "unsupported",
        )
    monkeypatch.setattr(fetcher, "_request_json", lambda url: {"body": "missing title"})
    with pytest.raises(IssueEvidenceError, match="no title"):
        fetcher.fetch(
            report_url="https://github.com/example/project/issues/1",
            report_id="1",
            artifact_dir=tmp_path / "missing-title",
        )


def test_issue_evidence_fetcher_uses_real_github_structured_html_fallback(
    tmp_path, monkeypatch
):
    fetcher = IssueEvidenceFetcher(timeout_seconds=10)

    def fail_api(url):
        raise IssueEvidenceError("rate limited")

    monkeypatch.setattr(fetcher, "_request_json", fail_api)
    monkeypatch.setattr(
        fetcher,
        "_request_github_html",
        lambda url: (
            {
                "@type": "DiscussionForumPosting",
                "headline": "Real upstream title",
                "articleBody": "Real upstream body",
            },
            b"<html>raw upstream response</html>",
        ),
    )
    evidence = fetcher.fetch(
        report_url="https://github.com/example/project/issues/1",
        report_id="1",
        artifact_dir=tmp_path / "fallback",
    )
    assert evidence.title == "Real upstream title"
    assert evidence.body == "Real upstream body"
    assert Path(evidence.raw_path).suffix == ".html"
    assert Path(evidence.raw_path).read_bytes().startswith(b"<html>")


def test_preflight_stops_after_failed_doctor_without_checkout(tmp_path, monkeypatch):
    project_root, _ = make_service_project(tmp_path)
    monkeypatch.delenv("AUTOBUGFIX_DOCKER_BIN", raising=False)
    monkeypatch.setattr("autobugfix.eval.benchmarks.defects4j.shutil.which", lambda _: None)
    manifest = tmp_path / "seed.yaml"
    manifest.write_text(yaml.safe_dump(seed_data(), sort_keys=False), encoding="utf-8")

    service = EvalBenchmarkService(project_root)
    with pytest.raises(EvalBenchmarkServiceError, match="doctor failed"):
        service.preflight(manifest, case_selector="d4j-gson-1")
    assert not (project_root / ".autobugfix/trusted-eval-cases/preflight-runs").exists()


def test_defects4j_failing_test_parser_and_deterministic_history_free_snapshot(tmp_path):
    project_root, _ = make_service_project(tmp_path)
    runtime = Defects4JRuntime(load_config(project_root).eval.benchmarks)
    failing = tmp_path / "failing_tests"
    failing.write_text(
        "--- example.ParserTest::dropsToken\njava.lang.AssertionError\n"
        "--- example.ParserTest::dropsToken\n",
        encoding="utf-8",
    )
    assert runtime._failing_tests(failing) == ("example.ParserTest::dropsToken",)
    assert runtime._triggering_tests(
        "example.ParserTest::dropsToken\nexample.OtherTest::fails\n"
    ) == (
        "example.ParserTest::dropsToken",
        "example.OtherTest::fails",
    )

    heads = []
    for index in (1, 2):
        source = tmp_path / f"snapshot-{index}"
        source.mkdir()
        (source / "src").mkdir()
        (source / "src/Main.java").write_text("class Main {}\n", encoding="utf-8")
        (source / ".defects4j.config").write_text("pid=Lang\nvid=1b\n", encoding="utf-8")
        (source / "defects4j.build.properties").write_text(
            "d4j.classes.modified=org.example.Secret\n",
            encoding="utf-8",
        )
        (source / ".git").mkdir()
        (source / ".git/exposed-fixed-tag").write_text("secret\n", encoding="utf-8")
        commands = []
        heads.append(
            runtime._sanitize_snapshot(
                source,
                artifact_root=tmp_path / f"snapshot-artifacts-{index}",
                commands=commands,
            )
        )
        assert not (source / ".git/exposed-fixed-tag").exists()
        assert not (source / ".defects4j.config").exists()
        assert not (source / "defects4j.build.properties").exists()
        assert commands[-1].name == "snapshot-git-head"
    assert heads[0] == heads[1]


def test_defects4j_repair_contract_allows_only_stable_fixed_baseline_failures():
    triggering = ("example.TriggerTest::fails",)
    baseline = ("example.EnvironmentTest::requiresNetwork",)
    assert Defects4JRuntime._repair_contract(
        triggering,
        (baseline + triggering, baseline + triggering),
        (baseline, baseline),
    ) == baseline
    assert unexpected_failures(baseline, baseline) == ()
    assert unexpected_failures(
        baseline + ("example.NewRegression::fails",), baseline
    ) == ("example.NewRegression::fails",)

    with pytest.raises(Exception, match="fixed failure baseline was unstable"):
        Defects4JRuntime._repair_contract(
            triggering,
            (baseline + triggering, baseline + triggering),
            (baseline, ()),
        )
    with pytest.raises(Exception, match="fixed revision still fails a triggering test"):
        Defects4JRuntime._repair_contract(
            triggering,
            (triggering, triggering),
            (triggering, triggering),
        )


def test_defects4j_verifier_scope_and_cleanup_preserve_writer_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src/main/java").mkdir(parents=True)
    (repo / "src/main/java/Main.java").write_text("class Main {}\n", encoding="utf-8")
    (repo / ".gitignore").write_text("target/\n", encoding="utf-8")
    run_command(
        ["git", "init", "--initial-branch=main"],
        cwd=repo,
        artifact_dir=tmp_path / "git-init",
        name="git-init",
        timeout_seconds=10,
    )
    run_command(
        ["git", "add", "-A"],
        cwd=repo,
        artifact_dir=tmp_path / "git-add",
        name="git-add",
        timeout_seconds=10,
    )
    run_command(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "base",
        ],
        cwd=repo,
        artifact_dir=tmp_path / "git-commit",
        name="git-commit",
        timeout_seconds=10,
    )
    (repo / "src/main/java/Main.java").write_text("class Main { int x; }\n", encoding="utf-8")
    before = ("src/main/java/Main.java",)
    assert validate_changed_paths(before, ("src/main/java",)) == ()
    assert validate_changed_paths(("pom.xml",), ("src/main/java",)) == ("pom.xml",)
    assert validate_changed_paths(("failing_tests",), ("src/main/java",)) == (
        "failing_tests",
    )

    (repo / "target/cache").mkdir(parents=True)
    (repo / "target/cache/writer.bin").write_bytes(b"writer")
    ignored_before = ("target/cache/writer.bin",)
    (repo / "target/classes").mkdir(parents=True)
    (repo / "target/classes/Main.class").write_bytes(b"generated")
    (repo / "all_tests").write_text("generated\n", encoding="utf-8")
    cleanup_test_artifacts(repo, before, ignored_before)

    assert (repo / "src/main/java/Main.java").read_text(encoding="utf-8") == "class Main { int x; }\n"
    assert (repo / "target/cache/writer.bin").read_bytes() == b"writer"
    assert not (repo / "target/classes/Main.class").exists()
    assert not (repo / "all_tests").exists()


def test_visible_defects4j_case_uses_managed_verifier_without_oracle_paths(
    tmp_path,
):
    project_root, _ = make_service_project(tmp_path)
    config = load_config(project_root)
    source = config.eval.benchmarks.cache_root / "cases/receipt/source"
    source.mkdir(parents=True)
    issue = tmp_path / "issue.yaml"
    issue.write_text(
        yaml.safe_dump(
            {
                "title": "Parser regression",
                "body": "Text escapes the script element.",
                "attachment_uris": ["https://example.invalid/reproducer.txt"],
            }
        ),
        encoding="utf-8",
    )
    failure = tmp_path / "buggy-failing-tests"
    failure.write_text(
        "--- example.ParserTest::regression\njava.lang.AssertionError: escaped token\n",
        encoding="utf-8",
    )
    manifest = BenchmarkSeedManifest.from_dict(seed_data())
    receipt = EligibilityReceipt(
        receipt_id="receipt",
        manifest_digest=manifest.manifest_digest,
        case_id="d4j-gson-1",
        project="Gson",
        bug_id=1,
        role="optimization",
        first_wave=3,
        framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
        dataset_revision="defects4j-v3.0.1",
        runtime_id="sha256:" + "a" * 64,
        verifier_runtime_id="sha256:" + "b" * 64,
        issue_evidence_digest="issue-digest",
        issue_evidence_path=str(issue),
        buggy_revision="buggy-secret",
        fixed_revision="fixed-secret",
        triggering_tests=("example.ParserTest::regression",),
        baseline_failing_tests=("secret.EnvironmentTest::fails",),
        source_roots=("src/main/java",),
        sanitized_repo_path=str(source),
        sanitized_base_sha="base-sha",
        gold_patch_path=str(config.eval.benchmarks.trusted_case_root / "gold.patch"),
        gold_patch_sha256="gold-digest",
        commands=(),
        status="eligible",
        reason="",
        created_at=utc_now(),
        failure_evidence_path=str(failure),
        failure_evidence_sha256=digest_file(failure),
        reproduction_command="defects4j test -w /workspace",
    )
    row = EvalBenchmarkService(project_root)._visible_case_row(receipt)
    encoded = yaml.safe_dump(row, sort_keys=False)

    assert row["execution"]["test_command"].startswith("managed:defects4j:")
    assert "verifier_contract" not in row["defects4j"]
    assert str(config.eval.benchmarks.cache_root) in row["repository"]["worktree_path"]
    assert str(config.eval.benchmarks.trusted_case_root) not in encoded
    assert "fixed-secret" not in encoded
    assert "gold.patch" not in encoded
    assert "secret.EnvironmentTest" not in encoded
    assert "java.lang.AssertionError: escaped token" in row["task"]["problem_statement"]
    assert "defects4j test -w /workspace" in row["task"]["problem_statement"]
    assert EvalCase.from_row(row).source.adapter == "defects4j"


def test_seal_encrypts_holdout_authority_and_deidentifies_visible_projection(
    tmp_path,
    monkeypatch,
):
    project_root, _ = make_service_project(tmp_path)
    manifest_path = project_root / "seed.yaml"
    manifest_path.write_text(
        yaml.safe_dump(seed_data(), sort_keys=False),
        encoding="utf-8",
    )

    class FakeDefects4JRuntime:
        def __init__(self, config):
            self.config = config
            self.runtime_id = "sha256:" + "d" * 64
            self.verifier_runtime_id = "sha256:" + "e" * 64
            self.docker_bin = "/usr/bin/docker"

        def bind_inspected_runtime(
            self,
            *,
            docker_bin,
            runtime_id,
            verifier_runtime_id,
        ):
            self.docker_bin = docker_bin
            self.runtime_id = runtime_id
            self.verifier_runtime_id = verifier_runtime_id

        def doctor(self, artifact_root):
            artifact_root.mkdir(parents=True, exist_ok=True)
            return DoctorReport(
                adapter="defects4j",
                framework_revision=DEFECTS4J_FRAMEWORK_REVISION,
                runtime_id=self.runtime_id,
                verifier_runtime_id=self.verifier_runtime_id,
                started_at=utc_now(),
                finished_at=utc_now(),
                checks=(DoctorCheck("docker", True, "available", "available"),),
            )

        def active_bug_ids(self, project):
            del project
            return (1, 2, 3, 4)

        def preflight_case(self, manifest, case, *, role, first_wave, artifact_root):
            artifact_root.mkdir(parents=True, exist_ok=True)
            source = self.config.cache_root / "fake-cases" / case.case_id
            source.mkdir(parents=True, exist_ok=True)
            return EligibilityReceipt(
                receipt_id=f"{case.case_id}-receipt",
                manifest_digest=manifest.manifest_digest,
                case_id=case.case_id,
                project=case.project,
                bug_id=case.bug_id,
                role=role,
                first_wave=first_wave,
                framework_revision=manifest.framework_revision,
                dataset_revision=manifest.dataset_revision,
                runtime_id=self.runtime_id,
                verifier_runtime_id=self.verifier_runtime_id,
                issue_evidence_digest="issue-digest",
                issue_evidence_path="unavailable",
                buggy_revision="buggy",
                fixed_revision="fixed",
                triggering_tests=("example.Test::fails",),
                baseline_failing_tests=(),
                source_roots=("src/main/java",),
                sanitized_repo_path=str(source),
                sanitized_base_sha="a" * 40,
                gold_patch_path=str(
                    self.config.trusted_case_root / case.case_id / "gold.patch"
                ),
                gold_patch_sha256="b" * 64,
                commands=(),
                status="eligible",
                reason="",
                created_at=utc_now(),
            )

    monkeypatch.setattr(
        "autobugfix.eval.benchmarks.service.Defects4JRuntime",
        FakeDefects4JRuntime,
    )
    identity = fake_guard_identity()
    service = EvalBenchmarkService(
        project_root,
        guard_authority_resolver=lambda root, trusted_ref: identity,
    )
    secret = "correct horse battery staple"
    result = service.seal(
        manifest_path,
        guard_secret=secret,
        holdout_projects=("JacksonCore", "JacksonDatabind", "JacksonXml"),
    )

    assert result["optimization_count"] == 10
    assert result["sealed_holdout_count"] == 6
    assert result["waves"] == {3: 3, 8: 8, 16: 16}
    visible = Path(result["visible_manifest"]).read_text(encoding="utf-8")
    assert "JacksonCore" not in visible
    assert "JacksonDatabind" not in visible
    assert "JacksonXml" not in visible
    assert len(Path(result["optimization_dataset"]).read_text(encoding="utf-8").splitlines()) == 10

    config = load_config(project_root)
    trusted_paths = list(
        (config.eval.benchmarks.trusted_case_root / "manifests").glob("**/*.yaml")
    )
    assert trusted_paths == []
    public, bundle, _ = service._load_guard_bundle(
        Path(result["visible_manifest"]),
        secret,
    )
    assert public["guard"]["bundle_sha256"] == result["encrypted_bundle_sha256"]
    assert public["guard"]["code_identity"]["record_digest"] == identity.identity_digest
    assert len(bundle.holdout_cases) == 6
    assert {item.project for item in bundle.holdout_cases} == {
        "JacksonCore",
        "JacksonDatabind",
        "JacksonXml",
    }
    guard_files = list(
        (config.eval.benchmarks.trusted_case_root / "guard").glob("**/*.abfg")
    )
    assert len(guard_files) == 2
    assert all(path.stat().st_mode & 0o077 == 0 for path in guard_files)
    receipt_cases = {
        path.parent.name
        for path in (config.eval.benchmarks.trusted_case_root / "receipts").glob(
            "defects4j/*/*.yaml"
        )
    }
    assert receipt_cases == {item["case_id"] for item in seed_data()["optimization_cases"]}

    def fake_execute(receipt, **kwargs):
        private_root = kwargs["private_root"]
        (private_root / "raw-sdk.jsonl").write_text("{}\n", encoding="utf-8")
        return {
            "receipt_digest": receipt.to_dict()["record_digest"],
            "report": {"decision": "pass"},
            "summary": {"passed_count": 1},
            "run_dir": str(private_root / "run"),
            "dataset": str(private_root / "dataset.jsonl"),
        }

    monkeypatch.setattr(service, "_execute_receipt", fake_execute)
    wave_token = public["guard"]["waves"]["3"]["token"]
    study_binding = record_with_digest(
        {
            "schema": "autobugfix-guard-study-binding-v1",
            "kind": "BASELINE",
            "study_id": "study-1",
            "subject_sha": identity.trusted_commit,
        }
    )
    guard_result = service.guard_run(
        Path(result["visible_manifest"]),
        wave_token=wave_token,
        out_root=project_root / ".autobugfix/guard-results",
        run_id="guard-wave-3",
        guard_secret=secret,
        study_binding=study_binding,
    )
    assert guard_result["case_count"] == 1
    assert guard_result["passed_count"] == 1
    metric = yaml.safe_load(
        Path(guard_result["metric_receipt"]).read_text(encoding="utf-8")
    )
    verify_signed_metric(metric, secret)
    assert metric["schema"] == "autobugfix-guard-metric-v2"
    assert metric["study_binding"] == study_binding
    assert metric["guard_code_identity"]["trusted_commit"] == "a" * 40
    assert metric["executed_subject_sha"] == identity.trusted_commit
    metric_text = yaml.safe_dump(metric, sort_keys=False)
    assert "JacksonCore" not in metric_text
    assert "holdout-" not in metric_text
    assert Path(guard_result["encrypted_artifacts"]).stat().st_mode & 0o077 == 0

    candidate_binding = record_with_digest(
        {
            "schema": "autobugfix-guard-study-binding-v1",
            "kind": "CANDIDATE",
            "study_id": "study-1",
            "subject_sha": "f" * 40,
        }
    )
    with pytest.raises(EvalBenchmarkServiceError, match="isolated subject broker"):
        service.guard_run(
            Path(result["visible_manifest"]),
            wave_token=wave_token,
            out_root=project_root / ".autobugfix/guard-results",
            run_id="guard-candidate-mismatch",
            guard_secret=secret,
            study_binding=candidate_binding,
        )

    with pytest.raises(EvalBenchmarkServiceError, match="invalid opaque"):
        service.guard_run(
            Path(result["visible_manifest"]),
            wave_token="wave-3-invalid-token-value",
            out_root=project_root / ".autobugfix/guard-results",
            run_id="invalid-token",
            guard_secret=secret,
        )
