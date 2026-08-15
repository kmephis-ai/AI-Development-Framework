"""Компиляция разрозненных ADWF policy-файлов в один неизменяемый контракт.

Компилятор не пытается «угадать» приоритет противоречащих правил. Любое
расхождение блокирует Config Health, а итоговый hash связывает принятое решение
с точным набором исходных файлов.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .contracts import validate
from .policy import ACTION_MIN_AUTONOMY, AUTONOMY_RANK, RISK_RANK, SAFE_HEALTH
from .project_gates import gate_configuration_findings
from .strict_json import load as strict_json_load


POLICY_FILES = (
    ".adwf/policies/evidence.json",
    ".adwf/policies/orchestration.json",
    ".adwf/policies/reality.json",
    ".adwf/policies/roadmap-quality.json",
    ".adwf/policies/trust-boundary.json",
)

POLICY_KEYS = {
    "evidence": {"version", "statuses", "ttl_hours", "exact_sha_required", "never_infer_pass"},
    "orchestration": {"version", "selection_order", "parallelism", "merge", "claim", "same_issue_multi_writer", "one_active_writer_default", "conflict_domain_overlap"},
    "reality": {"version", "baseline_required", "product_health_states", "golden_paths", "evidence_ttl_hours", "roadmap_permission"},
    "roadmap-quality": {"version", "issue_sizing", "verification_gap", "false_progress", "ready_queue", "checkpoint", "debt", "roadmap_entropy", "product_value"},
    "trust-boundary": {"version", "paths", "weakening_is_risk", "weakening_requires_human", "self_modification_in_feature_pr", "examples_of_weakening"},
}

PROFILE_REQUIRED_KEYS = {
    "id", "description", "repository_scope", "enforcement_truth",
    "github_private_branch_protection", "required_checks_truth",
    "platform_enforcement", "policy_enforcement", "default_autonomy",
    "promotable_autonomy", "max_autonomous_risk",
    "recommended_ci_capability", "allowed_capability_statuses",
    "forbidden_capability_statuses", "larger_runners_allowed",
    "mandatory_paid_ai_api", "owner_resource_attestation_required",
    "github_hosted_minutes_policy",
    "monetary_budget", "merge_rule", "deployment_rule",
}
PROFILE_OPTIONAL_KEYS: set[str] = {"runner"}


def _load(path: Path) -> Any:
    return strict_json_load(path)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_policy(root: str | Path) -> tuple[dict[str, Any], list[str]]:
    base = Path(root).resolve()
    errors: list[str] = []
    source_names = [
        ".adwf/config.json",
        ".adwf/providers.json",
        ".adwf/actions-lock.json",
        ".adwf/autonomy-matrix.json",
        ".adwf/state-machine.json",
        ".adwf/release-state-machine.json",
        ".adwf/healing-config.json",
        *POLICY_FILES,
    ]
    try:
        config = _load(base / ".adwf/config.json")
        profile_name = str(config.get("profile", ""))
        profile_path = base / ".adwf/profiles" / f"{profile_name}.json"
        source_names.append(profile_path.relative_to(base).as_posix())
        profile = _load(profile_path)
        policies = {Path(name).stem: _load(base / name) for name in POLICY_FILES}
        providers = _load(base / ".adwf/providers.json")
        actions_lock = _load(base / ".adwf/actions-lock.json")
        autonomy_matrix = _load(base / ".adwf/autonomy-matrix.json")
        state_machine = _load(base / ".adwf/state-machine.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, [f"POLICY_SOURCE_UNREADABLE:{type(exc).__name__}"]

    for data_name, schema_name, data in (
        ("config", "config.schema.json", config),
        ("providers", "providers.schema.json", providers),
        ("actions-lock", "actions-lock.schema.json", actions_lock),
    ):
        try:
            schema = _load(base / ".adwf/schemas" / schema_name)
            errors.extend(f"{data_name.upper()}_SCHEMA:{item.path}:{item.code}" for item in validate(data, schema))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{data_name.upper()}_SCHEMA_UNREADABLE:{type(exc).__name__}")

    if profile.get("id") != config.get("profile"):
        errors.append("PROFILE_ID_MISMATCH")
    profile_keys = set(profile)
    if not PROFILE_REQUIRED_KEYS.issubset(profile_keys) or not profile_keys.issubset(
        PROFILE_REQUIRED_KEYS | PROFILE_OPTIONAL_KEYS
    ):
        errors.append("PROFILE_CONTRACT_MISMATCH")
    if profile.get("monetary_budget") != 0 or config.get("cost", {}).get("monetary_budget") != 0:
        errors.append("NON_ZERO_BUDGET")
    if providers.get("hard_budget_usd") != 0:
        errors.append("PROVIDER_BUDGET_NOT_ZERO")
    if providers.get("allow_overage") is not False or providers.get("allow_credit_purchase") is not False:
        errors.append("AUTOMATIC_OVERAGE_NOT_BLOCKED")
    if config.get("ci", {}).get("mandatory_ai_api") is not False:
        errors.append("MANDATORY_AI_API_ENABLED")
    if profile.get("mandatory_paid_ai_api") is not False:
        errors.append("PROFILE_MANDATORY_PAID_AI_API_ENABLED")
    if profile.get("larger_runners_allowed") is not False or config.get("ci", {}).get("larger_runners_allowed") is not False:
        errors.append("LARGER_RUNNERS_NOT_BLOCKED")
    recommended = providers.get("providers", {}).get(profile.get("recommended_ci_capability"), {})
    if recommended.get("classification") == "OWNER_PROVIDED" and profile.get("owner_resource_attestation_required") is not True:
        errors.append("OWNER_RESOURCE_ATTESTATION_NOT_REQUIRED")
    configured_statuses = set(config.get("cost", {}).get("allowed_capability_statuses", []))
    profile_statuses = set(profile.get("allowed_capability_statuses", []))
    if configured_statuses != profile_statuses:
        errors.append("PROFILE_CAPABILITY_STATUS_CONTRADICTION")
    forbidden_statuses = set(profile.get("forbidden_capability_statuses", []))
    if forbidden_statuses != {"METERED", "PAID", "UNKNOWN", "STALE"}:
        errors.append("PROFILE_FORBIDDEN_CAPABILITIES_WEAKENED")
    if config.get("policy", {}).get("fail_mode") != "CLOSED":
        errors.append("FAIL_MODE_NOT_CLOSED")
    if config.get("policy", {}).get("a4_automatic") is not False:
        errors.append("A4_AUTOMATIC")
    if config.get("orchestration", {}).get("max_parallel_writers") != 1:
        errors.append("MULTI_WRITER_DEFAULT")
    if policies.get("orchestration", {}).get("one_active_writer_default") is not True:
        errors.append("ORCHESTRATION_POLICY_CONTRADICTION")
    for name, policy in policies.items():
        if set(policy) != POLICY_KEYS[name]:
            errors.append(f"POLICY_CONTRACT_MISMATCH:{name}")
        if policy.get("version") != config.get("framework_version"):
            errors.append(f"POLICY_VERSION_MISMATCH:{name}")
    if policies.get("evidence", {}).get("never_infer_pass") is not True:
        errors.append("EVIDENCE_POLICY_WEAKENED")
    if policies.get("trust-boundary", {}).get("weakening_requires_human") is not True:
        errors.append("TRUST_POLICY_WEAKENED")
    provider_mode = config.get("provider", {}).get("mode")
    if provider_mode not in {"local", "github", "gitlab"}:
        errors.append("CANONICAL_PROVIDER_INVALID")
    if config.get("provider", {}).get("secondary_write_enabled") is not False:
        errors.append("DUAL_WRITE_NOT_BLOCKED")
    default_capability = providers.get("providers", {}).get(config.get("cost", {}).get("default_ci_capability"))
    if not default_capability:
        errors.append("DEFAULT_CI_CAPABILITY_UNKNOWN")
    elif default_capability.get("plane") != "ci" or default_capability.get("provider_mode") != provider_mode:
        errors.append("DEFAULT_CI_CAPABILITY_PROVIDER_MISMATCH")
    if config.get("runtime", {}).get("node_major") != 24:
        errors.append("NODE_24_NOT_ENFORCED")
    errors.extend(gate_configuration_findings(config, base))
    levels = autonomy_matrix.get("levels", {})
    for action, minimum in ACTION_MIN_AUTONOMY.items():
        minimum_rank = int(minimum[1:])
        actual = [level for level, actions in levels.items() if action in actions]
        expected = [f"A{rank}" for rank in range(minimum_rank, 4)]
        if actual != expected:
            errors.append(f"AUTONOMY_MATRIX_CONTRADICTION:{action}")

    configured_quality = config.get("roadmap_quality", {})
    policy_quality = policies.get("roadmap-quality", {}).get("verification_gap", {})
    if configured_quality.get("verification_gap_warn") != policy_quality.get("warn"):
        errors.append("VERIFICATION_GAP_WARN_CONTRADICTION")
    if configured_quality.get("verification_gap_block") != policy_quality.get("block_feature_progression"):
        errors.append("VERIFICATION_GAP_BLOCK_CONTRADICTION")

    active_rank = {f"A{i}": i for i in range(5)}.get(config.get("policy", {}).get("active_autonomy"), 99)
    requested_rank = {f"A{i}": i for i in range(5)}.get(config.get("policy", {}).get("requested_autonomy"), -1)
    if active_rank > requested_rank:
        errors.append("ACTIVE_AUTONOMY_ABOVE_REQUESTED")

    sources = []
    for name in sorted(set(source_names)):
        path = base / name
        if not path.is_file():
            errors.append(f"POLICY_SOURCE_MISSING:{name}")
            continue
        sources.append({"path": name, "sha256": _digest(path)})

    compiled: dict[str, Any] = {
        "schema_version": 2,
        "framework_version": config.get("framework_version"),
        "profile": config.get("profile"),
        "canonical_provider": provider_mode,
        "active_autonomy": config.get("policy", {}).get("active_autonomy"),
        "max_autonomous_risk": config.get("policy", {}).get("max_autonomous_risk"),
        "hard_budget_usd": 0,
        "mandatory_ai_api": False,
        "max_parallel_writers": 1,
        "state_machine_version": state_machine.get("version"),
        "rules": {
            "action_min_autonomy": ACTION_MIN_AUTONOMY,
            "autonomy_rank": AUTONOMY_RANK,
            "risk_rank": RISK_RANK,
            "safe_health": sorted(SAFE_HEALTH),
            "non_mutating_actions": ["inspect", "plan", "reconcile", "review", "verify", "observe"],
            "control_plane_optional_actions": ["edit", "test"],
            "product_health_exempt_work_types": ["recovery", "verification", "governance"],
            "evidence_actions": ["merge", "deploy_dev", "deploy_prod", "promote", "rollback", "close_issue"],
            "always_human_actions": ["delete", "deploy_prod", "trust_change", "owner_accept", "certify_recipe"],
        },
        "sources": sources,
    }
    compiled["policy_hash"] = hashlib.sha256(_canonical(compiled)).hexdigest()
    return compiled, list(dict.fromkeys(errors))


def check_compiled_policy(root: str | Path) -> list[str]:
    base = Path(root).resolve()
    generated, errors = compile_policy(base)
    target = base / ".adwf/effective-policy.json"
    if not target.is_file():
        return errors + ["EFFECTIVE_POLICY_MISSING"]
    try:
        stored = _load(target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return errors + [f"EFFECTIVE_POLICY_UNREADABLE:{type(exc).__name__}"]
    if stored != generated:
        errors.append("EFFECTIVE_POLICY_STALE")
    return errors
