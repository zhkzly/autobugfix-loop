from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from autobugfix.config import DEFAULT_CONFIG
from autobugfix.git_utils import GitError, current_branch, rev_parse, run_git
from autobugfix.operator.approvals import (
    OperatorApprovalError,
    approval_matches,
    effective_approvals,
    verify_external_approval,
)
from autobugfix.operator.models import OperatorApproval, OperatorRequest, is_expired


class OperatorPolicyError(RuntimeError):
    pass


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "constitutional": 3}


@dataclass(slots=True)
class CandidateSnapshot:
    branch: str
    head_sha: str
    base_sha: str
    changed_files: list[str]
    metadata_files: list[str]
    patch_digest: str
    base_is_ancestor: bool


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    request_id: str
    trusted_policy_source: str
    trusted_policy: bool
    branch: str
    base_sha: str
    head_sha: str
    patch_digest: str
    changed_files: list[str]
    metadata_files: list[str]
    declared_layers: list[str]
    changed_layers: dict[str, list[str]]
    protected_files: list[str] = field(default_factory=list)
    out_of_scope_files: list[str] = field(default_factory=list)
    unclassified_files: list[str] = field(default_factory=list)
    requested_risk: str = "low"
    computed_risk: str = "low"
    effective_risk: str = "low"
    permission_class: str = "layer_local"
    required_profiles: list[str] = field(default_factory=list)
    review_required: bool = False
    human_required: bool = False
    merge_human_required: bool = False
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "request_id": self.request_id,
            "trusted_policy_source": self.trusted_policy_source,
            "trusted_policy": self.trusted_policy,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "patch_digest": self.patch_digest,
            "changed_files": self.changed_files,
            "metadata_files": self.metadata_files,
            "declared_layers": self.declared_layers,
            "changed_layers": self.changed_layers,
            "protected_files": self.protected_files,
            "out_of_scope_files": self.out_of_scope_files,
            "unclassified_files": self.unclassified_files,
            "requested_risk": self.requested_risk,
            "computed_risk": self.computed_risk,
            "effective_risk": self.effective_risk,
            "permission_class": self.permission_class,
            "required_profiles": self.required_profiles,
            "review_required": self.review_required,
            "human_required": self.human_required,
            "merge_human_required": self.merge_human_required,
            "violations": self.violations,
        }


def _match_path(pattern: str, path: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    normalized_path = path.replace("\\", "/")
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3]
        return normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
    return fnmatch.fnmatch(normalized_path, normalized_pattern)


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(_match_path(pattern, path) for pattern in patterns)


def layers_for_file(constitution: Mapping[str, Any], path: str) -> list[str]:
    layers = constitution.get("layers") or {}
    if not isinstance(layers, dict):
        return []
    matches: list[str] = []
    for layer, raw in layers.items():
        if not isinstance(raw, dict):
            continue
        patterns = [str(item) for item in raw.get("paths") or []]
        if _matches_any(path, patterns):
            matches.append(str(layer))
    if len(matches) > 1 and "docs_skills" in matches:
        matches.remove("docs_skills")
    return matches


def _protected_patterns(constitution: Mapping[str, Any]) -> list[str]:
    return [str(item) for item in constitution.get("protected_paths") or []]


def _git_changed_files(project_root: Path, base_sha: str) -> list[str]:
    try:
        diff = run_git(project_root, ["diff", "--name-only", base_sha, "--"], check=True).stdout
        untracked = run_git(project_root, ["ls-files", "--others", "--exclude-standard"], check=True).stdout
    except GitError as exc:
        raise OperatorPolicyError(str(exc)) from exc
    files = {line.strip().replace("\\", "/") for line in diff.splitlines() if line.strip()}
    files.update(line.strip().replace("\\", "/") for line in untracked.splitlines() if line.strip())
    return sorted(files)


