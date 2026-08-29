from __future__ import annotations

import secrets
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from autobugfix.eval.benchmarks.guard import (
    decrypt_json,
    encrypt_artifact_tree,
    encrypt_json,
    guard_artifact_digest,
)
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    canonical_json,
    record_with_digest,
    verify_record,
)


class SWEGuardStoreError(RuntimeError):
    pass


class SWEGuardStore:
    """Encrypted authority storage kept outside every Operator-visible root."""

    def __init__(
        self,
        guard_root: Path,
        *,
        forbidden_roots: Sequence[Path],
    ) -> None:
        self.root = guard_root.expanduser().resolve()
        forbidden = tuple(path.expanduser().resolve() for path in forbidden_roots)
        if self.root == Path("/") or any(
            self.root == path
            or self.root.is_relative_to(path)
            or path.is_relative_to(self.root)
            for path in forbidden
        ):
            raise SWEGuardStoreError(
                "SWE Guard root must be disjoint from project, Eval, and Operator roots"
            )
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        if self.root.stat().st_mode & 0o077:
            raise SWEGuardStoreError("SWE Guard root must not grant group/world access")

    @staticmethod
    def _aad(kind: str, **bindings: str) -> bytes:
        return canonical_json(
            {
                "schema": "autobugfix-swe-guard-aad-v1",
                "kind": kind,
                **bindings,
            }
        ).encode("ascii")

    def _catalog_path(self, protocol_digest: str, runtime_id: str) -> Path:
        token = hashlib.sha256(
            f"{protocol_digest}:{runtime_id}".encode("ascii")
        ).hexdigest()
        return self.root / "qualification-catalogs" / f"{token}.abfg"

    def _exposure_path(self, dataset_revision: str) -> Path:
        token = hashlib.sha256(dataset_revision.encode("utf-8")).hexdigest()
        return self.root / "exposure-ledgers" / f"{token}.abfg"

    def load_exposure_ledger(
        self,
        *,
        secret: str | bytes,
        dataset_revision: str,
    ) -> tuple[set[str], set[str]]:
        path = self._exposure_path(dataset_revision)
        if not path.is_file():
            return set(), set()
        aad = self._aad("exposure-ledger", dataset_revision=dataset_revision)
        try:
            payload = decrypt_json(path, secret=secret, aad=aad)
            verify_record(payload)
        except BenchmarkContractError as exc:
            raise SWEGuardStoreError("SWE exposure ledger is invalid") from exc
        if (
            payload.get("schema") != "autobugfix-swe-exposure-ledger-v1"
            or payload.get("dataset_revision") != dataset_revision
            or not isinstance(payload.get("instance_ids"), list)
            or not isinstance(payload.get("repositories"), list)
        ):
            raise SWEGuardStoreError("SWE exposure ledger binding drift")
        return (
            {str(item) for item in payload["instance_ids"]},
            {str(item) for item in payload["repositories"]},
        )

    def write_exposure_ledger(
        self,
        *,
        instance_ids: set[str],
        repositories: set[str],
        secret: str | bytes,
        dataset_revision: str,
    ) -> str:
        previous_ids, previous_repositories = self.load_exposure_ledger(
            secret=secret,
            dataset_revision=dataset_revision,
        )
        if not previous_ids.issubset(instance_ids) or not previous_repositories.issubset(
            repositories
        ):
            raise SWEGuardStoreError("SWE exposure ledger cannot remove prior evidence")
        payload = record_with_digest(
            {
                "schema": "autobugfix-swe-exposure-ledger-v1",
                "dataset_revision": dataset_revision,
                "instance_ids": sorted(instance_ids),
                "repositories": sorted(repositories),
            }
        )
        path = self._exposure_path(dataset_revision)
        encrypt_json(
            payload,
            path,
            secret=secret,
            aad=self._aad("exposure-ledger", dataset_revision=dataset_revision),
        )
        return guard_artifact_digest(path)

    def load_qualifications(
        self,
        *,
        secret: str | bytes,
        protocol_digest: str,
        runtime_id: str,
    ) -> list[dict[str, Any]]:
        path = self._catalog_path(protocol_digest, runtime_id)
        if not path.is_file():
            return []
        aad = self._aad(
            "qualification-catalog",
            protocol_digest=protocol_digest,
            runtime_id=runtime_id,
        )
        try:
            payload = decrypt_json(path, secret=secret, aad=aad)
            verify_record(payload)
        except BenchmarkContractError as exc:
            raise SWEGuardStoreError(
                f"SWE Guard qualification catalog is invalid: {exc}"
            ) from exc
        if (
            payload.get("schema") != "autobugfix-swe-guard-qualification-catalog-v1"
            or payload.get("protocol_digest") != protocol_digest
            or payload.get("runtime_id") != runtime_id
            or not isinstance(payload.get("records"), list)
        ):
            raise SWEGuardStoreError("SWE Guard qualification catalog binding drift")
        records = payload["records"]
        if not all(isinstance(item, Mapping) for item in records):
            raise SWEGuardStoreError("SWE Guard qualification records are invalid")
        return [dict(item) for item in records]

    def write_qualification(
        self,
        record: Mapping[str, Any],
        artifact_root: Path,
        *,
        secret: str | bytes,
        protocol_digest: str,
        runtime_id: str,
    ) -> dict[str, Any]:
        verify_record(record)
        if (
            record.get("schema") != "autobugfix-swe-qualification-v5"
            or record.get("adapter") != "swebench_live"
            or record.get("qualification_contract_digest") != protocol_digest
            or record.get("evaluator_runtime_id") != runtime_id
        ):
            raise SWEGuardStoreError("SWE Holdout qualification binding is invalid")
        artifact_token = secrets.token_hex(32)
        artifact_path = self.root / "qualification-artifacts" / f"{artifact_token}.abfg"
        artifact_aad = self._aad(
            "qualification-artifacts",
            protocol_digest=protocol_digest,
            runtime_id=runtime_id,
            artifact_token=artifact_token,
        )
        encrypt_artifact_tree(
            artifact_root,
            artifact_path,
            secret=secret,
            aad=artifact_aad,
        )
        qualification = dict(record)
        stored = {
            "qualification": qualification,
            "encrypted_artifact_token": artifact_token,
            "encrypted_artifact_sha256": guard_artifact_digest(artifact_path),
        }
        records = self.load_qualifications(
            secret=secret,
            protocol_digest=protocol_digest,
            runtime_id=runtime_id,
        )
        records = [
            item
            for item in records
            if not isinstance(item.get("qualification"), Mapping)
            or item["qualification"].get("instance_id")
            != qualification.get("instance_id")
        ]
        records.append(stored)
        records.sort(
            key=lambda item: str(
                (item.get("qualification") or {}).get("instance_id") or ""
            )
        )
        catalog = record_with_digest(
            {
                "schema": "autobugfix-swe-guard-qualification-catalog-v1",
                "protocol_digest": protocol_digest,
                "runtime_id": runtime_id,
                "records": records,
            }
        )
        catalog_path = self._catalog_path(protocol_digest, runtime_id)
        encrypt_json(
            catalog,
            catalog_path,
            secret=secret,
            aad=self._aad(
                "qualification-catalog",
                protocol_digest=protocol_digest,
                runtime_id=runtime_id,
            ),
        )
        return {
            "eligible": bool(record.get("eligible")),
            "record_digest": record.get("record_digest"),
            "encrypted_artifact_sha256": stored["encrypted_artifact_sha256"],
            "catalog_sha256": guard_artifact_digest(catalog_path),
        }

    def qualification_records(
        self,
        *,
        secret: str | bytes,
        protocol_digest: str,
        runtime_id: str,
    ) -> list[dict[str, Any]]:
        entries = self.load_qualifications(
            secret=secret,
            protocol_digest=protocol_digest,
            runtime_id=runtime_id,
        )
        records: list[dict[str, Any]] = []
        for entry in entries:
            qualification = entry.get("qualification")
            token = str(entry.get("encrypted_artifact_token") or "")
            digest = str(entry.get("encrypted_artifact_sha256") or "")
            if not isinstance(qualification, Mapping) or len(token) != 64:
                raise SWEGuardStoreError("encrypted qualification entry is invalid")
            artifact = self.root / "qualification-artifacts" / f"{token}.abfg"
            if not artifact.is_file() or guard_artifact_digest(artifact) != digest:
                raise SWEGuardStoreError(
                    "encrypted qualification evidence is missing or changed"
                )
            verify_record(qualification)
            records.append(dict(qualification))
        return records

    def write_preparation(
        self,
        preparation_id: str,
        payload: Mapping[str, Any],
        *,
        secret: str | bytes,
        protocol_digest: str,
        runtime_id: str,
    ) -> tuple[Path, str]:
        verify_record(payload)
        destination = self.root / "preparations" / f"{preparation_id}.abfg"
        encrypt_json(
            payload,
            destination,
            secret=secret,
            aad=self._aad(
                "preparation",
                preparation_id=preparation_id,
                protocol_digest=protocol_digest,
                runtime_id=runtime_id,
            ),
        )
        return destination, guard_artifact_digest(destination)

    def load_preparation(
        self,
        preparation_id: str,
        *,
        expected_sha256: str,
        secret: str | bytes,
        protocol_digest: str,
        runtime_id: str,
    ) -> dict[str, Any]:
        source = self.root / "preparations" / f"{preparation_id}.abfg"
        if not source.is_file() or guard_artifact_digest(source) != expected_sha256:
            raise SWEGuardStoreError("encrypted SWE preparation is missing or changed")
        try:
            payload = decrypt_json(
                source,
                secret=secret,
                aad=self._aad(
                    "preparation",
                    preparation_id=preparation_id,
                    protocol_digest=protocol_digest,
                    runtime_id=runtime_id,
                ),
            )
            verify_record(payload)
        except BenchmarkContractError as exc:
            raise SWEGuardStoreError(
                f"encrypted SWE preparation is invalid: {exc}"
            ) from exc
        return payload
