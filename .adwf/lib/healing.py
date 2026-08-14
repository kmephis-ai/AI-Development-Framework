"""Fail-closed Safe Healing H0-H6 без обязательного AI/API.

Движок не исполняет recipe actions. Он проверяет exact compatibility и выдаёт
машиночитаемый план, который обязан пройти существующие ADWF policy/lease/CI
контуры. Failing job не может изменить evaluator, provider или trust boundary.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any
import copy
import json
import math
import os
import re
import tempfile

from .incidents import digest


HEALING_LADDER = {
    "H0": "OBSERVE",
    "H1": "CONFIRM_ONCE",
    "H2": "RECREATE_DISPOSABLE_STATE",
    "H3": "APPLY_CERTIFIED_RECIPE_IN_SANDBOX",
    "H4": "VERIFY_TARGETED_AND_FULL_GATES",
    "H5": "PROPOSE_CHANGE_OR_ESCALATE",
    "H6": "PROMOTE_OR_ROLLBACK_VERIFIED_ARTIFACT",
}
RECIPE_STATES = {"DRAFT", "SHADOW", "CERTIFIED", "ACTIVE", "QUARANTINED", "RETIRED"}
RECIPE_TRANSITIONS = {
    "DRAFT": {"SHADOW", "RETIRED"},
    "SHADOW": {"CERTIFIED", "QUARANTINED", "RETIRED"},
    "CERTIFIED": {"ACTIVE", "QUARANTINED", "RETIRED"},
    "ACTIVE": {"QUARANTINED", "RETIRED"},
    "QUARANTINED": {"SHADOW", "RETIRED"},
    "RETIRED": set(),
}
FALSE_HEAL_OUTCOMES = {"FALSE_HEAL", "UNSAFE_ATTEMPT", "IMMEDIATE_RECURRENCE"}
MIN_SHADOW_PASSES = 2
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SEMVERISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
FORBIDDEN_EXECUTABLES = {
    "bash", "cmd", "curl", "docker", "gh", "glab", "kubectl", "powershell",
    "pwsh", "rm", "scp", "sh", "ssh", "sudo", "terraform", "wget", "zsh",
}
FORBIDDEN_ARGV_TOKENS = {
    "--eval", "--exec", "-c", "-e", "add", "install", "login", "publish",
    "remove", "uninstall", "update", "upgrade",
}


DEFAULT_PROTECTED_PATHS = [
    ".git/**",
    ".github/workflows/**",
    ".adwf/policies/**",
    ".adwf/schemas/**",
    ".adwf/actions-lock.json",
    ".adwf/providers.json",
    ".adwf/config.json",
    ".adwf/healing-config.json",
    ".adwf/lib/healing.py",
    ".adwf/effective-policy.json",
    ".adwf/knowledge/recipes/**",
    ".adwf/knowledge/incidents/**",
    ".adwf/knowledge/regressions/**",
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*credential*",
]


def _iso(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def load_healing_config(path: str | Path) -> dict[str, Any]:
    config = strict_loads(Path(path).read_text(encoding="utf-8"))
    validate_healing_config(config)
    return config


def validate_healing_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise ValueError("HEALING_CONFIG_DISABLED_OR_INVALID")
    if config.get("mandatory_ai_api") is not False:
        raise ValueError("HEALING_AI_API_MUST_BE_OPTIONAL")
    if config.get("monetary_budget") != 0:
        raise ValueError("HEALING_MONETARY_BUDGET_NOT_ZERO")
    budgets = config.get("budgets") or {}
    for key in ("max_attempts", "max_elapsed_seconds", "max_actions", "max_changed_files", "max_false_heals"):
        if not isinstance(budgets.get(key), int) or budgets[key] < 0:
            raise ValueError(f"HEALING_BUDGET_INVALID:{key}")
    paths = config.get("protected_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(item, str) and item for item in paths):
        raise ValueError("HEALING_PROTECTED_PATHS_MISSING")
    if not set(DEFAULT_PROTECTED_PATHS).issubset(set(paths)):
        raise ValueError("HEALING_DEFAULT_PROTECTION_WEAKENED")


def load_recipe_registry(path: str | Path) -> dict[str, Any]:
    registry = strict_loads(Path(path).read_text(encoding="utf-8"))
    validate_recipe_registry(registry)
    return registry


def validate_recipe_registry(registry: dict[str, Any]) -> None:
    if not isinstance(registry, dict) or not isinstance(registry.get("recipes"), list):
        raise ValueError("RECIPE_REGISTRY_INVALID")
    identities: set[tuple[str, str]] = set()
    for recipe in registry["recipes"]:
        if not isinstance(recipe, dict):
            raise ValueError("RECIPE_NOT_OBJECT")
        identity = (str(recipe.get("recipe_id")), str(recipe.get("version")))
        if identity in identities:
            raise ValueError("RECIPE_ID_VERSION_DUPLICATE")
        identities.add(identity)
        if re.fullmatch(r"REC-[A-Z0-9][A-Z0-9_-]{2,63}", identity[0]) is None:
            raise ValueError("RECIPE_ID_INVALID")
        if SEMVERISH.fullmatch(identity[1]) is None:
            raise ValueError("RECIPE_VERSION_INVALID")
        if recipe.get("lifecycle") not in RECIPE_STATES:
            raise ValueError("RECIPE_LIFECYCLE_INVALID")
        match = recipe.get("match")
        if not isinstance(match, dict):
            raise ValueError("RECIPE_MATCH_INVALID")
        fingerprint = match.get("fingerprint_hash")
        if not isinstance(fingerprint, str) or SHA256.fullmatch(fingerprint) is None:
            raise ValueError("RECIPE_FINGERPRINT_INVALID")
        compatibility = recipe.get("compatibility")
        if not isinstance(compatibility, dict):
            raise ValueError("RECIPE_COMPATIBILITY_INVALID")
        policy_hashes = compatibility.get("policy_hashes")
        if not isinstance(policy_hashes, list) or not policy_hashes or any(SHA256.fullmatch(str(item)) is None for item in policy_hashes):
            raise ValueError("RECIPE_POLICY_COMPATIBILITY_INVALID")
        for version_group in ("tool_versions", "runtime_versions"):
            versions = compatibility.get(version_group)
            if not isinstance(versions, dict) or not all(
                isinstance(key, str) and key and isinstance(value, str) and value
                for key, value in versions.items()
            ):
                raise ValueError(f"RECIPE_{version_group.upper()}_INVALID")
        certification = recipe.get("certification")
        if not isinstance(certification, dict):
            raise ValueError("RECIPE_CERTIFICATION_INVALID")
        shadow_passes = certification.get("shadow_passes")
        if not isinstance(shadow_passes, int) or isinstance(shadow_passes, bool) or shadow_passes < 0:
            raise ValueError("RECIPE_SHADOW_PASSES_INVALID")
        if not isinstance(certification.get("independent_approval"), bool):
            raise ValueError("RECIPE_CERTIFICATION_APPROVAL_INVALID")
        if recipe.get("lifecycle") in {"CERTIFIED", "ACTIVE"} and (
            certification.get("independent_approval") is not True
            or not isinstance(certification.get("certified_at"), str)
            or shadow_passes < MIN_SHADOW_PASSES
        ):
            raise ValueError("RECIPE_CERTIFICATION_EVIDENCE_MISSING")
        actions = recipe.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("RECIPE_ACTIONS_MISSING")
        for action in actions:
            _validate_action_shape(action)


def _validate_action_shape(action: Any) -> None:
    if not isinstance(action, dict):
        raise ValueError("RECIPE_ACTION_NOT_OBJECT")
    if action.get("level") not in {"H1", "H2", "H3", "H4"}:
        raise ValueError("RECIPE_ACTION_LEVEL_INVALID")
    argv = action.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("RECIPE_ACTION_ARGV_INVALID")
    if any("\x00" in item or "\n" in item or "\r" in item for item in argv):
        raise ValueError("RECIPE_ACTION_ARGV_CONTROL_CHARACTER")
    if action.get("idempotent") is not True:
        raise ValueError("RECIPE_ACTION_NOT_IDEMPOTENT")
    for flag in ("destructive", "network", "requires_ai_api"):
        if not isinstance(action.get(flag), bool):
            raise ValueError(f"RECIPE_ACTION_FLAG_INVALID:{flag}")
    if action.get("monetary_cost") != 0:
        raise ValueError("RECIPE_ACTION_MONETARY_COST")
    if not isinstance(action.get("writes"), list) or not all(isinstance(item, str) and item for item in action["writes"]):
        raise ValueError("RECIPE_ACTION_WRITES_INVALID")


def _atomic_json(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def save_recipe_registry(path: str | Path, registry: dict[str, Any]) -> None:
    validate_recipe_registry(registry)
    _atomic_json(path, registry)


def transition_recipe(
    recipe: dict[str, Any], target: str, *, independent_approval: bool = False,
    shadow_passes: int = 0, min_shadow_passes: int = MIN_SHADOW_PASSES, reason: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Выполнить разрешённый lifecycle transition; promotion требует review."""
    current = str(recipe.get("lifecycle"))
    if current not in RECIPE_STATES or target not in RECIPE_STATES:
        raise ValueError("RECIPE_LIFECYCLE_INVALID")
    if target not in RECIPE_TRANSITIONS[current]:
        raise ValueError(f"RECIPE_TRANSITION_FORBIDDEN:{current}->{target}")
    if target in {"CERTIFIED", "ACTIVE"} and not independent_approval:
        raise ValueError("RECIPE_INDEPENDENT_APPROVAL_REQUIRED")
    if target == "CERTIFIED" and shadow_passes < min_shadow_passes:
        raise ValueError("RECIPE_SHADOW_EVIDENCE_INSUFFICIENT")
    if target == "ACTIVE":
        certification = recipe.get("certification") or {}
        if certification.get("independent_approval") is not True or not certification.get("certified_at"):
            raise ValueError("RECIPE_CERTIFICATION_EVIDENCE_MISSING")
    updated = copy.deepcopy(recipe)
    updated["lifecycle"] = target
    if target == "CERTIFIED":
        updated["certification"] = {
            "shadow_passes": shadow_passes,
            "independent_approval": True,
            "certified_at": _iso(now),
        }
    history = list(updated.get("lifecycle_history") or [])
    history.append({
        "from": current, "to": target, "at": _iso(now),
        "reason": str(reason or "LIFECYCLE_TRANSITION")[:240],
        "independent_approval": bool(independent_approval),
    })
    updated["lifecycle_history"] = history
    return updated


