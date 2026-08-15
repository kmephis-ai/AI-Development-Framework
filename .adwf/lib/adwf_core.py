"""Совместимый фасад ADWF v1.6 над исполняемыми policy/state/evidence engines."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .strict_json import load as strict_json_load

from .leases import active_leases
from .orchestration import choose_next as policy_choose_next
from .policy import DecisionContext, evaluate_permission
from .roadmap_view import validate_roadmap_graph

RISK_RANK = {f"R{i}": i for i in range(5)}
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class ADWFError(Exception):
    pass


def load_json(path: str | Path) -> Any:
    return strict_json_load(path)


def save_json(path: str | Path, obj: Any) -> None:
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verification_gap(snapshot: dict[str, Any]) -> float:
    metrics = snapshot.get("metrics", snapshot.get("progress", {}))
    return max(0.0, float(metrics.get("implementation", 0)) - float(metrics.get("verification", 0)))


def dependency_cycles(issues: list[dict[str, Any]]) -> list[list[str]]:
    graph = {str(item["id"]): [str(value) for value in item.get("dependencies", [])] for item in issues}
    cycles: list[list[str]] = []
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for child in graph.get(node, []):
            if child not in graph:
                continue
            if color.get(child, 0) == 0:
                visit(child)
            elif color.get(child) == 1:
                cycle = stack[stack.index(child):] + [child]
                if cycle not in cycles:
                    cycles.append(cycle)
        stack.pop()
        color[node] = 2

    for node in graph:
        if color.get(node, 0) == 0:
            visit(node)
    return cycles


def issue_quality(issue: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or {}
    findings: list[str] = []
    required = {
        "id", "roadmap_id", "number", "title", "state", "priority", "risk", "type", "goal",
        "acceptance_criteria", "verification_plan", "conflict_domains", "dependencies",
        "dependencies_resolved", "human_required", "autonomy_allowed", "product_impact", "roadmap_order",
    }
    for name in sorted(required - set(issue)):
        findings.append(f"required_field_missing:{name}")
    if len(str(issue.get("title", "")).strip()) < 5:
        findings.append("title_missing_or_vague")
    if issue.get("state") not in {"BACKLOG", "SPECIFIED", "READY", "CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION", "DONE", "BLOCKED", "HOLD", "HUMAN_REQUIRED", "RECOVERY", "CANCELLED"}:
        findings.append("state_invalid")
    if issue.get("priority") not in PRIORITY_RANK:
        findings.append("priority_invalid")
    if issue.get("type") not in {"feature", "bug", "recovery", "verification", "governance", "docs", "migration"}:
        findings.append("type_invalid")
    if not isinstance(issue.get("number"), int) or isinstance(issue.get("number"), bool) or issue.get("number", 0) < 1:
        findings.append("number_invalid")
    if not isinstance(issue.get("dependencies"), list) or not all(isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", item) for item in issue.get("dependencies", [])):
        findings.append("dependencies_invalid")
    if len(str(issue.get("goal", "")).strip()) < 10:
        findings.append("goal_missing_or_vague")
    if not isinstance(issue.get("acceptance_criteria"), list) or not issue.get("acceptance_criteria") or not all(isinstance(item, str) and len(item.strip()) >= 3 for item in issue.get("acceptance_criteria", [])):
        findings.append("acceptance_missing")
    if not isinstance(issue.get("verification_plan"), list) or not issue.get("verification_plan") or not all(isinstance(item, str) and len(item.strip()) >= 3 for item in issue.get("verification_plan", [])):
        findings.append("verification_plan_missing")
    if not isinstance(issue.get("conflict_domains"), list) or not issue.get("conflict_domains") or not all(isinstance(item, str) and item for item in issue.get("conflict_domains", [])):
        findings.append("conflict_domain_missing")
    if issue.get("risk") not in RISK_RANK:
        findings.append("risk_missing")
    if issue.get("human_required") is None:
        findings.append("human_required_unknown")
    for name in ("dependencies_resolved", "human_required", "autonomy_allowed", "product_impact"):
        if not isinstance(issue.get(name), bool):
            findings.append(f"{name}_invalid")
    if not isinstance(issue.get("roadmap_order"), int) or isinstance(issue.get("roadmap_order"), bool) or issue.get("roadmap_order", -1) < 0:
        findings.append("roadmap_order_invalid")
    if not issue.get("roadmap_id"):
        findings.append("roadmap_id_missing")
    elif re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", str(issue.get("roadmap_id"))) is None:
        findings.append("roadmap_id_invalid")
    if issue.get("id") != issue.get("roadmap_id"):
        findings.append("issue_id_roadmap_mismatch")
    if issue.get("human_required") is True and issue.get("autonomy_allowed") is True:
        findings.append("human_autonomy_contradiction")
    sizing = policy.get("issue_sizing", policy)
    oversized_policy = sizing.get("oversized", {})
    independent = int(issue.get("independent_goals", 1) or 1)
    oversized = (
        independent > int(oversized_policy.get("independent_goals_gt", 1))
        or len(issue.get("conflict_domains", [])) > int(oversized_policy.get("conflict_domains_gt", 2))
        or len(issue.get("acceptance_criteria", [])) > int(oversized_policy.get("acceptance_criteria_gt", 10))
    )
    if oversized:
        findings.append("oversized")
    if issue.get("state") == "READY" and issue.get("dependencies_resolved") is not True:
        findings.append("ready_without_verified_dependencies")
    hard = {
        "goal_missing_or_vague", "acceptance_missing", "verification_plan_missing",
        "conflict_domain_missing", "risk_missing", "human_required_unknown",
        "roadmap_id_missing", "roadmap_id_invalid", "issue_id_roadmap_mismatch", "human_autonomy_contradiction", "ready_without_verified_dependencies",
        "title_missing_or_vague", "state_invalid", "priority_invalid", "dependencies_invalid",
        "type_invalid", "number_invalid", "dependencies_resolved_invalid", "human_required_invalid",
        "autonomy_allowed_invalid", "product_impact_invalid", "roadmap_order_invalid",
    }
    status = "FAIL" if hard.intersection(findings) or any(item.startswith("required_field_missing:") for item in findings) else ("NEEDS_SPLIT" if oversized else "PASS")
    return {
        "status": status,
        "findings": findings,
        "suggested_label": "roadmap:needs-split" if oversized else ("roadmap:needs-spec" if status == "FAIL" else "roadmap:ready"),
    }


def roadmap_audit(snapshot: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or {}
    findings: list[str] = []
    severity = "HEALTHY"
    metrics = snapshot.get("metrics", snapshot.get("progress", {}))
    issues = snapshot.get("issues", [])
    done_ratio = float(metrics.get("done_ratio", 0))
    if not done_ratio and metrics.get("issues_total"):
        done_ratio = float(metrics.get("issues_done", 0)) / max(1.0, float(metrics["issues_total"]))
    gap = verification_gap(snapshot)
    product = snapshot.get("product_health", snapshot.get("health", {}).get("product", "NOT_VERIFIED"))
    if product in {"BROKEN", "NOT_VERIFIED", "STALE"}:
        findings.append(f"product_health_blocks_feature:{product}")
        severity = "CRITICAL"
    false_progress = policy.get("false_progress", {})
    if product == "BROKEN" and done_ratio >= float(false_progress.get("done_ratio_min", 0.7)):
        findings.append("false_progress:many_done_but_product_broken")
    gap_policy = policy.get("verification_gap", policy)
    block = float(gap_policy.get("block_feature_progression", gap_policy.get("verification_gap_block", 0.30)))
    warn = float(gap_policy.get("warn", gap_policy.get("verification_gap_warn", 0.15)))
    if gap >= block:
        findings.append(f"verification_gap:block:{gap:.2f}")
        severity = "CRITICAL"
    elif gap >= warn:
        findings.append(f"verification_gap:warn:{gap:.2f}")
        if severity == "HEALTHY":
            severity = "ATTENTION"
    graph = validate_roadmap_graph(issues)
    if graph["status"] != "PASS":
        for finding in graph["errors"]:
            findings.append("roadmap_graph:" + finding)
        if any(item.startswith("DEPENDENCY_CYCLE:") for item in graph["errors"]):
            findings.append("dependency_cycle")
        severity = "CRITICAL"
    for issue in issues:
        result = issue_quality(issue, policy)
        if result["status"] == "NEEDS_SPLIT":
            findings.append(f"oversized:{issue.get('id')}")
            if severity == "HEALTHY":
                severity = "ATTENTION"
        if "ready_without_verified_dependencies" in result["findings"]:
            findings.append(f"invalid_ready:{issue.get('id')}")
            severity = "CRITICAL"
    debt = snapshot.get("debt", {})
    debt_policy = policy.get("debt", {}).get("budget", {})
    high_budget = int(debt.get("budget_high", debt_policy.get("high", 0)))
    medium_budget = int(debt.get("budget_medium", debt_policy.get("medium", 999999)))
    if int(debt.get("high", 0)) > high_budget or int(debt.get("medium", 0)) > medium_budget:
        findings.append("debt_budget_exceeded")
        if severity == "HEALTHY":
            severity = "ATTENTION"
    architecture = snapshot.get("architecture", {})
    if architecture.get("drift") in {"VIOLATION", "UNCONTROLLED"}:
        findings.append("architecture_drift")
        severity = "CRITICAL" if architecture.get("drift") == "VIOLATION" else "ATTENTION"
    if snapshot.get("orphan_work", 0) > 0:
        findings.append("orphan_work")
    if snapshot.get("uncovered_capabilities", 0) > 0:
        findings.append("uncovered_capabilities")
    return {
        "health": severity,
        "findings": findings,
        "verification_gap": round(gap, 4),
        "autopilot_feature_progression": severity != "CRITICAL" and product in {"VERIFIED", "HEALTHY"},
    }


def review_fresh(review_sha: str | None, head_sha: str | None) -> str:
    if not review_sha or not head_sha:
        return "NOT_VERIFIED"
    return "PASS" if review_sha == head_sha else "STALE"


def trust_change_audit(changes: list[dict[str, Any]]) -> dict[str, Any]:
    weakening = [item for item in changes if item.get("trust_boundary") and item.get("effect") in {"WEAKEN", "AUTONOMY_INCREASE", "PERMISSION_EXPANSION"}]
    return {"status": "FAIL" if weakening else "PASS", "risk": "R4" if weakening else "R0", "human_required": bool(weakening), "weakening": weakening}


def claim_conflicts(issue: dict[str, Any], leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    domains = set(issue.get("conflict_domains", []))
    for lease in active_leases(leases):
        if str(lease.get("issue_id")) == str(issue.get("id")):
            output.append({"type": "same_issue", "lease": lease})
        elif domains.intersection(lease.get("conflict_domains", [])):
            output.append({"type": "conflict_domain", "domains": sorted(domains.intersection(lease.get("conflict_domains", []))), "lease": lease})
    return output


def choose_next(queue: dict[str, Any], policy_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if policy_context is None:
        return None
    return policy_choose_next(queue, policy_context)


def merge_permission(risk: str, autonomy: str, ci: str, review: str, human_approved: bool = False) -> str:
    decision = evaluate_permission(DecisionContext(
        action="merge", autonomy=autonomy, risk=risk, max_autonomous_risk="R1",
        health={"package_integrity": "VERIFIED", "config_health": "VERIFIED", "control_plane_health": "VERIFIED", "product_health": "VERIFIED"},
        gates={"ci": ci, "review": review}, required_gates=("ci", "review"),
        exact_sha=ci == "PASS" and review == "PASS", evidence_fresh=ci == "PASS" and review == "PASS",
        human_approved=human_approved,
    ))
    return "ALLOWED" if decision.result == "ALLOW" else "BLOCKED"


def evidence_claim(status: str, evidence_present: bool) -> str:
    return "INVALID_PASS" if status == "PASS" and not evidence_present else status


def canonical_roadmap_state(project_state: str | None, labels: list[str], dependencies_resolved: bool = True) -> dict[str, Any]:
    mapping = {
        "roadmap:ready": "READY", "roadmap:in-progress": "IN_PROGRESS", "roadmap:review": "REVIEW", "roadmap:verification": "VERIFICATION",
        "roadmap:blocked": "BLOCKED", "roadmap:hold": "HOLD", "roadmap:stale": "SPECIFIED",
        "roadmap:needs-spec": "SPECIFIED", "roadmap:needs-split": "SPECIFIED",
    }
    present = [mapping[label] for label in labels if label in mapping]
    label_state = present[0] if present and len(set(present)) == 1 else ("SPLIT_BRAIN" if present else None)
    canonical = "BLOCKED" if dependencies_resolved is not True else (project_state or label_state or "SPECIFIED")
    split = label_state == "SPLIT_BRAIN" or bool(project_state and label_state not in {None, "SPLIT_BRAIN"} and project_state != label_state)
    return {"canonical": canonical, "split_brain": split, "label_state": label_state, "project_state": project_state}


def evaluate_adversarial_case(case: dict[str, Any]) -> tuple[bool, str]:
    kind = case.get("case")
    if kind == "false_progress":
        result = roadmap_audit(case["snapshot"])
        return result["health"] == "CRITICAL" and not result["autopilot_feature_progression"], str(result)
    if kind == "stale_review":
        status = review_fresh(case.get("review_sha"), case.get("head_sha"))
        return status == "STALE", status
    if kind == "blocked_ready":
        result = issue_quality(case["issue"])
        return "ready_without_verified_dependencies" in result["findings"], str(result)
    if kind == "trust_downgrade":
        result = trust_change_audit(case["changes"])
        return result["status"] == "FAIL" and result["risk"] == "R4" and result["human_required"], str(result)
    if kind == "agent_conflict":
        result = claim_conflicts(case["issue"], case["leases"])
        return bool(result), str(result)
    if kind == "dependency_cycle":
        result = dependency_cycles(case["issues"])
        return bool(result), str(result)
    if kind == "architecture_drift":
        result = roadmap_audit(case["snapshot"])
        return "architecture_drift" in result["findings"], str(result)
    if kind == "debt_budget":
        result = roadmap_audit(case["snapshot"])
        return "debt_budget_exceeded" in result["findings"], str(result)
    if kind == "oversized_issue":
        result = issue_quality(case["issue"])
        return result["status"] == "NEEDS_SPLIT", str(result)
    if kind == "r4_automerge":
        result = merge_permission(case.get("risk", "R4"), case.get("autonomy", "A4"), case.get("ci", "PASS"), case.get("review", "PASS"), case.get("human_approved", False))
        return result == "BLOCKED", result
    if kind == "false_pass":
        result = evidence_claim(case.get("status", "PASS"), case.get("evidence_present", False))
        return result == "INVALID_PASS", result
    if kind == "split_brain":
        result = canonical_roadmap_state(case.get("project_state"), case.get("labels", []), case.get("dependencies_resolved", True))
        return result["split_brain"] or result["canonical"] == "BLOCKED", str(result)
    raise ADWFError(f"Unknown adversarial case: {kind}")
