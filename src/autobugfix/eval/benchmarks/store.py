from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    EligibilityReceipt,
    SealedBenchmarkManifest,
    digest_file,
    safe_component,
    verify_record,
)


class BenchmarkStore:
    def __init__(self, trusted_root: Path, visible_root: Path, cache_root: Path | None = None):
        self.trusted_root = trusted_root.resolve()
        self.visible_root = visible_root.resolve()
        self.cache_root = cache_root.resolve() if cache_root is not None else None

    @staticmethod
    def _write_text_once(path: Path, text: str, mode: int) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _child(root: Path, *components: str) -> Path:
        candidate = root.joinpath(
            *(safe_component(value, "benchmark store path component") for value in components)
        ).resolve()
        if not candidate.is_relative_to(root):
            raise BenchmarkContractError("benchmark store path escapes configured root")
        return candidate

    def _atomic_yaml(self, path: Path, data: Mapping[str, Any], *, mode: int = 0o600) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        self._write_text_once(
            temporary,
            yaml.safe_dump(dict(data), sort_keys=False),
            mode,
        )
        os.replace(temporary, path)
        return path

    def _atomic_text(self, path: Path, text: str, *, mode: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        self._write_text_once(temporary, text, mode)
        os.replace(temporary, path)
        return path

    def write_doctor(self, adapter: str, report: Mapping[str, Any]) -> Path:
        verify_record(report)
        return self._atomic_yaml(
            self._child(
                self.trusted_root,
                "doctor",
                adapter,
                f"{report['record_digest']}.yaml",
            ),
            report,
        )

    def write_receipt(self, receipt: EligibilityReceipt) -> Path:
        data = receipt.to_dict()
        path = self._child(
            self.trusted_root,
            "receipts",
            "defects4j",
            receipt.case_id,
            f"{data['record_digest']}.yaml",
        )
        return self._atomic_yaml(path, data)

    def receipt_path(self, case_id: str, receipt_digest: str) -> Path:
        if len(receipt_digest) != 64 or any(
            value not in "0123456789abcdef" for value in receipt_digest
        ):
            raise BenchmarkContractError("receipt digest must be sha256")
        return self._child(
            self.trusted_root,
            "receipts",
            "defects4j",
            case_id,
            f"{receipt_digest}.yaml",
        )

    def read_receipt(self, path: Path) -> EligibilityReceipt:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.trusted_root):
            raise BenchmarkContractError("receipt path is outside trusted benchmark root")
        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("eligibility receipt must be a mapping")
        receipt = EligibilityReceipt.from_dict(data)
        expected_name = f"{receipt.to_dict()['record_digest']}.yaml"
        if resolved.name != expected_name or resolved.parent.name != receipt.case_id:
            raise BenchmarkContractError("eligibility receipt path does not match its digest and case")
        if receipt.status == "eligible":
            self._verify_receipt_artifacts(receipt)
        return receipt

    def _verify_receipt_artifacts(self, receipt: EligibilityReceipt) -> None:
        gold = Path(receipt.gold_patch_path).resolve()
        issue = Path(receipt.issue_evidence_path).resolve()
        snapshot = Path(receipt.sanitized_repo_path).resolve()
        if not gold.is_relative_to(self.trusted_root) or not gold.is_file():
            raise BenchmarkContractError("receipt gold patch is outside trusted root or missing")
        if digest_file(gold) != receipt.gold_patch_sha256:
            raise BenchmarkContractError("receipt gold patch digest mismatch")
        if receipt.issue_evidence_path != "unavailable":
            if not issue.is_relative_to(self.trusted_root) or not issue.is_file():
                raise BenchmarkContractError("receipt issue evidence is outside trusted root or missing")
            issue_data = yaml.safe_load(issue.read_text(encoding="utf-8")) or {}
            if not isinstance(issue_data, Mapping):
                raise BenchmarkContractError("receipt issue evidence must be a mapping")
            verify_record(issue_data)
            if str(issue_data["record_digest"]) != receipt.issue_evidence_digest:
                raise BenchmarkContractError("receipt issue evidence digest mismatch")
        if receipt.failure_evidence_path != "unavailable":
            failure = Path(receipt.failure_evidence_path).resolve()
            if not failure.is_relative_to(self.trusted_root) or not failure.is_file():
                raise BenchmarkContractError(
                    "receipt failure evidence is outside trusted root or missing"
                )
            if digest_file(failure) != receipt.failure_evidence_sha256:
                raise BenchmarkContractError("receipt failure evidence digest mismatch")
            if receipt.reproduction_command == "unavailable":
                raise BenchmarkContractError(
                    "receipt failure evidence requires a reproduction command"
                )
        if self.cache_root is None or not snapshot.is_relative_to(self.cache_root):
            raise BenchmarkContractError("receipt sanitized repository is outside cache root")
        if not (snapshot / ".git").exists():
            raise BenchmarkContractError("receipt sanitized repository is not a Git snapshot")
        for forbidden in (".defects4j.config", "defects4j.build.properties", "failing_tests"):
            if (snapshot / forbidden).exists():
                raise BenchmarkContractError(
                    f"sanitized repository retains forbidden benchmark hint: {forbidden}"
                )
        head = subprocess.run(
            ["git", "-C", str(snapshot), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(snapshot), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if head.returncode != 0 or head.stdout.strip() != receipt.sanitized_base_sha:
            raise BenchmarkContractError("sanitized repository HEAD does not match receipt")
        if status.returncode != 0 or status.stdout:
            raise BenchmarkContractError("sanitized repository is not clean")
        for command in receipt.commands:
            for field in ("stdout_path", "stderr_path"):
                artifact = Path(str(command[field])).resolve()
                if not artifact.is_relative_to(self.trusted_root) or not artifact.is_file():
                    raise BenchmarkContractError(
                        f"receipt command {field} is outside trusted root or missing"
                    )
                expected = str(command[f"{field.removesuffix('_path')}_sha256"])
                if digest_file(artifact) != expected:
                    raise BenchmarkContractError(
                        f"receipt command {field} digest mismatch"
                    )

    def write_trusted_manifest(
        self, manifest_id: str, name: str, data: Mapping[str, Any]
    ) -> Path:
        verify_record(data)
        return self._atomic_yaml(
            self._child(self.trusted_root, "manifests", manifest_id, name),
            data,
        )

    def read_trusted_manifest(self, path: Path) -> SealedBenchmarkManifest:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.trusted_root):
            raise BenchmarkContractError(
                "manifest path is outside trusted benchmark root"
            )
        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("sealed manifest must be a mapping")
        return SealedBenchmarkManifest.from_dict(data)

    def write_visible_yaml(
        self, manifest_id: str, name: str, data: Mapping[str, Any]
    ) -> Path:
        return self._atomic_yaml(
            self._child(self.visible_root, manifest_id, name),
            data,
            mode=0o644,
        )

    def write_visible_jsonl(self, manifest_id: str, name: str, row: Mapping[str, Any]) -> Path:
        return self.write_visible_jsonl_rows(manifest_id, name, (row,))

    def write_visible_jsonl_rows(
        self,
        manifest_id: str,
        name: str,
        rows: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    ) -> Path:
        text = "".join(
            json.dumps(dict(row), sort_keys=True, ensure_ascii=True) + "\n"
            for row in rows
        )
        return self._atomic_text(
            self._child(self.visible_root, manifest_id, name),
            text,
            mode=0o644,
        )