def _normalize_relative_path(value: str) -> str:
    text = str(value).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("HEALING_WRITE_PATH_UNSAFE")
    return path.as_posix()


def protected_path_findings(actions: list[dict[str, Any]], protected_paths: list[str]) -> list[str]:
    findings: list[str] = []
    for action in actions:
        for raw_path in action.get("writes", []):
            try:
                path = _normalize_relative_path(raw_path)
            except ValueError:
                findings.append(f"UNSAFE_PATH:{raw_path}")
                continue
            for pattern in protected_paths:
                prefix = pattern[:-3] if pattern.endswith("/**") else None
                root_pattern = pattern[3:] if pattern.startswith("**/") else None
                if (
                    fnmatchcase(path.casefold(), pattern.casefold())
                    or (root_pattern and fnmatchcase(path.casefold(), root_pattern.casefold()))
                    or (prefix and (path == prefix or path.startswith(prefix + "/")))
                ):
                    findings.append(f"PROTECTED_PATH:{path}:{pattern}")
                    break
    return sorted(set(findings))


def command_safety_findings(actions: list[dict[str, Any]]) -> list[str]:
    """Reject shell/network/destructive escape hatches even if recipe flags lie."""
    findings: list[str] = []
    for action in actions:
        argv = action.get("argv") or []
        if not argv:
            findings.append("EMPTY_ARGV")
            continue
        executable = Path(str(argv[0])).name.casefold()
        executable = executable[:-4] if executable.endswith(".exe") else executable
        if executable in FORBIDDEN_EXECUTABLES:
            findings.append(f"FORBIDDEN_EXECUTABLE:{executable}")
        for token in argv[1:]:
            normalized = str(token).strip().casefold()
            if normalized in FORBIDDEN_ARGV_TOKENS:
                findings.append(f"FORBIDDEN_ARGV_TOKEN:{normalized}")
            if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
                findings.append("ARGV_CONTROL_CHARACTER")
    return sorted(set(findings))


