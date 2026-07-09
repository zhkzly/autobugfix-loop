Utility scripts for Autobugfix development and validation live here.

- `validate_operator_policy.py`: validate runtime records or an exported bundle
  against a trusted ref/file.
- `validate_operator_pr.py`: base-checkout entrypoint used by the read-only
  GitHub Operator Governance check.
- `install_operator_governance.py`: generate repository-specific CODEOWNERS,
  reviewer/public-key allowlists, and optionally branch protection.
- `real_toy_acceptance.py`: real Codex SDK toy-repository E2E in an isolated
  temporary control root.
