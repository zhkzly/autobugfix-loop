from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from autobugfix.config import DEFAULT_CONFIG
from autobugfix.git_utils import GitError, current_branch, run_git
from autobugfix.operator.models import OperatorRequest, OperatorReview


class OperatorPolicyError(RuntimeError):
    pass


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    request_id: str
    branch: str
    changed_files: list[str]
    declared_layers: list[str]
    changed_layers: dict[str, list[str]]
    common_files: list[str] = field(default_factory=list)
    protected_files: list[str] = field(default_factory=list)
    out_of_scope_files: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    review_required: bool = False
    human_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "request_id": self.request_id,
            "branch": self.branch,
            "changed_files": self.changed_files,
            "declared_layers": self.declared_layers,
            "changed_layers": self.changed_layers,
            "common_files": self.common_files,
            "protected_files": self.protected_files,
            "out_of_scope_files": self.out_of_scope_files,
            "violations": self.violations,
            "review_required": self.review_required,
            "human_required": self.human_required,
        }


def load_constitution(path: Path | None = None) -> dict[str, Any]:
    source = path or Path(__file__).with_name("constitution.yaml")
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise OperatorPolicyError(f"operator constitution must be a mapping: {source}")
    return data


def _match_path(pattern: str, path: str) -> bool:
    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    return fnmatch.fnmatch(path, pattern)


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    return any(_match_path(pattern, path) for pattern in patterns)


def _layers_for_file(constitution: dict[str, Any], path: str) -> list[str]:
    layers = constitution.get("layers") or {}
    result: list[str] = []
    if not isinstance(layers, dict):
        return result
    for layer, raw in layers.items():
        if not isinstance(raw, dict):
            continue
        patterns = raw.get("paths") or []
        if isinstance(patterns, list) and _path_matches_any(path, [str(item) for item in patterns]):
            result.append(str(layer))
    return result


def _common_patterns(constitution: dict[str, Any]) -> list[str]:
    raw = constitution.get("common_paths") or []
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _protected_patterns(constitution: dict[str, Any]) -> list[str]:
    raw = ((constitution.get("architecture_change") or {}).get("protected_paths") or [])
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _changed_files(project_root: Path, base_ref: str, include_untracked: bool = True) -> list[str]:
    try:
        diff = run_git(project_root, ["diff", "--name-only", base_ref], check=True).stdout
    except GitError as exc:
        raise OperatorPolicyError(str(exc)) from exc
    files = {line.strip().replace("\\", "/") for line in diff.splitlines() if line.strip()}
    if include_untracked:
        untracked = run_git(project_root, ["ls-files", "--others", "--exclude-standard"], check=True).stdout
        files.update(line.strip().replace("\\", "/") for line in untracked.splitlines() if line.strip())
    return sorted(files)


def _has_approved_review(reviews: list[OperatorReview], human_required: bool, files: list[str]) -> bool:
    for review in reviews:
        if not review.approved:
            continue
        if human_required and not review.human:
            continue
        if review.approved_paths:
            if not all(_path_matches_any(path, review.approved_paths) for path in files):
                continue
        return True
    return False


