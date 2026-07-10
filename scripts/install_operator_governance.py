from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _repository() -> str:
    data = json.loads(_run(["gh", "repo", "view", "--json", "nameWithOwner"]).stdout)
    return str(data["nameWithOwner"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Install repository-specific Operator Governance enforcement.")
    parser.add_argument("--repository", help="GitHub owner/repository; detected with gh when omitted")
    parser.add_argument("--reviewer", action="append", required=True, help="Allowlisted GitHub human reviewer")
    parser.add_argument(
        "--signer",
        action="append",
        default=[],
        metavar="IDENTITY=PUBLIC_KEY_FILE",
        help="Add an OpenSSH signing identity and public key to the trusted allowlist",
    )
    parser.add_argument("--branch", default="main")
    parser.add_argument("--apply-branch-protection", action="store_true")
    args = parser.parse_args()

    root = Path.cwd()
    repository = args.repository or _repository()
    reviewers = sorted({value.lstrip("@") for value in args.reviewer})
    owners = " ".join(f"@{value}" for value in reviewers)
    constitution_path = root / "src/autobugfix/operator/constitution.yaml"
    constitution = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    protected_paths = [f"/{str(path).lstrip('/')}" for path in constitution.get("protected_paths") or []]
    if not protected_paths:
        raise RuntimeError("machine constitution has no protected_paths")
    codeowners = root / ".github/CODEOWNERS"
    codeowners.parent.mkdir(parents=True, exist_ok=True)
    codeowners.write_text(
        "\n".join(f"{path} {owners}" for path in protected_paths) + "\n",
        encoding="utf-8",
    )

    constitution.setdefault("approval", {})["github_allowed_reviewers"] = reviewers
    constitution_path.write_text(yaml.safe_dump(constitution, sort_keys=False), encoding="utf-8")

    signer_lines: list[str] = []
    for item in args.signer:
        if "=" not in item:
            raise RuntimeError(f"signer must be IDENTITY=PUBLIC_KEY_FILE: {item}")
        identity, public_key_path = item.split("=", 1)
        public_key = Path(public_key_path).expanduser().read_text(encoding="utf-8").strip()
        signer_lines.append(f"{identity} {public_key}")
    if signer_lines:
        (root / ".github/autobugfix-allowed-signers").write_text(
            "\n".join(signer_lines) + "\n", encoding="utf-8"
        )

    if args.apply_branch_protection:
        payload = {
            "required_status_checks": {"strict": True, "contexts": ["Operator Governance / trusted-operator-gate"]},
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": 1,
            },
            "restrictions": None,
        }
        result = subprocess.run(
            ["gh", "api", "--method", "PUT", f"repos/{repository}/branches/{args.branch}/protection", "--input", "-"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"failed to configure branch protection: {result.stderr.strip()}")
    print(codeowners)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
