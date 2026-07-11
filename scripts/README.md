Utility scripts for Autobugfix development and validation live here.

- `validate_operator_policy.py`: validate V3 runtime records or an advisory PR
  manifest against a trusted ref/file.
- `validate_operator_pr.py`: base-checkout entrypoint used by the read-only
  GitHub Operator Governance check; it re-derives Git facts, reviews, and
  validation instead of trusting candidate status claims.
- `install_operator_governance.py`: generate repository-specific CODEOWNERS,
  reviewer/public-key allowlists, and optionally branch protection.
- `real_toy_acceptance.py`: optional fast development fixture; it is not a
  release or promotion acceptance gate.
- `real_operator_acceptance.py`: real production Operator Supervisor/Writer,
  isolated checks, audit, and promotion-prepared acceptance in a temporary
  Autobugfix repository.
- `real_repository_acceptance.py`: clone a pinned ItsDangerous upstream commit,
  inject a reproducible URL-safe encoding regression, and run real
  `gpt-5.4-mini` Execution, Memory, and isolated Eval loops while preserving
  raw evidence and the target main checkout.