def _versions_match(required: Any, observed: Any) -> bool:
    if not isinstance(required, dict) or not isinstance(observed, dict):
        return required == {} and observed == {}
    return all(observed.get(key) == value for key, value in required.items())


def recipe_compatibility(recipe: dict[str, Any], incident: dict[str, Any], context: dict[str, Any], *, now: datetime | None = None) -> list[str]:
    findings: list[str] = []
    expected_fingerprint = recipe.get("match", {}).get("fingerprint_hash")
    actual_fingerprint = incident.get("fingerprint", {}).get("hash")
    if expected_fingerprint != actual_fingerprint:
        findings.append("FINGERPRINT_NOT_EXACT")
    compatibility = recipe.get("compatibility") or {}
    policy_hash = context.get("policy_hash")
    if policy_hash not in compatibility.get("policy_hashes", []):
        findings.append("POLICY_HASH_NOT_COMPATIBLE")
    if not _versions_match(compatibility.get("tool_versions", {}), context.get("tool_versions", {})):
        findings.append("TOOL_VERSION_NOT_EXACT")
    if not _versions_match(compatibility.get("runtime_versions", {}), context.get("runtime_versions", {})):
        findings.append("RUNTIME_VERSION_NOT_EXACT")
    expires_at = recipe.get("expires_at")
    if not isinstance(expires_at, str):
        findings.append("RECIPE_EXPIRY_MISSING")
    else:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            if expiry <= (now or datetime.now(timezone.utc)).astimezone(timezone.utc):
                findings.append("RECIPE_EXPIRED")
        except ValueError:
            findings.append("RECIPE_EXPIRY_INVALID")
    return findings


