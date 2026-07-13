from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.config import load_config
from autobugfix.eval.adapters import SubmissionApplyError, create_patch_checkout
from autobugfix.eval.baselines.isolation import (
    RawCodexProcessSandbox,
    RawProcessRun,
    RunnerMetadata,
)
from autobugfix.eval.baselines.models import (
    PreparedRawBaselineManifest,
    RawBaselineCase,
    RawBaselineSeedManifest,
    RawProcessObservation,
    raw_case_from_prepared,
)
from autobugfix.eval.baselines.reporting import write_raw_baseline_report
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    EligibilityReceipt,
    PreparedEvaluationManifest,
    digest_file,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.eval.benchmarks.store import BenchmarkStore
from autobugfix.eval.benchmarks.verify import (
    changed_paths,
    official_oracle_for_receipt,
    validate_changed_paths,
)
from autobugfix.git_utils import rev_parse, run_git
from autobugfix.models import utc_now
from autobugfix.worktree import git_diff_with_untracked


class RawCodexBaselineError(RuntimeError):
    pass


class RawCodexBaselineHarnessError(RawCodexBaselineError):
    pass


def _write_text_once(path: Path, text: str, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _write_yaml_once(path: Path, data: Mapping[str, Any]) -> Path:
    return _write_text_once(path, yaml.safe_dump(dict(data), sort_keys=False))


def _write_json_once(path: Path, data: Mapping[str, Any]) -> Path:
    return _write_text_once(
        path,
        json.dumps(dict(data), ensure_ascii=True, sort_keys=True) + "\n",
    )


def _read_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RawCodexBaselineError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RawCodexBaselineError(f"{label} must be a mapping")
    return dict(data)


def _read_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawCodexBaselineHarnessError(
            f"cannot read {label}: {exc}"
        ) from exc
    if not isinstance(data, Mapping):
        raise RawCodexBaselineHarnessError(f"{label} must be an object")
    return dict(data)


def _visible_problem_statement(
    receipt: EligibilityReceipt,
) -> tuple[str, list[dict[str, str]]]:
    """Project Defects4J receipt evidence for the standalone comparator."""

    issue_path = Path(receipt.issue_evidence_path)
    attachments: list[dict[str, str]] = []
    title = f"Repair Defects4J {receipt.project}-{receipt.bug_id}"
    body = ""
    if issue_path.is_file():
        data = yaml.safe_load(issue_path.read_text(encoding="utf-8")) or {}
        if isinstance(data, Mapping):
            title = str(data.get("title") or title)
            body = str(data.get("body") or "")
            for uri in data.get("attachment_uris") or []:
                attachments.append(
                    {
                        "kind": "upstream-attachment",
                        "uri": str(uri),
                        "description": "Attachment referenced by the upstream issue",
                    }
                )
    trigger_text = "\n".join(f"- {item}" for item in receipt.triggering_tests)
    failure_text = ""
    if receipt.failure_evidence_path != "unavailable":
        failure_path = Path(receipt.failure_evidence_path)
        if failure_path.is_file():
            failure_text = failure_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
    reproduction = (
        receipt.reproduction_command
        if receipt.reproduction_command != "unavailable"
        else "defects4j test -w /workspace"
    )
    problem = "\n\n".join(
        part
        for part in (
            title,
            body,
            "Official triggering tests:\n" + trigger_text,
            "Pinned reproduction command:\n" + reproduction,
            (
                "Observed buggy failure output and stack trace:\n" + failure_text
                if failure_text
                else ""
            ),
            "Modify production source only. The Execution verifier will run only the declared visible triggering tests.",
        )
        if part.strip()
    )
    return problem, attachments


class RawCodexBaselineService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        benchmark = self.config.eval.benchmarks
        self.store = BenchmarkStore(
            benchmark.trusted_case_root,
            benchmark.visible_manifest_root,
            benchmark.cache_root,
        )
        self.sandbox = RawCodexProcessSandbox(
            self.project_root,
            benchmark.raw_codex,
            hidden_roots=(
                benchmark.trusted_case_root,
                benchmark.visible_manifest_root,
                benchmark.cache_root,
            ),
        )

    def _run_directory(self, out_root: Path, run_id: str) -> Path:
        runtime_root = self.config.eval.benchmarks.raw_codex.runtime_root.resolve()
        output = out_root.resolve()
        if not output.is_relative_to(runtime_root):
            raise RawCodexBaselineError(
                "Raw baseline output must remain inside its configured runtime root"
            )
        run_dir = output / safe_component(run_id, "run_id")
        run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        return run_dir

    def _read_prepared_manifest(self, path: Path) -> PreparedRawBaselineManifest:
        resolved = path.resolve()
        manifest_root = (
            self.config.eval.benchmarks.trusted_case_root / "manifests"
        ).resolve()
        if not resolved.is_relative_to(manifest_root):
            raise RawCodexBaselineError(
                "prepared Raw baseline manifest is outside trusted benchmark root"
            )
        manifest = PreparedRawBaselineManifest.from_yaml(resolved)
        digest = str(manifest.to_dict()["record_digest"])
        if (
            resolved.name != f"raw-codex-{digest}.yaml"
            or resolved.parent.name != manifest.manifest_id
        ):
            raise RawCodexBaselineError(
                "prepared Raw baseline manifest path does not match its identity"
            )
        return manifest

    def _h0_report(self, path: Path) -> dict[str, Any]:
        report = _read_yaml_mapping(path.resolve(), "H0 evaluation report")
        try:
            verify_record(report)
        except BenchmarkContractError as exc:
            raise RawCodexBaselineError(f"invalid H0 evaluation report: {exc}") from exc
        if report.get("schema") != "autobugfix-formal-evaluation-report-v1":
            raise RawCodexBaselineError("unsupported H0 evaluation report schema")
        if int(report.get("case_count") or 0) != 16:
            raise RawCodexBaselineError("H0 report must contain exactly 16 cases")
        raw_cases = report.get("cases")
        if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
            raise RawCodexBaselineError("H0 report cases must be a list")
        case_ids = {
            str(item.get("case_id") or "")
            for item in raw_cases
            if isinstance(item, Mapping)
        }
        if len(case_ids) != 16:
            raise RawCodexBaselineError("H0 report case IDs are incomplete")
        return report

    def _git_identity(self, *, require_clean: bool) -> tuple[str, str, str]:
        status = run_git(
            self.project_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
        ).stdout
        if require_clean and status.strip():
            raise RawCodexBaselineError(
                "formal Raw baseline preparation requires a clean checkout"
            )
        branch = run_git(
            self.project_root,
            ["branch", "--show-current"],
            check=True,
        ).stdout.strip()
        if not branch or branch in {"main", "master"}:
            raise RawCodexBaselineError(
                "Raw baseline must run from a named non-main branch"
            )
        return (
            rev_parse(self.project_root, "HEAD"),
            rev_parse(self.project_root, "HEAD^{tree}"),
            status,
        )

    def _runner_source_digest(self, git_ref: str = "HEAD") -> str:
        try:
            relative = self.config.eval.benchmarks.raw_codex.runner_project.relative_to(
                self.project_root
            )
        except ValueError as exc:
            raise RawCodexBaselineError(
                "Raw runner project must be inside the Autobugfix source tree"
            ) from exc
        tree = run_git(
            self.project_root,
            ["ls-tree", "-r", git_ref, "--", relative.as_posix()],
            check=True,
        ).stdout.splitlines()
        if not tree:
            raise RawCodexBaselineError(
                "Raw runner project is not committed at the selected Git ref"
            )
        return digest_payload({"git_tree_entries": tree})

    def _validate_protocol_config(self, seed: RawBaselineSeedManifest) -> None:
        configured = self.config.eval.benchmarks.raw_codex
        observed = {
            "model": configured.model,
            "sdk_version": configured.sdk_version,
            "reasoning_effort": configured.reasoning_effort,
            "service_tier": configured.service_tier,
            "approval_mode": configured.approval_mode,
            "sandbox": configured.sandbox,
            "network_access": configured.network_access,
            "timeout_seconds": configured.timeout_seconds,
        }
        expected = {
            "model": seed.model,
            "sdk_version": seed.sdk_version,
            "reasoning_effort": seed.reasoning_effort,
            "service_tier": seed.service_tier,
            "approval_mode": seed.approval_mode,
            "sandbox": seed.sandbox,
            "network_access": seed.network_access,
            "timeout_seconds": seed.timeout_seconds,
        }
        if observed != expected:
            raise RawCodexBaselineError(
                "Raw baseline protocol differs from resolved configuration"
            )

    @staticmethod
    def _validate_source_binding(
        seed: RawBaselineSeedManifest,
        source: PreparedEvaluationManifest,
    ) -> None:
        if seed.source_evaluation_manifest_id != source.manifest_id:
            raise RawCodexBaselineError(
                "Raw protocol source manifest ID differs from prepared H0"
            )
        if seed.dataset_revision != source.dataset_revision:
            raise RawCodexBaselineError(
                "Raw protocol dataset revision differs from prepared H0"
            )
        if seed.expected_case_count != len(source.cases):
            raise RawCodexBaselineError(
                "Raw protocol case count differs from prepared H0"
            )

    @staticmethod
    def _validate_h0_binding(
        source: PreparedEvaluationManifest,
        report: Mapping[str, Any],
    ) -> None:
        if str(report.get("prepared_manifest_digest") or "") != str(
            source.to_dict()["record_digest"]
        ):
            raise RawCodexBaselineError(
                "H0 report is not bound to the source evaluation manifest"
            )
        if str(report.get("subject_sha") or "") != source.subject_sha:
            raise RawCodexBaselineError("H0 report subject differs from source H0")
        report_cases = {
            str(item.get("case_id") or "")
            for item in report.get("cases") or []
            if isinstance(item, Mapping)
        }
        if report_cases != {case.case_id for case in source.cases}:
            raise RawCodexBaselineError(
                "H0 report cases differ from the source evaluation manifest"
            )

    def prepare(
        self,
        protocol_path: Path,
        source_manifest_path: Path,
        h0_report_path: Path,
    ) -> dict[str, Any]:
        seed = RawBaselineSeedManifest.from_yaml(protocol_path.resolve())
        source = self.store.read_prepared_evaluation_manifest(
            source_manifest_path.resolve()
        )
        report = self._h0_report(h0_report_path)
        self._validate_h0_binding(source, report)
        self._validate_protocol_config(seed)
        self._validate_source_binding(seed, source)
        development = set(seed.development_case_ids)
        source_case_ids = {case.case_id for case in source.cases}
        if not development.issubset(source_case_ids):
            raise RawCodexBaselineError(
                "Raw development cases are not present in the H0 suite"
            )
        for source_case in source.cases:
            self.store.read_receipt(
                self.store.receipt_path(
                    source_case.case_id,
                    source_case.receipt_digest,
                )
            )
        doctor = EvalBenchmarkService(self.project_root).doctor("defects4j")
        if not doctor["passed"]:
            raise RawCodexBaselineError("Defects4J doctor failed")
        if (
            doctor["runtime_id"] != source.runtime_id
            or doctor["verifier_runtime_id"] != source.verifier_runtime_id
        ):
            raise RawCodexBaselineError(
                "current Docker identities differ from the H0 evaluator"
            )
        runner = self.sandbox.ensure_runner_environment()
        if (
            runner.sdk_version != seed.sdk_version
            or runner.approval_mode != seed.approval_mode
            or runner.sandbox != seed.sandbox
            or runner.network_access is not seed.network_access
        ):
            raise RawCodexBaselineError(
                "Raw SDK runtime authority differs from protocol"
            )
        git_sha, git_tree, _ = self._git_identity(require_clean=True)
        lock_path = self.config.eval.benchmarks.raw_codex.runner_project / "uv.lock"
        prepared = PreparedRawBaselineManifest(
            manifest_id=seed.manifest_id,
            seed_manifest_digest=seed.manifest_digest,
            source_evaluation_manifest_digest=str(
                source.to_dict()["record_digest"]
            ),
            h0_report_digest=str(report["record_digest"]),
            benchmark=source.benchmark,
            framework_revision=source.framework_revision,
            dataset_revision=source.dataset_revision,
            runtime_id=source.runtime_id,
            verifier_runtime_id=source.verifier_runtime_id,
            runner_git_sha=git_sha,
            runner_git_tree=git_tree,
            runner_source_digest=self._runner_source_digest(),
            runner_install_digest=runner.package_digest,
            runner_lock_digest=digest_file(lock_path),
            sdk_version=runner.sdk_version,
            prompt_template_digest=runner.prompt_template_digest,
            config_digest=digest_file(self.project_root / ".autobugfix/config.yaml"),
            model=seed.model,
            reasoning_effort=seed.reasoning_effort,
            service_tier=seed.service_tier,
            approval_mode=seed.approval_mode,
            sandbox=seed.sandbox,
            network_access=seed.network_access,
            timeout_seconds=seed.timeout_seconds,
            turns_per_case=seed.turns_per_case,
            concurrency=seed.concurrency,
            cases=tuple(
                raw_case_from_prepared(
                    case,
                    development_case_ids=development,
                )
                for case in source.cases
            ),
            prepared_at=utc_now(),
        )
        data = prepared.to_dict()
        path = self.store.write_trusted_manifest(
            prepared.manifest_id,
            f"raw-codex-{data['record_digest']}.yaml",
            data,
        )
        return {
            "prepared_manifest": str(path),
            "prepared_manifest_digest": data["record_digest"],
            "runner_git_sha": prepared.runner_git_sha,
            "runner_source_digest": prepared.runner_source_digest,
            "prompt_template_digest": prepared.prompt_template_digest,
            "case_count": len(prepared.cases),
            "primary_count": sum(case.cohort == "primary" for case in prepared.cases),
            "development_count": sum(
                case.cohort == "development" for case in prepared.cases
            ),
        }

    @staticmethod
    def _source_fingerprint(source: Path) -> dict[str, str]:
        return {
            "head": rev_parse(source, "HEAD"),
            "tree": rev_parse(source, "HEAD^{tree}"),
            "status": run_git(
                source,
                ["status", "--porcelain=v1", "--untracked-files=all"],
                check=True,
            ).stdout,
        }

    @staticmethod
    def _clone_source(source: Path, base_commit: str, destination: Path) -> Path:
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                str(source),
                str(destination),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if clone.returncode != 0:
            raise RawCodexBaselineHarnessError(
                clone.stderr.strip() or "cannot clone Raw baseline repository"
            )
        run_git(destination, ["checkout", "--detach", base_commit], check=True)
        run_git(destination, ["clean", "-fdx"], check=True)
        return destination

    @staticmethod
    def _case_bundle(receipt: EligibilityReceipt) -> dict[str, Any]:
        problem, attachment_records = _visible_problem_statement(receipt)
        return record_with_digest(
            {
                "schema": "raw-codex-sdk-case-v1",
                "case_id": receipt.case_id,
                "benchmark": "defects4j",
                "dataset_revision": receipt.dataset_revision,
                "base_commit": receipt.sanitized_base_sha,
                "problem_statement": problem,
                "expected_behavior": "The declared visible triggering tests pass after the repair.",
                "visible_evidence": [],
                "attachments": [
                    str(item.get("uri") or "")
                    for item in attachment_records
                    if str(item.get("uri") or "").strip()
                ],
            }
        )

    @staticmethod
    def _validate_observation(
        process: RawProcessRun,
        case_bundle: Mapping[str, Any],
        case: RawBaselineCase,
        *,
        model: str,
        reasoning_effort: str,
        service_tier: str | None,
        runner: RunnerMetadata,
    ) -> RawProcessObservation:
        if not process.process_result_path.is_file():
            raise RawCodexBaselineHarnessError(
                "Raw SDK process did not write process-result.json"
            )
        data = _read_json_mapping(
            process.process_result_path,
            "Raw SDK process result",
        )
        try:
            observation = RawProcessObservation.from_dict(data)
        except BenchmarkContractError as exc:
            raise RawCodexBaselineHarnessError(
                f"Raw SDK process result violates its contract: {exc}"
            ) from exc
        events_path = process.sdk_artifact_root / "events.jsonl"
        stderr_path = process.sdk_artifact_root / "stderr.log"
        request_path = process.sdk_artifact_root / "request.json"
        event_count = sum(
            1
            for _ in events_path.open(encoding="utf-8", errors="replace")
        ) if events_path.is_file() else -1
        if (
            observation.case_id != case.case_id
            or observation.case_digest != case_bundle["record_digest"]
            or observation.sdk_version != runner.sdk_version
            or observation.model != model
            or observation.reasoning_effort != reasoning_effort
            or observation.service_tier != service_tier
            or observation.approval_mode != runner.approval_mode
            or observation.sandbox != runner.sandbox
            or observation.network_access is not runner.network_access
            or observation.prompt_template_digest != runner.prompt_template_digest
            or data.get("sdk_package") != "openai-codex"
            or not request_path.is_file()
            or not events_path.is_file()
            or not stderr_path.is_file()
            or digest_file(request_path) != observation.request_sha256
            or digest_file(events_path) != observation.events_sha256
            or digest_file(stderr_path) != observation.stderr_sha256
            or event_count != observation.event_count
        ):
            raise RawCodexBaselineHarnessError(
                "Raw SDK process result disagrees with trusted launch inputs or logs"
            )
        if observation.status != "completed" or process.return_code != 0:
            raise RawCodexBaselineHarnessError(
                "Raw SDK process failed before completing its single turn: "
                + observation.error
            )
        return observation

    @staticmethod
    def _process_artifact_digests(process: RawProcessRun) -> dict[str, str]:
        candidates = {
            "worker_stdout": process.stdout_path,
            "worker_stderr": process.stderr_path,
            "codex_config": process.stdout_path.parent / "codex-config.toml",
            "sdk_request": process.sdk_artifact_root / "request.json",
            "sdk_events": process.sdk_artifact_root / "events.jsonl",
            "sdk_stderr": process.sdk_artifact_root / "stderr.log",
            "sdk_result": process.process_result_path,
        }
        return {
            name: digest_file(path) if path.is_file() else "missing"
            for name, path in candidates.items()
        }

    def _run_case(
        self,
        case: RawBaselineCase,
        receipt: EligibilityReceipt,
        *,
        run_dir: Path,
        manifest_digest: str,
        runner: RunnerMetadata,
        model: str,
        reasoning_effort: str,
        service_tier: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        started = time.monotonic()
        case_dir = run_dir / case.case_id
        case_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        source = Path(receipt.sanitized_repo_path).resolve()
        source_before = self._source_fingerprint(source)
        if source_before["head"] != receipt.sanitized_base_sha or source_before["status"]:
            raise RawCodexBaselineHarnessError(
                "sanitized source changed before Raw generation"
            )
        worktree = self._clone_source(
            source,
            receipt.sanitized_base_sha,
            case_dir / "worktree",
        )
        input_root = case_dir / "visible-input"
        input_root.mkdir(mode=0o700)
        bundle = self._case_bundle(receipt)
        bundle_path = _write_json_once(input_root / "case.json", bundle)
        process = self.sandbox.run(
            runner_metadata=runner,
            worktree=worktree,
            input_root=input_root,
            case_bundle=bundle_path,
            artifact_root=case_dir / "process",
            model=model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            timeout_seconds=timeout_seconds,
        )
        observation: RawProcessObservation | None = None
        process_status = "timed_out" if process.timed_out else "completed"
        if not process.timed_out:
            observation = self._validate_observation(
                process,
                bundle,
                case,
                model=model,
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
                runner=runner,
            )
            process_status = observation.status

        patch = git_diff_with_untracked(worktree, receipt.sanitized_base_sha)
        patch_path = _write_text_once(case_dir / "generated.diff", patch)
        paths = changed_paths(worktree)
        violations = validate_changed_paths(paths, receipt.source_roots)
        process_digests = self._process_artifact_digests(process)
        submission = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-submission-v1",
                "manifest_digest": manifest_digest,
                "case_id": case.case_id,
                "cohort": case.cohort,
                "receipt_digest": case.receipt_digest,
                "base_commit": receipt.sanitized_base_sha,
                "case_bundle_digest": bundle["record_digest"],
                "model": model,
                "reasoning_effort": reasoning_effort,
                "service_tier": service_tier,
                "approval_mode": runner.approval_mode,
                "sandbox": runner.sandbox,
                "network_access": runner.network_access,
                "sdk_version": runner.sdk_version,
                "prompt_template_digest": runner.prompt_template_digest,
                "process_status": process_status,
                "timed_out": process.timed_out,
                "process_return_code": process.return_code,
                "process_result_digest": (
                    observation.record_digest if observation is not None else None
                ),
                "process_artifact_digests": process_digests,
                "patch_sha256": digest_file(patch_path),
                "changed_paths": list(paths),
                "path_policy_passed": not violations,
                "path_policy_violations": list(violations),
                "source_fingerprint": source_before,
                "frozen_at": utc_now(),
            }
        )
        _write_yaml_once(case_dir / "submission.yaml", submission)
        expected_noninterference = {
            "patch_sha256": submission["patch_sha256"],
            "process_artifact_digests": process_digests,
            "case_bundle_sha256": digest_file(bundle_path),
            "worktree_patch_sha256": digest_payload({"patch": patch}),
            "source_fingerprint": source_before,
        }

        (case_dir / "oracle").mkdir(mode=0o700)
        try:
            candidate = create_patch_checkout(
                source,
                receipt.sanitized_base_sha,
                patch,
                case_dir / "oracle" / "candidate",
            )
        except SubmissionApplyError as exc:
            raise RawCodexBaselineHarnessError(
                "trusted Raw patch could not be applied to its source snapshot: "
                + str(exc)
            ) from exc
        official = official_oracle_for_receipt(
            receipt,
            self.config.eval.benchmarks,
        ).run(
            candidate,
            case_dir / "oracle" / "score",
            timeout_seconds=self.config.eval.benchmarks.command_timeout_seconds,
        )
        oracle_data = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-oracle-result-v1",
                "case_id": case.case_id,
                "submission_digest": submission["record_digest"],
                "command": official.command,
                "exit_code": official.exit_code,
                "outcome": official.outcome,
                "started_at": official.started_at,
                "finished_at": official.finished_at,
                "stdout_sha256": digest_payload({"text": official.stdout}),
                "stderr_sha256": digest_payload({"text": official.stderr}),
            }
        )
        _write_yaml_once(case_dir / "oracle-result.yaml", oracle_data)
        if official.outcome == "harness_error":
            raise RawCodexBaselineHarnessError(
                f"official evaluator harness error for {case.case_id}"
            )

        observed_noninterference = {
            "patch_sha256": digest_file(patch_path),
            "process_artifact_digests": self._process_artifact_digests(process),
            "case_bundle_sha256": digest_file(bundle_path),
            "worktree_patch_sha256": digest_payload(
                {
                    "patch": git_diff_with_untracked(
                        worktree,
                        receipt.sanitized_base_sha,
                    )
                }
            ),
            "source_fingerprint": self._source_fingerprint(source),
        }
        unchanged = observed_noninterference == expected_noninterference
        noninterference = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-noninterference-v1",
                "case_id": case.case_id,
                "submission_digest": submission["record_digest"],
                "unchanged": unchanged,
                "expected": expected_noninterference,
                "observed": observed_noninterference,
                "checked_at": utc_now(),
            }
        )
        _write_yaml_once(
            case_dir / "oracle-noninterference.yaml",
            noninterference,
        )
        if not unchanged:
            raise RawCodexBaselineHarnessError(
                f"official scoring changed frozen Raw state for {case.case_id}"
            )

        patch_present = bool(patch.strip())
        decision = (
            "pass"
            if official.passed and not violations and patch_present
            else "fail"
        )
        if violations:
            failure_stage = "path_policy"
        elif not patch_present:
            failure_stage = "empty_patch"
        elif not official.passed:
            failure_stage = "oracle"
        else:
            failure_stage = None
        report = record_with_digest(
            {
                "schema": "autobugfix-raw-codex-case-report-v1",
                "case_id": case.case_id,
                "cohort": case.cohort,
                "decision": decision,
                "failure_stage": failure_stage,
                "process_status": process_status,
                "timed_out": process.timed_out,
                "path_policy_passed": not violations,
                "patch_present": patch_present,
                "official_outcome": official.outcome,
                "official_passed": official.passed,
                "sdk_threads": 1,
                "sdk_turns": 1,
                "usage": dict(observation.usage or {}) if observation else None,
                "runtime_seconds": time.monotonic() - started,
                "submission_digest": submission["record_digest"],
                "oracle_digest": oracle_data["record_digest"],
                "noninterference_digest": noninterference["record_digest"],
            }
        )
        _write_yaml_once(case_dir / "report.yaml", report)
        return report

    def _receipt(self, case: RawBaselineCase) -> EligibilityReceipt:
        receipt = self.store.read_receipt(
            self.store.receipt_path(case.case_id, case.receipt_digest)
        )
        if (
            receipt.case_id != case.case_id
            or receipt.project != case.project
            or receipt.bug_id != case.bug_id
            or receipt.status != "eligible"
        ):
            raise RawCodexBaselineHarnessError(
                f"receipt differs from Raw case binding: {case.case_id}"
            )
        return receipt

    @staticmethod
    def _summary(
        run_id: str,
        reports: Sequence[Mapping[str, Any]],
        *,
        expected_case_count: int,
        harness_errors: Sequence[str],
        started_at: str,
        runtime_seconds: float,
        formal: bool,
    ) -> dict[str, Any]:
        passed = sum(item.get("decision") == "pass" for item in reports)
        failed = sum(item.get("decision") == "fail" for item in reports)
        return record_with_digest(
            {
                "schema": "autobugfix-raw-codex-run-summary-v1",
                "run_id": run_id,
                "formal": formal,
                "status": (
                    "invalid"
                    if harness_errors
                    else "completed"
                    if len(reports) == expected_case_count
                    else "incomplete"
                ),
                "started_at": started_at,
                "finished_at": utc_now(),
                "runtime_seconds": runtime_seconds,
                "expected_case_count": expected_case_count,
                "completed_case_count": len(reports),
                "passed_count": passed,
                "failed_count": failed,
                "harness_error_count": len(harness_errors),
                "pass_rate": passed / len(reports) if reports else 0.0,
                "harness_errors": list(harness_errors),
                "cases": [dict(item) for item in reports],
            }
        )

    def pilot(
        self,
        protocol_path: Path,
        source_manifest_path: Path,
        *,
        case_id: str,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        safe_component(run_id, "run_id")
        seed = RawBaselineSeedManifest.from_yaml(protocol_path.resolve())
        self._validate_protocol_config(seed)
        if case_id not in set(seed.development_case_ids):
            raise RawCodexBaselineError(
                "pilot case must be one of the predeclared development cases"
            )
        source = self.store.read_prepared_evaluation_manifest(
            source_manifest_path.resolve()
        )
        self._validate_source_binding(seed, source)
        source_case = next(
            (case for case in source.cases if case.case_id == case_id),
            None,
        )
        if source_case is None:
            raise RawCodexBaselineError("pilot case is absent from prepared H0")
        case = raw_case_from_prepared(
            source_case,
            development_case_ids=set(seed.development_case_ids),
        )
        runner = self.sandbox.ensure_runner_environment()
        run_dir = self._run_directory(out_root, run_id)
        started_at = utc_now()
        started = time.monotonic()
        errors: list[str] = []
        reports: list[dict[str, Any]] = []
        try:
            reports.append(
                self._run_case(
                    case,
                    self._receipt(case),
                    run_dir=run_dir,
                    manifest_digest=seed.manifest_digest,
                    runner=runner,
                    model=seed.model,
                    reasoning_effort=seed.reasoning_effort,
                    service_tier=seed.service_tier,
                    timeout_seconds=seed.timeout_seconds,
                )
            )
        except Exception as exc:
            errors.append(f"{case.case_id}: {type(exc).__name__}: {exc}")
        summary = self._summary(
            run_id,
            reports,
            expected_case_count=1,
            harness_errors=errors,
            started_at=started_at,
            runtime_seconds=time.monotonic() - started,
            formal=False,
        )
        _write_yaml_once(run_dir / "summary.yaml", summary)
        return {"run_dir": str(run_dir), "summary": summary}

    def _validate_prepared_runtime(
        self,
        prepared: PreparedRawBaselineManifest,
    ) -> RunnerMetadata:
        git_sha, git_tree, _ = self._git_identity(require_clean=True)
        config = self.config.eval.benchmarks.raw_codex
        runner = self.sandbox.ensure_runner_environment()
        observed = {
            "runner_git_sha": git_sha,
            "runner_git_tree": git_tree,
            "runner_source_digest": self._runner_source_digest(),
            "runner_install_digest": runner.package_digest,
            "runner_lock_digest": digest_file(config.runner_project / "uv.lock"),
            "sdk_version": runner.sdk_version,
            "prompt_template_digest": runner.prompt_template_digest,
            "config_digest": digest_file(self.project_root / ".autobugfix/config.yaml"),
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "service_tier": config.service_tier,
            "approval_mode": config.approval_mode,
            "sandbox": config.sandbox,
            "network_access": config.network_access,
            "timeout_seconds": config.timeout_seconds,
        }
        expected = {
            "runner_git_sha": prepared.runner_git_sha,
            "runner_git_tree": prepared.runner_git_tree,
            "runner_source_digest": prepared.runner_source_digest,
            "runner_install_digest": prepared.runner_install_digest,
            "runner_lock_digest": prepared.runner_lock_digest,
            "sdk_version": prepared.sdk_version,
            "prompt_template_digest": prepared.prompt_template_digest,
            "config_digest": prepared.config_digest,
            "model": prepared.model,
            "reasoning_effort": prepared.reasoning_effort,
            "service_tier": prepared.service_tier,
            "approval_mode": prepared.approval_mode,
            "sandbox": prepared.sandbox,
            "network_access": prepared.network_access,
            "timeout_seconds": prepared.timeout_seconds,
        }
        if observed != expected:
            raise RawCodexBaselineError(
                "current Raw runner inputs differ from prepared manifest"
            )
        return runner

    def run_formal(
        self,
        prepared_manifest_path: Path,
        *,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        safe_component(run_id, "run_id")
        prepared = self._read_prepared_manifest(
            prepared_manifest_path.resolve()
        )
        runner = self._validate_prepared_runtime(prepared)
        run_dir = self._run_directory(out_root, run_id)
        started_at = utc_now()
        started = time.monotonic()
        reports: list[dict[str, Any]] = []
        errors: list[str] = []
        for case in prepared.cases:
            try:
                reports.append(
                    self._run_case(
                        case,
                        self._receipt(case),
                        run_dir=run_dir,
                        manifest_digest=str(
                            prepared.to_dict()["record_digest"]
                        ),
                        runner=runner,
                        model=prepared.model,
                        reasoning_effort=prepared.reasoning_effort,
                        service_tier=prepared.service_tier,
                        timeout_seconds=prepared.timeout_seconds,
                    )
                )
            except Exception as exc:
                errors.append(f"{case.case_id}: {type(exc).__name__}: {exc}")
                break
        summary = self._summary(
            run_id,
            reports,
            expected_case_count=len(prepared.cases),
            harness_errors=errors,
            started_at=started_at,
            runtime_seconds=time.monotonic() - started,
            formal=True,
        )
        _write_yaml_once(run_dir / "summary.yaml", summary)
        _write_yaml_once(
            run_dir / "run-binding.yaml",
            record_with_digest(
                {
                    "schema": "autobugfix-raw-codex-run-binding-v1",
                    "prepared_manifest_digest": prepared.to_dict()["record_digest"],
                    "h0_report_digest": prepared.h0_report_digest,
                    "runner_git_sha": prepared.runner_git_sha,
                    "runner_source_digest": prepared.runner_source_digest,
                    "runner_install_digest": prepared.runner_install_digest,
                    "prompt_template_digest": prepared.prompt_template_digest,
                    "model": prepared.model,
                    "sdk_version": prepared.sdk_version,
                    "case_count": len(prepared.cases),
                    "summary_digest": summary["record_digest"],
                }
            ),
        )
        return {"run_dir": str(run_dir), "summary": summary}

    @staticmethod
    def report(run_dir: Path, h0_report_path: Path) -> dict[str, Any]:
        path = write_raw_baseline_report(run_dir, h0_report_path)
        report = _read_yaml_mapping(path, "Raw comparison report")
        verify_record(report)
        return {"report": str(path), **report}
