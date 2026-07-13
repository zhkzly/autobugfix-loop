from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.swe_models import SWESubmission
from autobugfix.models import utc_now


EVIDENCE_MANIFEST_NAME = "manifest.yaml"


def write_evidence_manifest(root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise BenchmarkContractError("SWE evidence root is missing")
    manifest_path = resolved / EVIDENCE_MANIFEST_NAME
    if manifest_path.exists():
        raise BenchmarkContractError("SWE evidence manifest already exists")
    files: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise BenchmarkContractError("SWE evidence cannot contain symlinks")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(resolved).as_posix(),
                    "sha256": digest_file(path),
                    "size": path.stat().st_size,
                }
            )
    record = record_with_digest(
        {
            "schema": "autobugfix-swe-evidence-manifest-v1",
            "files": files,
        }
    )
    manifest_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return record


def verify_evidence_manifest(root: Path, expected_digest: str) -> dict[str, Any]:
    resolved = root.resolve()
    manifest_path = resolved / EVIDENCE_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BenchmarkContractError("frozen SWE evidence manifest is missing")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise BenchmarkContractError("frozen SWE evidence manifest is invalid")
    verify_record(raw)
    if raw.get("schema") != "autobugfix-swe-evidence-manifest-v1":
        raise BenchmarkContractError("unsupported SWE evidence manifest schema")
    if raw.get("record_digest") != expected_digest:
        raise BenchmarkContractError("frozen SWE evidence manifest digest changed")
    entries = raw.get("files")
    if not isinstance(entries, list):
        raise BenchmarkContractError("frozen SWE evidence file list is invalid")
    expected_paths: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            raise BenchmarkContractError("frozen SWE evidence entry is invalid")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise BenchmarkContractError("frozen SWE evidence path is unsafe")
        relative_text = relative.as_posix()
        if relative_text in expected_paths or relative_text == EVIDENCE_MANIFEST_NAME:
            raise BenchmarkContractError("frozen SWE evidence path is duplicated")
        expected_paths.add(relative_text)
        path = resolved / relative
        if path.is_symlink() or not path.is_file():
            raise BenchmarkContractError("frozen SWE evidence artifact is missing")
        if digest_file(path) != str(item.get("sha256") or ""):
            raise BenchmarkContractError("frozen SWE evidence artifact digest changed")
        if path.stat().st_size != int(item.get("size", -1)):
            raise BenchmarkContractError("frozen SWE evidence artifact size changed")
    observed_paths: set[str] = set()
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise BenchmarkContractError("frozen SWE evidence cannot contain symlinks")
        if path.is_file() and path != manifest_path:
            observed_paths.add(path.relative_to(resolved).as_posix())
    if observed_paths != expected_paths:
        raise BenchmarkContractError("frozen SWE evidence file set changed")
    return dict(raw)


