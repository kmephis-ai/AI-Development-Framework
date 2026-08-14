"""Provider-neutral построение правдивого project snapshot из свежих API facts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import copy
import hashlib
import json
import re

from .adwf_core import issue_quality
from .evidence import parse_time
from .issue_contract import parse_issue_form, parse_issue_marker, parse_pr_contract
from .metrics import summarize_ci
from .workspaces import OCCUPYING

STATE_LABELS = {
    "roadmap:ready": "READY", "roadmap:in-progress": "IN_PROGRESS", "roadmap:review": "REVIEW",
    "roadmap:verification": "VERIFICATION", "roadmap:blocked": "BLOCKED", "roadmap:hold": "HOLD",
    "roadmap:needs-spec": "SPECIFIED", "roadmap:needs-split": "SPECIFIED", "roadmap:stale": "SPECIFIED",
    "recovery:active": "RECOVERY",
}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cost_status(cost: dict[str, Any]) -> str:
    if cost.get("result") == "ALLOW":
        return "ALLOW_ZERO_COST"
    reasons = set(cost.get("reason_codes", []))
    if cost.get("classification") in {"PAID", "POTENTIALLY_PAID"} or "MONETARY_BUDGET_EXCEEDED" in reasons:
        return "BLOCK_PAID"
    if any("QUOTA" in reason or "STORAGE" in reason for reason in reasons):
        return "BLOCK_QUOTA"
    if any("STALE" in reason for reason in reasons):
        return "STALE"
    return "BLOCK_UNKNOWN"


def _normalized_runs(runs: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
    samples = []
    for run in runs:
        started = run.get("run_started_at") or run.get("started_at")
        completed = run.get("updated_at")
        queued = run.get("created_at")
        if not started or not completed or not queued:
            continue
        conclusion = {"success": "PASS", "failure": "FAIL", "cancelled": "CANCELLED"}.get(run.get("conclusion"), "CANCELLED")
        samples.append({"id": str(run.get("id", "UNKNOWN")), "queued_at": queued, "started_at": started,
                        "completed_at": completed, "conclusion": conclusion, "first_failure_at": None,
                        "flaky": int(run.get("run_attempt", 1) or 1) > 1 and conclusion == "PASS"})
    if not samples:
        return {"status": "NOT_VERIFIED", "observed_at": observed_at, "runs": 0, "p50_duration_seconds": None,
                "p95_duration_seconds": None, "p95_time_to_first_failure_seconds": None,
                "p95_queue_seconds": None, "flake_rate": None}
    result = summarize_ci({"observed_at": observed_at, "runs": samples}, now=datetime.fromisoformat(observed_at.replace("Z", "+00:00")))
    result.pop("errors", None)
    return result


def reconcile_snapshot(
    previous: dict[str, Any], config: dict[str, Any], *, provider: str, main_sha: str,
    issues: list[dict[str, Any]], pulls: list[dict[str, Any]], runs: list[dict[str, Any]],
    cost: dict[str, Any], workspace_registry: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output = copy.deepcopy(previous)
    errors: list[str] = []
    decisions: list[str] = []
    if re.fullmatch(r"[0-9a-f]{40}", str(main_sha)) is None:
        errors.append("MAIN_SHA_NOT_EXACT")
    if provider != config.get("provider", {}).get("mode"):
        errors.append("CANONICAL_PROVIDER_MISMATCH")
    work_items = []
    roadmap_seen: dict[str, int] = {}
    for issue in issues:
        labels = [item.get("name", item) if isinstance(item, dict) else item for item in issue.get("labels", [])]
        machine = [STATE_LABELS[label] for label in labels if label in STATE_LABELS]
        body = issue.get("body") or ""
        marker: dict[str, Any] = {}
        if not machine and "### Roadmap ID" not in body:
            continue
        if issue.get("state") == "closed":
            marker = parse_issue_marker(body)
            if marker.get("valid") and marker.get("state") == "DONE":
                machine = ["DONE"]
            else:
                errors.append(f"CLOSED_ISSUE_WITHOUT_DONE_EVIDENCE:{issue.get('number')}")
                continue
        if len(set(machine)) != 1:
            errors.append(f"ISSUE_CONTRACT_INVALID:{issue.get('number')}")
            continue
        parsed, parse_errors = parse_issue_form(
            body, number=issue.get("number"), title=str(issue.get("title") or ""), state=machine[0],
            max_autonomous_risk=str(config.get("policy", {}).get("max_autonomous_risk", "R1")),
        )
        quality = issue_quality(parsed)
        if parse_errors or quality["status"] == "FAIL":
            details = parse_errors + quality["findings"]
            errors.append(f"ISSUE_CONTRACT_INVALID:{issue.get('number')}:" + ",".join(details[:5]))
            continue
        if quality["status"] == "NEEDS_SPLIT" and machine[0] in {"READY", "IN_PROGRESS", "REVIEW", "VERIFICATION"}:
            errors.append(f"ISSUE_NEEDS_SPLIT:{issue.get('number')}")
            continue
        roadmap_id = parsed["roadmap_id"]
        if machine[0] in {"IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY", "DONE"}:
            marker = marker or parse_issue_marker(body)
            if not marker.get("valid"):
                errors.append(f"ACTIVE_ISSUE_MARKER_INVALID:{issue.get('number')}")
                continue
            if marker.get("roadmap_id") != roadmap_id or marker.get("state") != machine[0]:
                errors.append(f"ISSUE_MARKER_SPLIT_BRAIN:{issue.get('number')}")
                continue
            if machine[0] in {"IN_PROGRESS", "REVIEW"}:
                try:
                    heartbeat = parse_time(marker["heartbeat_at"])
                    expires = parse_time(marker["expires_at"])
                    stall_seconds = int(config.get("workspace", {}).get("stall_timeout_minutes", 45)) * 60
                    if heartbeat > now or expires <= now or (now - heartbeat).total_seconds() > stall_seconds:
                        errors.append(f"ACTIVE_ISSUE_LEASE_STALE:{issue.get('number')}")
                except (AttributeError, KeyError, TypeError, ValueError):
                    errors.append(f"ACTIVE_ISSUE_LEASE_TIME_INVALID:{issue.get('number')}")
        roadmap_seen[roadmap_id] = roadmap_seen.get(roadmap_id, 0) + 1
        work_items.append({
            "id": roadmap_id, "roadmap_id": roadmap_id, "number": int(issue.get("number")), "title": parsed["title"],
            "state": machine[0], "priority": parsed["priority"], "risk": parsed["risk"], "type": parsed["type"],
            "goal": parsed["goal"], "conflict_domains": parsed["conflict_domains"], "dependencies": parsed["dependencies"],
            "dependencies_resolved": parsed["dependencies_resolved"], "human_required": parsed["human_required"],
            "autonomy_allowed": parsed["autonomy_allowed"], "product_impact": parsed["product_impact"],
            "roadmap_order": parsed["roadmap_order"], "critical_path_score": 0,
            "ready_since": issue.get("updated_at") if machine[0] == "READY" else None,
            "writer_id": marker.get("writer_id"), "lease_id": marker.get("lease_id"),
            "workspace_id": marker.get("workspace_id"), "heartbeat_at": marker.get("heartbeat_at"),
            "expires_at": marker.get("expires_at"), "updated_at": issue.get("updated_at"),
        })
    duplicates = sorted(name for name, count in roadmap_seen.items() if count != 1)
    if duplicates:
        errors.append("ROADMAP_ID_NOT_ONE_TO_ONE:" + ",".join(duplicates))
    state_by_roadmap = {item["roadmap_id"]: item["state"] for item in work_items}
    for item in work_items:
        actual_resolved = all(state_by_roadmap.get(dependency) == "DONE" for dependency in item["dependencies"])
        if item["dependencies_resolved"] is not actual_resolved:
            errors.append(f"DEPENDENCY_STATUS_MISMATCH:{item['roadmap_id']}")

    counts = {name: 0 for name in ("READY", "IN_PROGRESS", "REVIEW", "VERIFICATION", "BLOCKED", "HUMAN_REQUIRED")}
    for item in work_items:
        if item["state"] in counts:
            counts[item["state"]] += 1
    active_items = [item for item in work_items if item["state"] in {"IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY"}]
    if len(active_items) > 1:
        errors.append("MULTIPLE_ACTIVE_ITEMS")

    active = {"roadmap_id": None, "issue": None, "pr": None, "branch": None, "writer": None, "lease_id": None, "state": None}
    if len(active_items) == 1:
        item = active_items[0]
        active.update({"roadmap_id": item["roadmap_id"], "issue": int(item["number"]), "state": item["state"],
                       "writer": item.get("writer_id"), "lease_id": item.get("lease_id")})
        matching_prs = []
        for pull in pulls:
            contract = parse_pr_contract(pull.get("body") or "")
            if (contract.get("valid") and contract.get("issue_number") == item["number"]
                    and contract.get("roadmap_id") == item["roadmap_id"]
                    and str(contract.get("writer_lease", "")).lower() == str(item.get("lease_id", "")).lower()):
                matching_prs.append((pull, contract))
        if len(matching_prs) > 1:
            errors.append("MULTIPLE_PRS_FOR_ACTIVE_ISSUE")
        elif len(matching_prs) == 1:
            pull, contract = matching_prs[0]
            active.update({"pr": int(pull["number"]), "branch": (pull.get("head") or {}).get("ref"), "lease_id": contract.get("writer_lease")})

    runtime_workspaces = [item for item in workspace_registry.get("workspaces", []) if item.get("status") in OCCUPYING]
    workspace = {"status": "NOT_CONFIGURED", "workspace_id": None, "heartbeat_at": None, "expires_at": None,
                 "retry_count": 0, "next_retry_at": None}
    if len(active_items) == 1 and active_items[0].get("workspace_id"):
        declared = active_items[0]
        projected_status = {"IN_PROGRESS": "ACTIVE", "REVIEW": "ACTIVE", "VERIFICATION": "COMPLETED", "RECOVERY": "RECOVERY"}[declared["state"]]
        workspace = {"status": projected_status, "workspace_id": declared.get("workspace_id"),
                     "heartbeat_at": declared.get("heartbeat_at"), "expires_at": declared.get("expires_at"),
                     "retry_count": 0, "next_retry_at": None}
    if len(runtime_workspaces) > 1:
        errors.append("MULTIPLE_RUNTIME_WORKSPACES")
    elif len(runtime_workspaces) == 1:
        item = runtime_workspaces[0]
        workspace = {"status": item.get("status"), "workspace_id": item.get("workspace_id"), "heartbeat_at": item.get("heartbeat_at"),
                     "expires_at": item.get("expires_at"), "retry_count": int(item.get("retry_count", 0)),
                     "next_retry_at": item.get("next_retry_at")}
        if active.get("writer") and active["writer"] != item.get("worker_id"):
            errors.append("WORKSPACE_WRITER_SPLIT_BRAIN")
        active["writer"] = item.get("worker_id") or active.get("writer")
        if active.get("lease_id") and active["lease_id"] != item.get("lease_id"):
            errors.append("WORKSPACE_LEASE_SPLIT_BRAIN")
        if active_items and active_items[0].get("workspace_id") != item.get("workspace_id"):
            errors.append("WORKSPACE_ID_SPLIT_BRAIN")
        active["lease_id"] = item.get("lease_id") or active.get("lease_id")

    observed_at, valid_until = _iso(now), _iso(now + timedelta(minutes=int(config.get("orchestration", {}).get("reconcile_ttl_minutes", 60))))
    metrics = _normalized_runs(runs, observed_at)
    output["provider"] = {"mode": provider, "observed_at": observed_at}
    output["main"] = {"head": main_sha, "health": "PASS" if not errors else "FAIL"}
    output["queue"] = {"ready": counts["READY"], "in_progress": counts["IN_PROGRESS"],
                       "review": counts["REVIEW"] + counts["VERIFICATION"], "blocked": counts["BLOCKED"],
                       "human_required": counts["HUMAN_REQUIRED"]}
    output["work_items"] = work_items
    output["active"] = active
    output["workspace"] = workspace
    declared_leases = [item for item in work_items if item.get("lease_id") and item.get("state") in {"IN_PROGRESS", "REVIEW"}]
    output["orchestration"] = {"writers_active": 1 if counts["IN_PROGRESS"] else 0, "reviewers_active": counts["REVIEW"],
                               "leases_active": 1 if (runtime_workspaces or declared_leases) else 0, "conflicts": len(errors),
                               "merge_train": "BLOCKED" if errors else "IDLE"}
    output["ci_metrics"] = metrics
    usage = output.get("cost_usage", {})
    usage.update({"status": _cost_status(cost),
                  "capability": cost.get("provider"), "observed_at": observed_at})
    usage.update(cost.get("usage") or {})
    output["cost_usage"] = usage
    project_enabled = config.get("github", {}).get("project", {}).get("enabled") is True
    projection_required = provider == "github" and project_enabled and active.get("issue") is not None
    output["project_projection"] = {"status": "NOT_VERIFIED" if projection_required else "N/A",
                                    "observed_at": None, "project_id": None, "item_id": None}
    if cost.get("result") != "ALLOW":
        errors.append("COST_GUARD_BLOCKED:" + ",".join(cost.get("reason_codes", [])))
        decisions.append("Не подтверждён нулевой расход: автоматические provider jobs остановлены.")
    evidence_source = {"provider": provider, "main_sha": main_sha, "issues": work_items,
                       "pulls": [{"number": item.get("number"), "sha": (item.get("head") or {}).get("sha")} for item in pulls],
                       "run_ids": [item.get("id") for item in runs]}
    output["snapshot"] = {"observed_at": observed_at, "valid_until": valid_until, "source_main_sha": main_sha,
                          "evidence_digest": _digest(evidence_source)}
    output["health"]["adwf"] = "VERIFIED" if not errors else "BROKEN"
    output["health"]["roadmap"] = "HEALTHY" if work_items and not errors else ("CRITICAL" if errors else "NOT_VERIFIED")
    output["status"] = "ACTIVE" if not errors else "RECOVERY"
    output["blockers"] = [f"Reconciliation: {error}" for error in errors]
    if output.get("health", {}).get("product") not in {"VERIFIED", "HEALTHY"}:
        output["blockers"].append("Product Health не подтверждён: разрешены только Recovery/Verification.")
    output["owner_decisions"] = decisions
    output["last_reconciled_at"] = observed_at
    output["last_verified_at"] = observed_at if not errors else None
    return output
