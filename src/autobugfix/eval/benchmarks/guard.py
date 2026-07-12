from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.exceptions import InvalidTag

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    canonical_json,
    digest_file,
    record_with_digest,
    safe_component,
    verify_record,
)
from autobugfix.eval.benchmarks.authority import GuardCodeIdentity
from autobugfix.models import DEFECTS4J_FRAMEWORK_REVISION, utc_now


_MAGIC = b"AUTOBUGFIX-GUARD-AESGCM-V1\n"
_TAG_BYTES = 16


def _private_binary_writer(path: Path):
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    return os.fdopen(descriptor, "wb")


def _secret_bytes(secret: str | bytes) -> bytes:
    value = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(value) < 16:
        raise BenchmarkContractError("Guard secret must contain at least 16 bytes")
    return value


def _derive_key(secret: str | bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        _secret_bytes(secret)
    )


def _header(salt: bytes, nonce: bytes, aad: bytes) -> bytes:
    return canonical_json(
        {
            "schema": "autobugfix-guard-aesgcm-v1",
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "aad_sha256": hashlib.sha256(aad).hexdigest(),
        }
    ).encode("ascii")


def encrypt_file(
    source: Path,
    destination: Path,
    *,
    secret: str | bytes,
    aad: bytes,
) -> Path:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = _header(salt, nonce, aad)
    encryptor = Cipher(algorithms.AES(_derive_key(secret, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with source.open("rb") as reader, _private_binary_writer(temporary) as writer:
        writer.write(_MAGIC)
        writer.write(struct.pack(">I", len(header)))
        writer.write(header)
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(encryptor.update(chunk))
        writer.write(encryptor.finalize())
        writer.write(encryptor.tag)
        writer.flush()
        os.fsync(writer.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    return destination


def decrypt_file(
    source: Path,
    destination: Path,
    *,
    secret: str | bytes,
    aad: bytes,
) -> Path:
    with source.open("rb") as reader:
        if reader.read(len(_MAGIC)) != _MAGIC:
            raise BenchmarkContractError("invalid Guard envelope magic")
        length_raw = reader.read(4)
        if len(length_raw) != 4:
            raise BenchmarkContractError("truncated Guard envelope header")
        header_length = struct.unpack(">I", length_raw)[0]
        if header_length < 1 or header_length > 64 * 1024:
            raise BenchmarkContractError("invalid Guard envelope header length")
        header_raw = reader.read(header_length)
        try:
            header = json.loads(header_raw)
            salt = base64.b64decode(header["salt"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BenchmarkContractError("invalid Guard envelope header") from exc
        if header.get("schema") != "autobugfix-guard-aesgcm-v1":
            raise BenchmarkContractError("unsupported Guard envelope schema")
        if header.get("aad_sha256") != hashlib.sha256(aad).hexdigest():
            raise BenchmarkContractError("Guard envelope authority binding mismatch")
        payload_start = len(_MAGIC) + 4 + header_length
        payload_length = source.stat().st_size - payload_start - _TAG_BYTES
        if payload_length < 0:
            raise BenchmarkContractError("truncated Guard envelope payload")
        reader.seek(source.stat().st_size - _TAG_BYTES)
        tag = reader.read(_TAG_BYTES)
        reader.seek(payload_start)
        decryptor = Cipher(
            algorithms.AES(_derive_key(secret, salt)),
            modes.GCM(nonce, tag),
        ).decryptor()
        decryptor.authenticate_additional_data(aad)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        remaining = payload_length
        try:
            with _private_binary_writer(temporary) as writer:
                while remaining:
                    chunk = reader.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise BenchmarkContractError("truncated Guard ciphertext")
                    remaining -= len(chunk)
                    writer.write(decryptor.update(chunk))
                writer.write(decryptor.finalize())
                writer.flush()
                os.fsync(writer.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, destination)
        except InvalidTag as exc:
            temporary.unlink(missing_ok=True)
            raise BenchmarkContractError(
                "Guard envelope authentication failed"
            ) from exc
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return destination


def encrypt_json(
    payload: Mapping[str, Any],
    destination: Path,
    *,
    secret: str | bytes,
    aad: bytes,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="autobugfix-guard-json-") as root:
        plaintext = Path(root) / "payload.json"
        plaintext.write_text(canonical_json(payload), encoding="ascii")
        plaintext.chmod(0o600)
        return encrypt_file(plaintext, destination, secret=secret, aad=aad)


def decrypt_json(
    source: Path,
    *,
    secret: str | bytes,
    aad: bytes,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="autobugfix-guard-json-") as root:
        plaintext = Path(root) / "payload.json"
        decrypt_file(source, plaintext, secret=secret, aad=aad)
        try:
            value = json.loads(plaintext.read_text(encoding="ascii"))
        except json.JSONDecodeError as exc:
            raise BenchmarkContractError("Guard payload is not valid JSON") from exc
        if not isinstance(value, dict):
            raise BenchmarkContractError("Guard payload must be a mapping")
        return value


def encrypt_artifact_tree(
    source: Path,
    destination: Path,
    *,
    secret: str | bytes,
    aad: bytes,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="autobugfix-guard-archive-") as root:
        archive = Path(root) / "artifacts.tar"
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(source, arcname="artifacts", recursive=True)
        return encrypt_file(archive, destination, secret=secret, aad=aad)


def signed_metric(payload: Mapping[str, Any], secret: str | bytes) -> dict[str, Any]:
    salt = os.urandom(16)
    key = _derive_key(secret, salt)
    body = dict(payload)
    signature = hmac.new(
        key,
        b"autobugfix-guard-metric-v1\0" + canonical_json(body).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return record_with_digest(
        {
            **body,
            "authority": "guard-aesgcm-v1",
            "signature_salt": base64.b64encode(salt).decode("ascii"),
            "authority_signature": signature,
        }
    )


def verify_signed_metric(data: Mapping[str, Any], secret: str | bytes) -> None:
    verify_record(data)
    try:
        salt = base64.b64decode(str(data["signature_salt"]), validate=True)
        stored = str(data["authority_signature"])
    except (KeyError, ValueError) as exc:
        raise BenchmarkContractError("invalid Guard metric signature fields") from exc
    body = {
        key: value
        for key, value in data.items()
        if key not in {"record_digest", "authority", "signature_salt", "authority_signature"}
    }
    expected = hmac.new(
        _derive_key(secret, salt),
        b"autobugfix-guard-metric-v1\0" + canonical_json(body).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(stored, expected):
        raise BenchmarkContractError("Guard metric authority signature mismatch")


@dataclass(slots=True, frozen=True)
class GuardCaseSpec:
    case_token: str
    project: str
    bug_id: int
    first_wave: int
    semantic_fingerprint: str
    problem_statement: str
    attachments: tuple[Mapping[str, str], ...]

    def __post_init__(self) -> None:
        safe_component(self.case_token, "case_token")
        if self.bug_id < 1 or self.first_wave not in {3, 8, 16}:
            raise BenchmarkContractError("invalid Guard case identity or wave")
        if len(self.semantic_fingerprint) != 64:
            raise BenchmarkContractError("Guard case fingerprint must be sha256")
        if not self.project or not self.problem_statement:
            raise BenchmarkContractError("Guard case project and problem are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_token": self.case_token,
            "project": self.project,
            "bug_id": self.bug_id,
            "first_wave": self.first_wave,
            "semantic_fingerprint": self.semantic_fingerprint,
            "problem_statement": self.problem_statement,
            "attachments": [dict(item) for item in self.attachments],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuardCaseSpec":
        attachments = data.get("attachments") or []
        if not isinstance(attachments, Sequence) or isinstance(attachments, (str, bytes)):
            raise BenchmarkContractError("Guard case attachments must be a list")
        if not all(isinstance(item, Mapping) for item in attachments):
            raise BenchmarkContractError("Guard case attachment must be a mapping")
        return cls(
            case_token=safe_component(data.get("case_token"), "case_token"),
            project=str(data.get("project") or ""),
            bug_id=int(data.get("bug_id") or 0),
            first_wave=int(data.get("first_wave") or 0),
            semantic_fingerprint=str(data.get("semantic_fingerprint") or ""),
            problem_statement=str(data.get("problem_statement") or ""),
            attachments=tuple({str(key): str(value) for key, value in item.items()} for item in attachments),
        )


@dataclass(slots=True, frozen=True)
class GuardBundle:
    guard_id: str
    seed_manifest_digest: str
    framework_revision: str
    dataset_revision: str
    runtime_id: str
    verifier_runtime_id: str
    code_identity: GuardCodeIdentity
    preflight_archive_name: str
    preflight_archive_sha256: str
    wave_tokens: Mapping[str, str]
    holdout_cases: tuple[GuardCaseSpec, ...]
    created_at: str
    schema_version: int = 2

    def __post_init__(self) -> None:
        safe_component(self.guard_id, "guard_id")
        if self.schema_version != 2:
            raise BenchmarkContractError("unsupported Guard bundle schema")
        if self.framework_revision != DEFECTS4J_FRAMEWORK_REVISION:
            raise BenchmarkContractError("Guard framework revision is not pinned")
        if not self.runtime_id.startswith("sha256:") or not self.verifier_runtime_id.startswith(
            "sha256:"
        ):
            raise BenchmarkContractError("Guard runtimes must be immutable image IDs")
        safe_component(self.preflight_archive_name, "preflight archive name")
        if len(self.preflight_archive_sha256) != 64:
            raise BenchmarkContractError("Guard preflight archive digest must be sha256")
        if set(self.wave_tokens) != {"3", "8", "16"}:
            raise BenchmarkContractError("Guard wave tokens must cover 3, 8, and 16")
        for token in self.wave_tokens.values():
            safe_component(token, "wave token")
        if len(self.holdout_cases) != 6:
            raise BenchmarkContractError("Guard bundle requires six Holdout cases")
        if len({item.case_token for item in self.holdout_cases}) != 6:
            raise BenchmarkContractError("Guard Holdout tokens must be unique")
        projects = {item.project for item in self.holdout_cases}
        if len(projects) < 3:
            raise BenchmarkContractError("Guard Holdout requires three repository groups")
        counts = {
            wave: sum(1 for item in self.holdout_cases if item.first_wave <= wave)
            for wave in (3, 8, 16)
        }
        if counts != {3: 1, 8: 3, 16: 6}:
            raise BenchmarkContractError("Guard Holdout waves must contain 1, 3, and 6 cases")

    @property
    def aad(self) -> bytes:
        return canonical_json(
            {
                "guard_id": self.guard_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "guard_code_identity_digest": self.code_identity.identity_digest,
            }
        ).encode("ascii")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "guard_id": self.guard_id,
                "seed_manifest_digest": self.seed_manifest_digest,
                "framework_revision": self.framework_revision,
                "dataset_revision": self.dataset_revision,
                "runtime_id": self.runtime_id,
                "verifier_runtime_id": self.verifier_runtime_id,
                "code_identity": self.code_identity.to_dict(),
                "preflight_archive_name": self.preflight_archive_name,
                "preflight_archive_sha256": self.preflight_archive_sha256,
                "wave_tokens": dict(self.wave_tokens),
                "holdout_cases": [item.to_dict() for item in self.holdout_cases],
                "created_at": self.created_at,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuardBundle":
        verify_record(data)
        raw_cases = data.get("holdout_cases") or []
        raw_tokens = data.get("wave_tokens") or {}
        raw_identity = data.get("code_identity") or {}
        if not isinstance(raw_cases, Sequence) or isinstance(raw_cases, (str, bytes)):
            raise BenchmarkContractError("Guard holdout_cases must be a list")
        if not isinstance(raw_tokens, Mapping):
            raise BenchmarkContractError("Guard wave_tokens must be a mapping")
        if not isinstance(raw_identity, Mapping):
            raise BenchmarkContractError("Guard code_identity must be a mapping")
        if not all(isinstance(item, Mapping) for item in raw_cases):
            raise BenchmarkContractError("Guard case must be a mapping")
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            guard_id=safe_component(data.get("guard_id"), "guard_id"),
            seed_manifest_digest=str(data.get("seed_manifest_digest") or ""),
            framework_revision=str(data.get("framework_revision") or ""),
            dataset_revision=str(data.get("dataset_revision") or ""),
            runtime_id=str(data.get("runtime_id") or ""),
            verifier_runtime_id=str(data.get("verifier_runtime_id") or ""),
            code_identity=GuardCodeIdentity.from_dict(raw_identity),
            preflight_archive_name=str(data.get("preflight_archive_name") or ""),
            preflight_archive_sha256=str(
                data.get("preflight_archive_sha256") or ""
            ),
            wave_tokens={str(key): str(value) for key, value in raw_tokens.items()},
            holdout_cases=tuple(GuardCaseSpec.from_dict(item) for item in raw_cases),
            created_at=str(data.get("created_at") or ""),
        )


def guard_aad(
    guard_id: str,
    seed_manifest_digest: str,
    framework_revision: str,
    dataset_revision: str,
    guard_code_identity_digest: str,
) -> bytes:
    return canonical_json(
        {
            "guard_id": guard_id,
            "seed_manifest_digest": seed_manifest_digest,
            "framework_revision": framework_revision,
            "dataset_revision": dataset_revision,
            "guard_code_identity_digest": guard_code_identity_digest,
        }
    ).encode("ascii")


def new_guard_id() -> str:
    return f"guard-{uuid.uuid4().hex}"


def guard_artifact_digest(path: Path) -> str:
    return digest_file(path)


def metric_payload(
    *,
    guard_id: str,
    run_id: str,
    wave: int,
    case_count: int,
    passed_count: int,
    failed_count: int,
    harness_error_count: int,
    encrypted_artifact_sha256: str,
    public_manifest_digest: str,
    code_identity: GuardCodeIdentity,
    study_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    aggregate = {
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "harness_error_count": harness_error_count,
        "pass_rate": passed_count / case_count if case_count else 0.0,
    }
    payload: dict[str, Any] = {
        "schema": "autobugfix-guard-metric-v2",
        "guard_id": guard_id,
        "run_id": safe_component(run_id, "run_id"),
        "wave": wave,
        "metrics": aggregate,
        "encrypted_artifact_sha256": encrypted_artifact_sha256,
        "public_manifest_digest": public_manifest_digest,
        "guard_code_identity": code_identity.to_dict(),
        "executed_subject_sha": code_identity.trusted_commit,
        "created_at": utc_now(),
    }
    if study_binding is not None:
        verify_record(study_binding)
        payload["study_binding"] = dict(study_binding)
    return payload