@dataclass(slots=True, frozen=True)
class FrozenSWESubmission:
    submission: SWESubmission
    root: Path
    record_path: Path
    patch_path: Path
    evidence_root: Path

    def identity(self) -> dict[str, str]:
        if (
            self.record_path.is_symlink()
            or self.patch_path.is_symlink()
            or self.evidence_root.is_symlink()
        ):
            raise BenchmarkContractError("frozen SWE submission cannot contain symlinks")
        if not self.record_path.is_file() or not self.patch_path.is_file():
            raise BenchmarkContractError("frozen SWE submission artifact is missing")
        patch = self.patch_path.read_text(encoding="utf-8")
        data = yaml.safe_load(self.record_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("frozen SWE submission record must be a mapping")
        observed = SWESubmission.from_record(data, patch=patch)
        if observed != self.submission:
            raise BenchmarkContractError("frozen SWE submission record changed")
        evidence = verify_evidence_manifest(
            self.evidence_root,
            self.submission.evidence_manifest_digest,
        )
        return {
            "record_digest": str(data["record_digest"]),
            "record_sha256": digest_file(self.record_path),
            "patch_sha256": digest_file(self.patch_path),
            "evidence_manifest_digest": str(evidence["record_digest"]),
            "evidence_manifest_sha256": digest_file(
                self.evidence_root / EVIDENCE_MANIFEST_NAME
            ),
        }


class SWESubmissionAuthority:
    def __init__(self, trusted_root: Path):
        self.trusted_root = trusted_root.resolve()

    @staticmethod
    def _write_once(path: Path, text: str, mode: int = 0o400) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    def freeze(
        self,
        adapter: str,
        submission: SWESubmission,
        evidence_root: Path,
    ) -> FrozenSWESubmission:
        adapter_name = safe_component(adapter, "adapter")
        record = submission.record
        record_digest = str(record["record_digest"])
        parent = (
            self.trusted_root
            / "swe"
            / "submissions"
            / adapter_name
            / safe_component(submission.case_token, "case_token")
        ).resolve()
        if not parent.is_relative_to(self.trusted_root):
            raise BenchmarkContractError("SWE submission path escapes trusted root")
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination = parent / record_digest
        if destination.exists():
            raise BenchmarkContractError("frozen SWE submission already exists")
        staging = Path(tempfile.mkdtemp(prefix=".freeze-", dir=parent))
        try:
            staging.chmod(0o700)
            verify_evidence_manifest(
                evidence_root,
                submission.evidence_manifest_digest,
            )
            self._write_once(staging / "patch.diff", submission.patch)
            self._write_once(
                staging / "submission.yaml",
                yaml.safe_dump(record, sort_keys=False),
            )
            shutil.copytree(evidence_root, staging / "evidence", symlinks=False)
            for path in sorted((staging / "evidence").rglob("*"), reverse=True):
                path.chmod(0o500 if path.is_dir() else 0o400)
            (staging / "evidence").chmod(0o500)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        frozen = self.load(destination)
        if frozen.submission != submission:
            raise BenchmarkContractError("frozen SWE submission round-trip changed")
        return frozen

    def load(self, root: Path) -> FrozenSWESubmission:
        resolved = root.resolve()
        submissions_root = (self.trusted_root / "swe" / "submissions").resolve()
        if not resolved.is_relative_to(submissions_root):
            raise BenchmarkContractError("SWE submission is outside trusted root")
        record_path = resolved / "submission.yaml"
        patch_path = resolved / "patch.diff"
        evidence_root = resolved / "evidence"
        if record_path.is_symlink() or patch_path.is_symlink():
            raise BenchmarkContractError("frozen SWE submission cannot contain symlinks")
        if (
            not record_path.is_file()
            or not patch_path.is_file()
            or not evidence_root.is_dir()
        ):
            raise BenchmarkContractError("frozen SWE submission artifact is missing")
        data = yaml.safe_load(record_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("frozen SWE submission record must be a mapping")
        submission = SWESubmission.from_record(
            data,
            patch=patch_path.read_text(encoding="utf-8"),
        )
        if resolved.name != str(data["record_digest"]):
            raise BenchmarkContractError("SWE submission directory does not match digest")
        frozen = FrozenSWESubmission(
            submission=submission,
            root=resolved,
            record_path=record_path,
            patch_path=patch_path,
            evidence_root=evidence_root,
        )
        frozen.identity()
        return frozen

    @staticmethod
    def noninterference_receipt(
        frozen: FrozenSWESubmission,
        before: Mapping[str, str],
        *,
        official_result_digest: str,
    ) -> dict[str, Any]:
        after = frozen.identity()
        unchanged = dict(before) == after
        receipt = record_with_digest(
            {
                "schema": "autobugfix-swe-noninterference-v1",
                "case_token": frozen.submission.case_token,
                "submission_digest": frozen.submission.record["record_digest"],
                "official_result_digest": official_result_digest,
                "unchanged": unchanged,
                "before": dict(before),
                "after": after,
                "checked_at": utc_now(),
            }
        )
        if not unchanged:
            raise BenchmarkContractError(
                "official scorer changed the frozen SWE submission"
            )
        return receipt
