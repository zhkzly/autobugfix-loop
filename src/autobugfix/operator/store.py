from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from autobugfix.models import utc_now
from autobugfix.operator.models import (
    ArtifactReference,
    CheckRun,
    FeedbackPacket,
    GateSnapshot,
    OperatorApproval,
    OperatorEvent,
    OperatorRequest,
    OperatorTriage,
    ScopeRevision,
    WriterRun,
    digest_payload,
)


class OperatorStoreError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
T = TypeVar("T")


def safe_id(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise OperatorStoreError(f"operator id contains unsupported characters: {value!r}")
    return value


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(dict(data), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fresh_record(data: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(data)
    if "record_digest" in value:
        payload = {key: item for key, item in value.items() if key != "record_digest"}
        value["record_digest"] = digest_payload(payload)
    return value


def _verify_record_digest(data: Mapping[str, Any]) -> None:
    stored = data.get("record_digest")
    if stored is None:
        return
    payload = {key: item for key, item in data.items() if key != "record_digest"}
    if stored != digest_payload(payload):
        raise OperatorStoreError("operator record digest mismatch")


class OperatorStore:
    """Transactional authority store for Operator Governance V3."""

    def __init__(
        self,
        project_root: Path | str = ".",
        *,
        state_root: Path | str | None = None,
        artifact_root: Path | str | None = None,
        database_name: str = "governance.sqlite3",
        lease_timeout_seconds: int = 7200,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = Path(state_root).resolve() if state_root else self.project_root / ".autobugfix/operator-v3"
        self.artifact_root = (
            Path(artifact_root).resolve()
            if artifact_root
            else self.project_root / ".autobugfix/operator-artifacts"
        )
        if Path(database_name).name != database_name:
            raise OperatorStoreError("database_name must be a file name")
        self.db_path = self.root / database_name
        self.lease_timeout_seconds = lease_timeout_seconds
        self._lease_owner = f"{os.getpid()}-{uuid.uuid4().hex}"
        self._lease_local = threading.local()

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def init(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS triage (
          triage_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requests (
          request_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
          approval_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS approvals_request_idx ON approvals(request_id, created_at);
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          request_id TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
          data TEXT NOT NULL, event_hash TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS events_request_idx ON events(request_id, seq);
        CREATE TABLE IF NOT EXISTS workspaces (
          request_id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE TABLE IF NOT EXISTS validations (
          validation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE TABLE IF NOT EXISTS writer_runs (
          run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, attempt INTEGER NOT NULL,
          status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id), UNIQUE(request_id, attempt)
        );
        CREATE INDEX IF NOT EXISTS writer_runs_request_idx ON writer_runs(request_id, attempt);
        CREATE TABLE IF NOT EXISTS check_runs (
          check_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, status TEXT NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS check_runs_request_idx ON check_runs(request_id, created_at);
        CREATE TABLE IF NOT EXISTS gates (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
          patch_digest TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
          feedback_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS feedback_request_idx ON feedback(request_id, created_at);
        CREATE TABLE IF NOT EXISTS scope_revisions (
          revision_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, version INTEGER NOT NULL,
          status TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id), UNIQUE(request_id, version)
        );
        CREATE TABLE IF NOT EXISTS artifacts (
          artifact_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS artifacts_request_idx ON artifacts(request_id, created_at);
        CREATE TABLE IF NOT EXISTS experiments (
          experiment_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, status TEXT NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE TABLE IF NOT EXISTS promotions (
          promotion_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, status TEXT NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE TABLE IF NOT EXISTS request_leases (
          request_id TEXT PRIMARY KEY, owner TEXT NOT NULL, acquired_at REAL NOT NULL
        );
        """
        with self._connect() as connection:
            connection.executescript(schema)

    @contextmanager
    def request_lease(self, request_id: str):
        request_id = safe_id(request_id)
        held = getattr(self._lease_local, "held", {})
        if held.get(request_id, 0):
            held[request_id] += 1
            self._lease_local.held = held
            try:
                yield
            finally:
                held[request_id] -= 1
            return
        self.init()
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner,acquired_at FROM request_leases WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is not None and now - float(row["acquired_at"]) <= self.lease_timeout_seconds:
                connection.rollback()
                raise OperatorStoreError(
                    f"Operator request is locked by another command: {request_id}"
                )
            connection.execute("DELETE FROM request_leases WHERE request_id = ?", (request_id,))
            connection.execute(
                "INSERT INTO request_leases (request_id,owner,acquired_at) VALUES (?,?,?)",
                (request_id, self._lease_owner, now),
            )
            connection.commit()
        held[request_id] = 1
        self._lease_local.held = held
        try:
            yield
        finally:
            held[request_id] -= 1
            if held[request_id] == 0:
                held.pop(request_id, None)
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM request_leases WHERE request_id = ? AND owner = ?",
                        (request_id, self._lease_owner),
                    )

    def next_id(self, prefix: str = "op") -> str:
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
        return f"{safe_id(prefix)}-{stamp}-{uuid.uuid4().hex[:8]}"

    def _insert(self, table: str, key_name: str, key: str, data: Mapping[str, Any], **columns: Any) -> None:
        self.init()
        safe_id(key)
        names = [key_name, *columns, "data", "created_at"]
        values = [key, *columns.values(), _dump(data), str(data.get("created_at") or utc_now())]
        placeholders = ",".join("?" for _ in names)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders})", values
                )
        except sqlite3.IntegrityError as exc:
            raise OperatorStoreError(f"immutable operator record already exists: {table}/{key}") from exc

    def _update(self, table: str, key_name: str, key: str, data: Mapping[str, Any], **columns: Any) -> None:
        data = _fresh_record(data)
        assignments = [f"{name} = ?" for name in columns] + ["data = ?"]
        values = [*columns.values(), _dump(data), key]
        with self._connect() as connection:
            result = connection.execute(
                f"UPDATE {table} SET {', '.join(assignments)} WHERE {key_name} = ?", values
            )
            if result.rowcount != 1:
                raise OperatorStoreError(f"missing operator record: {table}/{key}")

    def _read(self, table: str, key_name: str, key: str) -> dict[str, Any]:
        self.init()
        with self._connect() as connection:
            row = connection.execute(f"SELECT data FROM {table} WHERE {key_name} = ?", (key,)).fetchone()
        if row is None:
            raise OperatorStoreError(f"missing operator record: {table}/{key}")
        data = json.loads(str(row["data"]))
        if not isinstance(data, dict):
            raise OperatorStoreError(f"invalid operator record: {table}/{key}")
        return data

    def _list(self, table: str, request_id: str, order: str = "created_at") -> list[dict[str, Any]]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT data FROM {table} WHERE request_id = ? ORDER BY {order}", (request_id,)
            ).fetchall()
        return [json.loads(str(row["data"])) for row in rows]

    def write_triage(self, triage: OperatorTriage) -> Path:
        self._insert("triage", "triage_id", triage.triage_id, triage.to_dict())
        return self.db_path

    def read_triage(self, triage_id: str) -> OperatorTriage:
        return OperatorTriage.from_dict(self._read("triage", "triage_id", safe_id(triage_id)))

    def write_request(self, request: OperatorRequest) -> Path:
        self._insert("requests", "request_id", request.request_id, request.to_dict())
        return self.db_path

    def read_request(self, request_id: str) -> OperatorRequest:
        return OperatorRequest.from_dict(self._read("requests", "request_id", safe_id(request_id)))

    def write_approval(self, approval: OperatorApproval) -> Path:
        self._insert(
            "approvals", "approval_id", approval.approval_id, approval.to_dict(), request_id=approval.request_id
        )
        return self.db_path

    def read_approvals(self, request_id: str) -> list[OperatorApproval]:
        return [OperatorApproval.from_dict(item) for item in self._list("approvals", safe_id(request_id))]

    def append_event(
        self, request_id: str, kind: str, actor: str, payload: Mapping[str, Any] | None = None
    ) -> OperatorEvent:
        self.init()
        request_id = safe_id(request_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous_row = connection.execute(
                "SELECT event_hash FROM events WHERE request_id = ? ORDER BY seq DESC LIMIT 1", (request_id,)
            ).fetchone()
            previous_hash = str(previous_row["event_hash"]) if previous_row else None
            event = OperatorEvent(
                event_id=uuid.uuid4().hex,
                request_id=request_id,
                kind=kind,
                actor=actor,
                payload=dict(payload or {}),
                previous_hash=previous_hash,
            )
            connection.execute(
                "INSERT INTO events (event_id,request_id,kind,actor,data,event_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    request_id,
                    kind,
                    actor,
                    _dump(event.to_dict()),
                    event.computed_hash,
                    event.created_at,
                ),
            )
            connection.commit()
        return event

    def read_events(self, request_id: str) -> list[OperatorEvent]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data,event_hash FROM events WHERE request_id = ? ORDER BY seq",
                (safe_id(request_id),),
            ).fetchall()
        events: list[OperatorEvent] = []
        for row in rows:
            event = OperatorEvent.from_dict(json.loads(str(row["data"])))
            if str(row["event_hash"]) != event.computed_hash:
                raise OperatorStoreError(f"event database hash mismatch for {request_id}")
            events.append(event)
        expected: str | None = None
        for event in events:
            if event.previous_hash != expected:
                raise OperatorStoreError(f"event hash chain mismatch for {request_id}")
            expected = event.computed_hash
        return events

    def write_workspace(self, request_id: str, data: Mapping[str, Any]) -> Path:
        self._insert("workspaces", "request_id", request_id, data)
        return self.db_path

    def read_workspace(self, request_id: str) -> dict[str, Any]:
        return self._read("workspaces", "request_id", safe_id(request_id))

    def write_validation(self, request_id: str, validation_id: str, data: Mapping[str, Any]) -> Path:
        self._insert("validations", "validation_id", validation_id, data, request_id=request_id)
        return self.db_path

    def read_validation(self, request_id: str, validation_id: str) -> dict[str, Any]:
        data = self._read("validations", "validation_id", safe_id(validation_id))
        if str(data.get("request_id")) != request_id:
            raise OperatorStoreError("validation belongs to another request")
        return data

    def write_writer_run(self, run: WriterRun) -> None:
        self._insert(
            "writer_runs", "run_id", run.run_id, run.to_dict(),
            request_id=run.request_id, attempt=run.attempt, status=run.status,
        )

    def update_writer_run(self, run: WriterRun) -> None:
        self._update("writer_runs", "run_id", run.run_id, run.to_dict(), status=run.status)

    def read_writer_run(self, run_id: str) -> WriterRun:
        return WriterRun.from_dict(self._read("writer_runs", "run_id", safe_id(run_id)))

    def read_writer_runs(self, request_id: str) -> list[WriterRun]:
        return [WriterRun.from_dict(item) for item in self._list("writer_runs", safe_id(request_id), "attempt")]

    def write_check_run(self, run: CheckRun) -> None:
        self._insert(
            "check_runs", "check_id", run.check_id, run.to_dict(), request_id=run.request_id, status=run.status
        )

    def update_check_run(self, run: CheckRun) -> None:
        self._update("check_runs", "check_id", run.check_id, run.to_dict(), status=run.status)

    def read_check_run(self, check_id: str) -> CheckRun:
        return CheckRun.from_dict(self._read("check_runs", "check_id", safe_id(check_id)))

    def read_check_runs(self, request_id: str) -> list[CheckRun]:
        return [CheckRun.from_dict(item) for item in self._list("check_runs", safe_id(request_id))]

    def write_gate(self, gate: GateSnapshot) -> None:
        self.init()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO gates (request_id,patch_digest,data,created_at) VALUES (?,?,?,?)",
                (gate.request_id, gate.patch_digest, _dump(gate.to_dict()), gate.created_at),
            )

    def read_latest_gate(self, request_id: str) -> GateSnapshot | None:
        self.init()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM gates WHERE request_id = ? ORDER BY seq DESC LIMIT 1", (safe_id(request_id),)
            ).fetchone()
        if not row:
            return None
        data = json.loads(str(row["data"]))
        return GateSnapshot(
            request_id=str(data["request_id"]), patch_digest=str(data["patch_digest"]),
            scope_version=int(data["scope_version"]), scope=str(data["scope"]), tests=str(data["tests"]),
            semantic=str(data["semantic"]), approval=str(data["approval"]), merge=str(data["merge"]),
            check_run_id=data.get("check_run_id"), created_at=str(data["created_at"]),
        )

    def write_feedback(self, feedback: FeedbackPacket) -> None:
        self._insert(
            "feedback", "feedback_id", feedback.feedback_id, feedback.to_dict(), request_id=feedback.request_id
        )

    def read_feedback(self, request_id: str) -> list[FeedbackPacket]:
        records: list[FeedbackPacket] = []
        for data in self._list("feedback", safe_id(request_id)):
            records.append(FeedbackPacket(
                feedback_id=str(data["feedback_id"]), request_id=str(data["request_id"]),
                category=str(data["category"]), summary=str(data["summary"]), patch_digest=data.get("patch_digest"),
                writer_run_id=data.get("writer_run_id"), check_run_id=data.get("check_run_id"),
                failures=tuple(str(item) for item in data.get("failures") or []),
                artifact_ids=tuple(str(item) for item in data.get("artifact_ids") or []),
                allowed_actions=tuple(str(item) for item in data.get("allowed_actions") or []),
                created_at=str(data["created_at"]),
            ))
        return records

    def write_scope_revision(self, revision: ScopeRevision) -> None:
        self._insert(
            "scope_revisions", "revision_id", revision.revision_id, revision.to_dict(),
            request_id=revision.request_id, version=revision.version, status=revision.status,
        )

    def update_scope_revision(self, revision: ScopeRevision) -> None:
        self._update(
            "scope_revisions", "revision_id", revision.revision_id, revision.to_dict(), status=revision.status
        )

    def read_scope_revisions(self, request_id: str) -> list[ScopeRevision]:
        return [
            ScopeRevision(
                revision_id=str(data["revision_id"]), request_id=str(data["request_id"]),
                version=int(data["version"]), status=str(data["status"]),
                layers=tuple(str(item) for item in data.get("layers") or []),
                paths=tuple(str(item) for item in data.get("paths") or []),
                requested_risk=str(data["requested_risk"]), reason=str(data["reason"]),
                creator=str(data["creator"]), approval_ids=tuple(str(item) for item in data.get("approval_ids") or []),
                created_at=str(data["created_at"]),
            )
            for data in self._list("scope_revisions", safe_id(request_id), "version")
        ]

    def write_artifact(
        self,
        request_id: str,
        *,
        producer: str,
        trust_class: str,
        kind: str,
        content: bytes | str,
        filename: str,
        writer_run_id: str | None = None,
        check_run_id: str | None = None,
        patch_digest: str | None = None,
    ) -> ArtifactReference:
        request_id = safe_id(request_id)
        filename = Path(filename).name
        raw = content.encode("utf-8") if isinstance(content, str) else content
        sha = hashlib.sha256(raw).hexdigest()
        artifact_id = self.next_id("artifact")
        safe_id(kind)
        path = self.artifact_root / "objects" / sha[:2] / sha / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise OperatorStoreError(f"content-addressed artifact collision: {sha}")
        else:
            temporary = path.with_name(f".{filename}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(raw)
            temporary.replace(path)
        reference = ArtifactReference(
            artifact_id=artifact_id,
            request_id=request_id,
            producer=producer,
            trust_class=trust_class,
            kind=kind,
            path=str(path),
            sha256=sha,
            writer_run_id=writer_run_id,
            check_run_id=check_run_id,
            patch_digest=patch_digest,
        )
        self._insert("artifacts", "artifact_id", artifact_id, reference.to_dict(), request_id=request_id)
        return reference

    def register_artifact_file(
        self,
        request_id: str,
        *,
        producer: str,
        trust_class: str,
        kind: str,
        path: Path,
        writer_run_id: str | None = None,
        check_run_id: str | None = None,
        patch_digest: str | None = None,
    ) -> ArtifactReference:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.artifact_root.resolve())
        except ValueError as exc:
            raise OperatorStoreError("registered artifact must live under configured artifact root") from exc
        if not resolved.is_file():
            raise OperatorStoreError(f"artifact file does not exist: {resolved}")
        reference = ArtifactReference(
            artifact_id=self.next_id("artifact"), request_id=safe_id(request_id), producer=producer,
            trust_class=trust_class, kind=kind, path=str(resolved),
            sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(), writer_run_id=writer_run_id,
            check_run_id=check_run_id, patch_digest=patch_digest,
        )
        self._insert(
            "artifacts", "artifact_id", reference.artifact_id, reference.to_dict(), request_id=reference.request_id
        )
        return reference

    def read_artifacts(self, request_id: str) -> list[dict[str, Any]]:
        records = self._list("artifacts", safe_id(request_id))
        for record in records:
            _verify_record_digest(record)
        return records

    def write_experiment(self, data: Mapping[str, Any]) -> None:
        self._insert(
            "experiments", "experiment_id", str(data["experiment_id"]), data,
            request_id=str(data["request_id"]), status=str(data["status"]),
        )

    def update_experiment(self, data: Mapping[str, Any]) -> None:
        self._update(
            "experiments", "experiment_id", str(data["experiment_id"]), data, status=str(data["status"])
        )

    def read_experiments(self, request_id: str) -> list[dict[str, Any]]:
        return self._list("experiments", safe_id(request_id))

    def write_promotion(self, data: Mapping[str, Any]) -> None:
        self._insert(
            "promotions", "promotion_id", str(data["promotion_id"]), data,
            request_id=str(data["request_id"]), status=str(data["status"]),
        )

    def update_promotion(self, data: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _fresh_record(data)
        self._update(
            "promotions",
            "promotion_id",
            str(normalized["promotion_id"]),
            normalized,
            status=str(normalized["status"]),
        )
        return normalized

    def read_promotion(self, promotion_id: str) -> dict[str, Any]:
        data = self._read("promotions", "promotion_id", safe_id(promotion_id))
        _verify_record_digest(data)
        return data

    def read_promotions(self, request_id: str) -> list[dict[str, Any]]:
        records = self._list("promotions", safe_id(request_id))
        for record in records:
            _verify_record_digest(record)
        return records

    @property
    def legacy_root(self) -> Path:
        return self.project_root / ".autobugfix/operator"
