from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from autobugfix.config import load_config
from autobugfix.eval.benchmarks.authority import (
    GuardCodeIdentity,
    resolve_guard_code_identity,
)
from autobugfix.eval.benchmarks.defects4j import Defects4JRuntime
from autobugfix.eval.benchmarks.guard import (
    GuardBundle,
    GuardCaseSpec,
    decrypt_json,
    encrypt_artifact_tree,
    encrypt_json,
    guard_aad,
    guard_artifact_digest,
    metric_payload,
    new_guard_id,
    signed_metric,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    BenchmarkCaseSeed,
    BenchmarkSeedManifest,
    EligibilityReceipt,
    digest_payload,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.artifacts import write_yaml
from autobugfix.eval.benchmarks.store import BenchmarkStore
from autobugfix.eval.benchmarks.verify import managed_verifier_for_receipt
from autobugfix.eval.runner import run_eval
from autobugfix.eval.scorers import normalize_diff
from autobugfix.models import utc_now


class EvalBenchmarkServiceError(RuntimeError):
    pass


class EvalBenchmarkService:
    def __init__(
        self,
        project_root: Path,
        *,
        guard_authority_resolver: Callable[
            [Path, str], GuardCodeIdentity
        ] = resolve_guard_code_identity,
    ):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        self._guard_authority_resolver = guard_authority_resolver
        benchmark_config = self.config.eval.benchmarks
        self.store = BenchmarkStore(
            benchmark_config.trusted_case_root,
            benchmark_config.visible_manifest_root,
            benchmark_config.cache_root,
        )

    def guard_authority(self) -> GuardCodeIdentity:
        return self._guard_authority_resolver(
            self.project_root,
            self.config.eval.benchmarks.guard.trusted_ref,
        )

    def _doctor_runtime(
        self,
        adapter: str,
        runtime: Defects4JRuntime,
    ) -> dict[str, Any]:
        if adapter != "defects4j":
            raise EvalBenchmarkServiceError(f"unsupported benchmark adapter: {adapter}")
        artifact_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "doctor-artifacts"
            / adapter
            / uuid.uuid4().hex
        )
        report = runtime.doctor(artifact_root)
        data = report.to_dict()
        path = self.store.write_doctor(adapter, data)
        return {
            "adapter": adapter,
            "passed": report.passed,
            "framework_revision": report.framework_revision,
            "runtime_id": report.runtime_id,
            "verifier_runtime_id": report.verifier_runtime_id,
            "checks": [item.to_dict() for item in report.checks],
            "report_digest": data["record_digest"],
            "report_path": str(path),
        }

    def doctor(self, adapter: str) -> dict[str, Any]:
        return self._doctor_runtime(
            adapter,
            Defects4JRuntime(self.config.eval.benchmarks),
        )

    def preflight(
        self,
        manifest_path: Path,
        *,
        case_selector: str | None = None,
    ) -> dict[str, Any]:
        manifest = BenchmarkSeedManifest.from_yaml(manifest_path.resolve())
        if manifest.benchmark != "defects4j":
            raise EvalBenchmarkServiceError("preflight manifest is not Defects4J")
        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed: {doctor['report_digest']}"
            )
        selected = [
            case
            for case in manifest.optimization_cases
            if case_selector is None or case.case_id == case_selector
        ]
        if not selected:
            raise EvalBenchmarkServiceError("no visible Optimization case selected")
        run_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "preflight-runs"
            / manifest.manifest_id
            / uuid.uuid4().hex
        )
        projected: list[dict[str, Any]] = []
        eligible = 0
        for case in selected:
            receipt = runtime.preflight_case(
                manifest,
                case,
                role="optimization",
                first_wave=case.first_wave,
                artifact_root=run_root,
            )
            receipt_path = self.store.write_receipt(receipt)
            if receipt.status == "eligible":
                eligible += 1
            projected.append(
                {
                    "case_id": receipt.case_id,
                    "status": receipt.status,
                    "reason": receipt.reason,
                    "first_wave": receipt.first_wave,
                    "receipt_digest": receipt.to_dict()["record_digest"],
                    "receipt_path": str(receipt_path),
                }
            )
        return {
            "manifest_id": manifest.manifest_id,
            "manifest_digest": manifest.manifest_digest,
            "selected_count": len(selected),
            "eligible_count": eligible,
            "failed_count": len(selected) - eligible,
            "cases": projected,
        }

    @staticmethod
    def _holdout_wave(index: int) -> int:
        if index == 0:
            return 3
        if index < 3:
            return 8
        return 16

    @staticmethod
    def _holdout_candidates(
        runtime: Defects4JRuntime,
        manifest: BenchmarkSeedManifest,
        project: str,
    ) -> list[int]:
        def rank(bug_id: int) -> str:
            payload = (
                f"{manifest.manifest_digest}:sealed-holdout:{project}:{bug_id}"
            )
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return sorted(runtime.active_bug_ids(project), key=rank)

    @staticmethod
    def _receipt_semantic_fingerprint(receipt: EligibilityReceipt) -> str:
        return digest_payload(
            {
                "project": receipt.project,
                "bug_id": receipt.bug_id,
                "first_wave": receipt.first_wave,
                "framework_revision": receipt.framework_revision,
                "dataset_revision": receipt.dataset_revision,
                "runtime_id": receipt.runtime_id,
                "verifier_runtime_id": receipt.verifier_runtime_id,
                "triggering_tests": list(receipt.triggering_tests),
                "baseline_failing_tests": list(receipt.baseline_failing_tests),
                "source_roots": list(receipt.source_roots),
                "sanitized_base_sha": receipt.sanitized_base_sha,
                "gold_patch_sha256": receipt.gold_patch_sha256,
                "status": receipt.status,
            }
        )

    @classmethod
    def _guard_case(cls, receipt: EligibilityReceipt) -> GuardCaseSpec:
        problem, attachments = cls._problem_statement(receipt)
        return GuardCaseSpec(
            case_token=receipt.case_id,
            project=receipt.project,
            bug_id=receipt.bug_id,
            first_wave=receipt.first_wave,
            semantic_fingerprint=cls._receipt_semantic_fingerprint(receipt),
            problem_statement=problem,
            attachments=tuple(attachments),
        )

    @staticmethod
    def _private_holdout_projects(
        manifest: BenchmarkSeedManifest,
        projects: Sequence[str],
    ) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(item).strip() for item in projects if str(item).strip()))
        if len(normalized) < 3:
            raise EvalBenchmarkServiceError(
                "trusted Guard requires at least three private Holdout repository groups"
            )
        optimization_projects = {item.project for item in manifest.optimization_cases}
        overlap = optimization_projects & set(normalized)
        if overlap:
            raise EvalBenchmarkServiceError(
                "private Holdout repositories overlap Optimization: "
                + ", ".join(sorted(overlap))
            )
        return normalized

    def seal(
        self,
        manifest_path: Path,
        *,
        guard_secret: str | bytes,
        holdout_projects: Sequence[str],
    ) -> dict[str, Any]:
        code_identity = self.guard_authority()
        manifest = BenchmarkSeedManifest.from_yaml(manifest_path.resolve())
        if manifest.benchmark != "defects4j":
            raise EvalBenchmarkServiceError("seal manifest is not Defects4J")
        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed: {doctor['report_digest']}"
            )

        optimization_run_root = (
            self.config.eval.benchmarks.trusted_case_root
            / "seal-runs"
            / manifest.manifest_id
            / uuid.uuid4().hex
            / "optimization"
        )
        optimization_references: list[dict[str, Any]] = []
        optimization_receipts: list[EligibilityReceipt] = []
        failed_optimization: list[str] = []
        for case in manifest.optimization_cases:
            receipt = runtime.preflight_case(
                manifest,
                case,
                role="optimization",
                first_wave=case.first_wave,
                artifact_root=optimization_run_root,
            )
            receipt_path = self.store.write_receipt(receipt)
            if receipt.status != "eligible":
                failed_optimization.append(f"{case.case_id}: {receipt.reason}")
                continue
            optimization_receipts.append(receipt)
            optimization_references.append(
                {
                    "case_token": case.case_id,
                    "case_id": receipt.case_id,
                    "project": receipt.project,
                    "bug_id": receipt.bug_id,
                    "first_wave": receipt.first_wave,
                    "receipt_digest": str(receipt.to_dict()["record_digest"]),
                }
            )
        if failed_optimization:
            raise EvalBenchmarkServiceError(
                "Optimization preflight failed: " + "; ".join(failed_optimization)
            )

        private_projects = self._private_holdout_projects(manifest, holdout_projects)
        guard_id = new_guard_id()
        aad = guard_aad(
            guard_id,
            manifest.manifest_digest,
            manifest.framework_revision,
            manifest.dataset_revision,
            code_identity.identity_digest,
        )
        guard_root = self.config.eval.benchmarks.trusted_case_root / "guard" / guard_id
        guard_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        guard_root.chmod(0o700)
        preflight_archive_name = "holdout-preflight.abfg"
        preflight_archive = guard_root / preflight_archive_name
        holdout_cases: list[GuardCaseSpec] = []
        with tempfile.TemporaryDirectory(prefix="autobugfix-holdout-seal-") as temporary:
            private_root = Path(temporary)
            private_root.chmod(0o700)
            private_config = replace(
                self.config.eval.benchmarks,
                cache_root=private_root / "cache",
                trusted_case_root=private_root / "trusted",
                visible_manifest_root=private_root / "visible",
            )
            private_runtime = Defects4JRuntime(private_config)
            docker_bin = runtime.docker_bin
            if docker_bin is None:
                raise EvalBenchmarkServiceError("inspected Docker binary disappeared")
            private_runtime.bind_inspected_runtime(
                docker_bin=docker_bin,
                runtime_id=str(doctor["runtime_id"]),
                verifier_runtime_id=str(doctor["verifier_runtime_id"]),
            )
            candidate_queues = {
                project: self._holdout_candidates(private_runtime, manifest, project)
                for project in private_projects
            }
            active_projects = list(private_projects)
            while len(holdout_cases) < manifest.holdout_count and active_projects:
                accepted_this_round = False
                for project in tuple(active_projects):
                    queue = candidate_queues[project]
                    accepted_for_project = False
                    while queue:
                        bug_id = queue.pop(0)
                        case_token = f"holdout-{secrets.token_hex(24)}"
                        case = BenchmarkCaseSeed(
                            case_id=case_token,
                            project=project,
                            bug_id=bug_id,
                            first_wave=self._holdout_wave(len(holdout_cases)),
                        )
                        receipt = private_runtime.preflight_case(
                            manifest,
                            case,
                            role="sealed_holdout",
                            first_wave=case.first_wave,
                            artifact_root=private_root / "preflight",
                        )
                        if receipt.status != "eligible":
                            continue
                        holdout_cases.append(self._guard_case(receipt))
                        accepted_this_round = True
                        accepted_for_project = True
                        break
                    if not queue and not accepted_for_project:
                        active_projects.remove(project)
                    if len(holdout_cases) == manifest.holdout_count:
                        break
                if not accepted_this_round:
                    break
            if len(holdout_cases) != manifest.holdout_count:
                raise EvalBenchmarkServiceError(
                    "private Holdout pool did not yield six eligible repository-isolated cases"
                )
            encrypt_artifact_tree(
                private_root,
                preflight_archive,
                secret=guard_secret,
                aad=aad + b":preflight",
            )

        wave_tokens = {
            str(wave): f"wave-{wave}-{secrets.token_hex(24)}"
            for wave in (3, 8, 16)
        }
        bundle = GuardBundle(
            guard_id=guard_id,
            seed_manifest_digest=manifest.manifest_digest,
            framework_revision=manifest.framework_revision,
            dataset_revision=manifest.dataset_revision,
            runtime_id=str(doctor["runtime_id"]),
            verifier_runtime_id=str(doctor["verifier_runtime_id"]),
            code_identity=code_identity,
            preflight_archive_name=preflight_archive_name,
            preflight_archive_sha256=guard_artifact_digest(preflight_archive),
            wave_tokens=wave_tokens,
            holdout_cases=tuple(holdout_cases),
            created_at=utc_now(),
        )
        bundle_path = guard_root / "holdout.bundle.abfg"
        encrypt_json(bundle.to_dict(), bundle_path, secret=guard_secret, aad=aad)
        bundle_digest = guard_artifact_digest(bundle_path)
        manifest_id = f"{manifest.manifest_id}-guarded"
        public_manifest = record_with_digest(
            {
                "schema_version": 3,
                "manifest_id": manifest_id,
                "seed_manifest_digest": manifest.manifest_digest,
                "framework_revision": manifest.framework_revision,
                "dataset_revision": manifest.dataset_revision,
                "runtime_id": str(doctor["runtime_id"]),
                "verifier_runtime_id": str(doctor["verifier_runtime_id"]),
                "optimization_cases": optimization_references,
                "guard": {
                    "guard_id": guard_id,
                    "code_identity": code_identity.to_dict(),
                    "bundle_sha256": bundle_digest,
                    "preflight_archive_sha256": bundle.preflight_archive_sha256,
                    "waves": {
                        str(wave): {
                            "token": wave_tokens[str(wave)],
                            "holdout_count": {3: 1, 8: 3, 16: 6}[wave],
                            "total_case_count": wave,
                        }
                        for wave in (3, 8, 16)
                    },
                },
            }
        )
        visible_path = self.store.write_visible_yaml(
            manifest_id,
            "manifest.yaml",
            public_manifest,
        )
        visible_dataset = self.store.write_visible_jsonl_rows(
            manifest_id,
            "optimization.jsonl",
            [
                self._visible_case_row(receipt)
                for receipt in optimization_receipts
            ],
        )
        return {
            "manifest_id": manifest_id,
            "guard_id": guard_id,
            "encrypted_bundle_sha256": bundle_digest,
            "visible_manifest": str(visible_path),
            "optimization_dataset": str(visible_dataset),
            "optimization_count": len(optimization_references),
            "sealed_holdout_count": len(holdout_cases),
            "waves": {3: 3, 8: 8, 16: 16},
        }

    @staticmethod
    def _problem_statement(receipt) -> tuple[str, list[dict[str, str]]]:
        issue_path = Path(receipt.issue_evidence_path)
        attachments: list[dict[str, str]] = []
        title = f"Repair Defects4J {receipt.project}-{receipt.bug_id}"
        body = ""
        if issue_path.is_file():
            data = yaml.safe_load(issue_path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
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
                "Modify production source only. The harness will run the pinned official Defects4J test suite after each attempt.",
            )
            if part.strip()
        )
        return problem, attachments

    def _visible_case_row(
        self,
        receipt,
        *,
        problem_override: str | None = None,
        attachments_override: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        source_repo = Path(receipt.sanitized_repo_path).resolve()
        receipt_digest = str(receipt.to_dict()["record_digest"])
        managed_verifier = managed_verifier_for_receipt(
            receipt,
            self.config.eval.benchmarks,
        )
        if problem_override is None:
            problem, attachments = self._problem_statement(receipt)
        else:
            problem = problem_override
            attachments = [dict(item) for item in attachments_override or ()]
        return {
            "schema_version": 1,
            "case_id": receipt.case_id,
            "source": {
                "adapter": "defects4j",
                "benchmark": "defects4j",
                "revision": receipt.dataset_revision,
                "split": receipt.role,
                "instance_id": receipt.case_id,
            },
            "task": {
                "type": "bugfix",
                "problem_statement": problem,
                "agent_prompt": problem,
                "expected_behavior": "The pinned official Defects4J test suite has no failing tests.",
                "attachments": attachments,
            },
            "repository": {
                "repo_id": f"defects4j-{receipt.project.lower()}-{receipt.bug_id}",
                "worktree_path": str(source_repo),
                "base_commit": receipt.sanitized_base_sha,
            },
            "environment": {
                "image": receipt.verifier_runtime_id,
                "platform": self.config.eval.benchmarks.defects4j.platform,
            },
            "execution": {"test_command": managed_verifier.command_id},
            "oracle": {
                "type": "defects4j",
                "require_patch": True,
                "timeout_seconds": self.config.eval.benchmarks.command_timeout_seconds,
                "visibility": "hidden",
            },
            "benchmark": {
                "framework_revision": receipt.framework_revision,
                "dataset_revision": receipt.dataset_revision,
                "runtime_id": receipt.runtime_id,
                "eligibility_receipt_digest": receipt_digest,
                "visible_evidence_digest": receipt.issue_evidence_digest,
            },
            "experiment": {
                "role": receipt.role,
                "first_wave": receipt.first_wave,
                "repository_group": receipt.project,
                "case_token": receipt.case_id,
            },
            "defects4j": {
                "project": receipt.project,
                "bug_id": receipt.bug_id,
                "triggering_tests": list(receipt.triggering_tests),
                "source_roots": list(receipt.source_roots),
                "verifier_command_id": managed_verifier.command_id,
            },
        }

    def _execute_receipt(
        self,
        receipt: EligibilityReceipt,
        *,
        manifest_id: str,
        out_root: Path,
        run_id: str,
        max_attempts: int,
        private_root: Path | None = None,
        problem_override: str | None = None,
        attachments_override: Sequence[Mapping[str, str]] | None = None,
    ) -> dict[str, Any]:
        safe_component(run_id, "run_id")
        row = self._visible_case_row(
            receipt,
            problem_override=problem_override,
            attachments_override=attachments_override,
        )
        if private_root is None:
            dataset = self.store.write_visible_jsonl(
                manifest_id,
                f"{run_id}-{receipt.case_id}.jsonl",
                row,
            )
        else:
            dataset = private_root / "datasets" / f"{run_id}.jsonl"
            dataset.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            dataset.write_text(
                json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            dataset.chmod(0o600)
        run_dir = run_eval(
            self.project_root,
            dataset,
            out_root.resolve(),
            case_selector=receipt.case_id,
            run_id=run_id,
            model="gpt-5.4-mini",
            max_attempts=max_attempts,
            verifier_backends={
                receipt.case_id: managed_verifier_for_receipt(
                    receipt,
                    self.config.eval.benchmarks,
                )
            },
            sdk_hidden_paths=tuple(
                dict.fromkeys(
                    path.resolve()
                    for path in (
                        self.config.eval.benchmarks.cache_root,
                        self.config.eval.benchmarks.trusted_case_root,
                        self.config.operator.state.root,
                        self.config.operator.artifacts.root,
                        (
                            self.config.task_root
                            if self.config.task_root.is_absolute()
                            else self.project_root / self.config.task_root
                        ),
                        self.project_root / ".autobugfix-memory",
                        self.project_root / ".autobugfix/archive",
                        self.project_root / ".autobugfix/controller",
                        *((private_root,) if private_root is not None else ()),
                    )
                )
            ),
        )
        summary = yaml.safe_load((run_dir / "summary.yaml").read_text(encoding="utf-8")) or {}
        report = yaml.safe_load(
            (run_dir / receipt.case_id / "report.yaml").read_text(encoding="utf-8")
        ) or {}
        generated = (run_dir / receipt.case_id / "generated.diff").read_text(
            encoding="utf-8"
        )
        gold = Path(receipt.gold_patch_path).read_text(encoding="utf-8")
        report["gold_diff_equal"] = normalize_diff(generated) == normalize_diff(gold)
        write_yaml(run_dir / receipt.case_id / "report.yaml", report)
        return {
            "run_dir": str(run_dir),
            "dataset": str(dataset),
            "receipt_digest": str(receipt.to_dict()["record_digest"]),
            "summary": summary,
            "report": report,
        }

    def run_case(
        self,
        manifest_path: Path,
        *,
        case_selector: str,
        out_root: Path,
        run_id: str,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        if model != "gpt-5.4-mini":
            raise EvalBenchmarkServiceError(
                "Defects4J experiment model must be gpt-5.4-mini"
            )
        manifest_data = yaml.safe_load(manifest_path.resolve().read_text(encoding="utf-8")) or {}
        if not isinstance(manifest_data, dict):
            raise EvalBenchmarkServiceError("benchmark manifest must be a mapping")
        if "guard" in manifest_data:
            try:
                verify_record(manifest_data)
            except BenchmarkContractError as exc:
                raise EvalBenchmarkServiceError(str(exc)) from exc
            if int(manifest_data.get("schema_version") or 0) != 3:
                raise EvalBenchmarkServiceError("unsupported guarded manifest schema")
            raw_references = manifest_data.get("optimization_cases") or []
            if not isinstance(raw_references, list):
                raise EvalBenchmarkServiceError(
                    "guarded Optimization references must be a list"
                )
            references = [
                item
                for item in raw_references
                if isinstance(item, dict)
                and case_selector in {item.get("case_id"), item.get("case_token")}
            ]
            if len(references) != 1:
                raise EvalBenchmarkServiceError(
                    "no unique guarded Optimization case selected; Holdout requires guard-run"
                )
            reference = references[0]
            receipt_digest = str(reference.get("receipt_digest") or "")
            receipt_path = self.store.receipt_path(
                str(reference.get("case_id") or ""),
                receipt_digest,
            )
            receipt = self.store.read_receipt(receipt_path)
            if (
                str(receipt.to_dict()["record_digest"]) != receipt_digest
                or receipt.role != "optimization"
                or receipt.project != str(reference.get("project") or "")
                or receipt.bug_id != int(reference.get("bug_id") or 0)
                or receipt.runtime_id != str(manifest_data.get("runtime_id") or "")
                or receipt.verifier_runtime_id
                != str(manifest_data.get("verifier_runtime_id") or "")
            ):
                raise EvalBenchmarkServiceError(
                    "guarded Optimization reference does not match its receipt"
                )
            manifest_id = safe_component(
                manifest_data.get("manifest_id"),
                "manifest_id",
            )
            projected_receipt_digest = receipt_digest
        elif "holdout_cases" in manifest_data:
            raise EvalBenchmarkServiceError(
                "legacy plaintext sealed manifests are compromised and cannot be executed; "
                "create a schema v3 code-bound encrypted Guard seal"
            )
        else:
            preflight = self.preflight(manifest_path, case_selector=case_selector)
            case_projection = preflight["cases"][0]
            if case_projection["status"] != "eligible":
                raise EvalBenchmarkServiceError(
                    f"Defects4J case is not eligible: {case_projection['reason']}"
                )
            manifest = BenchmarkSeedManifest.from_yaml(manifest_path.resolve())
            receipt_path = Path(str(case_projection["receipt_path"])).resolve()
            receipt = self.store.read_receipt(receipt_path)
            manifest_id = manifest.manifest_id
            projected_receipt_digest = str(case_projection["receipt_digest"])
        result = self._execute_receipt(
            receipt,
            manifest_id=manifest_id,
            out_root=out_root,
            run_id=run_id,
            max_attempts=max_attempts,
        )
        if result["receipt_digest"] != projected_receipt_digest:
            raise EvalBenchmarkServiceError("executed receipt digest changed")
        return result

    def _load_guard_bundle(
        self,
        public_manifest_path: Path,
        guard_secret: str | bytes,
    ) -> tuple[dict[str, Any], GuardBundle, bytes]:
        public = yaml.safe_load(
            public_manifest_path.resolve().read_text(encoding="utf-8")
        ) or {}
        if not isinstance(public, dict):
            raise EvalBenchmarkServiceError("Guard public manifest must be a mapping")
        try:
            verify_record(public)
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        guard = public.get("guard")
        if int(public.get("schema_version") or 0) != 3 or not isinstance(guard, dict):
            raise EvalBenchmarkServiceError("unsupported Guard public manifest")
        guard_id = safe_component(guard.get("guard_id"), "guard_id")
        raw_identity = guard.get("code_identity") or {}
        if not isinstance(raw_identity, Mapping):
            raise EvalBenchmarkServiceError("Guard public code identity must be a mapping")
        try:
            public_identity = GuardCodeIdentity.from_dict(raw_identity)
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(str(exc)) from exc
        aad = guard_aad(
            guard_id,
            str(public.get("seed_manifest_digest") or ""),
            str(public.get("framework_revision") or ""),
            str(public.get("dataset_revision") or ""),
            public_identity.identity_digest,
        )
        bundle_path = (
            self.config.eval.benchmarks.trusted_case_root
            / "guard"
            / guard_id
            / "holdout.bundle.abfg"
        ).resolve()
        if not bundle_path.is_relative_to(
            self.config.eval.benchmarks.trusted_case_root
        ) or not bundle_path.is_file():
            raise EvalBenchmarkServiceError("encrypted Guard bundle is missing")
        if guard_artifact_digest(bundle_path) != str(guard.get("bundle_sha256") or ""):
            raise EvalBenchmarkServiceError("encrypted Guard bundle digest mismatch")
        try:
            bundle = GuardBundle.from_dict(
                decrypt_json(
                    bundle_path,
                    secret=guard_secret,
                    aad=aad,
                )
            )
        except BenchmarkContractError as exc:
            raise EvalBenchmarkServiceError(f"Guard bundle authentication failed: {exc}") from exc
        if (
            bundle.guard_id != guard_id
            or bundle.seed_manifest_digest
            != str(public.get("seed_manifest_digest") or "")
            or bundle.runtime_id != str(public.get("runtime_id") or "")
            or bundle.verifier_runtime_id
            != str(public.get("verifier_runtime_id") or "")
            or bundle.code_identity != public_identity
        ):
            raise EvalBenchmarkServiceError(
                "decrypted Guard bundle does not match public authority projection"
            )
        preflight_archive = bundle_path.parent / bundle.preflight_archive_name
        if (
            not preflight_archive.is_file()
            or guard_artifact_digest(preflight_archive)
            != bundle.preflight_archive_sha256
        ):
            raise EvalBenchmarkServiceError(
                "encrypted Guard preflight evidence is missing or changed"
            )
        return public, bundle, aad

    def guard_run(
        self,
        public_manifest_path: Path,
        *,
        wave_token: str,
        out_root: Path,
        run_id: str,
        guard_secret: str | bytes,
        model: str = "gpt-5.4-mini",
        max_attempts: int = 2,
        study_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if model != "gpt-5.4-mini":
            raise EvalBenchmarkServiceError(
                "Guard experiment model must be gpt-5.4-mini"
            )
        safe_component(run_id, "run_id")
        current_identity = self.guard_authority()
        public, bundle, aad = self._load_guard_bundle(
            public_manifest_path,
            guard_secret,
        )
        if current_identity != bundle.code_identity:
            raise EvalBenchmarkServiceError(
                "current Guard control-plane identity differs from the sealed authority"
            )
        if study_binding is not None:
            try:
                verify_record(study_binding)
            except BenchmarkContractError as exc:
                raise EvalBenchmarkServiceError(
                    f"Guard Study binding is invalid: {exc}"
                ) from exc
            if (
                str(study_binding.get("subject_sha") or "")
                != current_identity.trusted_commit
            ):
                raise EvalBenchmarkServiceError(
                    "direct Guard runner can measure only its trusted checkout; "
                    "candidate Study metrics require an isolated subject broker"
                )
        matched_waves = [
            int(wave)
            for wave, token in bundle.wave_tokens.items()
            if hmac.compare_digest(token, wave_token)
        ]
        if len(matched_waves) != 1:
            raise EvalBenchmarkServiceError("invalid opaque Guard wave token")
        wave = matched_waves[0]
        selected = [case for case in bundle.holdout_cases if case.first_wave <= wave]
        expected_count = {3: 1, 8: 3, 16: 6}[wave]
        if len(selected) != expected_count:
            raise EvalBenchmarkServiceError("Guard wave selection contract is invalid")

        runtime = Defects4JRuntime(self.config.eval.benchmarks)
        doctor = self._doctor_runtime("defects4j", runtime)
        if not doctor["passed"]:
            raise EvalBenchmarkServiceError(
                f"Defects4J doctor failed before Guard execution: {doctor['report_digest']}"
            )
        if (
            doctor["runtime_id"] != bundle.runtime_id
            or doctor["verifier_runtime_id"] != bundle.verifier_runtime_id
        ):
            raise EvalBenchmarkServiceError(
                "current Docker authorities differ from the sealed Guard runtimes"
            )

        output_root = out_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        encrypted_artifacts = output_root / f"{run_id}.artifacts.abfg"
        metric_path = output_root / f"{run_id}.metric.yaml"
        if encrypted_artifacts.exists() or metric_path.exists():
            raise EvalBenchmarkServiceError("Guard output already exists for run_id")
        reports: list[dict[str, Any]] = []
        fatal: BaseException | None = None
        artifact_aad = aad + f":run:{run_id}:wave:{wave}".encode("ascii")
        with tempfile.TemporaryDirectory(prefix="autobugfix-holdout-run-") as temporary:
            private_root = Path(temporary)
            private_root.chmod(0o700)
            private_config = replace(
                self.config.eval.benchmarks,
                cache_root=private_root / "cache",
                trusted_case_root=private_root / "trusted",
                visible_manifest_root=private_root / "visible",
            )
            private_runtime = Defects4JRuntime(private_config)
            docker_bin = runtime.docker_bin
            if docker_bin is None:
                raise EvalBenchmarkServiceError("inspected Docker binary disappeared")
            private_runtime.bind_inspected_runtime(
                docker_bin=docker_bin,
                runtime_id=bundle.runtime_id,
                verifier_runtime_id=bundle.verifier_runtime_id,
            )
            try:
                for index, case_spec in enumerate(selected, start=1):
                    case_root = private_root / "cases" / f"case-{index:02d}"
                    case_root.mkdir(parents=True, mode=0o700, exist_ok=False)
                    case = BenchmarkCaseSeed(
                        case_id=case_spec.case_token,
                        project=case_spec.project,
                        bug_id=case_spec.bug_id,
                        first_wave=case_spec.first_wave,
                    )
                    receipt = private_runtime.preflight_case(
                        BenchmarkSeedManifest(
                            manifest_id="guard-private-revalidation",
                            benchmark="defects4j",
                            framework_revision=bundle.framework_revision,
                            dataset_revision=bundle.dataset_revision,
                            optimization_cases=tuple(
                                BenchmarkCaseSeed(
                                    case_id=f"placeholder-{number}",
                                    project="GuardPlaceholderA"
                                    if number <= 5
                                    else "GuardPlaceholderB",
                                    bug_id=number,
                                    first_wave=3 if number <= 2 else 8 if number <= 5 else 16,
                                )
                                for number in range(1, 11)
                            ),
                        ),
                        case,
                        role="sealed_holdout",
                        first_wave=case.first_wave,
                        artifact_root=case_root / "preflight",
                    )
                    observed_fingerprint = self._receipt_semantic_fingerprint(receipt)
                    write_yaml(
                        case_root / "guard-revalidation.yaml",
                        {
                            "status": receipt.status,
                            "expected_fingerprint": case_spec.semantic_fingerprint,
                            "observed_fingerprint": observed_fingerprint,
                            "matched": observed_fingerprint
                            == case_spec.semantic_fingerprint,
                        },
                    )
                    if (
                        receipt.status != "eligible"
                        or observed_fingerprint != case_spec.semantic_fingerprint
                    ):
                        reports.append(
                            {
                                "decision": "error",
                                "failure_stage": "guard_revalidation",
                            }
                        )
                        continue
                    result = self._execute_receipt(
                        receipt,
                        manifest_id=bundle.guard_id,
                        out_root=case_root / "eval-runs",
                        run_id=f"case-{index:02d}",
                        max_attempts=max_attempts,
                        private_root=case_root,
                        problem_override=case_spec.problem_statement,
                        attachments_override=case_spec.attachments,
                    )
                    reports.append(dict(result["report"]))
            except BaseException as exc:
                fatal = exc
                (private_root / "guard-fatal.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n",
                    encoding="utf-8",
                )
            finally:
                encrypt_artifact_tree(
                    private_root,
                    encrypted_artifacts,
                    secret=guard_secret,
                    aad=artifact_aad,
                )

        if fatal is not None:
            raise EvalBenchmarkServiceError(
                f"Guard run failed after encrypting partial evidence: {fatal}"
            ) from fatal
        passed = sum(1 for report in reports if report.get("decision") == "pass")
        failed = sum(1 for report in reports if report.get("decision") == "fail")
        errors = len(reports) - passed - failed
        metric = signed_metric(
            metric_payload(
                guard_id=bundle.guard_id,
                run_id=run_id,
                wave=wave,
                case_count=len(selected),
                passed_count=passed,
                failed_count=failed,
                harness_error_count=errors,
                encrypted_artifact_sha256=guard_artifact_digest(encrypted_artifacts),
                public_manifest_digest=str(public["record_digest"]),
                code_identity=current_identity,
                study_binding=study_binding,
            ),
            guard_secret,
        )
        write_yaml(metric_path, metric)
        metric_path.chmod(0o644)
        return {
            "guard_id": bundle.guard_id,
            "run_id": run_id,
            "wave": wave,
            "case_count": len(selected),
            "passed_count": passed,
            "failed_count": failed,
            "harness_error_count": errors,
            "pass_rate": passed / len(selected),
            "metric_receipt": str(metric_path),
            "encrypted_artifacts": str(encrypted_artifacts),
            "encrypted_artifacts_sha256": metric[
                "encrypted_artifact_sha256"
            ],
            "public_manifest_digest": public["record_digest"],
        }
