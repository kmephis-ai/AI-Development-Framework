"""Воспроизводимая семантика пользовательской команды «делай далее»."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from .leases import (
    DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
    active_leases,
    conflict_domains,
    lease_freshness_errors,
)
from .policy import DecisionContext, evaluate_permission

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
RISK_RANK = {f"R{i}": i for i in range(5)}
SAFE_HEALTH = {"VERIFIED", "HEALTHY"}
ACTIVE_ISSUE_STATES = {"CLAIMED", "IN_PROGRESS", "REVIEW"}
LEASED_ISSUE_STATES = ACTIVE_ISSUE_STATES | {"VERIFICATION", "RECOVERY"}


def _sort_key(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if issue.get("type") == "recovery" else 1,
        PRIORITY_RANK.get(issue.get("priority"), 99),
        int(issue.get("roadmap_order", 999999)),
        -int(issue.get("critical_path_score", 0)),
        RISK_RANK.get(issue.get("risk"), 99),
        str(issue.get("ready_since", "9999")),
        str(issue.get("id", "")),
    )


def choose_next(
    queue: dict[str, Any],
    policy_context: dict[str, Any],
    *,
    policy_ir: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if _queue_contract_errors(queue) or _common_safety_findings(policy_context, require_control=True):
        return None
    leases = active_leases(queue.get("leases", []))
    candidates: list[dict[str, Any]] = []
    for issue in queue.get("issues", []):
        if issue.get("state") != "READY" or issue.get("dependencies_resolved") is not True or not issue.get("ready_since"):
            continue
        if issue.get("human_required") is not False or issue.get("autonomy_allowed") is not True:
            continue
        if conflict_domains(issue, leases):
            continue
        context = dict(policy_context)
        context.update({"action": "claim", "risk": issue.get("risk", "UNKNOWN"), "work_type": issue.get("type", "feature")})
        try:
            permission = evaluate_permission(DecisionContext.from_dict(context), policy_ir)
        except (TypeError, ValueError):
            continue
        if permission.result != "ALLOW":
            continue
        candidates.append(issue)
    return sorted(candidates, key=_sort_key)[0] if candidates else None


def _outcome(
    action: str,
    reason: str,
    issue: dict[str, Any] | None,
    *,
    result: str = "ALLOW",
    reason_codes: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "result": result,
        "action": action,
        "reason": reason,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "issue": issue,
    }


def _common_safety_findings(policy_context: dict[str, Any], *, require_control: bool) -> list[str]:
    """Независимый preflight: policy engine нельзя обойти выбором ветви."""
    findings: list[str] = []
    if not isinstance(policy_context, dict):
        return ["POLICY_CONTEXT_INVALID"]
    health = policy_context.get("health")
    if not isinstance(health, dict):
        return ["HEALTH_CONTEXT_INVALID"]
    for name in ("package_integrity", "config_health"):
        if health.get(name) not in SAFE_HEALTH:
            findings.append(f"HEALTH_NOT_SAFE:{name}")
    if require_control and health.get("control_plane_health") not in SAFE_HEALTH:
        findings.append("HEALTH_NOT_SAFE:control_plane_health")
    if policy_context.get("provider_allowed") is not True:
        findings.append("PROVIDER_NOT_ALLOWED")
    if policy_context.get("provider_potentially_paid") is not False:
        findings.append("PROVIDER_COST_CLASS_UNKNOWN_OR_PAID")
    try:
        raw_cost = policy_context.get("projected_cost")
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
            raise TypeError("projected_cost must be numeric")
        projected_cost = float(raw_cost)
        if not math.isfinite(projected_cost) or projected_cost != 0:
            findings.append("PROJECTED_COST_NOT_ZERO")
    except (TypeError, ValueError):
        findings.append("PROJECTED_COST_INVALID")
    if policy_context.get("writer_conflict") is True:
        findings.append("WRITER_CONFLICT")
    return findings


def _permission_for(
    policy_context: dict[str, Any],
    issue: dict[str, Any],
    *,
    action: str,
    work_type: str | None = None,
    require_control: bool = True,
    policy_ir: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    findings = _common_safety_findings(policy_context, require_control=require_control)
    if findings:
        return "BLOCK", findings
    context = dict(policy_context)
    context.update(
        {
            "action": action,
            "risk": issue.get("risk", "UNKNOWN"),
            "work_type": work_type or issue.get("type", "feature"),
        }
    )
    try:
        permission = evaluate_permission(DecisionContext.from_dict(context), policy_ir)
    except (TypeError, ValueError) as exc:
        return "BLOCK", [f"POLICY_CONTEXT_INVALID:{exc}"]
    return permission.result, list(permission.reason_codes)


def _effective_lease(lease: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    """Provider adapters могут проецировать heartbeat/workspace рядом с Issue."""
    effective = dict(lease)
    for name in ("heartbeat_at", "expires_at", "workspace_status", "workspace_id", "worker_id"):
        if effective.get(name) is None and issue.get(name) is not None:
            effective[name] = issue[name]
    return effective


def _queue_contract_errors(queue: Any) -> list[str]:
    if not isinstance(queue, dict):
        return ["QUEUE_NOT_OBJECT"]
    issues = queue.get("issues", [])
    leases = queue.get("leases", [])
    if not isinstance(issues, list) or not all(isinstance(item, dict) for item in issues):
        return ["QUEUE_ISSUES_INVALID"]
    if not isinstance(leases, list) or not all(isinstance(item, dict) for item in leases):
        return ["QUEUE_LEASES_INVALID"]
    errors: list[str] = []
    issue_ids = [str(item.get("id") or "") for item in issues]
    if any(not ident for ident in issue_ids) or len(set(issue_ids)) != len(issue_ids):
        errors.append("QUEUE_ISSUE_IDS_INVALID_OR_DUPLICATE")
    lease_ids = [str(item.get("lease_id") or "") for item in leases]
    if any(not ident for ident in lease_ids) or len(set(lease_ids)) != len(lease_ids):
        errors.append("QUEUE_LEASE_IDS_INVALID_OR_DUPLICATE")
    return errors


def authorize_next_action(
    queue: dict[str, Any],
    policy_context: dict[str, Any],
    *,
    now: datetime | None = None,
    heartbeat_timeout_minutes: int = DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
    policy_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Единая fail-closed авторизация claim/continue/review/recovery.

    Функция никогда не возвращает работу без Health, provider/cost и policy
    preflight. Структурный конфликт вместо feature work возвращает только
    наблюдаемое действие ``RECONCILE``.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    contract_errors = _queue_contract_errors(queue)
    if contract_errors:
        return _outcome("BLOCKED", "QUEUE_CONTRACT_INVALID", None, result="BLOCK", reason_codes=contract_errors)
    if not isinstance(policy_context, dict):
        return _outcome("BLOCKED", "POLICY_CONTEXT_INVALID", None, result="BLOCK", reason_codes=["POLICY_CONTEXT_INVALID"])
    issues = queue.get("issues", [])
    declared = [item for item in queue.get("leases", []) if item.get("status", "ACTIVE") == "ACTIVE"]
    if len(declared) > 1:
        return _outcome("RECONCILE", "MULTIPLE_ACTIVE_WRITERS", None, result="BLOCK", reason_codes=["MULTIPLE_ACTIVE_WRITERS"])
    if len(declared) == 1:
        lease = declared[0]
        matches = [item for item in issues if str(item.get("id")) == str(lease.get("issue_id"))]
        if len(matches) != 1 or matches[0].get("state") not in LEASED_ISSUE_STATES:
            return _outcome("RECONCILE", "LEASE_STATE_SPLIT_BRAIN", None, result="BLOCK", reason_codes=["LEASE_STATE_SPLIT_BRAIN"])
        issue = matches[0]
        effective = _effective_lease(lease, issue)
        if str(issue.get("lease_id") or "") != str(lease.get("lease_id") or ""):
            return _outcome("RECONCILE", "LEASE_IDENTITY_SPLIT_BRAIN", issue, result="BLOCK", reason_codes=["LEASE_IDENTITY_SPLIT_BRAIN"])
        if not str(effective.get("workspace_id") or "").strip():
            return _outcome("RECONCILE", "WORKSPACE_IDENTITY_NOT_VERIFIED", issue, result="BLOCK", reason_codes=["WORKSPACE_IDENTITY_NOT_VERIFIED"])
        if str(issue.get("workspace_id") or "") != str(effective.get("workspace_id") or ""):
            return _outcome("RECONCILE", "WORKSPACE_IDENTITY_SPLIT_BRAIN", issue, result="BLOCK", reason_codes=["WORKSPACE_IDENTITY_SPLIT_BRAIN"])
        freshness = lease_freshness_errors(
            effective,
            now,
            heartbeat_timeout_minutes=heartbeat_timeout_minutes,
        )
        if freshness:
            return _outcome("RECONCILE", "EXPIRED_OR_INVALID_ACTIVE_LEASE", issue, result="BLOCK", reason_codes=freshness)
        if effective.get("workspace_status") != "ACTIVE":
            return _outcome("RECONCILE", "WORKSPACE_NOT_ACTIVE", issue, result="BLOCK", reason_codes=["WORKSPACE_NOT_ACTIVE"])
        state = issue.get("state")
        is_recovery = state == "RECOVERY" or issue.get("type") == "recovery"
        policy_action = "test" if state in {"REVIEW", "VERIFICATION"} else "edit"
        result, reasons = _permission_for(
            policy_context,
            issue,
            action=policy_action,
            work_type="recovery" if is_recovery else ("verification" if state in {"REVIEW", "VERIFICATION"} else None),
            require_control=not is_recovery,
            policy_ir=policy_ir,
        )
        if result != "ALLOW":
            return _outcome("BLOCKED", "EXISTING_WORK_NOT_AUTHORIZED", issue, result=result, reason_codes=reasons)
        return _outcome("CONTINUE_EXISTING", "ONE_ACTIVE_WRITER", issue)

    orphaned = sorted(
        [item for item in issues if item.get("state") in ACTIVE_ISSUE_STATES],
        key=_sort_key,
    )
    if orphaned:
        return _outcome(
            "RECONCILE",
            "ACTIVE_ISSUE_WITHOUT_LEASE",
            orphaned[0],
            result="BLOCK",
            reason_codes=["ACTIVE_ISSUE_WITHOUT_LEASE"],
        )

    higher_priority = sorted(
        [item for item in issues if item.get("state") in {"REVIEW", "VERIFICATION", "RECOVERY"}],
        key=_sort_key,
    )
    if higher_priority:
        issue = higher_priority[0]
        is_recovery = issue.get("state") == "RECOVERY" or issue.get("type") == "recovery"
        result, reasons = _permission_for(
            policy_context,
            issue,
            action="edit" if is_recovery else "inspect",
            work_type="recovery" if is_recovery else "verification",
            require_control=not is_recovery,
            policy_ir=policy_ir,
        )
        if result != "ALLOW":
            return _outcome("BLOCKED", "REVIEW_OR_RECOVERY_NOT_AUTHORIZED", issue, result=result, reason_codes=reasons)
        return _outcome("CONTINUE_REVIEW_OR_RECOVERY", "UNFINISHED_HIGHER_PRIORITY_FLOW", issue)

    common = _common_safety_findings(policy_context, require_control=True)
    if common:
        return _outcome("BLOCKED", "SAFETY_PREFLIGHT_BLOCKED", None, result="BLOCK", reason_codes=common)
    next_issue = choose_next(queue, policy_context, policy_ir=policy_ir)
    if next_issue:
        result, reasons = _permission_for(
            policy_context, next_issue, action="claim", require_control=True, policy_ir=policy_ir,
        )
        if result != "ALLOW":
            return _outcome("BLOCKED", "CLAIM_NOT_AUTHORIZED", next_issue, result=result, reason_codes=reasons)
        return _outcome("CLAIM_ONE_READY", "DETERMINISTIC_ROADMAP_ORDER", next_issue)
    if any(item.get("state") == "READY" for item in issues):
        return _outcome("BLOCKED", "READY_ITEMS_NOT_CLAIMABLE", None, result="BLOCK", reason_codes=["READY_ITEMS_NOT_CLAIMABLE"])
    return _outcome("ROADMAP_COMPLETE_OR_EMPTY", "NO_ACTIVE_OR_READY_ITEM", None)


def continue_decision(
    queue: dict[str, Any],
    policy_context: dict[str, Any],
    *,
    policy_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backward-compatible facade над единственной authorization boundary."""
    return authorize_next_action(queue, policy_context, policy_ir=policy_ir)
