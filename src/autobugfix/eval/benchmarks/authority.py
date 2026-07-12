from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import (
    BenchmarkContractError,
    digest_payload,
    record_with_digest,
    verify_record,
)
from autobugfix.git_utils import GitError, rev_parse, run_git


class GuardAuthorityError(BenchmarkContractError):
    pass


_HARNESS_PATHS = (
    "src/autobugfix",
    "containers",
    "benchmarks",
    ".agents/role-skills",
    ".agents/skills/autobugfix-eval-operator",
    ".agents/skills/autobugfix-operator-governance",
)
_CONSTITUTION_PATH = "src/autobugfix/operator/constitution.yaml"


def _git_object_id(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise GuardAuthorityError(f"Guard {field} is not a Git object ID")
    return normalized


@dataclass(slots=True, frozen=True)
class GuardCodeIdentity:
    trusted_ref: str
    trusted_commit: str
    source_tree: str
    machine_constitution_digest: str
    harness_digest: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise GuardAuthorityError("unsupported Guard code identity schema")
        if not self.trusted_ref.strip():
            raise GuardAuthorityError("Guard trusted_ref is required")
        _git_object_id(self.trusted_commit, "trusted_commit")
        _git_object_id(self.source_tree, "source_tree")
        for value, field in (
            (self.machine_constitution_digest, "machine constitution digest"),
            (self.harness_digest, "harness digest"),
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise GuardAuthorityError(f"Guard {field} must be sha256")

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "schema_version": self.schema_version,
                "trusted_ref": self.trusted_ref,
                "trusted_commit": self.trusted_commit,
                "source_tree": self.source_tree,
                "machine_constitution_digest": self.machine_constitution_digest,
                "harness_digest": self.harness_digest,
            }
        )

    @property
    def identity_digest(self) -> str:
        return str(self.to_dict()["record_digest"])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuardCodeIdentity":
        verify_record(data)
        return cls(
            schema_version=int(data.get("schema_version") or 0),
            trusted_ref=str(data.get("trusted_ref") or ""),
            trusted_commit=str(data.get("trusted_commit") or ""),
            source_tree=str(data.get("source_tree") or ""),
            machine_constitution_digest=str(
                data.get("machine_constitution_digest") or ""
            ),
            harness_digest=str(data.get("harness_digest") or ""),
        )


def resolve_guard_code_identity(
    project_root: Path,
    trusted_ref: str,
) -> GuardCodeIdentity:
    """Resolve the immutable control-plane identity used by Eval Guard.

    This check is meaningful only when invoked from the protected control
    checkout. Candidate code cannot establish its own authority by calling it.
    """

    root = project_root.resolve()
    try:
        top = Path(
            run_git(root, ["rev-parse", "--show-toplevel"]).stdout.strip()
        ).resolve()
        if top != root:
            raise GuardAuthorityError(
                "Guard must run from the Autobugfix Git repository root"
            )
        trusted_commit = _git_object_id(
            rev_parse(root, f"{trusted_ref}^{{commit}}"), "trusted_commit"
        )
        current_commit = _git_object_id(rev_parse(root, "HEAD"), "current HEAD")
        if current_commit != trusted_commit:
            raise GuardAuthorityError(
                "Guard control checkout HEAD does not match the configured trusted ref"
            )
        status = run_git(
            root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        ).stdout
        if status:
            raise GuardAuthorityError(
                "Guard control checkout contains uncommitted or untracked source changes"
            )
        source_tree = _git_object_id(
            rev_parse(root, f"{trusted_commit}^{{tree}}"), "source_tree"
        )
        constitution_text = run_git(
            root,
            ["show", f"{trusted_commit}:{_CONSTITUTION_PATH}"],
        ).stdout
        constitution = yaml.safe_load(constitution_text) or {}
        if not isinstance(constitution, Mapping):
            raise GuardAuthorityError(
                "trusted machine constitution must be a mapping"
            )
        if int(constitution.get("version") or 0) < 3:
            raise GuardAuthorityError(
                "trusted machine constitution is too old for Guard authority"
            )
        listing = run_git(
            root,
            [
                "ls-tree",
                "-r",
                "--full-tree",
                trusted_commit,
                "--",
                *_HARNESS_PATHS,
            ],
        ).stdout
        if not listing.strip():
            raise GuardAuthorityError("trusted benchmark harness tree is empty")
    except GitError as exc:
        raise GuardAuthorityError(f"cannot establish Guard Git authority: {exc}") from exc

    return GuardCodeIdentity(
        trusted_ref=trusted_ref,
        trusted_commit=trusted_commit,
        source_tree=source_tree,
        machine_constitution_digest=digest_payload(dict(constitution)),
        harness_digest=hashlib.sha256(listing.encode("utf-8")).hexdigest(),
    )
