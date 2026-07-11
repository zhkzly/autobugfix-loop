from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from autobugfix.git_utils import GitError, run_git


class TrustedPolicyError(RuntimeError):
    pass


CONSTITUTION_REPO_PATH = "src/autobugfix/operator/constitution.yaml"


@dataclass(slots=True, frozen=True)
class TrustedPolicy:
    data: dict[str, Any]
    source: str
    trusted: bool


def _parse_constitution(text: str, source: str) -> dict[str, Any]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise TrustedPolicyError(f"operator constitution must be a mapping: {source}")
    if int(data.get("version", 0)) < 3:
        raise TrustedPolicyError(f"trusted operator constitution must be version 3 or newer: {source}")
    required_keys = [
        "project",
        "operator_prompt_context",
        "loops",
        "operator_roles",
        "hook_assignments",
        "transition_contract",
        "operator_runtime_minimums",
        "layers",
        "protected_paths",
        "validation_profiles",
        "metrics",
    ]
    if int(data.get("version", 0)) >= 4:
        required_keys.append("experiment_governance")
    for key in required_keys:
        if key not in data:
            raise TrustedPolicyError(f"trusted operator constitution missing {key}: {source}")
    return data


def load_trusted_policy(
    project_root: Path,
    *,
    trusted_ref: str | None = "origin/main",
    trusted_file: Path | None = None,
    bootstrap: bool = False,
) -> TrustedPolicy:
    root = project_root.resolve()
    if trusted_file is not None:
        source = trusted_file.expanduser().resolve()
        return TrustedPolicy(_parse_constitution(source.read_text(encoding="utf-8"), str(source)), str(source), True)
    if trusted_ref:
        try:
            result = run_git(root, ["show", f"{trusted_ref}:{CONSTITUTION_REPO_PATH}"], check=True)
        except GitError as exc:
            if not bootstrap:
                raise TrustedPolicyError(
                    f"cannot load trusted operator policy from {trusted_ref!r}; "
                    "use --trusted-file or explicit --bootstrap-policy for the first governance installation"
                ) from exc
        else:
            source = f"git:{trusted_ref}:{CONSTITUTION_REPO_PATH}"
            return TrustedPolicy(_parse_constitution(result.stdout, source), source, True)
    if not bootstrap:
        raise TrustedPolicyError("no trusted operator policy source configured")
    source = Path(__file__).with_name("constitution.yaml")
    return TrustedPolicy(_parse_constitution(source.read_text(encoding="utf-8"), str(source)), f"bootstrap:{source}", False)
