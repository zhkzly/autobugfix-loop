from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from autobugfix.eval.benchmarks.guard import guard_artifact_digest
from autobugfix.eval.benchmarks.models import record_with_digest, safe_component
from autobugfix.eval.benchmarks.service import EvalBenchmarkService
from autobugfix.eval.benchmarks.swe_guard import SWEGuardStore
from autobugfix.eval.benchmarks.swe_live import SWELiveAdapter
from autobugfix.eval.benchmarks.swe_models import SWEExperimentProtocol
from autobugfix.eval.benchmarks.swe_constants import SWE_LIVE_DATASET_REVISION
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime
from autobugfix.models import utc_now


class SWEHoldoutCohortError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SWEHoldoutCandidate:
    instance_id: str
    repository: str
    language: str

    @classmethod
    def from_live_row(cls, row: Mapping[str, Any]) -> "SWEHoldoutCandidate":
        return cls(
            instance_id=safe_component(row.get("instance_id"), "instance_id"),
            repository=_required(row.get("repo"), "repository"),
            language=_required(
                row.get("autobugfix_dataset_split"),
                "language",
            ),
        )

    @classmethod
    def from_qualification(
        cls, record: Mapping[str, Any]
    ) -> "SWEHoldoutCandidate":
        return cls(
            instance_id=safe_component(record.get("instance_id"), "instance_id"),
            repository=_required(record.get("repository"), "repository"),
            language=_required(record.get("language"), "language"),
        )


@dataclass(slots=True, frozen=True)
class SWEHoldoutCohortProgress:
    attempted_count: int
    new_attempt_count: int
    eligible_count: int
    repository_count: int
    language_count: int

    def aggregate(self) -> dict[str, int]:
        return {
            "attempted_count": self.attempted_count,
            "new_attempt_count": self.new_attempt_count,
            "eligible_count": self.eligible_count,
            "repository_count": self.repository_count,
            "language_count": self.language_count,
        }


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SWEHoldoutCohortError(f"Holdout {field} is missing")
    return text


def _secret_bytes(secret: str | bytes) -> bytes:
    value = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(value) < 16:
        raise SWEHoldoutCohortError(
            "SWE Guard secret must contain at least 16 bytes"
        )
    return value