def _base_decision(level: str, result: str, reason_codes: list[str], *, automatic_change: bool = False) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "level": level,
        "action": HEALING_LADDER[level],
        "result": result,
        "reason_codes": reason_codes,
        "automatic_change": automatic_change,
        "mandatory_ai_api": False,
        "monetary_budget": 0,
        "requires_next_level": None,
        "recipe": None,
        "actions": [],
    }


def _safe_metric(context: dict[str, Any], key: str, *, integer: bool = False) -> tuple[float, str | None]:
    value = context.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        return 0, f"INVALID_BUDGET_METRIC:{key}"
    if integer and not isinstance(value, int):
        return 0, f"INVALID_BUDGET_METRIC:{key}"
    return float(value), None


def _budget_findings(
    context: dict[str, Any], config: dict[str, Any], *, action_count: int = 0,
    additional_changed_files: int = 0,
) -> list[str]:
    budgets = config["budgets"]
    findings: list[str] = []
    attempts, error = _safe_metric(context, "attempts", integer=True)
    if error:
        findings.append(error)
    elapsed, error = _safe_metric(context, "elapsed_seconds")
    if error:
        findings.append(error)
    changed_files, error = _safe_metric(context, "changed_files", integer=True)
    if error:
        findings.append(error)
    monetary_cost, error = _safe_metric(context, "monetary_cost")
    if error:
        findings.append(error)
    if attempts >= budgets["max_attempts"]:
        findings.append("ATTEMPT_BUDGET_EXHAUSTED")
    if elapsed > budgets["max_elapsed_seconds"]:
        findings.append("TIME_BUDGET_EXHAUSTED")
    if changed_files + additional_changed_files > budgets["max_changed_files"]:
        findings.append("CHANGED_FILES_BUDGET_EXHAUSTED")
    if action_count > budgets["max_actions"]:
        findings.append("ACTION_BUDGET_EXHAUSTED")
    if monetary_cost != 0:
        findings.append("NON_ZERO_COST")
    if context.get("ai_api_required") is True:
        findings.append("AI_API_REQUIRED")
    return findings


def _circuit_open(circuit: dict[str, Any], fingerprint: str, recipe_key: str | None = None) -> bool:
    if not isinstance(circuit, dict):
        return True
    if circuit.get("global_open") is True:
        return True
    fingerprints = circuit.get("fingerprints") or {}
    recipes = circuit.get("recipes") or {}
    if not isinstance(fingerprints, dict) or not isinstance(recipes, dict):
        return True
    fingerprint_state = fingerprints.get(fingerprint, {})
    recipe_state = recipes.get(recipe_key, {}) if recipe_key else {}
    if not isinstance(fingerprint_state, dict) or not isinstance(recipe_state, dict):
        return True
    if fingerprint_state.get("open") is True:
        return True
    return bool(recipe_key and recipe_state.get("open") is True)


