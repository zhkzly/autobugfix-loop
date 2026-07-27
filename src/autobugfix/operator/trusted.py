from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.git_utils import GitError, run_git


class TrustedPolicyError(RuntimeError):
    pass


CONSTITUTION_REPO_PATH = "src/autobugfix/operator/constitution.yaml"


# The project constitution is intentionally frozen at v3 while the experiment
# line control plane is introduced.  This projection is trusted runtime code,
# not candidate configuration or prompt text: it preserves the v4 experiment
# invariants without changing the digest or contents of the v3 constitution.
_V3_EXPERIMENT_GOVERNANCE_CONTRACT: dict[str, Any] = {
    "state_owner": "OperatorGovernanceService",
    "authority_store": "external_sqlite",
    "candidate_state_authority": "forbidden",
    "request_phases_unchanged": ["REQUESTED", "ACTIVE", "VERIFIED", "CLOSED"],
    "studies": {
        "common_baseline": "H0_per_cohort",
        "independent_successors": ["H_bug", "H_general"],
        "frozen_inputs": [
            "base_subject_sha",
            "harness_sha",
            "policy_digest",
            "role_config_digest",
            "base_config_digest",
            "base_model_digest",
            "base_skills_digest",
            "benchmark_manifest_snapshot",
            "memory_snapshot",
            "success_contract",
        ],
        "cohort_mismatch": "reject",
        "snapshot_storage": "trusted_read_only_checkpoint_root",
        "operator_projection": "digests_without_snapshot_paths",
        "forbidden_inheritance": ["H_bug_to_H_general", "H_general_to_H_bug"],
        "checkpoint_lineage_owner": "trusted_guard",
    },
    "sealed_holdout": {
        "manifest_owner": "external_guard",
        "operator_store_case_ids": "forbidden",
        "operator_store_case_level_results": "forbidden",
        "operator_role_filesystem": "absent",
        "allowed_release": "aggregate_metric_receipt_only",
    },
    "lines": {
        "update": "compare_and_swap",
        "expected_inputs": ["head_sha", "generation"],
        "force_push": "forbidden",
        "integration_worktree": "trusted_host_only",
        "candidate_direct_update": "forbidden",
    },
    "budgets": {
        "waves": [3, 8, 16],
        "allowed_primary_models": ["gpt-5.4-mini"],
        "max_case_concurrency": 1,
        "model_fallback": "forbidden",
        "expansion_owner": "human_operator",
        "transfer_between_studies": "forbidden",
        "reserve_before_sdk_call": "required",
        "indeterminate_call_counts_as_consumed": True,
    },
    "checkpoints": {
        "immutable": True,
        "required_digests": [
            "subject",
            "harness",
            "policy",
            "config",
            "model",
            "skills",
            "memory",
            "manifest",
            "budget",
            "metrics",
        ],
    },
    "metrics": {
        "registration_owner": "trusted_benchmark_guard",
        "generic_operator_import": "forbidden",
        "storage": "content_addressed_artifact_plus_digest_protected_record",
        "transition_input": "registered_metric_id_only",
        "candidate_path_authority": "forbidden",
        "sealed_case_level_payload": "forbidden",
    },
    "rollback": {
        "reset": "forbidden",
        "force_push": "forbidden",
        "history_preserving_commit": "required",
    },
}


def _validate_experiment_governance_contract(contract: Mapping[str, Any]) -> None:
    """Reject incomplete governance data before it can authorize a study."""

    required_values = {
        "state_owner": "OperatorGovernanceService",
        "authority_store": "external_sqlite",
        "candidate_state_authority": "forbidden",
    }
    for key, expected in required_values.items():
        if contract.get(key) != expected:
            raise TrustedPolicyError(
                f"experiment governance contract requires {key}={expected!r}"
            )

    studies = contract.get("studies")
    budgets = contract.get("budgets")
    metrics = contract.get("metrics")
    lines = contract.get("lines")
    checkpoints = contract.get("checkpoints")
    rollback = contract.get("rollback")
    if not all(
        isinstance(section, Mapping)
        for section in (studies, budgets, metrics, lines, checkpoints, rollback)
    ):
        raise TrustedPolicyError("experiment governance contract has an invalid section")
    if studies.get("common_baseline") != "H0_per_cohort":
        raise TrustedPolicyError("experiment governance requires a shared H0 per cohort")
    if studies.get("independent_successors") != ["H_bug", "H_general"]:
        raise TrustedPolicyError("experiment governance requires independent H_bug and H_general")
    if budgets.get("waves") != [3, 8, 16]:
        raise TrustedPolicyError("experiment governance requires budget waves [3, 8, 16]")
    if budgets.get("allowed_primary_models") != ["gpt-5.4-mini"]:
        raise TrustedPolicyError("experiment governance requires gpt-5.4-mini")
    if budgets.get("model_fallback") != "forbidden":
        raise TrustedPolicyError("experiment governance forbids model fallback")
    if metrics.get("registration_owner") != "trusted_benchmark_guard":
        raise TrustedPolicyError("experiment governance requires the trusted benchmark guard")
    if metrics.get("transition_input") != "registered_metric_id_only":
        raise TrustedPolicyError("experiment governance requires registered metric ids")
    if lines.get("update") != "compare_and_swap" or lines.get("force_push") != "forbidden":
        raise TrustedPolicyError("experiment governance requires CAS experiment lines")
    if checkpoints.get("immutable") is not True:
        raise TrustedPolicyError("experiment governance requires immutable checkpoints")
    if rollback.get("history_preserving_commit") != "required":
        raise TrustedPolicyError("experiment governance requires history-preserving rollback")


def experiment_governance_contract(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a validated, mutable projection of trusted experiment invariants.

    Constitution v3 predates ExperimentLine but is the frozen trust root for
    this repository.  Its compatibility contract is therefore supplied by
    trusted service code.  v4+ constitutions must carry the equivalent data
    explicitly.  Callers receive a deep copy so no consumer can mutate the
    process-wide contract.
    """

    version = int(data.get("version", 0))
    if version == 3:
        contract = deepcopy(_V3_EXPERIMENT_GOVERNANCE_CONTRACT)
        source = "trusted-v3-compatibility-contract"
    elif version >= 4:
        configured = data.get("experiment_governance")
        if not isinstance(configured, Mapping):
            raise TrustedPolicyError("trusted machine constitution lacks experiment governance")
        contract = deepcopy(dict(configured))
        source = "machine-constitution"
    else:
        raise TrustedPolicyError("experiment governance requires constitution version 3 or newer")
    _validate_experiment_governance_contract(contract)
    return contract, source


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