def _candidate_rank(
    secret: bytes,
    protocol_digest: str,
    candidate: SWEHoldoutCandidate,
) -> bytes:
    return hmac.new(
        secret,
        (
            "autobugfix-swe-holdout-order-v1\0"
            f"{protocol_digest}\0{candidate.instance_id}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _validate_existing(
    candidates: Mapping[str, SWEHoldoutCandidate],
    records: Sequence[Mapping[str, Any]],
    *,
    excluded_ids: set[str],
    excluded_repositories: set[str],
    optimization_repositories: set[str],
) -> tuple[list[SWEHoldoutCandidate], set[str]]:
    attempted: set[str] = set()
    eligible: list[SWEHoldoutCandidate] = []
    for record in records:
        candidate = SWEHoldoutCandidate.from_qualification(record)
        expected = candidates.get(candidate.instance_id)
        if expected != candidate:
            raise SWEHoldoutCohortError(
                "encrypted Holdout qualification differs from the pinned dataset"
            )
        attempted.add(candidate.instance_id)
        if bool(record.get("eligible")):
            if candidate.instance_id in excluded_ids:
                raise SWEHoldoutCohortError(
                    "encrypted Holdout cohort contains an Operator-visible identity"
                )
            if candidate.repository in optimization_repositories:
                raise SWEHoldoutCohortError(
                    "encrypted Holdout cohort overlaps an Optimization repository"
                )
            if candidate.repository in excluded_repositories:
                raise SWEHoldoutCohortError(
                    "encrypted Holdout cohort contains an Operator-visible repository"
                )
            eligible.append(candidate)
    if len(eligible) > 6:
        raise SWEHoldoutCohortError(
            "encrypted Holdout catalog contains more than six eligible cases"
        )
    if len({item.repository for item in eligible}) != len(eligible):
        raise SWEHoldoutCohortError(
            "encrypted Holdout catalog contains duplicate repositories"
        )
    return eligible, attempted


def _discover_operator_visible_values(
    root: Path,
    candidates: set[str],
) -> set[str]:
    observed: set[str] = set()
    if not root.is_dir():
        return observed

    encoded_ids = {
        candidate.encode("utf-8"): candidate for candidate in candidates
    }
    if not encoded_ids:
        return observed
    matcher = re.compile(
        b"|".join(
            re.escape(value)
            for value in sorted(encoded_ids, key=len, reverse=True)
        )
    )
    overlap = max(len(value) for value in encoded_ids) - 1
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SWEHoldoutCohortError(
                "Operator-visible SWE evidence contains a symbolic link"
            )
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        observed.update(
            candidate
            for candidate in candidates
            if candidate in relative_text
        )
        if not path.is_file():
            continue
        try:
            tail = b""
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    value = tail + chunk
                    observed.update(
                        encoded_ids[match.group(0)] for match in matcher.finditer(value)
                    )
                    tail = value[-overlap:] if overlap else b""
        except OSError:
            raise SWEHoldoutCohortError(
                "Operator-visible SWE evidence cannot be audited"
            ) from None
    return observed


def discover_operator_visible_live_ids(
    root: Path,
    candidate_ids: set[str],
) -> set[str]:
    """Find pinned Live instance identities in every Operator-visible byte stream."""

    return _discover_operator_visible_values(root, candidate_ids)


def discover_operator_visible_live_repositories(
    root: Path,
    repositories: set[str],
) -> set[str]:
    """Find repository identities already exposed through Operator-visible evidence."""

    return _discover_operator_visible_values(root, repositories)


def discover_git_exposed_values(
    project_root: Path,
    candidates: set[str],
) -> set[str]:
    """Find identities in the tracked tree and every ref-reachable Git patch."""

    if not candidates:
        return set()
    encoded = {value.encode("utf-8"): value for value in candidates}
    matcher = re.compile(
        b"|".join(re.escape(value) for value in sorted(encoded, key=len, reverse=True))
    )
    observed: set[str] = set()

    def scan(value: bytes) -> None:
        observed.update(encoded[match.group(0)] for match in matcher.finditer(value))

    listed = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        raise SWEHoldoutCohortError("Operator-visible Git tree cannot be audited")
    for raw_relative in listed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        scan(raw_relative)
        path = project_root / Path(os.fsdecode(raw_relative))
        if path.is_symlink():
            try:
                scan(os.fsencode(os.readlink(path)))
            except OSError as exc:
                raise SWEHoldoutCohortError(
                    "Operator-visible Git symlink cannot be audited"
                ) from exc
            continue
        if not path.exists():
            # A tracked deletion is already represented in reachable history.
            continue
        if not path.is_file():
            raise SWEHoldoutCohortError(
                "Operator-visible Git entry is not a regular file"
            )
        try:
            scan(path.read_bytes())
        except OSError as exc:
            raise SWEHoldoutCohortError(
                "Operator-visible tracked source cannot be audited"
            ) from exc

    history = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "--all",
            "--format=",
            "--patch",
            "--no-ext-diff",
            "--no-renames",
        ],
        capture_output=True,
        check=False,
    )
    if history.returncode != 0:
        raise SWEHoldoutCohortError("Operator-visible Git history cannot be audited")
    scan(history.stdout)
    return observed


def advance_holdout_cohort(
    candidates: Sequence[SWEHoldoutCandidate],
    existing_records: Sequence[Mapping[str, Any]],
    *,
    protocol_digest: str,
    guard_secret: str | bytes,
    optimization_repositories: set[str],
    excluded_ids: set[str],
    excluded_repositories: set[str] | None = None,
    max_candidates: int,
    qualify: Callable[[SWEHoldoutCandidate], bool],
    progress: Callable[[SWEHoldoutCohortProgress], None] | None = None,
) -> SWEHoldoutCohortProgress:
    """Qualify a secret-ordered 6-repository/4-language Holdout cohort."""

    if max_candidates < 6 or max_candidates > 128:
        raise SWEHoldoutCohortError("max_candidates must be between 6 and 128")
    secret = _secret_bytes(guard_secret)
    excluded_repository_values = set(excluded_repositories or ())
    by_id = {item.instance_id: item for item in candidates}
    if len(by_id) != len(candidates):
        raise SWEHoldoutCohortError("pinned Live dataset contains duplicate identities")
    eligible, attempted = _validate_existing(
        by_id,
        existing_records,
        excluded_ids=excluded_ids,
        excluded_repositories=excluded_repository_values,
        optimization_repositories=optimization_repositories,
    )
    new_attempts = 0

    while len(eligible) < 6:
        repositories = {item.repository for item in eligible}
        languages = {item.language for item in eligible}
        available = [
            item
            for item in candidates
            if item.instance_id not in attempted
            and item.instance_id not in excluded_ids
            and item.repository not in optimization_repositories
            and item.repository not in excluded_repository_values
            and item.repository not in repositories
        ]
        remaining_slots = 6 - len(eligible)
        available_repositories = {item.repository for item in available}
        possible_languages = languages | {item.language for item in available}
        if (
            len(available_repositories) < remaining_slots
            or len(possible_languages) < 4
        ):
            raise SWEHoldoutCohortError(
                "pinned Live cohort cannot satisfy repository/language isolation"
            )
        if new_attempts >= max_candidates:
            raise SWEHoldoutCohortError(
                "Holdout qualification candidate budget was exhausted"
            )
        if len(languages) < 4:
            available = [item for item in available if item.language not in languages]
        candidate = min(
            available,
            key=lambda item: _candidate_rank(secret, protocol_digest, item),
        )
        attempted.add(candidate.instance_id)
        new_attempts += 1
        if qualify(candidate):
            eligible.append(candidate)
        state = SWEHoldoutCohortProgress(
            attempted_count=len(attempted),
            new_attempt_count=new_attempts,
            eligible_count=len(eligible),
            repository_count=len({item.repository for item in eligible}),
            language_count=len({item.language for item in eligible}),
        )
        if progress is not None:
            progress(state)

    final = SWEHoldoutCohortProgress(
        attempted_count=len(attempted),
        new_attempt_count=new_attempts,
        eligible_count=len(eligible),
        repository_count=len({item.repository for item in eligible}),
        language_count=len({item.language for item in eligible}),
    )
    if (
        final.eligible_count != 6
        or final.repository_count != 6
        or final.language_count < 4
    ):
        raise SWEHoldoutCohortError("Holdout cohort invariant failed")
    return final