def static_constitution_violations(project_root: Path, constitution: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    invariants = constitution.get("static_invariants") or {}
    if not isinstance(invariants, dict):
        return ["static_invariants must be a mapping"]

    gitignore = project_root / ".gitignore"
    gitignore_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    for required in invariants.get("required_gitignore") or []:
        if str(required) not in gitignore_text:
            violations.append(f"missing gitignore runtime pattern: {required}")

    roles = ((DEFAULT_CONFIG.get("codex") or {}).get("roles") or {})
    expected_roles = invariants.get("role_defaults") or {}
    if isinstance(expected_roles, dict):
        for role, expected in expected_roles.items():
            actual = roles.get(role) if isinstance(roles, dict) else None
            if not isinstance(actual, dict) or not isinstance(expected, dict):
                violations.append(f"missing role default for {role}")
                continue
            for key, expected_value in expected.items():
                if actual.get(key) != expected_value:
                    violations.append(f"role {role}.{key} expected {expected_value!r}, got {actual.get(key)!r}")

    service_py = project_root / "src/autobugfix/service.py"
    service_text = service_py.read_text(encoding="utf-8") if service_py.exists() else ""
    for marker in invariants.get("production_service_requires") or []:
        if str(marker) not in service_text:
            violations.append(f"production service missing required marker: {marker}")

    eval_root = project_root / "src/autobugfix/eval"
    forbidden = [str(item) for item in invariants.get("eval_forbidden_calls") or []]
    if eval_root.exists():
        for path in sorted(eval_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    rel = path.relative_to(project_root).as_posix()
                    violations.append(f"eval layer contains forbidden marker {marker!r} in {rel}")
    return violations


def evaluate_policy(
    project_root: Path,
    request: OperatorRequest,
    reviews: list[OperatorReview],
    base_ref: str = "HEAD",
    constitution: dict[str, Any] | None = None,
    include_untracked: bool = True,
) -> PolicyDecision:
    root = project_root.resolve()
    constitution = constitution or load_constitution()
    try:
        branch = current_branch(root)
    except GitError as exc:
        raise OperatorPolicyError(str(exc)) from exc
    changed_files = _changed_files(root, base_ref, include_untracked=include_untracked)
    declared_layers = sorted(request.declared_layers)
    changed_layers: dict[str, list[str]] = {}
    common_files: list[str] = []
    out_of_scope: list[str] = []
    protected_files: list[str] = []
    violations: list[str] = []

    protected_patterns = _protected_patterns(constitution)
    common_patterns = _common_patterns(constitution)
    for file_path in changed_files:
        file_layers = _layers_for_file(constitution, file_path)
        if _path_matches_any(file_path, protected_patterns):
            protected_files.append(file_path)
        if _path_matches_any(file_path, common_patterns):
            common_files.append(file_path)
            continue
        if not file_layers:
            out_of_scope.append(file_path)
            continue
        for layer in file_layers:
            changed_layers.setdefault(layer, []).append(file_path)
        if not (set(file_layers) & request.declared_layers):
            out_of_scope.append(file_path)

    protected_branches = [str(item) for item in constitution.get("protected_branches") or []]
    if branch in protected_branches:
        violations.append(f"operator patch is not allowed on protected branch {branch!r}")
    for file_path in out_of_scope:
        violations.append(f"changed file is outside declared operator scope: {file_path}")

    review_policy = constitution.get("review_policy") or {}
    review_required = False
    human_required = False
    if request.secondary_layers and bool(review_policy.get("cross_layer_requires_review", True)):
        review_required = True
    if request.risk in {"medium", "high", "architecture"} and bool(review_policy.get("medium_risk_requires_review", True)):
        review_required = True
    if request.risk == "high" and bool(review_policy.get("high_risk_requires_human", True)):
        human_required = True
    if request.risk == "architecture" and bool(review_policy.get("architecture_requires_human", True)):
        human_required = True
    if protected_files:
        human_required = True
        review_required = True

    if review_required and not _has_approved_review(reviews, human_required=False, files=changed_files):
        violations.append("operator request requires an approved review")
    if human_required and not _has_approved_review(reviews, human_required=True, files=changed_files):
        violations.append("operator request requires human approval")

    violations.extend(static_constitution_violations(root, constitution))
    return PolicyDecision(
        allowed=not violations,
        request_id=request.request_id,
        branch=branch,
        changed_files=changed_files,
        declared_layers=declared_layers,
        changed_layers={layer: sorted(files) for layer, files in sorted(changed_layers.items())},
        common_files=sorted(common_files),
        protected_files=sorted(protected_files),
        out_of_scope_files=sorted(set(out_of_scope)),
        violations=violations,
        review_required=review_required,
        human_required=human_required,
    )
