"""Provider-neutral idle bootstrap for one Roadmap-authorized durable run.

Fresh provider facts are supplied by a trusted adapter. This module only
normalizes those facts, reuses the canonical orchestration authorization
boundary, and atomically creates at most one durable run + Work Memory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from .assurance import validate_assurance_snapshot
from .durable_orchestrator import OrchestrationJournal, new_run
from .file_lock import exclusive_file_lock
from .lease_registry import validate_lease_registry
from .orchestration import authorize_next_action
from .policy_runtime import load_effective_policy
from .work_memory import WorkMemoryStore, new_work_memory

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SAFE_HEALTH = {"HEALTHY", "VERIFIED"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ORCH_NEXT_TIME_NAIVE")
    return parsed.astimezone(timezone.utc)


def _validate_fresh_inputs(
    root: Path,
    project_state: dict[str, Any],
    lease_registry: dict[str, Any],
    assurance_snapshot: dict[str, Any],
    provider_readback: dict[str, Any],
    *,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(project_state, dict):
        raise ValueError("ORCH_NEXT_PROJECT_STATE_INVALID")
    main_sha = str((project_state.get("main") or {}).get("head") or "")
    source_sha = str((project_state.get("snapshot") or {}).get("source_main_sha") or "")
    if _SHA40.fullmatch(main_sha) is None or source_sha != main_sha:
        raise ValueError("ORCH_NEXT_MAIN_BINDING_INVALID")
    if project_state.get("status") != "ACTIVE" or (project_state.get("health") or {}).get("adwf") not in _SAFE_HEALTH:
        raise ValueError("ORCH_NEXT_RECONCILIATION_NOT_HEALTHY")
    snapshot = project_state.get("snapshot") or {}
    try:
        observed = _parse_time(snapshot.get("observed_at"))
        valid_until = _parse_time(snapshot.get("valid_until"))
    except (TypeError, ValueError) as exc:
        raise ValueError("ORCH_NEXT_SNAPSHOT_TIME_INVALID") from exc
    if observed > now or valid_until <= now:
        raise ValueError("ORCH_NEXT_SNAPSHOT_STALE")

    policy = load_effective_policy(root)
    assurance_errors = validate_assurance_snapshot(
        assurance_snapshot,
        expected_sha=main_sha,
        expected_policy_hash=policy["policy_hash"],
    )
    if assurance_errors:
        raise ValueError("ORCH_NEXT_ASSURANCE_INVALID:" + ",".join(assurance_errors))
    if provider_readback.get("subject_sha") != main_sha or provider_readback.get("facts_readback_verified") is not True:
        raise ValueError("ORCH_NEXT_PROVIDER_READBACK_INVALID")
    if provider_readback.get("repository_visibility") != "PUBLIC" or provider_readback.get("larger_runner") is True:
        raise ValueError("ORCH_NEXT_PROVIDER_PROFILE_INVALID")
    if (assurance_snapshot.get("cost") or {}).get("status") != "VERIFIED_ZERO":
        raise ValueError("ORCH_NEXT_COST_NOT_VERIFIED_ZERO")
    if float((assurance_snapshot.get("cost") or {}).get("projected_cost_usd", -1)) != 0:
        raise ValueError("ORCH_NEXT_COST_NOT_ZERO")

    lease_errors = validate_lease_registry(lease_registry)
    if lease_errors:
        raise ValueError("ORCH_NEXT_LEASE_REGISTRY_INVALID:" + ",".join(lease_errors))
    active_leases = [item for item in lease_registry.get("leases", []) if item.get("status") == "ACTIVE"]
    if lease_registry.get("observed_main_sha") != main_sha and active_leases:
        raise ValueError("ORCH_NEXT_LEASE_MAIN_DRIFT")
    if lease_registry.get("max_parallel_writers") != policy.get("max_parallel_writers"):
        raise ValueError("ORCH_NEXT_LEASE_POLICY_DRIFT")
    return main_sha, policy


def selector_queue(project_state: dict[str, Any], lease_registry: dict[str, Any]) -> dict[str, Any]:
    """Translate reconciliation shape into the canonical selector contract.

    ``project_state['queue']`` contains counters only; selectable items are in
    ``work_items``. Provider leases are used only to block idle bootstrap here;
    continuing an existing writer remains the active-supervisor path.
    """
    work_items = project_state.get("work_items")
    if not isinstance(work_items, list) or not all(isinstance(item, dict) for item in work_items):
        raise ValueError("ORCH_NEXT_WORK_ITEMS_INVALID")
    active = [item for item in lease_registry.get("leases", []) if item.get("status") == "ACTIVE"]
    if active:
        return {"issues": work_items, "leases": active}
    return {"issues": work_items, "leases": []}


def _policy_context(policy: dict[str, Any], assurance_snapshot: dict[str, Any]) -> dict[str, Any]:
    health = dict(assurance_snapshot.get("health") or {})
    return {
        "action": "claim",
        "autonomy": policy.get("active_autonomy"),
        "risk": "R0",
        "max_autonomous_risk": policy.get("max_autonomous_risk"),
        "health": health,
        "provider_allowed": True,
        "provider_potentially_paid": False,
        "projected_cost": 0.0,
        "writer_conflict": False,
        "exact_sha": True,
        "evidence_fresh": (assurance_snapshot.get("evidence") or {}).get("refs_resolved") is True,
        "provider_facts_fresh": True,
    }


def bootstrap_next_run(
    root: str | Path,
    *,
    project_state: dict[str, Any],
    lease_registry: dict[str, Any],
    assurance_snapshot: dict[str, Any],
    provider_readback: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically create at most one durable run from fresh trusted Roadmap facts."""
    base = Path(root).resolve()
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lock = base / ".adwf-runtime" / "orch-next.lock"
    with exclusive_file_lock(lock):
        journal = OrchestrationJournal(base)
        active_runs = journal.list_active()
        if active_runs:
            if len(active_runs) != 1 or active_runs[0].get("status") == "BROKEN":
                return {"status": "BLOCK", "reason": "MULTIPLE_OR_BROKEN_ACTIVE_RUNS", "durable_run_created": False}
            return {
                "status": "ACTIVE_RUN_EXISTS",
                "reason": "DURABLE_RUN_ALREADY_ACTIVE",
                "run_id": active_runs[0].get("run_id"),
                "durable_run_created": False,
            }

        try:
            main_sha, policy = _validate_fresh_inputs(
                base, project_state, lease_registry, assurance_snapshot, provider_readback, now=current_time
            )
        except (TypeError, ValueError) as exc:
            return {"status": "BLOCK", "reason": str(exc)[:240], "durable_run_created": False}

        provider_active = [item for item in lease_registry.get("leases", []) if item.get("status") == "ACTIVE"]
        if provider_active:
            return {
                "status": "WRITER_BUSY",
                "reason": "PROVIDER_WRITER_ACTIVE",
                "lease_id": provider_active[0].get("lease_id") if len(provider_active) == 1 else None,
                "durable_run_created": False,
            }

        queue = selector_queue(project_state, lease_registry)
        decision = authorize_next_action(
            queue,
            _policy_context(policy, assurance_snapshot),
            now=current_time,
            policy_ir=policy,
        )
        if decision.get("action") != "CLAIM_ONE_READY":
            status = "NO_ROADMAP_WORK" if decision.get("action") == "ROADMAP_COMPLETE_OR_EMPTY" else "BLOCK"
            return {
                "status": status,
                "reason": decision.get("reason"),
                "decision": decision.get("action"),
                "reason_codes": decision.get("reason_codes") or [],
                "durable_run_created": False,
            }

        issue = decision.get("issue") or {}
        issue_number = issue.get("number")
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number < 1:
            return {"status": "BLOCK", "reason": "ORCH_NEXT_ISSUE_NUMBER_INVALID", "durable_run_created": False}
        roadmap_id = str(issue.get("roadmap_id") or issue.get("id") or "")
        provenance = {
            "main_sha": main_sha,
            "snapshot_digest": (project_state.get("snapshot") or {}).get("evidence_digest"),
            "lease_registry_revision": lease_registry.get("revision"),
            "roadmap_id": roadmap_id,
            "issue_number": issue_number,
            "policy_hash": policy.get("policy_hash"),
        }
        run = new_run(
            base,
            roadmap_id=roadmap_id,
            issue_id=str(issue_number),
            risk=str(issue.get("risk") or "R1"),
            work_type=str(issue.get("type") or "feature"),
            product_impact=bool(issue.get("product_impact")),
            owner_request_digest=_digest(provenance),
            max_elapsed_minutes=1440,
        )
        task = str(issue.get("goal") or issue.get("title") or f"Roadmap {roadmap_id}").strip()
        memory = new_work_memory(brief_id=roadmap_id, task_ru=task, run_id=run["run_id"])
        memory["status"] = "ACTIVE"
        memory["constraints"] = [
            "FREE_ONLY monetary budget $0.",
            "Fail closed on stale provider facts, policy drift, or writer conflict.",
            f"Exact bootstrap main SHA: {main_sha}.",
        ]
        memory["references"]["issues"] = [issue_number]
        memory["next_action_ru"] = "Продолжить durable run через fresh provider reconciliation и Execution Node."
        WorkMemoryStore(base).save(memory)
        return {
            "status": "RUN_CREATED",
            "reason": "ROADMAP_AUTHORIZED_SUCCESSOR",
            "run_id": run["run_id"],
            "roadmap_id": roadmap_id,
            "issue_id": str(issue_number),
            "main_sha": main_sha,
            "lease_registry_revision": lease_registry.get("revision"),
            "durable_run_created": True,
            "provider_claim_mutation": False,
            "monetary_cost_usd": 0,
        }