class SWEHoldoutGuardService:
    """Human-Guard-only orchestration that never projects Holdout identities."""

    def __init__(
        self,
        project_root: Path,
        *,
        benchmark_service: EvalBenchmarkService | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.benchmark = benchmark_service or EvalBenchmarkService(self.project_root)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise SWEHoldoutCohortError(
                        "pinned SWE dataset row is not a mapping"
                    )
                rows.append(value)
        return rows

    def _operator_visible_live_identities(
        self,
        protocol: SWEExperimentProtocol,
        candidates: Sequence[SWEHoldoutCandidate],
    ) -> tuple[set[str], set[str]]:
        config = self.benchmark.config
        candidate_ids = {item.instance_id for item in candidates}
        candidate_repositories = {item.repository for item in candidates}
        roots = (
            config.task_root,
            self.project_root / ".autobugfix/archive",
            self.project_root / ".autobugfix/controller",
            self.project_root / ".autobugfix-memory",
            config.eval.benchmarks.trusted_case_root / "swe",
            config.eval.benchmarks.visible_manifest_root,
            config.eval.benchmarks.raw_codex.runtime_root,
            config.operator.state.root,
            config.operator.artifacts.root,
            config.operator.worktrees.root,
            config.operator.experiment_lines.root,
            config.operator.experiment_lines.checkpoint_root,
            config.operator.experiment_lines.active_release_root,
            config.operator.promotion.release_root,
            config.operator.promotion.active_release_link,
            self.project_root / ".trellis/tasks",
            self.project_root / ".trellis/workspace",
        )
        identities = set(protocol.holdout_excluded_instances)
        unknown = identities - candidate_ids
        if unknown:
            raise SWEHoldoutCohortError(
                "SWE Holdout exclusion ledger contains identities outside the pinned dataset"
            )
        for root in roots:
            identities.update(
                discover_operator_visible_live_ids(root.resolve(), candidate_ids)
            )
        identities.update(discover_git_exposed_values(self.project_root, candidate_ids))
        repositories: set[str] = set()
        for root in roots:
            repositories.update(
                discover_operator_visible_live_repositories(
                    root.resolve(), candidate_repositories
                )
            )
        repositories.update(
            discover_git_exposed_values(self.project_root, candidate_repositories)
        )
        return identities, repositories

    def _optimization_repositories(
        self, protocol: SWEExperimentProtocol
    ) -> set[str]:
        runtime = SWERuntime(
            self.project_root,
            self.benchmark.config.eval.benchmarks,
        )
        snapshot = runtime.read_dataset_snapshot("swebench_verified")
        selected = {item.instance_id for item in protocol.optimization_cases}
        rows = self._read_jsonl(Path(snapshot.path))
        repositories = {
            _required(row.get("repo"), "Optimization repository")
            for row in rows
            if row.get("instance_id") in selected
        }
        observed = {
            str(row.get("instance_id"))
            for row in rows
            if row.get("instance_id") in selected
        }
        if observed != selected:
            raise SWEHoldoutCohortError(
                "Optimization identities are absent from the pinned dataset"
            )
        return repositories

    @staticmethod
    def _write_private_report(root: Path, data: Mapping[str, Any]) -> Path:
        destination = root / "runtime-reports" / f"{data['record_digest']}.yaml"
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            return destination
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(yaml.safe_dump(dict(data), sort_keys=False))
            stream.flush()
            os.fsync(stream.fileno())
        return destination

    def _private_runner(
        self,
        store: SWEGuardStore,
    ) -> tuple[SWELiveAdapter, str]:
        runtime = self.benchmark._swe_guard_runtime(store)
        doctor = runtime.doctor(
            "swebench_live",
            store.root / "runtime-doctor" / uuid.uuid4().hex,
        )
        doctor_record = doctor.to_dict()
        self._write_private_report(store.root, doctor_record)
        if not doctor.passed:
            raise SWEHoldoutCohortError(
                "private SWE Guard runtime doctor failed; inspect Guard-local evidence"
            )
        return SWELiveAdapter(runtime), str(doctor_record["record_digest"])

    def qualify_cohort(
        self,
        protocol_path: Path,
        *,
        guard_root: Path,
        guard_secret: str | bytes,
        max_candidates: int = 24,
        progress: Callable[[SWEHoldoutCohortProgress], None] | None = None,
    ) -> dict[str, Any]:
        _secret_bytes(guard_secret)
        if max_candidates < 6 or max_candidates > 128:
            raise SWEHoldoutCohortError("max_candidates must be between 6 and 128")
        protocol = SWEExperimentProtocol.from_yaml(protocol_path)
        store = self.benchmark._swe_guard_store(guard_root)
        runner, doctor_digest = self._private_runner(store)
        if runner.snapshot.dataset != protocol.holdout_dataset:
            raise SWEHoldoutCohortError(
                "private Guard dataset differs from the frozen protocol"
            )
        candidates = [
            SWEHoldoutCandidate.from_live_row(row)
            for row in self._read_jsonl(Path(runner.snapshot.path))
        ]
        records = store.qualification_records(
            secret=guard_secret,
            protocol_digest=protocol.protocol_digest,
            runtime_id=runner.runtime.runtime_id,
        )
        excluded_ids, excluded_repositories = self._operator_visible_live_identities(
            protocol,
            candidates,
        )
        prior_ids, prior_repositories = store.load_exposure_ledger(
            secret=guard_secret,
            dataset_revision=SWE_LIVE_DATASET_REVISION,
        )
        excluded_ids.update(prior_ids)
        excluded_repositories.update(prior_repositories)
        exposure_digest = store.write_exposure_ledger(
            instance_ids=excluded_ids,
            repositories=excluded_repositories,
            secret=guard_secret,
            dataset_revision=SWE_LIVE_DATASET_REVISION,
        )
        optimization_repositories = self._optimization_repositories(protocol)

        def qualify(candidate: SWEHoldoutCandidate) -> bool:
            try:
                result = self.benchmark.qualify_swe(
                    protocol_path,
                    "swebench_live",
                    candidate.instance_id,
                    guard_root=store.root,
                    guard_secret=guard_secret,
                )
            except Exception:
                raise SWEHoldoutCohortError(
                    "Holdout qualification failed inside the private Guard; "
                    "inspect Guard-local evidence"
                ) from None
            return bool(result.get("eligible"))

        final = advance_holdout_cohort(
            candidates,
            records,
            protocol_digest=protocol.protocol_digest,
            guard_secret=guard_secret,
            optimization_repositories=optimization_repositories,
            excluded_ids=excluded_ids,
            excluded_repositories=excluded_repositories,
            max_candidates=max_candidates,
            qualify=qualify,
            progress=progress,
        )
        final_records = store.qualification_records(
            secret=guard_secret,
            protocol_digest=protocol.protocol_digest,
            runtime_id=runner.runtime.runtime_id,
        )
        eligible = [record for record in final_records if record.get("eligible")]
        if len(eligible) != 6:
            raise SWEHoldoutCohortError(
                "private Guard catalog does not contain exactly six eligible cases"
            )
        catalog_path = store._catalog_path(
            protocol.protocol_digest,
            runner.runtime.runtime_id,
        )
        result = record_with_digest(
            {
                "schema": "autobugfix-swe-holdout-qualification-summary-v1",
                "status": "qualified",
                "protocol_digest": protocol.protocol_digest,
                "runtime_id": runner.runtime.runtime_id,
                "runtime_doctor_digest": doctor_digest,
                **final.aggregate(),
                "operator_visible_exclusion_count": len(excluded_ids),
                "operator_visible_repository_exclusion_count": len(
                    excluded_repositories
                ),
                "encrypted_exposure_ledger_sha256": exposure_digest,
                "encrypted_catalog_sha256": guard_artifact_digest(catalog_path),
                "completed_at": utc_now(),
            }
        )
        forbidden = {
            item.instance_id for item in candidates
        } | {item.repository for item in candidates}
        encoded = yaml.safe_dump(result, sort_keys=True)
        if any(value and value in encoded for value in forbidden):
            raise SWEHoldoutCohortError(
                "Holdout identity leaked into the public qualification summary"
            )
        if any(key in result for key in ("cases", "repositories", "languages")):
            raise SWEHoldoutCohortError(
                "Holdout cohort details leaked into the public qualification summary"
            )
        return result