def evaluate_healing(
    incident: dict[str, Any], registry: dict[str, Any], config: dict[str, Any],
    context: dict[str, Any], circuit_state: dict[str, Any] | None = None,
    *, now: datetime | None = None,
) -> dict[str, Any]:
    """Выдать H0-H5 decision. Никаких команд функция не запускает."""
    validate_healing_config(config)
    validate_recipe_registry(registry)
    circuit = circuit_state if circuit_state is not None else {}
    fingerprint_record = incident.get("fingerprint")
    fingerprint = fingerprint_record.get("hash") if isinstance(fingerprint_record, dict) else None
    if not isinstance(fingerprint, str) or SHA256.fullmatch(fingerprint) is None:
        return _base_decision("H0", "BLOCK", ["INCIDENT_FINGERPRINT_NOT_VERIFIED"])
    if context.get("policy_result") != "ALLOW":
        return _base_decision("H0", "BLOCK", ["POLICY_NOT_ALLOW"])
    budget_findings = _budget_findings(context, config)
    if budget_findings:
        return _base_decision("H5", "HUMAN_REQUIRED", budget_findings)
    if _circuit_open(circuit, fingerprint):
        return _base_decision("H5", "HUMAN_REQUIRED", ["CIRCUIT_BREAKER_OPEN"])

    classification_record = incident.get("classification")
    classification = str(classification_record.get("type", "UNKNOWN")) if isinstance(classification_record, dict) else "UNKNOWN"
    transient = set(config.get("transient_failure_types") or [])
    deterministic = set(config.get("deterministic_failure_types") or [])
    attempts = context.get("attempts", 0)
    if classification in transient:
        if attempts == 0:
            decision = _base_decision("H1", "ALLOW", ["EXACT_TRANSIENT_CLASS"], automatic_change=False)
            decision["requires_next_level"] = "H4"
            return decision
        if attempts < config["budgets"]["max_attempts"]:
            decision = _base_decision("H2", "ALLOW", ["TRANSIENT_RECURRED_RECREATE_DISPOSABLE"], automatic_change=True)
            decision["requires_next_level"] = "H4"
            return decision
        return _base_decision("H5", "HUMAN_REQUIRED", ["TRANSIENT_RETRY_BUDGET_EXHAUSTED"])

    candidates: list[dict[str, Any]] = []
    rejected: list[str] = []
    for recipe in registry["recipes"]:
        if recipe.get("match", {}).get("fingerprint_hash") != fingerprint:
            continue
        findings = recipe_compatibility(recipe, incident, context, now=now)
        if findings:
            rejected.extend(f"{recipe['recipe_id']}@{recipe['version']}:{item}" for item in findings)
            continue
        if recipe.get("lifecycle") == "ACTIVE":
            candidates.append(recipe)
        elif recipe.get("lifecycle") == "SHADOW":
            rejected.append(f"{recipe['recipe_id']}@{recipe['version']}:SHADOW_ONLY")
        else:
            rejected.append(f"{recipe['recipe_id']}@{recipe['version']}:NOT_ACTIVE")
    if len(candidates) > 1:
        return _base_decision("H5", "HUMAN_REQUIRED", ["MULTIPLE_ACTIVE_RECIPES"])
    if len(candidates) == 1:
        recipe = candidates[0]
        key = f"{recipe['recipe_id']}@{recipe['version']}"
        if _circuit_open(circuit, fingerprint, key):
            return _base_decision("H5", "HUMAN_REQUIRED", ["RECIPE_CIRCUIT_BREAKER_OPEN"])
        actions = copy.deepcopy(recipe["actions"])
        action_findings = protected_path_findings(actions, config["protected_paths"])
        action_findings.extend(command_safety_findings(actions))
        if any(action.get("destructive") for action in actions):
            action_findings.append("DESTRUCTIVE_ACTION_FORBIDDEN")
        if any(action.get("network") for action in actions) and config.get("allow_network") is not True:
            action_findings.append("NETWORK_ACTION_FORBIDDEN")
        if any(action.get("requires_ai_api") for action in actions):
            action_findings.append("AI_API_ACTION_FORBIDDEN")
        declared_writes: set[str] = set()
        for action in actions:
            for path in action.get("writes", []):
                try:
                    declared_writes.add(_normalize_relative_path(path))
                except (TypeError, ValueError):
                    action_findings.append(f"UNSAFE_PATH:{path}")
        action_findings.extend(_budget_findings(
            context, config, action_count=len(actions),
            additional_changed_files=len(declared_writes),
        ))
        if action_findings:
            return _base_decision("H5", "HUMAN_REQUIRED", sorted(set(action_findings)))
        decision = _base_decision("H3", "ALLOW", ["ACTIVE_RECIPE_EXACT_MATCH"], automatic_change=True)
        decision.update({
            "requires_next_level": "H4",
            "recipe": {"recipe_id": recipe["recipe_id"], "version": recipe["version"], "digest": digest(recipe)},
            "actions": actions,
            "sandbox_only": True,
            "self_approval": False,
        })
        return decision

    reason = ["DETERMINISTIC_FAILURE_NO_BLIND_RETRY"] if classification in deterministic else ["UNKNOWN_FAILURE_NO_RECIPE"]
    if rejected:
        reason.append("RECIPE_COMPATIBILITY_REJECTED")
        reason.extend(sorted(rejected)[:10])
    return _base_decision("H5", "HUMAN_REQUIRED", reason)


