from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.config import load_config
from autobugfix.eval.baselines.isolation import (
    RawCodexProcessSandbox,
    RawProcessRun,
    RunnerMetadata,
)
from autobugfix.eval.baselines.models import RawProcessObservation
from autobugfix.eval.baselines.swe_raw_models import (
    PreparedSWERawCase,
    PreparedSWERawManifest,
    SWERawSubmission,
    SWERawTreatmentProtocol,
)
from autobugfix.eval.baselines.swe_raw_submission import SWERawSubmissionAuthority
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.eval.benchmarks.swe_materialize import SWEMaterializedRepository
from autobugfix.eval.benchmarks.swe_models import (
    SWEExperimentProtocol,
    SWEInstance,
    SWEVisibleCase,
)
from autobugfix.eval.benchmarks.swe_submission import write_evidence_manifest
from autobugfix.eval.benchmarks.swe_verified import SWEVerifiedAdapter
from autobugfix.eval.benchmarks.verify import changed_paths
from autobugfix.git_utils import rev_parse, run_git
from autobugfix.models import utc_now
from autobugfix.worktree import git_diff_with_untracked


class SWERawCodexBaselineError(RuntimeError):
    pass


class SWERawCodexHarnessError(SWERawCodexBaselineError):
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


def _read_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SWERawCodexHarnessError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise SWERawCodexHarnessError(f"{label} must be a JSON object")
    return dict(value)


