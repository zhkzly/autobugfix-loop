from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from autobugfix.operator.models import OperatorApproval, OperatorRequest, canonical_json, is_expired


class OperatorApprovalError(RuntimeError):
    pass


def approval_signing_payload(
    request: OperatorRequest,
    *,
    approver: str,
    stage: str,
    reason: str,
    allowed_layers: Iterable[str] | None = None,
    allowed_paths: Iterable[str] = (),
    expires_at: str | None = None,
    patch_digest: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "autobugfix-operator-approval-v2",
        "request_id": request.request_id,
        "request_digest": request.request_digest,
        "base_sha": request.base_sha,
        "approver": approver,
        "stage": stage,
        "decision": "approve",
        "reason": reason,
        "allowed_layers": sorted(set(allowed_layers or request.declared_layers)),
        "allowed_paths": sorted(set(allowed_paths)),
        "expires_at": expires_at,
        "patch_digest": patch_digest,
        "head_sha": head_sha,
    }


def signing_bytes(payload: Mapping[str, Any]) -> bytes:
    return canonical_json(payload).encode("utf-8")


def write_signing_payload(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(signing_bytes(payload))
    return path


def _allowed_signers_path(constitution: Mapping[str, Any], explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    approval = constitution.get("approval") or {}
    env_name = str(approval.get("allowed_signers_env") or "AUTOBUGFIX_OPERATOR_ALLOWED_SIGNERS")
    value = os.environ.get(env_name)
    if not value:
        raise OperatorApprovalError(f"signed approval requires {env_name} or --allowed-signers")
    return Path(value).expanduser().resolve()


def verify_ssh_signature(
    payload: Mapping[str, Any],
    signature: bytes,
    identity: str,
    constitution: Mapping[str, Any],
    *,
    allowed_signers: Path | None = None,
) -> None:
    signers = _allowed_signers_path(constitution, allowed_signers)
    if not signers.is_file():
        raise OperatorApprovalError(f"allowed signers file does not exist: {signers}")
    namespace = str((constitution.get("approval") or {}).get("namespace") or "autobugfix-operator")
    with tempfile.NamedTemporaryFile(prefix="autobugfix-approval-", suffix=".sig") as handle:
        handle.write(signature)
        handle.flush()
        result = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(signers),
                "-I",
                identity,
                "-n",
                namespace,
                "-s",
                handle.name,
            ],
            input=signing_bytes(payload),
            capture_output=True,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise OperatorApprovalError(f"invalid OpenSSH human approval for {identity}: {detail}")


def signed_approval_from_files(
    request: OperatorRequest,
    approval_id: str,
    payload_path: Path,
    signature_path: Path,
    constitution: Mapping[str, Any],
    *,
    allowed_signers: Path | None = None,
) -> OperatorApproval:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OperatorApprovalError("signed approval payload must be an object")
    if payload.get("schema") != "autobugfix-operator-approval-v2":
        raise OperatorApprovalError("unsupported signed approval payload schema")
    if payload.get("request_id") != request.request_id or payload.get("request_digest") != request.request_digest:
        raise OperatorApprovalError("signed approval does not bind to this request")
    if payload.get("base_sha") != request.base_sha:
        raise OperatorApprovalError("signed approval base SHA mismatch")
    signature = signature_path.read_bytes()
    identity = str(payload.get("approver") or "")
    verify_ssh_signature(payload, signature, identity, constitution, allowed_signers=allowed_signers)
    return OperatorApproval(
        approval_id=approval_id,
        request_id=request.request_id,
        request_digest=request.request_digest,
        base_sha=request.base_sha,
        approver=identity,
        kind="human_signed",
        stage=str(payload.get("stage") or "scope"),
        decision=str(payload.get("decision") or "approve"),
        reason=str(payload.get("reason") or "signed human approval"),
        allowed_layers=tuple(str(item) for item in payload.get("allowed_layers") or []),
        allowed_paths=tuple(str(item) for item in payload.get("allowed_paths") or []),
        expires_at=payload.get("expires_at"),
        patch_digest=payload.get("patch_digest"),
        head_sha=payload.get("head_sha"),
        proof={
            "signing_payload": payload,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    )


def _run_gh_api(endpoint: str) -> dict[str, Any]:
    result = subprocess.run(["gh", "api", endpoint], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise OperatorApprovalError(f"GitHub approval lookup failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise OperatorApprovalError("GitHub approval response must be an object")
    return data


def github_approval(
    request: OperatorRequest,
    approval_id: str,
    *,
    repository: str,
    pull_request: int,
    review_id: int,
    constitution: Mapping[str, Any],
    reason: str,
    stage: str = "merge",
) -> OperatorApproval:
    review = _run_gh_api(f"repos/{repository}/pulls/{pull_request}/reviews/{review_id}")
    login = str((review.get("user") or {}).get("login") or "")
    allowed = {str(item) for item in (constitution.get("approval") or {}).get("github_allowed_reviewers") or []}
    if login not in allowed:
        raise OperatorApprovalError(f"GitHub reviewer {login!r} is not allowlisted")
    if str(review.get("state") or "").upper() != "APPROVED":
        raise OperatorApprovalError(f"GitHub review {review_id} is not approved")
    marker = f"Autobugfix-Request-Digest: {request.request_digest}"
    if marker not in str(review.get("body") or ""):
        raise OperatorApprovalError("GitHub review body does not bind the Autobugfix request digest")
    commit_id = str(review.get("commit_id") or "")
    if not commit_id:
        raise OperatorApprovalError("GitHub review is missing commit_id")
    return OperatorApproval(
        approval_id=approval_id,
        request_id=request.request_id,
        request_digest=request.request_digest,
        base_sha=request.base_sha,
        approver=login,
        kind="github",
        stage=stage,
        decision="approve",
        reason=reason,
        allowed_layers=tuple(sorted(request.declared_layers)),
        head_sha=commit_id,
        proof={
            "repository": repository,
            "pull_request": pull_request,
            "review_id": review_id,
            "html_url": review.get("html_url"),
        },
    )


def verify_external_approval(
    approval: OperatorApproval,
    constitution: Mapping[str, Any],
    *,
    allowed_signers: Path | None = None,
    expected_github_repository: str | None = None,
    expected_pull_request: int | None = None,
) -> None:
    if approval.kind == "human_signed":
        payload = approval.proof.get("signing_payload")
        signature_text = approval.proof.get("signature_base64")
        if not isinstance(payload, dict) or not isinstance(signature_text, str):
            raise OperatorApprovalError("signed approval proof is incomplete")
        expected_payload = {
            "schema": "autobugfix-operator-approval-v2",
            "request_id": approval.request_id,
            "request_digest": approval.request_digest,
            "base_sha": approval.base_sha,
            "approver": approval.approver,
            "stage": approval.stage,
            "decision": approval.decision,
            "reason": approval.reason,
            "allowed_layers": list(approval.allowed_layers),
            "allowed_paths": list(approval.allowed_paths),
            "expires_at": approval.expires_at,
            "patch_digest": approval.patch_digest,
            "head_sha": approval.head_sha,
        }
        if payload != expected_payload:
            raise OperatorApprovalError("signed payload fields do not match approval record")
        verify_ssh_signature(
            payload,
            base64.b64decode(signature_text),
            approval.approver,
            constitution,
            allowed_signers=allowed_signers,
        )
    elif approval.kind == "github":
        expected = {str(item) for item in (constitution.get("approval") or {}).get("github_allowed_reviewers") or []}
        if approval.approver not in expected:
            raise OperatorApprovalError(f"GitHub reviewer {approval.approver!r} is not allowlisted")
        proof = approval.proof
        if expected_github_repository and proof.get("repository") != expected_github_repository:
            raise OperatorApprovalError("GitHub approval belongs to a different repository")
        if expected_pull_request is not None and int(proof.get("pull_request", -1)) != expected_pull_request:
            raise OperatorApprovalError("GitHub approval belongs to a different pull request")
        if bool((constitution.get("approval") or {}).get("github_require_online_recheck", True)):
            review = _run_gh_api(
                f"repos/{proof['repository']}/pulls/{int(proof['pull_request'])}/reviews/{int(proof['review_id'])}"
            )
            if str(review.get("state") or "").upper() != "APPROVED":
                raise OperatorApprovalError("GitHub review is no longer approved")
            if str(review.get("commit_id") or "") != approval.head_sha:
                raise OperatorApprovalError("GitHub approval commit changed")
            marker = f"Autobugfix-Request-Digest: {approval.request_digest}"
            if marker not in str(review.get("body") or ""):
                raise OperatorApprovalError("GitHub review no longer binds the request digest")


def effective_approvals(approvals: Iterable[OperatorApproval]) -> list[OperatorApproval]:
    latest: dict[tuple[str, str], OperatorApproval] = {}
    for approval in sorted(approvals, key=lambda item: (item.created_at, item.approval_id)):
        latest[(approval.approver, approval.stage)] = approval
    return [item for item in latest.values() if item.approved and not is_expired(item.expires_at)]


def approval_matches(
    approval: OperatorApproval,
    request: OperatorRequest,
    *,
    files: Iterable[str],
    require_human: bool,
    stage: str = "scope",
    patch_digest: str | None = None,
    head_sha: str | None = None,
) -> bool:
    if not approval.approved or approval.stage != stage:
        return False
    if approval.request_id != request.request_id or approval.request_digest != request.request_digest:
        return False
    if approval.base_sha != request.base_sha:
        return False
    if approval.approver == request.creator and approval.kind == "reviewer":
        return False
    if require_human and not approval.human_verified_kind:
        return False
    if not request.declared_layers.issubset(set(approval.allowed_layers)):
        return False
    changed = list(files)
    if approval.allowed_paths:
        import fnmatch

        if not all(any(fnmatch.fnmatch(path, pattern) for pattern in approval.allowed_paths) for path in changed):
            return False
    if stage == "merge":
        if approval.patch_digest and approval.patch_digest != patch_digest:
            return False
        if approval.head_sha and approval.head_sha != head_sha:
            return False
        if not approval.patch_digest and not approval.head_sha:
            return False
    return True
