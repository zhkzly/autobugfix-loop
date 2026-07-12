from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    pass


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def digest_payload(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_source_digest() -> str:
    package_root = Path(__file__).resolve().parent
    files = sorted(package_root.rglob("*.py"))
    return digest_payload(
        {
            "files": [
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "sha256": digest_file(path),
                }
                for path in files
            ]
        }
    )


def record_with_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(value)
    record.pop("record_digest", None)
    record["record_digest"] = digest_payload(record)
    return record


def verify_record(value: Mapping[str, Any]) -> None:
    expected = str(value.get("record_digest") or "")
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ContractError("record_digest must be a lowercase sha256")
    unsigned = dict(value)
    unsigned.pop("record_digest", None)
    if digest_payload(unsigned) != expected:
        raise ContractError("record digest mismatch")


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"{name} is required")
    return text


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ContractError(f"{name} must be a list")
    return tuple(_required(item, name) for item in value)


@dataclass(slots=True, frozen=True)
class CaseBundle:
    case_id: str
    benchmark: str
    dataset_revision: str
    base_commit: str
    problem_statement: str
    expected_behavior: str
    visible_evidence: tuple[str, ...]
    attachments: tuple[str, ...]
    record_digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CaseBundle":
        verify_record(value)
        if value.get("schema") != "raw-codex-sdk-case-v1":
            raise ContractError("unsupported Raw Codex case schema")
        return cls(
            case_id=_required(value.get("case_id"), "case_id"),
            benchmark=_required(value.get("benchmark"), "benchmark"),
            dataset_revision=_required(
                value.get("dataset_revision"), "dataset_revision"
            ),
            base_commit=_required(value.get("base_commit"), "base_commit"),
            problem_statement=_required(
                value.get("problem_statement"), "problem_statement"
            ),
            expected_behavior=str(value.get("expected_behavior") or "").strip(),
            visible_evidence=_string_tuple(
                value.get("visible_evidence"), "visible_evidence"
            ),
            attachments=_string_tuple(value.get("attachments"), "attachments"),
            record_digest=str(value["record_digest"]),
        )

    @classmethod
    def from_json(cls, path: Path) -> "CaseBundle":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read case bundle: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ContractError("case bundle must be a JSON object")
        return cls.from_dict(value)
