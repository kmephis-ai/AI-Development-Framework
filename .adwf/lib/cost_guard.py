"""Fail-closed Cost Guard с нулевым денежным бюджетом."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import math

from .evidence import parse_time

CAPABILITY_STATUSES = {
    "FREE_VERIFIED", "INCLUDED_QUOTA", "CONDITIONAL_FREE", "OWNER_PROVIDED",
    "METERED", "PAID", "UNKNOWN", "STALE",
}
ALLOWED_CLASSIFICATIONS = {"FREE_VERIFIED", "INCLUDED_QUOTA", "CONDITIONAL_FREE", "OWNER_PROVIDED"}


def _number(value: Any) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("invalid non-negative number")
    return parsed


def _fresh(start: Any, end: Any, now: datetime) -> bool:
    verified, valid_until = parse_time(str(start)), parse_time(str(end))
    return verified <= now < valid_until and verified < valid_until


def evaluate_provider(registry: dict[str, Any], request: dict[str, Any], *, now: datetime | None = None, canonical_provider: str | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    name = str(request.get("provider", ""))
    provider = registry.get("providers", {}).get(name)
    reasons: list[str] = []
    usage = {"hosted_minutes_used": 0.0, "hosted_minutes_internal_hard": 0.0, "artifact_mb": 0.0, "cache_mb": 0.0}
    if not provider:
        return {"result": "BLOCK", "reason_codes": ["UNKNOWN_PROVIDER"], "provider": name,
                "classification": "UNKNOWN", "effective_classification": "UNKNOWN"}
    classification = str(provider.get("classification", "UNKNOWN"))
    effective_classification = classification
    if provider.get("enabled") is not True:
        reasons.append("PROVIDER_DISABLED")
    if classification not in CAPABILITY_STATUSES:
        reasons.append("CAPABILITY_STATUS_INVALID")
        effective_classification = "UNKNOWN"
    if classification not in ALLOWED_CLASSIFICATIONS:
        reasons.append("UNKNOWN_OR_PAID_PROVIDER")
    if classification in {"METERED", "PAID", "UNKNOWN", "STALE"}:
        reasons.append(f"CAPABILITY_{classification}_FORBIDDEN")
    if provider.get("billing_model") in {"metered", "unknown"} or registry.get("allow_metered_provider") is not False:
        reasons.append("METERED_PROVIDER_FORBIDDEN")
    try:
        if not _fresh(provider.get("verified_at"), provider.get("valid_until"), now):
            reasons.append("PROVIDER_EVIDENCE_STALE")
            effective_classification = "STALE"
    except (TypeError, ValueError):
        reasons.append("PROVIDER_EVIDENCE_INVALID")
        effective_classification = "STALE"
    if not str(provider.get("official_source_url", "")).startswith("https://"):
        reasons.append("OFFICIAL_SOURCE_MISSING")
    if request.get("mandatory_ci") is True:
        if provider.get("requires_ai_api") is True:
            reasons.append("AI_API_FOR_MANDATORY_CI")
        if provider.get("mandatory_ci_allowed") is not True:
            reasons.append("CAPABILITY_NOT_ALLOWED_FOR_MANDATORY_CI")
    if request.get("automated") is True and provider.get("automatic_execution_allowed") is not True:
        reasons.append("AUTOMATIC_EXECUTION_NOT_ALLOWED")
    if canonical_provider and provider.get("plane") == "ci" and provider.get("provider_mode") not in {"local", canonical_provider}:
        reasons.append("CANONICAL_PROVIDER_MISMATCH")
    try:
        projected_cost = _number(request.get("projected_cost", 0))
    except (TypeError, ValueError):
        projected_cost = 1.0
        reasons.append("PROJECTED_COST_INVALID")
    try:
        hard_budget = _number(registry["hard_budget_usd"])
    except (KeyError, TypeError, ValueError):
        hard_budget = 0
        reasons.append("MONETARY_BUDGET_INVALID")
    if hard_budget != 0:
        reasons.append("ZERO_COST_POLICY_INVALID")
    if projected_cost > hard_budget:
        reasons.append("MONETARY_BUDGET_EXCEEDED")
    if (registry.get("allow_overage") is not False or registry.get("allow_credit_purchase") is not False
            or registry.get("allow_unknown_provider") is not False):
        reasons.append("ZERO_COST_POLICY_INVALID")

    visibility = str(request.get("repository_visibility", "")).upper()
    runner_class = str(request.get("runner_class", "")).lower()
    visibility_scope = provider.get("repository_visibility_scope")
    if visibility_scope == "PUBLIC_ONLY" and visibility != "PUBLIC":
        reasons.append("PUBLIC_REPOSITORY_NOT_VERIFIED")
    elif visibility_scope == "PRIVATE_ONLY" and visibility != "PRIVATE":
        reasons.append("PRIVATE_REPOSITORY_NOT_VERIFIED")
    elif visibility_scope not in {"ANY", "PUBLIC_ONLY", "PRIVATE_ONLY", "NOT_APPLICABLE"}:
        reasons.append("REPOSITORY_VISIBILITY_SCOPE_INVALID")
    execution_class = provider.get("execution_class")
    if execution_class == "STANDARD_HOSTED" and runner_class != "standard":
        reasons.append("STANDARD_RUNNER_NOT_VERIFIED")
    elif execution_class == "SELF_HOSTED" and runner_class != "self_hosted":
        reasons.append("SELF_HOSTED_RUNNER_NOT_VERIFIED")
    elif execution_class not in {"LOCAL", "STANDARD_HOSTED", "SELF_HOSTED", "LARGER_HOSTED", "INTERACTIVE", "METERED_API", "PLATFORM_CONTROL"}:
        reasons.append("EXECUTION_CLASS_INVALID")
    requirements = provider.get("zero_cost_requirements")
    if not isinstance(requirements, list) or not requirements or not all(isinstance(item, str) and item.strip() for item in requirements):
        reasons.append("ZERO_COST_REQUIREMENTS_MISSING")

    if classification == "OWNER_PROVIDED":
        owner_resource = request.get("owner_resource") or {}
        try:
            if owner_resource.get("confirmed") is not True:
                reasons.append("OWNER_RESOURCE_NOT_CONFIRMED")
            if owner_resource.get("metered_control_plane") is not False:
                reasons.append("OWNER_RESOURCE_METERED_CONTROL_PLANE_NOT_BLOCKED")
            if not _fresh(owner_resource["verified_at"], owner_resource["valid_until"], now):
                reasons.append("OWNER_RESOURCE_EVIDENCE_STALE")
        except (KeyError, TypeError, ValueError):
            reasons.append("OWNER_RESOURCE_NOT_VERIFIED")
    if classification == "INCLUDED_QUOTA":
        quota = request.get("quota") or {}
        try:
            used, platform_limit = _number(quota["used"]), _number(quota["limit"])
            projected = _number(request.get("projected_units", 0))
            observed = parse_time(str(quota["observed_at"]))
            ttl_minutes = int(registry["quota_evidence_ttl_minutes"])
            if ttl_minutes < 1 or ttl_minutes > 1440:
                raise ValueError("invalid quota evidence TTL")
            if observed > now or (now - observed).total_seconds() > ttl_minutes * 60:
                reasons.append("QUOTA_EVIDENCE_STALE")
            internal = provider.get("internal_limits", {})
            hard_limit = _number(internal.get("hard_minutes", platform_limit))
            usage["hosted_minutes_used"] = used
            usage["hosted_minutes_internal_hard"] = hard_limit
            declared_platform = internal.get("platform_minutes")
            if declared_platform is not None and platform_limit > _number(declared_platform):
                reasons.append("QUOTA_LIMIT_EXCEEDS_VERIFIED_PLAN")
            if used > platform_limit or used + projected > min(platform_limit, hard_limit):
                reasons.append("FREE_QUOTA_WOULD_BE_EXCEEDED")
            if quota.get("hard_spend_limit_zero") is not True or quota.get("allow_overage") is not False:
                reasons.append("ZERO_SPEND_LIMIT_NOT_VERIFIED")
        except (KeyError, TypeError, ValueError):
            reasons.append("QUOTA_NOT_VERIFIED")

        internal = provider.get("internal_limits", {})
        if "artifact_stop_mb" in internal or "cache_stop_mb" in internal:
            storage = request.get("storage") or {}
            try:
                artifact = _number(storage["artifact_mb"])
                cache = _number(storage["cache_mb"])
                projected_artifact = _number(request.get("projected_artifact_mb", 0))
                projected_cache = _number(request.get("projected_cache_mb", 0))
                observed = parse_time(str(storage["observed_at"]))
                ttl_minutes = int(registry["quota_evidence_ttl_minutes"])
                if ttl_minutes < 1 or ttl_minutes > 1440:
                    raise ValueError("invalid quota evidence TTL")
                if observed > now or (now - observed).total_seconds() > ttl_minutes * 60:
                    reasons.append("STORAGE_EVIDENCE_STALE")
                if artifact + projected_artifact > _number(internal.get("artifact_stop_mb", 0)):
                    reasons.append("ARTIFACT_STORAGE_WOULD_EXCEED_INTERNAL_LIMIT")
                if cache + projected_cache > _number(internal.get("cache_stop_mb", 0)):
                    reasons.append("CACHE_STORAGE_WOULD_EXCEED_INTERNAL_LIMIT")
                usage["artifact_mb"] = artifact
                usage["cache_mb"] = cache
            except (KeyError, TypeError, ValueError):
                reasons.append("STORAGE_USAGE_NOT_VERIFIED")

    if classification == "CONDITIONAL_FREE":
        subscription = request.get("subscription") or {}
        try:
            if not _fresh(subscription["verified_at"], subscription["valid_until"], now):
                reasons.append("SUBSCRIPTION_EVIDENCE_STALE")
            if subscription.get("auto_credit_purchase") is not False:
                reasons.append("AUTO_CREDIT_PURCHASE_NOT_BLOCKED")
            if request.get("mandatory_ci") is True or request.get("automated") is True:
                reasons.append("SUBSCRIPTION_INTERACTIVE_ONLY")
        except (KeyError, TypeError, ValueError):
            reasons.append("SUBSCRIPTION_NOT_VERIFIED")

    return {"result": "ALLOW" if not reasons else "BLOCK", "reason_codes": list(dict.fromkeys(reasons)),
            "provider": name, "provider_id": provider.get("provider_id"), "capability_id": provider.get("capability_id"),
            "classification": classification, "effective_classification": effective_classification,
            "valid_until": provider.get("valid_until"), "projected_cost_usd": projected_cost, "usage": usage}
