from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.baselines.swe_raw_models import SWERawSubmission
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
    safe_component,
)
from autobugfix.eval.benchmarks.swe_submission import (
    EVIDENCE_MANIFEST_NAME,
    verify_evidence_manifest,
)
from autobugfix.models import utc_now


@dataclass(slots=True, frozen=True)
class FrozenSWERawSubmission:
    submission: SWERawSubmission
    root: Path
    record_path: Path
    patch_path: Path
    evidence_root: Path

    def identity(self) -> dict[str, str]:
        for path in (self.root, self.record_path, self.patch_path, self.evidence_root):
            if path.is_symlink():
                raise BenchmarkContractError(
                    "frozen SWE Raw submission cannot contain symlinks"
                )
        if not self.record_path.is_file() or not self.patch_path.is_file():
            raise BenchmarkContractError("frozen SWE Raw submission is incomplete")
        raw = yaml.safe_load(self.record_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise BenchmarkContractError("frozen SWE Raw record must be a mapping")
        observed = SWERawSubmission.from_record(
            raw,
            patch=self.patch_path.read_text(encoding="utf-8"),
        )
        if observed != self.submission:
            raise BenchmarkContractError("frozen SWE Raw submission changed")
        evidence = verify_evidence_manifest(
            self.evidence_root,
            self.submission.evidence_manifest_digest,
        )
        return {
            "record_digest": str(raw["record_digest"]),
            "record_sha256": digest_file(self.record_path),
            "patch_sha256": digest_file(self.patch_path),
            "evidence_manifest_digest": str(evidence["record_digest"]),
            "evidence_manifest_sha256": digest_file(
                self.evidence_root / EVIDENCE_MANIFEST_NAME
            ),
        }


class SWERawSubmissionAuthority:
    def __init__(self, trusted_root: Path):
        self.trusted_root = trusted_root.resolve()

    @staticmethod
    def _write_once(path: Path, text: str) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    def freeze(
        self,
        submission: SWERawSubmission,
        evidence_root: Path,
    ) -> FrozenSWERawSubmission:
        verify_evidence_manifest(
            evidence_root,
            submission.evidence_manifest_digest,
        )
        record = submission.record
        record_digest = str(record["record_digest"])
        parent = (
            self.trusted_root
            / "swe"
            / "raw-submissions"
            / safe_component(submission.case_token, "case_token")
        ).resolve()
        if not parent.is_relative_to(self.trusted_root):
            raise BenchmarkContractError("SWE Raw submission path escapes trusted state")
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination = parent / record_digest
        if destination.exists():
            raise BenchmarkContractError("frozen SWE Raw submission already exists")
        staging = Path(tempfile.mkdtemp(prefix=".freeze-", dir=parent))
        try:
            staging.chmod(0o700)
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
        return self.load(destination)

    def load(self, root: Path) -> FrozenSWERawSubmission:
        resolved = root.resolve()
        expected = (self.trusted_root / "swe" / "raw-submissions").resolve()
        if not resolved.is_relative_to(expected):
            raise BenchmarkContractError("SWE Raw submission is outside trusted state")
        record_path = resolved / "submission.yaml"
        patch_path = resolved / "patch.diff"
        evidence_root = resolved / "evidence"
        if not record_path.is_file() or not patch_path.is_file() or not evidence_root.is_dir():
            raise BenchmarkContractError("frozen SWE Raw submission is incomplete")
        raw = yaml.safe_load(record_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise BenchmarkContractError("frozen SWE Raw record must be a mapping")
        submission = SWERawSubmission.from_record(
            raw,
            patch=patch_path.read_text(encoding="utf-8"),
        )
        if resolved.name != str(raw.get("record_digest") or ""):
            raise BenchmarkContractError("SWE Raw submission directory digest drift")
        frozen = FrozenSWERawSubmission(
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
        frozen: FrozenSWERawSubmission,
        before: Mapping[str, str],
        *,
        official_result_digest: str,
        source_unchanged: bool,
        worktree_unchanged: bool,
    ) -> dict[str, Any]:
        after = frozen.identity()
        unchanged = (
            dict(before) == after and source_unchanged and worktree_unchanged
        )
        receipt = record_with_digest(
            {
                "schema": "autobugfix-swe-raw-noninterference-v1",
                "case_token": frozen.submission.case_token,
                "submission_digest": frozen.submission.record["record_digest"],
                "official_result_digest": official_result_digest,
                "submission_unchanged": dict(before) == after,
                "source_unchanged": source_unchanged,
                "worktree_unchanged": worktree_unchanged,
                "unchanged": unchanged,
                "before": dict(before),
                "after": after,
                "checked_at": utc_now(),
            }
        )
        if not unchanged:
            raise BenchmarkContractError(
                "official scorer changed frozen SWE Raw generation state"
            )
        return receipt