class SWERawCodexBaselineService:
    """Eval-owned direct Codex comparator for SWE-bench.

    The standalone worker owns one SDK turn and the target worktree only. This
    service owns case preparation, patch freeze, official scoring, and reports.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        benchmark = self.config.eval.benchmarks
        self.benchmark_service = EvalBenchmarkService(self.project_root)
        self.sandbox = RawCodexProcessSandbox(
            self.project_root,
            benchmark.raw_codex,
            hidden_roots=(
                benchmark.trusted_case_root,
                benchmark.visible_manifest_root,
                benchmark.cache_root,
                self.project_root / ".autobugfix-memory",
                self.config.operator.state.root,
                self.config.operator.artifacts.root,
            ),
        )
        self.submissions = SWERawSubmissionAuthority(benchmark.trusted_case_root)

    def _runtime(self) -> SWEVerifiedAdapter:
        return self.benchmark_service._swe_adapter("swebench_verified")

    def _load_protocols(
        self,
        source_protocol_path: Path,
        treatment_path: Path,
    ) -> tuple[SWEExperimentProtocol, SWERawTreatmentProtocol]:
        source = SWEExperimentProtocol.from_yaml(source_protocol_path.resolve())
        treatment = SWERawTreatmentProtocol.from_yaml(treatment_path.resolve())
        if source.protocol_digest != treatment.source_protocol_digest:
            raise SWERawCodexBaselineError(
                "SWE Raw treatment is not bound to the selected source protocol"
            )
        if (
            source.optimization_count != treatment.expected_case_count
            or source.codex_runtime.model != treatment.model
            or source.codex_runtime.sdk_version != treatment.sdk_version
            or source.codex_runtime.cli_version != treatment.cli_version
            or source.codex_runtime.reasoning_effort != treatment.reasoning_effort
            or source.codex_runtime.service_tier != treatment.service_tier
            or source.codex_runtime.timeout_seconds != treatment.timeout_seconds
            or source.case_concurrency != treatment.case_concurrency
        ):
            raise SWERawCodexBaselineError(
                "SWE Raw treatment differs from shared experiment controls"
            )
        configured = self.config.eval.benchmarks.raw_codex
        try:
            runner_relative = configured.runner_project.resolve().relative_to(
                self.project_root
            ).as_posix()
        except ValueError as exc:
            raise SWERawCodexBaselineError(
                "Raw SDK runner project must be inside the control repository"
            ) from exc
        if (
            runner_relative != treatment.runner_project
            or configured.model != treatment.model
            or configured.sdk_version != treatment.sdk_version
            or configured.cli_version != treatment.cli_version
            or configured.reasoning_effort != treatment.reasoning_effort
            or configured.service_tier != treatment.service_tier
            or configured.approval_mode != treatment.approval_mode
            or configured.sandbox != treatment.sandbox
            or configured.network_access is not treatment.network_access
            or configured.swe_timeout_seconds != treatment.timeout_seconds
            or not configured.require_process_sandbox
        ):
            raise SWERawCodexBaselineError(
                "resolved Raw SDK configuration differs from treatment protocol"
            )
        return source, treatment

    def _config_digest(self) -> str:
        benchmark = self.config.eval.benchmarks
        raw = benchmark.raw_codex
        swe = benchmark.swe
        return digest_payload(
            {
                "raw_codex": {
                    "runner_project": raw.runner_project.resolve().relative_to(
                        self.project_root
                    ).as_posix(),
                    "sdk_version": raw.sdk_version,
                    "cli_version": raw.cli_version,
                    "model": raw.model,
                    "reasoning_effort": raw.reasoning_effort,
                    "service_tier": raw.service_tier,
                    "approval_mode": raw.approval_mode,
                    "sandbox": raw.sandbox,
                    "network_access": raw.network_access,
                    "swe_timeout_seconds": raw.swe_timeout_seconds,
                    "require_process_sandbox": raw.require_process_sandbox,
                },
                "swe": {
                    "platform": swe.platform,
                    "swebench_version": swe.swebench_version,
                    "swebench_commit": swe.swebench_commit,
                    "swebench_tree": swe.swebench_tree,
                    "verified_dataset": swe.verified_dataset,
                    "verified_dataset_revision": swe.verified_dataset_revision,
                    "verified_namespace": swe.verified_namespace,
                    "scorer_timeout_seconds": swe.scorer_timeout_seconds,
                    "memory_limit": swe.memory_limit,
                    "cpu_limit": swe.cpu_limit,
                    "pids_limit": swe.pids_limit,
                },
            }
        )

    def _git_identity(self, *, require_clean: bool) -> tuple[str, str]:
        status = run_git(
            self.project_root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
        ).stdout
        if require_clean and status.strip():
            raise SWERawCodexBaselineError(
                "formal SWE Raw preparation and execution require a clean checkout"
            )
        branch = run_git(
            self.project_root,
            ["branch", "--show-current"],
            check=True,
        ).stdout.strip()
        if not branch or branch in {"main", "master"}:
            raise SWERawCodexBaselineError(
                "SWE Raw baseline must run from a named non-main branch"
            )
        return (
            rev_parse(self.project_root, "HEAD"),
            rev_parse(self.project_root, "HEAD^{tree}"),
        )

    @staticmethod
    def _source_fingerprint(source: Path) -> dict[str, Any]:
        return {
            "head": run_git(source, ["rev-parse", "HEAD"], check=True).stdout.strip(),
            "tree": run_git(source, ["rev-parse", "HEAD^{tree}"], check=True).stdout.strip(),
            "status": run_git(
                source,
                ["status", "--porcelain=v1", "--untracked-files=all"],
                check=True,
            ).stdout,
            "refs": run_git(
                source, ["for-each-ref", "--format=%(refname)"], check=True
            ).stdout,
            "remotes": run_git(source, ["remote"], check=True).stdout,
        }

    @staticmethod
    def _worktree_authority(worktree: Path) -> dict[str, Any]:
        marker = worktree / ".git" / "autobugfix-raw-sdk-v1"
        return {
            "head": run_git(worktree, ["rev-parse", "HEAD"], check=True).stdout.strip(),
            "refs": run_git(
                worktree, ["for-each-ref", "--format=%(refname)"], check=True
            ).stdout,
            "remotes": run_git(worktree, ["remote"], check=True).stdout,
            "config": run_git(
                worktree,
                ["config", "--local", "--list", "--show-origin"],
                check=True,
            ).stdout,
            "marker": (
                marker.read_text(encoding="utf-8").strip()
                if marker.is_file() and not marker.is_symlink()
                else "missing"
            ),
        }

    @staticmethod
    def _clone_source(source: Path, base_commit: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        initialized = subprocess.run(
            ["git", "init", str(destination)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if initialized.returncode != 0:
            raise SWERawCodexHarnessError(
                initialized.stderr.strip() or "cannot initialize Raw SWE worktree"
            )
        run_git(
            destination,
            [
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "--depth=1",
                "--no-tags",
                source.resolve().as_uri(),
                base_commit,
            ],
            check=True,
        )
        run_git(destination, ["checkout", "--detach", "FETCH_HEAD"], check=True)
        (destination / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
        run_git(destination, ["clean", "-fdx"], check=True)
        (destination / ".git" / "autobugfix-raw-sdk-v1").write_text(
            base_commit + "\n", encoding="utf-8"
        )
        authority = SWERawCodexBaselineService._worktree_authority(destination)
        if (
            authority["head"] != base_commit
            or authority["refs"]
            or authority["remotes"]
            or authority["marker"] != base_commit
        ):
            raise SWERawCodexHarnessError(
                "Raw SWE worktree did not preserve a detached sanitized base"
            )
        return destination

    @staticmethod
    def _case_bundle(visible: SWEVisibleCase) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema": "raw-codex-sdk-case-v1",
                "case_id": visible.case_token,
                "benchmark": visible.benchmark,
                "dataset_revision": visible.dataset_revision,
                "base_commit": visible.base_commit,
                "problem_statement": visible.problem_statement,
                "expected_behavior": "Resolve the reported repository issue.",
                "visible_evidence": list(visible.public_hints),
                "attachments": [attachment.uri for attachment in visible.attachments],
            }
        )

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
            "timeout_receipt": process.stdout_path.parent / "timeout-receipt.yaml",
        }
        return {
            name: digest_file(path) if path.is_file() else "missing"
            for name, path in candidates.items()
        }

    @staticmethod
    def _freeze_process_evidence(
        process: RawProcessRun,
        destination: Path,
    ) -> None:
        destination.mkdir(mode=0o700)
        artifacts = {
            "worker.stdout.log": (process.stdout_path, 16 * 1024 * 1024),
            "worker.stderr.log": (process.stderr_path, 16 * 1024 * 1024),
            "codex-config.toml": (
                process.stdout_path.parent / "codex-config.toml",
                1024 * 1024,
            ),
        }
        timeout_receipt = process.stdout_path.parent / "timeout-receipt.yaml"
        if process.timed_out:
            artifacts["timeout-receipt.yaml"] = (timeout_receipt, 1024 * 1024)
        elif timeout_receipt.exists():
            raise SWERawCodexHarnessError(
                "completed Raw SDK process contains an unexpected timeout receipt"
            )
        for name, (source, size_limit) in artifacts.items():
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size > size_limit
            ):
                raise SWERawCodexHarnessError(
                    f"Raw SDK process evidence is missing or oversized: {name}"
                )
            shutil.copy2(source, destination / name, follow_symlinks=False)
        sdk_files = {
            "request.json": 1024 * 1024,
            "events.jsonl": 64 * 1024 * 1024,
            "stderr.log": 16 * 1024 * 1024,
            "process-result.json": 4 * 1024 * 1024,
        }
        if process.sdk_artifact_root.is_symlink():
            raise SWERawCodexHarnessError("Raw SDK artifact directory is unsafe")
        observed = (
            {
                path.relative_to(process.sdk_artifact_root).as_posix()
                for path in process.sdk_artifact_root.rglob("*")
                if path.is_file()
            }
            if process.sdk_artifact_root.is_dir()
            else set()
        )
        expected = set(sdk_files)
        required = expected - ({"process-result.json"} if process.timed_out else set())
        invalid_set = observed != required
        if invalid_set:
            raise SWERawCodexHarnessError(
                "Raw SDK artifact file set differs from the frozen evidence contract"
            )
        sdk_destination = destination / "sdk"
        sdk_destination.mkdir(mode=0o700)
        for name in sorted(observed):
            size_limit = sdk_files[name]
            source = process.sdk_artifact_root / name
            if (
                source.is_symlink()
                or source.stat().st_size > size_limit
            ):
                raise SWERawCodexHarnessError(
                    f"Raw SDK artifact is unsafe or oversized: {name}"
                )
            shutil.copy2(
                source,
                sdk_destination / name,
                follow_symlinks=False,
            )

    @staticmethod
    def _validate_observation(
        process: RawProcessRun,
        case_bundle: Mapping[str, Any],
        *,
        runner: RunnerMetadata,
        treatment: SWERawTreatmentProtocol,
    ) -> RawProcessObservation:
        if not process.process_result_path.is_file():
            raise SWERawCodexHarnessError(
                "Raw SDK process did not write process-result.json"
            )
        data = _read_json_mapping(process.process_result_path, "Raw SDK process result")
        try:
            observation = RawProcessObservation.from_dict(data)
        except BenchmarkContractError as exc:
            raise SWERawCodexHarnessError(
                f"Raw SDK process result violates its contract: {exc}"
            ) from exc
        events_path = process.sdk_artifact_root / "events.jsonl"
        stderr_path = process.sdk_artifact_root / "stderr.log"
        request_path = process.sdk_artifact_root / "request.json"
        event_count = (
            sum(1 for _ in events_path.open(encoding="utf-8", errors="replace"))
            if events_path.is_file()
            else -1
        )
        if (
            observation.case_id != case_bundle["case_id"]
            or observation.case_digest != case_bundle["record_digest"]
            or observation.sdk_version != treatment.sdk_version
            or observation.sdk_version != runner.sdk_version
            or observation.model != treatment.model
            or observation.reasoning_effort != treatment.reasoning_effort
            or observation.service_tier != treatment.service_tier
            or observation.approval_mode != treatment.approval_mode
            or observation.approval_mode != runner.approval_mode
            or observation.sandbox != treatment.sandbox
            or observation.sandbox != runner.sandbox
            or observation.network_access is not treatment.network_access
            or observation.network_access is not runner.network_access
            or observation.prompt_template_digest != treatment.prompt_template_digest
            or observation.prompt_template_digest != runner.prompt_template_digest
            or data.get("sdk_package") != "openai-codex"
            or data.get("cli_package") != "openai-codex-cli-bin"
            or data.get("cli_version") != treatment.cli_version
            or data.get("cli_version") != runner.cli_version
            or not request_path.is_file()
            or not events_path.is_file()
            or not stderr_path.is_file()
            or digest_file(request_path) != observation.request_sha256
            or digest_file(events_path) != observation.events_sha256
            or digest_file(stderr_path) != observation.stderr_sha256
            or event_count != observation.event_count
        ):
            raise SWERawCodexHarnessError(
                "Raw SDK result disagrees with trusted launch inputs or logs"
            )
        if observation.status != "completed" or process.return_code != 0:
            raise SWERawCodexHarnessError(
                "Raw SDK process failed before completing its single turn: "
                + observation.error
            )
        return observation

    def _runner_metadata(
        self, treatment: SWERawTreatmentProtocol
    ) -> RunnerMetadata:
        runner = self.sandbox.ensure_runner_environment()
        if (
            runner.sdk_version != treatment.sdk_version
            or runner.cli_version != treatment.cli_version
            or runner.approval_mode != treatment.approval_mode
            or runner.sandbox != treatment.sandbox
            or runner.network_access is not treatment.network_access
            or runner.prompt_template_digest != treatment.prompt_template_digest
        ):
            raise SWERawCodexBaselineError(
                "locked Raw SDK runner differs from treatment protocol"
            )
        return runner

    def _qualified_cases(
        self,
        protocol: SWEExperimentProtocol,
        *,
        artifact_root: Path,
        selected_instance_ids: frozenset[str] | None = None,
    ) -> tuple[tuple[PreparedSWERawCase, SWEInstance, SWEMaterializedRepository], ...]:
        adapter = self._runtime()
        receipts = {
            str(item["instance_id"]): item
            for item in self.benchmark_service._swe_qualification_pool(
                protocol, "swebench_verified"
            )
        }
        selections = tuple(
            selection
            for selection in protocol.optimization_cases
            if selected_instance_ids is None
            or selection.instance_id in selected_instance_ids
        )
        expected = {selection.instance_id for selection in selections}
        if selected_instance_ids is not None and expected != set(selected_instance_ids):
            raise SWERawCodexBaselineError(
                "requested Raw development case is absent from Optimization"
            )
        if set(receipts) & expected != expected:
            missing = sorted(expected - set(receipts))
            raise SWERawCodexBaselineError(
                "SWE Raw preparation lacks current eligible qualifications: "
                + ", ".join(missing)
            )
        prepared: list[
            tuple[PreparedSWERawCase, SWEInstance, SWEMaterializedRepository]
        ] = []
        source_indexes = {
            selection.instance_id: index
            for index, selection in enumerate(protocol.optimization_cases)
        }
        for selection in selections:
            receipt = receipts[selection.instance_id]
            instance = adapter.load_instance(
                selection.instance_id,
                artifact_root / selection.instance_id / "inspection",
            )
            materialized = self.benchmark_service._validate_swe_qualification_source(
                adapter,
                instance,
                receipt,
                artifact_root / selection.instance_id / "source-validation",
            )
            token = "opt-" + hashlib.sha256(
                f"{protocol.protocol_digest}:{instance.instance_id}".encode("utf-8")
            ).hexdigest()[:24]
            visible = self.benchmark_service._swe_visible_case(
                adapter,
                instance,
                receipt,
                case_token=token,
                first_wave=self.benchmark_service._swe_first_wave(
                    source_indexes[selection.instance_id], role="optimization"
                ),
                task_type=selection.task_type,
            )
            case = PreparedSWERawCase(
                instance_id=instance.instance_id,
                qualification_digest=str(receipt["record_digest"]),
                image_id=materialized.image_id,
                source_tree=materialized.source_tree,
                source_digest=materialized.source_digest,
                visible_case=visible,
            )
            prepared.append((case, instance, materialized))
        return tuple(prepared)

    def _run_directory(self, out_root: Path, run_id: str) -> Path:
        runtime_root = self.config.eval.benchmarks.raw_codex.runtime_root.resolve()
        output = out_root.resolve()
        if not output.is_relative_to(runtime_root):
            raise SWERawCodexBaselineError(
                "SWE Raw output must remain inside its configured runtime root"
            )
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        run_dir = output / safe_component(run_id, "run_id")
        run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        return run_dir

    def _run_case(
        self,
        case: PreparedSWERawCase,
        instance: SWEInstance,
        materialized: SWEMaterializedRepository,
        *,
        run_dir: Path,
        manifest_digest: str,
        treatment: SWERawTreatmentProtocol,
        runner_metadata: RunnerMetadata,
        official_runner: SWEVerifiedAdapter,
        official_run_id: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        case_dir = run_dir / safe_component(case.instance_id, "instance_id")
        case_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        source = Path(materialized.source_path).resolve()
        source_before = self._source_fingerprint(source)
        if (
            source_before["head"] != case.visible_case.base_commit
            or source_before["tree"] != case.source_tree
            or source_before["status"]
        ):
            raise SWERawCodexHarnessError(
                "sanitized SWE source changed before Raw generation"
            )
        worktree = self._clone_source(
            source,
            case.visible_case.base_commit,
            case_dir / "worktree",
        )
        worktree_authority = self._worktree_authority(worktree)
        input_root = case_dir / "visible-input"
        input_root.mkdir(mode=0o700)
        bundle = self._case_bundle(case.visible_case)
        bundle_path = _write_json_once(input_root / "case.json", bundle)
        process = self.sandbox.run(
            runner_metadata=runner_metadata,
            worktree=worktree,
            input_root=input_root,
            case_bundle=bundle_path,
            artifact_root=case_dir / "process",
            model=treatment.model,
            reasoning_effort=treatment.reasoning_effort,
            service_tier=treatment.service_tier,
            timeout_seconds=treatment.timeout_seconds,
        )
        observation: RawProcessObservation | None = None
        terminal_digest: str
        if process.timed_out:
            process_status = "timed_out"
            request = process.sdk_artifact_root / "request.json"
            events = process.sdk_artifact_root / "events.jsonl"
            sdk_stderr = process.sdk_artifact_root / "stderr.log"
            if not all(path.is_file() and not path.is_symlink() for path in (request, events, sdk_stderr)):
                raise SWERawCodexHarnessError(
                    "timed-out Raw SDK process did not produce the required "
                    "request and event evidence"
                )
            timeout_receipt = record_with_digest(
                {
                    "schema": "autobugfix-swe-raw-timeout-v1",
                    "case_digest": str(bundle["record_digest"]),
                    "manifest_digest": manifest_digest,
                    "treatment_digest": digest_payload(treatment.to_dict()),
                    "return_code": process.return_code,
                    "duration_seconds": process.duration_seconds,
                    "request_sha256": digest_file(request),
                    "events_sha256": digest_file(events),
                    "stderr_sha256": digest_file(sdk_stderr),
                    "recorded_at": utc_now(),
                }
            )
            timeout_path = process.stdout_path.parent / "timeout-receipt.yaml"
            _write_yaml_once(timeout_path, timeout_receipt)
            timeout_path.chmod(0o600)
            terminal_digest = str(timeout_receipt["record_digest"])
        else:
            observation = self._validate_observation(
                process,
                bundle,
                runner=runner_metadata,
                treatment=treatment,
            )
            process_status = observation.status
            terminal_digest = observation.record_digest
        if self._worktree_authority(worktree) != worktree_authority:
            raise SWERawCodexHarnessError(
                "Raw SDK worker changed protected Git authority"
            )
        patch = git_diff_with_untracked(worktree, case.visible_case.base_commit)
        modified_paths = changed_paths(worktree)
        process_digests = self._process_artifact_digests(process)
        evidence_root = case_dir / "freeze-evidence"
        evidence_root.mkdir(mode=0o700)
        shutil.copytree(input_root, evidence_root / "visible-input", symlinks=False)
        self._freeze_process_evidence(process, evidence_root / "process")
        evidence_manifest = write_evidence_manifest(evidence_root)
        submission = SWERawSubmission(
            case_token=case.visible_case.case_token,
            instance_id=case.instance_id,
            manifest_digest=manifest_digest,
            base_commit=case.visible_case.base_commit,
            patch=patch,
            patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            case_bundle_digest=str(bundle["record_digest"]),
            process_status=process_status,
            timed_out=process.timed_out,
            process_result_digest=terminal_digest,
            process_artifact_digests=process_digests,
            evidence_manifest_digest=str(evidence_manifest["record_digest"]),
            changed_paths=modified_paths,
            source_digest=case.source_digest,
            frozen_at=utc_now(),
        )
        frozen = self.submissions.freeze(submission, evidence_root)
        frozen_before = frozen.identity()
        generation_identity = {
            "authority": self._worktree_authority(worktree),
            "patch_sha256": hashlib.sha256(
                git_diff_with_untracked(
                    worktree, case.visible_case.base_commit
                ).encode("utf-8")
            ).hexdigest(),
        }
        official = official_runner.score(
            instance,
            case_dir / "official-score",
            run_id=official_run_id,
            submission=submission,
            expected_image_id=case.image_id,
        )
        official_record = official.to_dict()
        _write_yaml_once(case_dir / "official-result.yaml", official_record)
        generation_after = {
            "authority": self._worktree_authority(worktree),
            "patch_sha256": hashlib.sha256(
                git_diff_with_untracked(
                    worktree, case.visible_case.base_commit
                ).encode("utf-8")
            ).hexdigest(),
        }
        noninterference = self.submissions.noninterference_receipt(
            frozen,
            frozen_before,
            official_result_digest=str(official_record["record_digest"]),
            source_unchanged=self._source_fingerprint(source) == source_before,
            worktree_unchanged=generation_after == generation_identity,
        )
        _write_yaml_once(case_dir / "noninterference.yaml", noninterference)
        patch_present = bool(patch.strip())
        report = record_with_digest(
            {
                "schema": "autobugfix-swe-raw-case-report-v1",
                "instance_id": case.instance_id,
                "case_token": case.visible_case.case_token,
                "treatment_id": treatment.treatment_id,
                "decision": "pass" if official.resolved else "fail",
                "failure_stage": (
                    None
                    if official.resolved
                    else "harness"
                    if official.harness_error
                    else "empty_patch"
                    if not patch_present
                    else "official_scorer"
                ),
                "process_status": process_status,
                "timed_out": process.timed_out,
                "patch_present": patch_present,
                "changed_paths": list(modified_paths),
                "official_resolved": official.resolved,
                "harness_error": official.harness_error,
                "sdk_threads": 1,
                "sdk_turns": 1,
                "approval_mode": treatment.approval_mode,
                "sandbox": treatment.sandbox,
                "network_access": treatment.network_access,
                "usage": dict(observation.usage or {}) if observation else None,
                "runtime_seconds": time.monotonic() - started,
                "submission_digest": submission.record["record_digest"],
                "official_result_digest": official_record["record_digest"],
                "noninterference_digest": noninterference["record_digest"],
            }
        )
        _write_yaml_once(case_dir / "report.yaml", report)
        return report

    @staticmethod
    def _summary(
        run_id: str,
        reports: Sequence[Mapping[str, Any]],
        *,
        expected_case_count: int,
        harness_errors: Sequence[str],
        formal: bool,
        started_at: str,
        runtime_seconds: float,
    ) -> dict[str, Any]:
        passed = sum(report.get("decision") == "pass" for report in reports)
        failed = sum(report.get("decision") == "fail" for report in reports)
        return record_with_digest(
            {
                "schema": "autobugfix-swe-raw-run-summary-v1",
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
                "cases": [dict(report) for report in reports],
            }
        )

    def run_development(
        self,
        source_protocol_path: Path,
        treatment_path: Path,
        *,
        instance_id: str,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        protocol, treatment = self._load_protocols(
            source_protocol_path, treatment_path
        )
        selected = {item.instance_id for item in protocol.optimization_cases}
        if instance_id not in selected:
            raise SWERawCodexBaselineError(
                "development case must be predeclared in Optimization"
            )
        runner_metadata = self._runner_metadata(treatment)
        run_dir = self._run_directory(out_root, run_id)
        qualified = self._qualified_cases(
            protocol,
            artifact_root=run_dir / "preflight",
            selected_instance_ids=frozenset({instance_id}),
        )
        case, instance, materialized = next(
            item for item in qualified if item[0].instance_id == instance_id
        )
        started_at = utc_now()
        started = time.monotonic()
        reports: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            report = self._run_case(
                case,
                instance,
                materialized,
                run_dir=run_dir,
                manifest_digest=treatment.treatment_digest,
                treatment=treatment,
                runner_metadata=runner_metadata,
                official_runner=self._runtime(),
                official_run_id=f"raw-dev-{safe_component(run_id, 'run_id')}",
            )
            reports.append(report)
            if report.get("harness_error"):
                errors.append(
                    f"{instance_id}: official scorer: {report['harness_error']}"
                )
        except Exception as exc:
            errors.append(f"{instance_id}: {type(exc).__name__}: {exc}")
        summary = self._summary(
            run_id,
            reports,
            expected_case_count=1,
            harness_errors=errors,
            formal=False,
            started_at=started_at,
            runtime_seconds=time.monotonic() - started,
        )
        _write_yaml_once(run_dir / "summary.yaml", summary)
        return {"run_dir": str(run_dir), "summary": summary}

    def prepare(
        self,
        source_protocol_path: Path,
        treatment_path: Path,
    ) -> dict[str, Any]:
        protocol, treatment = self._load_protocols(
            source_protocol_path, treatment_path
        )
        control_sha, control_tree = self._git_identity(require_clean=True)
        runner_metadata = self._runner_metadata(treatment)
        doctor = self.benchmark_service.doctor("swebench_verified")
        if not doctor.get("passed"):
            raise SWERawCodexBaselineError(
                "official SWE runtime doctor failed before Raw preparation"
            )
        preparation_root = (
            self.config.eval.benchmarks.raw_codex.runtime_root
            / "swe"
            / "preparation-runs"
            / treatment.treatment_id
        )
        preparation_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        qualified = self._qualified_cases(
            protocol,
            artifact_root=preparation_root / control_sha,
        )
        manifest = PreparedSWERawManifest(
            manifest_id=f"{treatment.treatment_id}-{control_sha[:12]}",
            source_protocol_digest=protocol.protocol_digest,
            treatment=treatment,
            runtime_id=str(doctor["runtime_id"]),
            control_sha=control_sha,
            control_tree=control_tree,
            runner_source_digest=runner_metadata.source_digest,
            runner_install_digest=runner_metadata.package_digest,
            runner_lock_digest=digest_file(
                self.config.eval.benchmarks.raw_codex.runner_project / "uv.lock"
            ),
            runner_runtime_digest=runner_metadata.runtime_digest,
            config_digest=self._config_digest(),
            cases=tuple(item[0] for item in qualified),
            prepared_at=utc_now(),
        )
        data = manifest.to_dict()
        destination = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe"
            / "raw-prepared"
            / manifest.manifest_id
            / f"{data['record_digest']}.yaml"
        )
        _write_yaml_once(destination, data)
        return {
            "manifest_id": manifest.manifest_id,
            "manifest_digest": data["record_digest"],
            "prepared_manifest": str(destination),
            "case_count": len(manifest.cases),
            "control_sha": control_sha,
            "runtime_id": manifest.runtime_id,
        }

    def _load_prepared(self, path: Path) -> PreparedSWERawManifest:
        resolved = path.resolve()
        root = (
            self.config.eval.benchmarks.trusted_case_root
            / "swe"
            / "raw-prepared"
        ).resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise SWERawCodexBaselineError(
                "prepared SWE Raw manifest is outside trusted state"
            )
        manifest = PreparedSWERawManifest.from_yaml(resolved)
        digest = str(manifest.to_dict()["record_digest"])
        if resolved.name != f"{digest}.yaml" or resolved.parent.name != manifest.manifest_id:
            raise SWERawCodexBaselineError(
                "prepared SWE Raw manifest path identity drift"
            )
        return manifest

    def _validate_prepared_runtime(
        self,
        prepared: PreparedSWERawManifest,
    ) -> tuple[RunnerMetadata, SWEVerifiedAdapter]:
        control_sha, control_tree = self._git_identity(require_clean=True)
        runner_metadata = self._runner_metadata(prepared.treatment)
        official_runner = self._runtime()
        observed = {
            "control_sha": control_sha,
            "control_tree": control_tree,
            "runner_source_digest": runner_metadata.source_digest,
            "runner_install_digest": runner_metadata.package_digest,
            "runner_lock_digest": digest_file(
                self.config.eval.benchmarks.raw_codex.runner_project / "uv.lock"
            ),
            "runner_runtime_digest": runner_metadata.runtime_digest,
            "config_digest": self._config_digest(),
            "runtime_id": official_runner.runtime.runtime_id,
        }
        expected = {
            "control_sha": prepared.control_sha,
            "control_tree": prepared.control_tree,
            "runner_source_digest": prepared.runner_source_digest,
            "runner_install_digest": prepared.runner_install_digest,
            "runner_lock_digest": prepared.runner_lock_digest,
            "runner_runtime_digest": prepared.runner_runtime_digest,
            "config_digest": prepared.config_digest,
            "runtime_id": prepared.runtime_id,
        }
        if observed != expected:
            raise SWERawCodexBaselineError(
                "current SWE Raw runtime differs from prepared manifest"
            )
        return runner_metadata, official_runner

    def run_formal(
        self,
        prepared_manifest_path: Path,
        *,
        out_root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        prepared = self._load_prepared(prepared_manifest_path)
        runner_metadata, official_runner = self._validate_prepared_runtime(prepared)
        protocol = SWEExperimentProtocol.from_yaml(
            self.project_root / "benchmarks" / "swe-experiment-2.yaml"
        )
        if protocol.protocol_digest != prepared.source_protocol_digest:
            raise SWERawCodexBaselineError(
                "source SWE protocol changed after Raw preparation"
            )
        qualified = {
            item[0].instance_id: item
            for item in self._qualified_cases(
                protocol,
                artifact_root=(
                    self.config.eval.benchmarks.raw_codex.runtime_root
                    / "swe"
                    / "formal-preflight"
                    / safe_component(run_id, "run_id")
                ),
            )
        }
        run_dir = self._run_directory(out_root, run_id)
        started_at = utc_now()
        started = time.monotonic()
        reports: list[dict[str, Any]] = []
        errors: list[str] = []
        manifest_digest = str(prepared.to_dict()["record_digest"])
        for index, case in enumerate(prepared.cases, start=1):
            current, instance, materialized = qualified[case.instance_id]
            if current != case:
                errors.append(f"{case.instance_id}: prepared case identity drift")
                break
            try:
                report = self._run_case(
                    case,
                    instance,
                    materialized,
                    run_dir=run_dir,
                    manifest_digest=manifest_digest,
                    treatment=prepared.treatment,
                    runner_metadata=runner_metadata,
                    official_runner=official_runner,
                    official_run_id=(
                        f"raw-{safe_component(run_id, 'run_id')}-{index:02d}"
                    ),
                )
                reports.append(report)
                if report.get("harness_error"):
                    errors.append(
                        f"{case.instance_id}: official scorer: {report['harness_error']}"
                    )
                    break
            except Exception as exc:
                errors.append(f"{case.instance_id}: {type(exc).__name__}: {exc}")
                break
        summary = self._summary(
            run_id,
            reports,
            expected_case_count=len(prepared.cases),
            harness_errors=errors,
            formal=True,
            started_at=started_at,
            runtime_seconds=time.monotonic() - started,
        )
        _write_yaml_once(run_dir / "summary.yaml", summary)
        binding = record_with_digest(
            {
                "schema": "autobugfix-swe-raw-run-binding-v1",
                "prepared_manifest_digest": manifest_digest,
                "source_protocol_digest": prepared.source_protocol_digest,
                "treatment_digest": prepared.treatment.treatment_digest,
                "control_sha": prepared.control_sha,
                "runner_source_digest": prepared.runner_source_digest,
                "runner_install_digest": prepared.runner_install_digest,
                "prompt_template_digest": prepared.treatment.prompt_template_digest,
                "model": prepared.treatment.model,
                "sdk_version": prepared.treatment.sdk_version,
                "cli_version": prepared.treatment.cli_version,
                "runner_runtime_digest": prepared.runner_runtime_digest,
                "approval_mode": prepared.treatment.approval_mode,
                "sandbox": prepared.treatment.sandbox,
                "network_access": prepared.treatment.network_access,
                "case_count": len(prepared.cases),
                "summary_digest": summary["record_digest"],
            }
        )
        _write_yaml_once(run_dir / "run-binding.yaml", binding)
        return {"run_dir": str(run_dir), "summary": summary}