def _patch_digest(
    project_root: Path,
    base_sha: str,
    changed_files: Iterable[str],
    metadata_patterns: Iterable[str] = (),
) -> str:
    digest = hashlib.sha256()
    digest.update(f"base:{base_sha}\n".encode("utf-8"))
    code_files = [path for path in changed_files if not _matches_any(path, metadata_patterns)]
    diff = (
        run_git(project_root, ["diff", "--binary", base_sha, "--", *code_files], check=True).stdout.encode("utf-8")
        if code_files
        else b""
    )
    digest.update(diff)
    tracked = set(run_git(project_root, ["ls-files"], check=True).stdout.splitlines())
    for relative in sorted(set(code_files) - tracked):
        digest.update(f"untracked:{relative}\0".encode("utf-8"))
        path = project_root / relative
        if path.is_symlink():
            digest.update(f"symlink:{os.readlink(path)}".encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def collect_candidate_snapshot(
    project_root: Path,
    base_sha: str,
    metadata_patterns: Iterable[str] = (),
) -> CandidateSnapshot:
    root = project_root.resolve()
    try:
        resolved_base = rev_parse(root, base_sha)
        head_sha = rev_parse(root, "HEAD")
        branch = current_branch(root)
        ancestor = run_git(root, ["merge-base", "--is-ancestor", resolved_base, head_sha], check=False).returncode == 0
    except GitError as exc:
        raise OperatorPolicyError(str(exc)) from exc
    all_changed_files = _git_changed_files(root, resolved_base)
    metadata_files = [path for path in all_changed_files if _matches_any(path, metadata_patterns)]
    changed_files = [path for path in all_changed_files if path not in metadata_files]
    return CandidateSnapshot(
        branch=branch,
        head_sha=head_sha,
        base_sha=resolved_base,
        changed_files=changed_files,
        metadata_files=metadata_files,
        patch_digest=_patch_digest(root, resolved_base, changed_files, metadata_patterns),
        base_is_ancestor=ancestor,
    )


def _max_risk(*risks: str) -> str:
    return max(risks, key=lambda value: RISK_ORDER[value])


def _computed_risk(protected_files: list[str], changed_layers: set[str], unclassified: list[str]) -> str:
    if protected_files:
        return "constitutional"
    if unclassified:
        return "high"
    if len(changed_layers) > 1:
        return "medium"
    return "low"


def _required_profiles(
    constitution: Mapping[str, Any], request: OperatorRequest, changed_layers: set[str], effective_risk: str
) -> tuple[list[str], list[str]]:
    profiles = set(request.validation_profiles)
    profiles.update(changed_layers or request.declared_layers)
    risk_profiles = constitution.get("risk_required_profiles") or {}
    profiles.update(str(item) for item in risk_profiles.get(effective_risk) or [])
    available = constitution.get("validation_profiles") or {}
    missing = sorted(profile for profile in profiles if profile not in available)
    return sorted(profiles), missing


def _call_name(node: ast.Call) -> str:
    current: ast.AST = node.func
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def static_constitution_violations(project_root: Path, constitution: Mapping[str, Any]) -> list[str]:
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
    for role, expected in (invariants.get("role_defaults") or {}).items():
        actual = roles.get(role) if isinstance(roles, dict) else None
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            violations.append(f"missing role default for {role}")
            continue
        for key, expected_value in expected.items():
            if actual.get(key) != expected_value:
                violations.append(f"role {role}.{key} expected {expected_value!r}, got {actual.get(key)!r}")

    service_text = (project_root / "src/autobugfix/service.py").read_text(encoding="utf-8")
    for marker in invariants.get("production_service_requires") or []:
        if str(marker) not in service_text:
            violations.append(f"production service missing required marker: {marker}")

    forbidden = {str(item) for item in invariants.get("eval_forbidden_calls") or []}
    eval_root = project_root / "src/autobugfix/eval"
    if eval_root.exists():
        for path in sorted(eval_root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                violations.append(f"cannot parse eval module {path.relative_to(project_root)}: {exc}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node).split(".")[-1] in forbidden:
                    violations.append(
                        f"eval layer calls forbidden function {_call_name(node)!r} in {path.relative_to(project_root).as_posix()}"
                    )
    return violations


def evaluate_policy(
    project_root: Path,
    request: OperatorRequest,
    approvals: list[OperatorApproval],
    *,
    constitution: Mapping[str, Any],
    trusted_policy_source: str,
    trusted_policy: bool,
    phase: str = "postflight",
    allowed_signers: Path | None = None,
    expected_github_repository: str | None = None,
    expected_pull_request: int | None = None,
) -> PolicyDecision:
    root = project_root.resolve()
    metadata_patterns = [str(item) for item in constitution.get("governance_metadata_paths") or []]
    snapshot = collect_candidate_snapshot(root, request.base_sha, metadata_patterns)
    changed_layers: dict[str, list[str]] = {}
    protected: list[str] = []
    unclassified: list[str] = []
    out_of_scope: list[str] = []
    violations: list[str] = []
    protected_patterns = _protected_patterns(constitution)

    for file_path in snapshot.changed_files:
        file_layers = layers_for_file(constitution, file_path)
        if _matches_any(file_path, protected_patterns):
            protected.append(file_path)
        if not file_layers:
            unclassified.append(file_path)
            out_of_scope.append(file_path)
            continue
        for layer in file_layers:
            changed_layers.setdefault(layer, []).append(file_path)
        if not (set(file_layers) & request.declared_layers):
            out_of_scope.append(file_path)

    changed_layer_set = set(changed_layers)
    computed_risk = _computed_risk(protected, changed_layer_set, unclassified)
    effective_risk = _max_risk(request.requested_risk, computed_risk)
    permission_class = (
        "constitutional"
        if effective_risk == "constitutional"
        else "cross_layer"
        if effective_risk in {"medium", "high"} or len(request.declared_layers) > 1
        else "layer_local"
    )
    review_required = permission_class in {"cross_layer", "constitutional"}
    human_required = permission_class == "constitutional"
    merge_human_required = human_required and phase == "merge"
    required_profiles, missing_profiles = _required_profiles(
        constitution, request, changed_layer_set, effective_risk
    )

    protected_branches = {str(item) for item in constitution.get("protected_branches") or []}
    if snapshot.branch in protected_branches:
        violations.append(f"operator patch is not allowed on protected branch {snapshot.branch!r}")
    if snapshot.branch != request.branch:
        violations.append(f"candidate branch {snapshot.branch!r} does not match frozen request branch {request.branch!r}")
    if snapshot.base_sha != request.base_sha:
        violations.append("request base SHA does not resolve to the frozen commit")
    if not snapshot.base_is_ancestor:
        violations.append("request base SHA is not an ancestor of candidate HEAD")
    if is_expired(request.expires_at):
        violations.append("operator request has expired")
    for file_path in sorted(set(out_of_scope)):
        violations.append(f"changed file is outside declared operator scope: {file_path}")
    for profile in missing_profiles:
        violations.append(f"unknown validation profile: {profile}")
    if phase in {"postflight", "merge"} and not snapshot.changed_files:
        violations.append("operator candidate has no changes from frozen base SHA")

    valid_approvals: list[OperatorApproval] = []
    for approval in effective_approvals(approvals):
        if approval.human_verified_kind:
            try:
                verify_external_approval(
                    approval,
                    constitution,
                    allowed_signers=allowed_signers,
                    expected_github_repository=expected_github_repository,
                    expected_pull_request=expected_pull_request,
                )
            except OperatorApprovalError as exc:
                violations.append(f"invalid external approval {approval.approval_id}: {exc}")
                continue
        valid_approvals.append(approval)

    if review_required:
        has_scope = any(
            approval_matches(
                approval,
                request,
                files=snapshot.changed_files,
                require_human=human_required,
                stage="scope",
            )
            for approval in valid_approvals
        )
        if not has_scope:
            violations.append("operator request lacks a valid independent scope approval")
    if merge_human_required:
        has_merge = any(
            approval_matches(
                approval,
                request,
                files=snapshot.changed_files,
                require_human=True,
                stage="merge",
                patch_digest=snapshot.patch_digest,
                head_sha=snapshot.head_sha,
            )
            for approval in valid_approvals
        )
        if not has_merge:
            violations.append("constitutional candidate lacks human merge approval bound to patch or HEAD")

    violations.extend(static_constitution_violations(root, constitution))
    return PolicyDecision(
        allowed=not violations,
        request_id=request.request_id,
        trusted_policy_source=trusted_policy_source,
        trusted_policy=trusted_policy,
        branch=snapshot.branch,
        base_sha=snapshot.base_sha,
        head_sha=snapshot.head_sha,
        patch_digest=snapshot.patch_digest,
        changed_files=snapshot.changed_files,
        metadata_files=snapshot.metadata_files,
        declared_layers=sorted(request.declared_layers),
        changed_layers={key: sorted(value) for key, value in sorted(changed_layers.items())},
        protected_files=sorted(protected),
        out_of_scope_files=sorted(set(out_of_scope)),
        unclassified_files=sorted(unclassified),
        requested_risk=request.requested_risk,
        computed_risk=computed_risk,
        effective_risk=effective_risk,
        permission_class=permission_class,
        required_profiles=required_profiles,
        review_required=review_required,
        human_required=human_required,
        merge_human_required=merge_human_required,
        violations=violations,
    )
