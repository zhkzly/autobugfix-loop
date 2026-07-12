from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from autobugfix.verifier import (
    HARNESS_ERROR_MARKER,
    POLICY_VIOLATION_MARKER,
    classify_verifier_result,
)
from autobugfix.eval.benchmarks.defects4j import Defects4JError, Defects4JRuntime
from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_file,
    record_with_digest,
    verify_record,
)
from autobugfix.models import (
    Defects4JBenchmarkConfig,
    EvalBenchmarkConfig,
)


@dataclass(slots=True, frozen=True)
class Defects4JVerifierContract:
    image_id: str
    platform: str
    framework_revision: str
    project: str
    bug_id: int
    source_roots: tuple[str, ...]
    triggering_tests: tuple[str, ...]
    verifier_metadata_path: str
    verifier_metadata_sha256: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not self.image_id.startswith("sha256:"):
            raise BenchmarkContractError("verifier image must be an immutable ID")
        if self.platform != "linux/amd64":
            raise BenchmarkContractError("verifier platform must be linux/amd64")
        if not self.framework_revision.strip():
            raise BenchmarkContractError("verifier framework revision is required")
        if not self.project.strip() or self.bug_id < 1:
            raise BenchmarkContractError("verifier Defects4J identity is invalid")
        if not self.source_roots:
            raise BenchmarkContractError("verifier source roots must not be empty")
        if not self.triggering_tests:
            raise BenchmarkContractError(
                "Execution verifier triggering tests must not be empty"
            )
        if self.timeout_seconds < 1:
            raise BenchmarkContractError("verifier timeout must be positive")
        if not Path(self.verifier_metadata_path).is_absolute():
            raise BenchmarkContractError("verifier metadata path must be absolute")
        if len(self.verifier_metadata_sha256) != 64 or any(
            value not in "0123456789abcdef"
            for value in self.verifier_metadata_sha256
        ):
            raise BenchmarkContractError("verifier metadata digest must be sha256")
        for root in self.source_roots:
            path = Path(root)
            if path.is_absolute() or ".." in path.parts:
                raise BenchmarkContractError(
                    f"verifier source root must be repository-relative: {root}"
                )

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": 4,
                "image_id": self.image_id,
                "platform": self.platform,
                "framework_revision": self.framework_revision,
                "project": self.project,
                "bug_id": self.bug_id,
                "source_roots": list(self.source_roots),
                "triggering_tests": list(self.triggering_tests),
                "verifier_metadata_path": self.verifier_metadata_path,
                "verifier_metadata_sha256": self.verifier_metadata_sha256,
                "timeout_seconds": self.timeout_seconds,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Defects4JVerifierContract":
        verify_record(data)
        if int(data.get("schema_version") or 0) != 4:
            raise BenchmarkContractError("unsupported verifier contract schema")
        roots = data.get("source_roots")
        if not isinstance(roots, Sequence) or isinstance(roots, (str, bytes)):
            raise BenchmarkContractError("verifier source_roots must be a list")
        return cls(
            image_id=str(data.get("image_id") or ""),
            platform=str(data.get("platform") or ""),
            framework_revision=str(data.get("framework_revision") or ""),
            project=str(data.get("project") or ""),
            bug_id=int(data.get("bug_id") or 0),
            source_roots=tuple(str(item) for item in roots),
            triggering_tests=tuple(
                str(item) for item in data.get("triggering_tests") or []
            ),
            verifier_metadata_path=str(data.get("verifier_metadata_path") or ""),
            verifier_metadata_sha256=str(
                data.get("verifier_metadata_sha256") or ""
            ),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "Defects4JVerifierContract":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise BenchmarkContractError("verifier contract must be a mapping")
        return cls.from_dict(data)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False), encoding="utf-8")
        return path