def verify_h4(context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Verify the exact sandbox result before H6; never infer a green status."""
    validate_healing_config(config)
    required = ("target_sha", "verified_sha", "recipe_digest", "applied_recipe_digest")
    if any(not context.get(item) for item in required):
        return _base_decision("H4", "BLOCK", ["H4_EVIDENCE_MISSING"])
    if GIT_SHA.fullmatch(str(context["target_sha"])) is None or GIT_SHA.fullmatch(str(context["verified_sha"])) is None:
        return _base_decision("H4", "BLOCK", ["H4_SHA_INVALID"])
    if SHA256.fullmatch(str(context["recipe_digest"])) is None or SHA256.fullmatch(str(context["applied_recipe_digest"])) is None:
        return _base_decision("H4", "BLOCK", ["H4_RECIPE_DIGEST_INVALID"])
    if context["target_sha"] != context["verified_sha"] or context["recipe_digest"] != context["applied_recipe_digest"]:
        return _base_decision("H4", "BLOCK", ["H4_EXACT_EVIDENCE_MISMATCH"])
    if context.get("sandbox") is not True:
        return _base_decision("H4", "BLOCK", ["H4_SANDBOX_NOT_VERIFIED"])
    if context.get("targeted_gates") != "PASS" or context.get("full_gates") != "PASS" or context.get("policy_result") != "ALLOW":
        return _base_decision("H4", "BLOCK", ["H4_GATES_NOT_PASS"])
    actual = context.get("actual_changed_paths")
    declared = context.get("declared_changed_paths")
    if not isinstance(actual, list) or not isinstance(declared, list):
        return _base_decision("H4", "BLOCK", ["H4_CHANGED_PATH_EVIDENCE_MISSING"])
    try:
        actual_paths = {_normalize_relative_path(path) for path in actual}
        declared_paths = {_normalize_relative_path(path) for path in declared}
    except (TypeError, ValueError):
        return _base_decision("H4", "BLOCK", ["H4_CHANGED_PATH_UNSAFE"])
    if not actual_paths.issubset(declared_paths):
        return _base_decision("H4", "BLOCK", ["H4_UNDECLARED_WRITE"])
    path_findings = protected_path_findings([{"writes": sorted(actual_paths)}], config["protected_paths"])
    path_findings.extend(_budget_findings(
        context, config, additional_changed_files=len(actual_paths),
    ))
    if path_findings:
        return _base_decision("H4", "BLOCK", sorted(set(path_findings)))
    decision = _base_decision("H4", "VERIFIED", ["EXACT_SANDBOX_RESULT_VERIFIED"])
    decision["requires_next_level"] = "H6"
    decision["verified_sha"] = context["verified_sha"]
    decision["recipe_digest"] = context["recipe_digest"]
    return decision


def authorize_h6(action: str, context: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Разрешить только exact verified promotion/rollback; default promotion human-gated."""
    validate_healing_config(config)
    if action not in {"PROMOTE", "ROLLBACK"}:
        return _base_decision("H6", "BLOCK", ["H6_ACTION_INVALID"])
    required = ("target_sha", "verified_sha", "artifact_digest", "verified_artifact_digest")
    if any(not context.get(item) for item in required):
        return _base_decision("H6", "BLOCK", ["H6_EVIDENCE_MISSING"])
    if GIT_SHA.fullmatch(str(context["target_sha"])) is None or GIT_SHA.fullmatch(str(context["verified_sha"])) is None:
        return _base_decision("H6", "BLOCK", ["H6_SHA_INVALID"])
    if SHA256.fullmatch(str(context["artifact_digest"])) is None or SHA256.fullmatch(str(context["verified_artifact_digest"])) is None:
        return _base_decision("H6", "BLOCK", ["H6_ARTIFACT_DIGEST_INVALID"])
    if context["target_sha"] != context["verified_sha"] or context["artifact_digest"] != context["verified_artifact_digest"]:
        return _base_decision("H6", "BLOCK", ["H6_EXACT_EVIDENCE_MISMATCH"])
    if context.get("policy_result") != "ALLOW" or context.get("all_mandatory_gates") != "PASS":
        return _base_decision("H6", "BLOCK", ["H6_GATES_NOT_PASS"])
    if context.get("requires_ai_api") is True or context.get("monetary_cost", 0) != 0:
        return _base_decision("H6", "BLOCK", ["H6_FREE_ONLY_VIOLATION"])
    if action == "ROLLBACK":
        if config.get("automatic_rollback_certified") is not True or context.get("rollback_certified") is not True:
            return _base_decision("H6", "HUMAN_REQUIRED", ["ROLLBACK_NOT_CERTIFIED"])
        if context.get("data_migration") is True:
            return _base_decision("H6", "HUMAN_REQUIRED", ["ROLLBACK_DATA_MIGRATION_RISK"])
        return _base_decision("H6", "ALLOW", ["EXACT_VERIFIED_ROLLBACK"], automatic_change=True)
    if context.get("owner_acceptance") != "ACCEPTED":
        return _base_decision("H6", "HUMAN_REQUIRED", ["OWNER_ACCEPTANCE_REQUIRED"])
    return _base_decision("H6", "HUMAN_REQUIRED", ["PRODUCTION_PROMOTION_HUMAN_GATED"])


def record_heal_outcome(
    registry: dict[str, Any], circuit_state: dict[str, Any], *, recipe_id: str,
    recipe_version: str, fingerprint_hash: str, outcome: str, config: dict[str, Any],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Обновить learning metadata; false heal немедленно quarantines recipe."""
    validate_healing_config(config)
    validate_recipe_registry(registry)
    if SHA256.fullmatch(str(fingerprint_hash)) is None:
        raise ValueError("HEAL_OUTCOME_FINGERPRINT_INVALID")
    updated_registry = copy.deepcopy(registry)
    updated_circuit = copy.deepcopy(circuit_state or {})
    matches = [
        item for item in updated_registry["recipes"]
        if item.get("recipe_id") == recipe_id and item.get("version") == recipe_version
    ]
    if len(matches) != 1:
        raise ValueError("RECIPE_NOT_UNIQUE")
    recipe = matches[0]
    key = f"{recipe_id}@{recipe_version}"
    recipes = updated_circuit.setdefault("recipes", {})
    fingerprints = updated_circuit.setdefault("fingerprints", {})
    recipe_state = recipes.setdefault(key, {"false_heals": 0, "successes": 0, "open": False})
    fp_state = fingerprints.setdefault(fingerprint_hash, {"false_heals": 0, "successes": 0, "open": False})
    if outcome in FALSE_HEAL_OUTCOMES:
        recipe_state["false_heals"] += 1
        fp_state["false_heals"] += 1
        recipe_state["open"] = True
        if fp_state["false_heals"] >= config["budgets"]["max_false_heals"]:
            fp_state["open"] = True
        if recipe.get("lifecycle") not in {"QUARANTINED", "RETIRED"}:
            previous_lifecycle = recipe.get("lifecycle")
            recipe["lifecycle"] = "QUARANTINED"
            recipe.setdefault("lifecycle_history", []).append({
                "from": previous_lifecycle, "to": "QUARANTINED", "at": _iso(now),
                "reason": outcome, "independent_approval": False,
            })
    elif outcome == "SUCCESS":
        recipe_state["successes"] += 1
        fp_state["successes"] += 1
    else:
        raise ValueError("HEAL_OUTCOME_INVALID")
    updated_circuit["updated_at"] = _iso(now)
    return updated_registry, updated_circuit
