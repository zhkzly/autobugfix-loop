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
    BudgetGrantRecord,
    BudgetRequestRecord,
    CheckRun,
    CheckpointRecord,
    ExperimentLineRecord,
    FeedbackPacket,
    GateSnapshot,
    IntegrationRecord,
    OperatorApproval,
    OperatorEvent,
    OperatorRequest,
    OperatorTriage,
    ScopeRevision,
    StudyMetricRecord,
    StudyRecord,
    UsageEntryRecord,
    WriterRun,
    digest_payload,
    is_expired,
)


class OperatorStoreError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SCHEMA_VERSION = 4
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
    """Transactional authority store for Operator Governance V4."""

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
        CREATE TABLE IF NOT EXISTS operator_schema (
          singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version INTEGER NOT NULL
        );
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
        CREATE TABLE IF NOT EXISTS studies (
          study_id TEXT PRIMARY KEY, line_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS study_metrics (
          metric_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, line_id TEXT NOT NULL,
          kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(study_id) REFERENCES studies(study_id)
        );
        CREATE INDEX IF NOT EXISTS study_metrics_study_idx
          ON study_metrics(study_id, kind, created_at);
        CREATE TABLE IF NOT EXISTS experiment_lines (
          line_id TEXT PRIMARY KEY, study_id TEXT UNIQUE NOT NULL, branch TEXT UNIQUE NOT NULL,
          head_sha TEXT NOT NULL, generation INTEGER NOT NULL, status TEXT NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(study_id) REFERENCES studies(study_id)
        );
        CREATE INDEX IF NOT EXISTS experiment_lines_status_idx
          ON experiment_lines(status, created_at);
        CREATE TABLE IF NOT EXISTS integrations (
          integration_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, line_id TEXT NOT NULL,
          request_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(study_id) REFERENCES studies(study_id),
          FOREIGN KEY(line_id) REFERENCES experiment_lines(line_id),
          FOREIGN KEY(request_id) REFERENCES requests(request_id)
        );
        CREATE INDEX IF NOT EXISTS integrations_line_idx
          ON integrations(line_id, created_at);
        CREATE TABLE IF NOT EXISTS checkpoints (
          checkpoint_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, line_id TEXT NOT NULL,
          name TEXT NOT NULL, subject_sha TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(study_id, name),
          FOREIGN KEY(study_id) REFERENCES studies(study_id),
          FOREIGN KEY(line_id) REFERENCES experiment_lines(line_id)
        );
        CREATE INDEX IF NOT EXISTS checkpoints_line_idx
          ON checkpoints(line_id, created_at);
        CREATE TABLE IF NOT EXISTS budget_requests (
          budget_request_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, wave INTEGER NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(study_id) REFERENCES studies(study_id)
        );
        CREATE INDEX IF NOT EXISTS budget_requests_study_idx
          ON budget_requests(study_id, wave, created_at);
        CREATE TABLE IF NOT EXISTS budget_grants (
          grant_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, wave INTEGER NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(study_id, wave),
          FOREIGN KEY(study_id) REFERENCES studies(study_id)
        );
        CREATE INDEX IF NOT EXISTS budget_grants_study_idx
          ON budget_grants(study_id, wave);
        CREATE TABLE IF NOT EXISTS usage_entries (
          usage_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, study_id TEXT NOT NULL,
          call_key TEXT UNIQUE NOT NULL, case_id TEXT, role TEXT NOT NULL, status TEXT NOT NULL,
          data TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(grant_id) REFERENCES budget_grants(grant_id),
          FOREIGN KEY(study_id) REFERENCES studies(study_id)
        );
        CREATE INDEX IF NOT EXISTS usage_entries_grant_idx
          ON usage_entries(grant_id, status, created_at);
        CREATE INDEX IF NOT EXISTS usage_entries_case_idx
          ON usage_entries(study_id, case_id, role, created_at);
        CREATE TABLE IF NOT EXISTS experiment_line_leases (
          line_id TEXT PRIMARY KEY, owner TEXT NOT NULL, acquired_at REAL NOT NULL
        );
        """
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise OperatorStoreError(
                    f"operator store schema {version} is newer than supported {_SCHEMA_VERSION}"
                )
            connection.executescript(schema)
            connection.execute(
                "INSERT INTO operator_schema (singleton,version) VALUES (1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET version = excluded.version",
                (_SCHEMA_VERSION,),
            )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

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

    @contextmanager
    def experiment_line_lease(self, line_id: str):
        line_id = safe_id(line_id)
        held = getattr(self._lease_local, "held_lines", {})
        if held.get(line_id, 0):
            held[line_id] += 1
            self._lease_local.held_lines = held
            try:
                yield
            finally:
                held[line_id] -= 1
            return
        self.init()
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner,acquired_at FROM experiment_line_leases WHERE line_id = ?",
                (line_id,),
            ).fetchone()
            if row is not None and now - float(row["acquired_at"]) <= self.lease_timeout_seconds:
                connection.rollback()
                raise OperatorStoreError(
                    f"Operator experiment line is locked by another command: {line_id}"
                )
            connection.execute("DELETE FROM experiment_line_leases WHERE line_id = ?", (line_id,))
            connection.execute(
                "INSERT INTO experiment_line_leases (line_id,owner,acquired_at) VALUES (?,?,?)",
                (line_id, self._lease_owner, now),
            )
            connection.commit()
        held[line_id] = 1
        self._lease_local.held_lines = held
        try:
            yield
        finally:
            held[line_id] -= 1
            if held[line_id] == 0:
                held.pop(line_id, None)
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM experiment_line_leases WHERE line_id = ? AND owner = ?",
                        (line_id, self._lease_owner),
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

    def write_study(self, study: StudyRecord) -> None:
        self._insert(
            "studies",
            "study_id",
            study.study_id,
            study.to_dict(),
            line_id=study.line_id,
        )

    def read_study(self, study_id: str) -> StudyRecord:
        return StudyRecord.from_dict(self._read("studies", "study_id", safe_id(study_id)))

    def read_studies(self) -> list[StudyRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute("SELECT data FROM studies ORDER BY created_at,study_id").fetchall()
        return [StudyRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    def write_study_metric_artifact(
        self,
        content: bytes,
        *,
        filename: str = "receipt.yaml",
    ) -> tuple[Path, str]:
        filename = Path(filename).name
        sha = hashlib.sha256(content).hexdigest()
        path = self.artifact_root / "study-metrics" / sha[:2] / sha / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise OperatorStoreError(f"content-addressed study metric collision: {sha}")
        else:
            temporary = path.with_name(f".{filename}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        return path.resolve(), sha

    def write_study_metric(self, metric: StudyMetricRecord) -> None:
        artifact = Path(metric.artifact_path).resolve()
        try:
            artifact.relative_to(self.artifact_root.resolve())
        except ValueError as exc:
            raise OperatorStoreError(
                "study metric artifact must live under configured artifact root"
            ) from exc
        if not artifact.is_file():
            raise OperatorStoreError(f"study metric artifact is missing: {artifact}")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != metric.artifact_sha256:
            raise OperatorStoreError("study metric artifact digest mismatch")
        self._insert(
            "study_metrics",
            "metric_id",
            metric.metric_id,
            metric.to_dict(),
            study_id=metric.study_id,
            line_id=metric.line_id,
            kind=metric.kind,
        )

    def read_study_metric(self, metric_id: str) -> StudyMetricRecord:
        metric = StudyMetricRecord.from_dict(
            self._read("study_metrics", "metric_id", safe_id(metric_id))
        )
        artifact = Path(metric.artifact_path).resolve()
        try:
            artifact.relative_to(self.artifact_root.resolve())
        except ValueError as exc:
            raise OperatorStoreError(
                "study metric artifact escaped configured artifact root"
            ) from exc
        if not artifact.is_file():
            raise OperatorStoreError(f"study metric artifact is missing: {artifact}")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != metric.artifact_sha256:
            raise OperatorStoreError("study metric artifact digest mismatch")
        return metric

    def read_study_metrics(self, study_id: str) -> list[StudyMetricRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT metric_id FROM study_metrics WHERE study_id = ? "
                "ORDER BY created_at,metric_id",
                (safe_id(study_id),),
            ).fetchall()
        return [self.read_study_metric(str(row["metric_id"])) for row in rows]

    def write_experiment_line(self, line: ExperimentLineRecord) -> None:
        self._insert(
            "experiment_lines",
            "line_id",
            line.line_id,
            line.to_dict(),
            study_id=line.study_id,
            branch=line.branch,
            head_sha=line.head_sha,
            generation=line.generation,
            status=line.status,
        )

    def initialize_experiment_line(
        self,
        line: ExperimentLineRecord,
        checkpoint: CheckpointRecord,
    ) -> None:
        if checkpoint.name != "H0":
            raise OperatorStoreError("experiment line initialization requires an H0 checkpoint")
        if checkpoint.study_id != line.study_id or checkpoint.line_id != line.line_id:
            raise OperatorStoreError("H0 checkpoint belongs to another experiment line")
        self.init()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                study_row = connection.execute(
                    "SELECT data FROM studies WHERE study_id = ?",
                    (safe_id(line.study_id),),
                ).fetchone()
                if study_row is None:
                    raise OperatorStoreError(f"missing operator record: studies/{line.study_id}")
                study = StudyRecord.from_dict(json.loads(str(study_row["data"])))
                if study.line_id != line.line_id:
                    raise OperatorStoreError("study designates another experiment line")
                if study.base_checkpoint_id != checkpoint.checkpoint_id:
                    raise OperatorStoreError("H0 checkpoint does not match study baseline identity")
                if study.base_subject_sha != checkpoint.subject_sha or line.base_sha != checkpoint.subject_sha:
                    raise OperatorStoreError("H0 subject SHA does not match study baseline")
                connection.execute(
                    "INSERT INTO experiment_lines "
                    "(line_id,study_id,branch,head_sha,generation,status,data,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        safe_id(line.line_id),
                        line.study_id,
                        line.branch,
                        line.head_sha,
                        line.generation,
                        line.status,
                        _dump(line.to_dict()),
                        line.created_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO checkpoints "
                    "(checkpoint_id,study_id,line_id,name,subject_sha,data,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        safe_id(checkpoint.checkpoint_id),
                        checkpoint.study_id,
                        checkpoint.line_id,
                        checkpoint.name,
                        checkpoint.subject_sha,
                        _dump(checkpoint.to_dict()),
                        checkpoint.created_at,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise OperatorStoreError(
                f"experiment line or H0 checkpoint already exists: {line.line_id}"
            ) from exc

    def _read_experiment_line_row(
        self,
        connection: sqlite3.Connection,
        line_id: str,
    ) -> tuple[ExperimentLineRecord, sqlite3.Row]:
        row = connection.execute(
            "SELECT study_id,branch,head_sha,generation,status,data "
            "FROM experiment_lines WHERE line_id = ?",
            (safe_id(line_id),),
        ).fetchone()
        if row is None:
            raise OperatorStoreError(f"missing operator record: experiment_lines/{line_id}")
        line = ExperimentLineRecord.from_dict(json.loads(str(row["data"])))
        database_values = (
            str(row["study_id"]),
            str(row["branch"]),
            str(row["head_sha"]),
            int(row["generation"]),
            str(row["status"]),
        )
        record_values = (
            line.study_id,
            line.branch,
            line.head_sha,
            line.generation,
            line.status,
        )
        if database_values != record_values:
            raise OperatorStoreError(f"experiment line columns disagree with record: {line_id}")
        return line, row

    def read_experiment_line(self, line_id: str) -> ExperimentLineRecord:
        self.init()
        with self._connect() as connection:
            line, _ = self._read_experiment_line_row(connection, line_id)
        return line

    def read_experiment_lines(self, study_id: str | None = None) -> list[ExperimentLineRecord]:
        self.init()
        with self._connect() as connection:
            if study_id is None:
                rows = connection.execute(
                    "SELECT line_id FROM experiment_lines ORDER BY created_at,line_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT line_id FROM experiment_lines WHERE study_id = ? ORDER BY created_at,line_id",
                    (safe_id(study_id),),
                ).fetchall()
            return [self._read_experiment_line_row(connection, str(row["line_id"]))[0] for row in rows]

    @staticmethod
    def _validate_line_update(
        current: ExperimentLineRecord,
        updated: ExperimentLineRecord,
        *,
        expected_head_sha: str,
        expected_generation: int,
    ) -> None:
        if current.head_sha != expected_head_sha or current.generation != expected_generation:
            raise OperatorStoreError("stale experiment line head or generation")
        immutable_before = (
            current.line_id,
            current.study_id,
            current.branch,
            current.base_sha,
            current.remote,
            current.created_at,
        )
        immutable_after = (
            updated.line_id,
            updated.study_id,
            updated.branch,
            updated.base_sha,
            updated.remote,
            updated.created_at,
        )
        if immutable_before != immutable_after:
            raise OperatorStoreError("experiment line immutable identity changed")
        if updated.generation != expected_generation + 1:
            raise OperatorStoreError("experiment line generation must advance by one")

    def compare_and_swap_experiment_line(
        self,
        line: ExperimentLineRecord,
        *,
        expected_head_sha: str,
        expected_generation: int,
    ) -> ExperimentLineRecord:
        self.init()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current, _ = self._read_experiment_line_row(connection, line.line_id)
            self._validate_line_update(
                current,
                line,
                expected_head_sha=expected_head_sha,
                expected_generation=expected_generation,
            )
            result = connection.execute(
                "UPDATE experiment_lines SET head_sha = ?, generation = ?, status = ?, data = ? "
                "WHERE line_id = ? AND head_sha = ? AND generation = ?",
                (
                    line.head_sha,
                    line.generation,
                    line.status,
                    _dump(line.to_dict()),
                    line.line_id,
                    expected_head_sha,
                    expected_generation,
                ),
            )
            if result.rowcount != 1:
                connection.rollback()
                raise OperatorStoreError("stale experiment line compare-and-swap")
            connection.commit()
        return line

    def write_integration(self, integration: IntegrationRecord) -> None:
        self._insert(
            "integrations",
            "integration_id",
            integration.integration_id,
            integration.to_dict(),
            study_id=integration.study_id,
            line_id=integration.line_id,
            request_id=integration.request_id,
            kind=integration.kind,
        )

    def advance_experiment_line(
        self,
        line: ExperimentLineRecord,
        integration: IntegrationRecord,
    ) -> ExperimentLineRecord:
        if integration.line_id != line.line_id or integration.study_id != line.study_id:
            raise OperatorStoreError("integration belongs to another experiment line")
        if integration.result_head_sha != line.head_sha:
            raise OperatorStoreError("integration result head does not match updated experiment line")
        self.init()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current, _ = self._read_experiment_line_row(connection, line.line_id)
                self._validate_line_update(
                    current,
                    line,
                    expected_head_sha=integration.expected_head_sha,
                    expected_generation=integration.expected_generation,
                )
                connection.execute(
                    "INSERT INTO integrations "
                    "(integration_id,study_id,line_id,request_id,kind,data,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        safe_id(integration.integration_id),
                        integration.study_id,
                        integration.line_id,
                        integration.request_id,
                        integration.kind,
                        _dump(integration.to_dict()),
                        integration.created_at,
                    ),
                )
                result = connection.execute(
                    "UPDATE experiment_lines SET head_sha = ?, generation = ?, status = ?, data = ? "
                    "WHERE line_id = ? AND head_sha = ? AND generation = ?",
                    (
                        line.head_sha,
                        line.generation,
                        line.status,
                        _dump(line.to_dict()),
                        line.line_id,
                        integration.expected_head_sha,
                        integration.expected_generation,
                    ),
                )
                if result.rowcount != 1:
                    connection.rollback()
                    raise OperatorStoreError("stale experiment line compare-and-swap")
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise OperatorStoreError(
                f"immutable operator record already exists: integrations/{integration.integration_id}"
            ) from exc
        return line

    def read_integration(self, integration_id: str) -> IntegrationRecord:
        return IntegrationRecord.from_dict(
            self._read("integrations", "integration_id", safe_id(integration_id))
        )

    def read_integrations(self, line_id: str) -> list[IntegrationRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM integrations WHERE line_id = ? ORDER BY created_at,integration_id",
                (safe_id(line_id),),
            ).fetchall()
        return [IntegrationRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    def write_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        self._insert(
            "checkpoints",
            "checkpoint_id",
            checkpoint.checkpoint_id,
            checkpoint.to_dict(),
            study_id=checkpoint.study_id,
            line_id=checkpoint.line_id,
            name=checkpoint.name,
            subject_sha=checkpoint.subject_sha,
        )

    def write_checkpoint_and_activate(
        self,
        line: ExperimentLineRecord,
        checkpoint: CheckpointRecord,
        *,
        expected_head_sha: str,
        expected_generation: int,
    ) -> ExperimentLineRecord:
        if checkpoint.study_id != line.study_id or checkpoint.line_id != line.line_id:
            raise OperatorStoreError("checkpoint belongs to another experiment line")
        if line.active_checkpoint_id != checkpoint.checkpoint_id:
            raise OperatorStoreError("line does not activate the supplied checkpoint")
        if line.head_sha != checkpoint.subject_sha:
            raise OperatorStoreError("checkpoint subject does not match line head")
        self.init()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current, _ = self._read_experiment_line_row(connection, line.line_id)
                self._validate_line_update(
                    current,
                    line,
                    expected_head_sha=expected_head_sha,
                    expected_generation=expected_generation,
                )
                connection.execute(
                    "INSERT INTO checkpoints "
                    "(checkpoint_id,study_id,line_id,name,subject_sha,data,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        safe_id(checkpoint.checkpoint_id),
                        checkpoint.study_id,
                        checkpoint.line_id,
                        checkpoint.name,
                        checkpoint.subject_sha,
                        _dump(checkpoint.to_dict()),
                        checkpoint.created_at,
                    ),
                )
                result = connection.execute(
                    "UPDATE experiment_lines SET head_sha = ?, generation = ?, status = ?, data = ? "
                    "WHERE line_id = ? AND head_sha = ? AND generation = ?",
                    (
                        line.head_sha,
                        line.generation,
                        line.status,
                        _dump(line.to_dict()),
                        line.line_id,
                        expected_head_sha,
                        expected_generation,
                    ),
                )
                if result.rowcount != 1:
                    connection.rollback()
                    raise OperatorStoreError("stale experiment line compare-and-swap")
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise OperatorStoreError(
                f"immutable checkpoint already exists: {checkpoint.checkpoint_id}"
            ) from exc
        return line

    def read_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        return CheckpointRecord.from_dict(
            self._read("checkpoints", "checkpoint_id", safe_id(checkpoint_id))
        )

    def read_checkpoints(self, study_id: str) -> list[CheckpointRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM checkpoints WHERE study_id = ? ORDER BY created_at,checkpoint_id",
                (safe_id(study_id),),
            ).fetchall()
        return [CheckpointRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    def write_budget_grant(self, grant: BudgetGrantRecord) -> None:
        self._insert(
            "budget_grants",
            "grant_id",
            grant.grant_id,
            grant.to_dict(),
            study_id=grant.study_id,
            wave=grant.wave,
        )

    def write_budget_request(self, request: BudgetRequestRecord) -> None:
        self._insert(
            "budget_requests",
            "budget_request_id",
            request.budget_request_id,
            request.to_dict(),
            study_id=request.study_id,
            wave=request.wave,
        )

    def read_budget_request(self, budget_request_id: str) -> BudgetRequestRecord:
        return BudgetRequestRecord.from_dict(
            self._read(
                "budget_requests",
                "budget_request_id",
                safe_id(budget_request_id),
            )
        )

    def read_budget_requests(self, study_id: str) -> list[BudgetRequestRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM budget_requests WHERE study_id = ? "
                "ORDER BY created_at,budget_request_id",
                (safe_id(study_id),),
            ).fetchall()
        return [BudgetRequestRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    def read_budget_grant(self, grant_id: str) -> BudgetGrantRecord:
        return BudgetGrantRecord.from_dict(
            self._read("budget_grants", "grant_id", safe_id(grant_id))
        )

    def read_budget_grants(self, study_id: str) -> list[BudgetGrantRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM budget_grants WHERE study_id = ? ORDER BY wave",
                (safe_id(study_id),),
            ).fetchall()
        return [BudgetGrantRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    def write_usage_entry(self, entry: UsageEntryRecord) -> None:
        self._insert(
            "usage_entries",
            "usage_id",
            entry.usage_id,
            entry.to_dict(),
            grant_id=entry.grant_id,
            study_id=entry.study_id,
            call_key=entry.call_key,
            case_id=entry.case_id,
            role=entry.role,
            status=entry.status,
        )

    def reserve_usage_entry(self, entry: UsageEntryRecord) -> UsageEntryRecord:
        if entry.status != "RESERVED":
            raise OperatorStoreError("usage reservation must start in RESERVED status")
        self.init()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                grant_row = connection.execute(
                    "SELECT data FROM budget_grants WHERE grant_id = ?",
                    (safe_id(entry.grant_id),),
                ).fetchone()
                if grant_row is None:
                    raise OperatorStoreError(f"missing operator record: budget_grants/{entry.grant_id}")
                grant = BudgetGrantRecord.from_dict(json.loads(str(grant_row["data"])))
                request_row = connection.execute(
                    "SELECT data FROM budget_requests WHERE budget_request_id = ?",
                    (grant.budget_request_id,),
                ).fetchone()
                if request_row is None:
                    raise OperatorStoreError("budget grant has no authority request")
                budget_request = BudgetRequestRecord.from_dict(
                    json.loads(str(request_row["data"]))
                )
                if budget_request.budget_request_digest != grant.budget_request_digest:
                    raise OperatorStoreError("budget grant request digest mismatch")
                if grant.study_id != entry.study_id or budget_request.study_id != entry.study_id:
                    raise OperatorStoreError("usage belongs to another study")
                if grant.model != entry.model:
                    raise OperatorStoreError("usage model does not match budget grant")
                if is_expired(grant.expires_at):
                    raise OperatorStoreError("budget grant has expired")
                latest_wave_row = connection.execute(
                    "SELECT MAX(wave) FROM budget_grants WHERE study_id = ?",
                    (entry.study_id,),
                ).fetchone()
                if latest_wave_row is None or int(latest_wave_row[0]) != grant.wave:
                    raise OperatorStoreError("budget grant is superseded by a later wave")
                rows = connection.execute(
                    "SELECT status,data FROM usage_entries WHERE study_id = ?",
                    (entry.study_id,),
                ).fetchall()
                usage: list[UsageEntryRecord] = []
                for row in rows:
                    item = UsageEntryRecord.from_dict(json.loads(str(row["data"])))
                    if str(row["status"]) != item.status:
                        raise OperatorStoreError("usage status column disagrees with record")
                    usage.append(item)
                if len(usage) >= grant.max_calls:
                    raise OperatorStoreError("study SDK call budget is exhausted")
                running = sum(item.status == "RESERVED" for item in usage)
                if running >= grant.case_concurrency:
                    raise OperatorStoreError("study SDK call concurrency is exhausted")
                case_roles = {"writer", "evaluator", "eval_judge"}
                operator_roles = {
                    "operator_supervisor",
                    "operator_writer",
                    "operator_verifier",
                }
                if entry.role in case_roles:
                    if not entry.case_id or entry.case_id not in grant.case_ids:
                        raise OperatorStoreError("usage case is outside budget grant")
                    if entry.attempt < 1:
                        raise OperatorStoreError("case usage attempt must be positive")
                    if entry.role == "writer":
                        prior_attempts = sum(
                            item.role == "writer"
                            and item.case_id == entry.case_id
                            and item.execution_id == entry.execution_id
                            for item in usage
                        )
                        if prior_attempts >= grant.max_writer_attempts:
                            raise OperatorStoreError("writer attempt budget is exhausted")
                        if entry.attempt != prior_attempts + 1:
                            raise OperatorStoreError("writer attempt is not the next reserved attempt")
                elif entry.role in operator_roles:
                    if entry.case_id is not None:
                        raise OperatorStoreError("operator revision usage must not expose a case id")
                    if entry.revision < 1 or entry.revision > grant.max_operator_revisions:
                        raise OperatorStoreError("operator revision is outside budget grant")
                    prior_role_calls = sum(
                        item.role == entry.role
                        and item.execution_id == entry.execution_id
                        for item in usage
                    )
                    if entry.revision != prior_role_calls + 1:
                        raise OperatorStoreError(
                            "operator revision is not the next reserved revision"
                        )
                else:
                    raise OperatorStoreError(f"role is outside study budget: {entry.role}")
                connection.execute(
                    "INSERT INTO usage_entries "
                    "(usage_id,grant_id,study_id,call_key,case_id,role,status,data,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        safe_id(entry.usage_id),
                        entry.grant_id,
                        entry.study_id,
                        entry.call_key,
                        entry.case_id,
                        entry.role,
                        entry.status,
                        _dump(entry.to_dict()),
                        entry.reserved_at,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise OperatorStoreError(
                f"usage call key or id already exists: {entry.call_key}"
            ) from exc
        return entry

    def finalize_usage_entry(self, entry: UsageEntryRecord) -> UsageEntryRecord:
        if entry.status == "RESERVED":
            raise OperatorStoreError("usage finalization requires a terminal status")
        self.init()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data,status FROM usage_entries WHERE usage_id = ?",
                (safe_id(entry.usage_id),),
            ).fetchone()
            if row is None:
                raise OperatorStoreError(f"missing operator record: usage_entries/{entry.usage_id}")
            current = UsageEntryRecord.from_dict(json.loads(str(row["data"])))
            immutable_before = (
                current.usage_id,
                current.grant_id,
                current.study_id,
                current.call_key,
                current.execution_id,
                current.case_id,
                current.role,
                current.model,
                current.attempt,
                current.revision,
                current.reserved_at,
            )
            immutable_after = (
                entry.usage_id,
                entry.grant_id,
                entry.study_id,
                entry.call_key,
                entry.execution_id,
                entry.case_id,
                entry.role,
                entry.model,
                entry.attempt,
                entry.revision,
                entry.reserved_at,
            )
            if immutable_before != immutable_after:
                raise OperatorStoreError("usage immutable reservation fields changed")
            if current.status != "RESERVED" or str(row["status"]) != "RESERVED":
                raise OperatorStoreError("usage reservation is already finalized")
            result = connection.execute(
                "UPDATE usage_entries SET status = ?, data = ? "
                "WHERE usage_id = ? AND status = 'RESERVED'",
                (entry.status, _dump(entry.to_dict()), entry.usage_id),
            )
            if result.rowcount != 1:
                connection.rollback()
                raise OperatorStoreError("usage reservation was finalized concurrently")
            connection.commit()
        return entry

    def read_usage_entry(self, usage_id: str) -> UsageEntryRecord:
        return UsageEntryRecord.from_dict(
            self._read("usage_entries", "usage_id", safe_id(usage_id))
        )

    def read_usage_entries(self, grant_id: str) -> list[UsageEntryRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM usage_entries WHERE grant_id = ? ORDER BY created_at,usage_id",
                (safe_id(grant_id),),
            ).fetchall()
        return [UsageEntryRecord.from_dict(json.loads(str(row["data"]))) for row in rows]

    @property
    def legacy_root(self) -> Path:
        return self.project_root / ".autobugfix/operator"