class Defects4JManagedVerifier:
    def __init__(self, contract: Defects4JVerifierContract):
        self.contract = contract

    @property
    def command_id(self) -> str:
        return f"managed:defects4j:{self.contract.to_dict()['record_digest']}"

    def run(
        self,
        worktree: Path,
        artifact_dir: Path,
        *,
        timeout_seconds: int | None,
    ):
        from autobugfix.models import VerifierResult, utc_now

        started = utc_now()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        if timeout_seconds is not None and timeout_seconds < self.contract.timeout_seconds:
            contract = Defects4JVerifierContract(
                image_id=self.contract.image_id,
                platform=self.contract.platform,
                framework_revision=self.contract.framework_revision,
                project=self.contract.project,
                bug_id=self.contract.bug_id,
                source_roots=self.contract.source_roots,
                triggering_tests=self.contract.triggering_tests,
                verifier_metadata_path=self.contract.verifier_metadata_path,
                verifier_metadata_sha256=self.contract.verifier_metadata_sha256,
                timeout_seconds=timeout_seconds,
            )
        else:
            contract = self.contract
        try:
            passed, _, stdout, stderr, exit_code = run_visible_verifier(
                worktree,
                contract,
                artifact_dir / "official",
            )
            resolved_exit = int(exit_code or (0 if passed else 1))
        except Exception as exc:
            stdout = ""
            stderr = f"{HARNESS_ERROR_MARKER} {type(exc).__name__}: {exc}\n"
            resolved_exit = 2
        (artifact_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (artifact_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        return VerifierResult(
            command=self.command_id,
            exit_code=resolved_exit,
            stdout=stdout,
            stderr=stderr,
            started_at=started,
            finished_at=utc_now(),
            outcome=classify_verifier_result(resolved_exit, stderr),
        )


def managed_verifier_for_receipt(
    receipt,
    benchmark_config: EvalBenchmarkConfig,
) -> Defects4JManagedVerifier:
    return Defects4JManagedVerifier(
        Defects4JVerifierContract(
            image_id=receipt.verifier_runtime_id,
            platform=benchmark_config.defects4j.platform,
            framework_revision=receipt.framework_revision,
            project=receipt.project,
            bug_id=receipt.bug_id,
            source_roots=receipt.source_roots,
            triggering_tests=receipt.triggering_tests,
            verifier_metadata_path=receipt.verifier_metadata_path,
            verifier_metadata_sha256=receipt.verifier_metadata_sha256,
            timeout_seconds=benchmark_config.command_timeout_seconds,
        )
    )


@dataclass(slots=True, frozen=True)
class Defects4JOracleContract:
    image_id: str
    platform: str
    framework_revision: str
    project: str
    bug_id: int
    eligibility_receipt_digest: str
    source_roots: tuple[str, ...]
    baseline_failing_tests: tuple[str, ...]
    verifier_metadata_path: str
    verifier_metadata_sha256: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not self.image_id.startswith("sha256:"):
            raise BenchmarkContractError("oracle image must be an immutable ID")
        if self.platform != "linux/amd64":
            raise BenchmarkContractError("oracle platform must be linux/amd64")
        if not self.framework_revision.strip():
            raise BenchmarkContractError("oracle framework revision is required")
        if not self.project.strip() or self.bug_id < 1:
            raise BenchmarkContractError("oracle Defects4J identity is invalid")
        if len(self.eligibility_receipt_digest) != 64 or any(
            value not in "0123456789abcdef"
            for value in self.eligibility_receipt_digest
        ):
            raise BenchmarkContractError(
                "oracle eligibility receipt digest must be sha256"
            )
        if not self.source_roots:
            raise BenchmarkContractError("oracle source roots must not be empty")
        if self.timeout_seconds < 1:
            raise BenchmarkContractError("oracle timeout must be positive")
        if not Path(self.verifier_metadata_path).is_absolute():
            raise BenchmarkContractError("oracle metadata path must be absolute")
        if len(self.verifier_metadata_sha256) != 64 or any(
            value not in "0123456789abcdef"
            for value in self.verifier_metadata_sha256
        ):
            raise BenchmarkContractError("oracle metadata digest must be sha256")
        for root in self.source_roots:
            path = Path(root)
            if path.is_absolute() or ".." in path.parts:
                raise BenchmarkContractError(
                    f"oracle source root must be repository-relative: {root}"
                )


class Defects4JOfficialOracle:
    """Hidden final evaluator. This object is never passed to Execution."""

    def __init__(self, contract: Defects4JOracleContract):
        self.contract = contract

    def run(
        self,
        worktree: Path,
        artifact_dir: Path,
        *,
        timeout_seconds: int | None,
    ):
        from autobugfix.models import VerifierResult, utc_now

        started = utc_now()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        timeout = min(
            self.contract.timeout_seconds,
            timeout_seconds
            if timeout_seconds is not None
            else self.contract.timeout_seconds,
        )
        contract = Defects4JOracleContract(
            image_id=self.contract.image_id,
            platform=self.contract.platform,
            framework_revision=self.contract.framework_revision,
            project=self.contract.project,
            bug_id=self.contract.bug_id,
            eligibility_receipt_digest=self.contract.eligibility_receipt_digest,
            source_roots=self.contract.source_roots,
            baseline_failing_tests=self.contract.baseline_failing_tests,
            verifier_metadata_path=self.contract.verifier_metadata_path,
            verifier_metadata_sha256=self.contract.verifier_metadata_sha256,
            timeout_seconds=timeout,
        )
        try:
            passed, _, stdout, stderr, exit_code = run_official_oracle(
                worktree,
                contract,
                artifact_dir / "official",
            )
            resolved_exit = int(exit_code or (0 if passed else 1))
        except Exception as exc:
            stdout = ""
            stderr = f"{HARNESS_ERROR_MARKER} {type(exc).__name__}: {exc}\n"
            resolved_exit = 2
        (artifact_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (artifact_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        return VerifierResult(
            command=(
                "official:defects4j:"
                + self.contract.eligibility_receipt_digest
            ),
            exit_code=resolved_exit,
            stdout=stdout,
            stderr=stderr,
            started_at=started,
            finished_at=utc_now(),
            outcome=classify_verifier_result(resolved_exit, stderr),
        )


def official_oracle_for_receipt(
    receipt,
    benchmark_config: EvalBenchmarkConfig,
) -> Defects4JOfficialOracle:
    return Defects4JOfficialOracle(
        Defects4JOracleContract(
            image_id=receipt.verifier_runtime_id,
            platform=benchmark_config.defects4j.platform,
            framework_revision=receipt.framework_revision,
            project=receipt.project,
            bug_id=receipt.bug_id,
            eligibility_receipt_digest=str(receipt.to_dict()["record_digest"]),
            source_roots=receipt.source_roots,
            baseline_failing_tests=receipt.baseline_failing_tests,
            verifier_metadata_path=receipt.verifier_metadata_path,
            verifier_metadata_sha256=receipt.verifier_metadata_sha256,
            timeout_seconds=benchmark_config.command_timeout_seconds,
        )
    )


def _git_paths(worktree: Path, args: Sequence[str]) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise Defects4JError(result.stderr.strip() or "Git changed-path query failed")
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def changed_paths(worktree: Path) -> tuple[str, ...]:
    tracked = _git_paths(worktree, ("diff", "--name-only", "HEAD"))
    untracked = _git_paths(
        worktree,
        ("ls-files", "--others", "--exclude-standard"),
    )
    return tuple(dict.fromkeys((*tracked, *untracked)))


def ignored_paths(worktree: Path) -> tuple[str, ...]:
    return _git_paths(
        worktree,
        ("ls-files", "--others", "--ignored", "--exclude-standard"),
    )


def validate_changed_paths(
    paths: Sequence[str], source_roots: Sequence[str]
) -> tuple[str, ...]:
    roots = tuple(Path(root) for root in source_roots)
    violations: list[str] = []
    for value in paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            violations.append(value)
            continue
        if not any(path == root or path.is_relative_to(root) for root in roots):
            violations.append(value)
    return tuple(violations)


def unexpected_failures(
    failures: Sequence[str], baseline_failing_tests: Sequence[str]
) -> tuple[str, ...]:
    baseline = set(baseline_failing_tests)
    return tuple(item for item in failures if item not in baseline)


def cleanup_test_artifacts(
    worktree: Path,
    before: Sequence[str],
    ignored_before: Sequence[str] = (),
) -> None:
    before_set = set(before)
    tracked_after = set(_git_paths(worktree, ("diff", "--name-only", "HEAD")))
    generated_tracked = sorted(tracked_after - before_set)
    if generated_tracked:
        result = subprocess.run(
            ["git", "-C", str(worktree), "restore", "--worktree", "--", *generated_tracked],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise Defects4JError(
                result.stderr.strip() or "failed to restore test-generated tracked files"
            )
    untracked_after = set(
        _git_paths(worktree, ("ls-files", "--others", "--exclude-standard"))
    )
    generated_untracked = untracked_after - before_set
    generated_ignored = set(ignored_paths(worktree)) - set(ignored_before)
    tracked = set(_git_paths(worktree, ("ls-files",)))
    protected = tracked | before_set | set(ignored_before)
    for value in _minimal_removal_roots(
        generated_untracked | generated_ignored,
        protected,
    ):
        path = worktree / value
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _minimal_removal_roots(
    generated: set[str],
    protected: set[str],
) -> tuple[str, ...]:
    blocked_directories: set[Path] = set()
    for value in protected:
        path = Path(value)
        blocked_directories.add(path)
        blocked_directories.update(
            parent for parent in path.parents if parent != Path(".")
        )
    candidates: set[Path] = set()
    for value in generated:
        path = Path(value)
        candidate = path
        for parent in path.parents:
            if parent == Path(".") or parent in blocked_directories:
                break
            candidate = parent
        candidates.add(candidate)
    roots: list[Path] = []
    for candidate in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        if any(candidate == root or candidate.is_relative_to(root) for root in roots):
            continue
        roots.append(candidate)
    return tuple(str(item) for item in roots)


def _runtime_for_contract(
    artifact_dir: Path,
    *,
    image_id: str,
    platform: str,
    framework_revision: str,
    timeout_seconds: int,
) -> Defects4JRuntime:
    return Defects4JRuntime(
        EvalBenchmarkConfig(
            cache_root=artifact_dir / "cache",
            trusted_case_root=artifact_dir / "trusted",
            visible_manifest_root=artifact_dir / "visible",
            command_timeout_seconds=timeout_seconds,
            min_free_disk_gb=1,
            defects4j=Defects4JBenchmarkConfig(
                image=image_id,
                platform=platform,
                framework_revision=framework_revision,
            ),
        )
    )


def _inject_metadata(
    worktree: Path,
    *,
    project: str,
    bug_id: int,
    metadata_path: str,
    metadata_sha256: str,
) -> tuple[Path, Path]:
    injected_config = worktree / ".defects4j.config"
    injected_metadata = worktree / "defects4j.build.properties"
    if (
        injected_config.exists()
        or injected_config.is_symlink()
        or injected_metadata.exists()
        or injected_metadata.is_symlink()
    ):
        raise Defects4JError(
            POLICY_VIOLATION_MARKER
            + " candidate contains service-owned Defects4J metadata"
        )
    metadata_source = Path(metadata_path)
    if metadata_source.is_symlink():
        raise Defects4JError("trusted verifier metadata must not be a symlink")
    metadata_source = metadata_source.resolve()
    if (
        not metadata_source.is_file()
        or metadata_source == worktree
        or metadata_source.is_relative_to(worktree)
        or digest_file(metadata_source) != metadata_sha256
    ):
        raise Defects4JError("trusted verifier metadata is missing or changed")
    injected_config.write_text(
        f"pid={project}\nvid={bug_id}b\n",
        encoding="utf-8",
    )
    shutil.copyfile(metadata_source, injected_metadata)
    return injected_config, injected_metadata


def run_visible_verifier(
    worktree: Path,
    contract: Defects4JVerifierContract,
    artifact_dir: Path,
) -> tuple[bool, tuple[str, ...], str, str, int | None]:
    worktree = worktree.resolve()
    paths = changed_paths(worktree)
    ignored = ignored_paths(worktree)
    violations = validate_changed_paths(paths, contract.source_roots)
    if violations:
        message = POLICY_VIOLATION_MARKER + " changed paths outside production source roots:\n" + "\n".join(
            f"- {item}" for item in violations
        )
        return False, (), "", message + "\n", 3

    runtime = _runtime_for_contract(
        artifact_dir,
        image_id=contract.image_id,
        platform=contract.platform,
        framework_revision=contract.framework_revision,
        timeout_seconds=contract.timeout_seconds,
    )
    try:
        _inject_metadata(
            worktree,
            project=contract.project,
            bug_id=contract.bug_id,
            metadata_path=contract.verifier_metadata_path,
            metadata_sha256=contract.verifier_metadata_sha256,
        )
    except Defects4JError as exc:
        if str(exc).startswith(POLICY_VIOLATION_MARKER):
            return False, (), "", str(exc) + "\n", 3
        raise
    all_failures: list[str] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    command_failed = False
    try:
        for index, triggering_test in enumerate(contract.triggering_tests, start=1):
            evidence, failures = runtime.verify_worktree(
                worktree,
                artifact_root=artifact_dir,
                name=f"visible-trigger-{index}",
                image=contract.image_id,
                single_test=triggering_test,
            )
            stdout_parts.append(
                Path(evidence.stdout_path).read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            stderr_parts.append(
                Path(evidence.stderr_path).read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            all_failures.extend(failures)
            if evidence.timed_out or evidence.exit_code is None:
                raise Defects4JError(
                    f"visible triggering test did not complete: {triggering_test}"
                )
            if evidence.exit_code != 0:
                command_failed = True
    finally:
        cleanup_test_artifacts(worktree, paths, ignored)
    failures = tuple(dict.fromkeys(all_failures))
    stdout = "\n".join(part for part in stdout_parts if part)
    stderr = "\n".join(part for part in stderr_parts if part)
    if failures:
        stderr += "\nVisible Defects4J triggering-test failures:\n" + "\n".join(
            f"- {item}" for item in failures
        )
        stderr += "\n"
    passed = not command_failed and not failures
    return passed, failures, stdout, stderr, 0 if passed else 1


def run_official_oracle(
    worktree: Path,
    contract: Defects4JOracleContract,
    artifact_dir: Path,
) -> tuple[bool, tuple[str, ...], str, str, int | None]:
    worktree = worktree.resolve()
    paths = changed_paths(worktree)
    ignored = ignored_paths(worktree)
    violations = validate_changed_paths(paths, contract.source_roots)
    if violations:
        message = (
            POLICY_VIOLATION_MARKER
            + " changed paths outside production source roots:\n"
            + "\n".join(f"- {item}" for item in violations)
        )
        return False, (), "", message + "\n", 3
    runtime = _runtime_for_contract(
        artifact_dir,
        image_id=contract.image_id,
        platform=contract.platform,
        framework_revision=contract.framework_revision,
        timeout_seconds=contract.timeout_seconds,
    )
    _inject_metadata(
        worktree,
        project=contract.project,
        bug_id=contract.bug_id,
        metadata_path=contract.verifier_metadata_path,
        metadata_sha256=contract.verifier_metadata_sha256,
    )
    try:
        evidence, failures = runtime.verify_worktree(
            worktree,
            artifact_root=artifact_dir,
            name="official-full-suite",
            image=contract.image_id,
        )
        stdout = Path(evidence.stdout_path).read_text(
            encoding="utf-8", errors="replace"
        )
        stderr = Path(evidence.stderr_path).read_text(
            encoding="utf-8", errors="replace"
        )
        if evidence.timed_out or evidence.exit_code is None:
            raise Defects4JError("official Defects4J oracle did not complete")
    finally:
        cleanup_test_artifacts(worktree, paths, ignored)
    if failures:
        stderr += "\nOfficial Defects4J failing tests:\n" + "\n".join(
            f"- {item}" for item in failures
        )
        stderr += "\n"
    unexpected = unexpected_failures(failures, contract.baseline_failing_tests)
    if unexpected:
        stderr += "\nFailures outside the fixed-revision baseline:\n" + "\n".join(
            f"- {item}" for item in unexpected
        )
        stderr += "\n"
    passed = evidence.exit_code == 0 and not unexpected
    return passed, failures, stdout, stderr, 0 if passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autobugfix-defects4j-verify")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--expected-contract-digest", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--worktree", default=".")
    args = parser.parse_args(argv)
    try:
        contract = Defects4JVerifierContract.from_yaml(Path(args.contract).resolve())
        contract_digest = str(contract.to_dict()["record_digest"])
        if contract_digest != args.expected_contract_digest:
            raise BenchmarkContractError(
                "verifier contract digest does not match trusted Execution configuration"
            )
        worktree = Path(args.worktree).resolve()
        module_path = Path(__file__).resolve()
        if module_path == worktree or module_path.is_relative_to(worktree):
            raise BenchmarkContractError(
                "verifier module resolved from the untrusted candidate worktree"
            )
        artifact_root = Path(args.artifact_root).resolve()
        if artifact_root == worktree or artifact_root.is_relative_to(worktree):
            raise BenchmarkContractError(
                "verifier artifact root must be outside the task worktree"
            )
        run_dir = artifact_root / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        passed, failures, stdout, stderr, exit_code = run_visible_verifier(
            worktree,
            contract,
            run_dir,
        )
        (run_dir / "result.yaml").write_text(
            yaml.safe_dump(
                {
                    "passed": passed,
                    "failures": list(failures),
                    "exit_code": exit_code,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        sys.stdout.write(stdout)
        sys.stdout.write(f"\nVerifier artifacts: {run_dir}\n")
        sys.stderr.write(stderr)
        return 0 if passed else int(exit_code or 1)
    except Exception as exc:
        sys.stderr.write(f"{HARNESS_ERROR_MARKER} {type(exc).__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
