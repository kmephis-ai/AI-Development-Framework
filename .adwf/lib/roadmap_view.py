"""Executive Roadmap DAG projection with evidence-gated outcome truth."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re

from .strict_json import loads as strict_loads

IMPLEMENTED = {"REVIEW", "VERIFICATION", "DONE"}
PLANNING = {"BACKLOG", "PLANNED", "SPECIFIED", "READY", "BLOCKED", "HOLD", "HUMAN_REQUIRED"}
BLOCKED_STATES = {"BLOCKED", "RECOVERY", "HUMAN_REQUIRED"}
SAFE_PRODUCT = {"VERIFIED", "HEALTHY"}
SAFE_ADWF = {"VERIFIED", "HEALTHY"}


def _load_template(root: Path) -> dict[str, Any]:
    path = root / ".adwf" / "roadmap.json"
    if not path.is_file():
        return {"schema_version": 1, "goals": []}
    value = strict_loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {"schema_version": 1, "goals": []}


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def validate_roadmap_graph(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate roadmap IDs/edges as one fail-closed DAG."""
    ids = [str(item.get("roadmap_id") or item.get("id") or "") for item in tasks]
    errors: list[str] = []
    seen: set[str] = set()
    duplicate: set[str] = set()
    for roadmap_id in ids:
        if not roadmap_id:
            errors.append("ROADMAP_ID_MISSING")
        elif roadmap_id in seen:
            duplicate.add(roadmap_id)
        seen.add(roadmap_id)
    errors.extend(f"DUPLICATE_ID:{value}" for value in sorted(duplicate))
    known = {value for value in ids if value}
    graph: dict[str, list[str]] = {}
    for item in tasks:
        roadmap_id = str(item.get("roadmap_id") or item.get("id") or "")
        deps = [str(value) for value in (item.get("dependencies") or [])]
        graph[roadmap_id] = deps
        for dependency in deps:
            if dependency == roadmap_id:
                errors.append(f"SELF_DEPENDENCY:{roadmap_id}")
            elif dependency not in known:
                errors.append(f"UNKNOWN_DEPENDENCY:{roadmap_id}:{dependency}")

    color: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dependency in graph.get(node, []):
            if dependency not in graph:
                continue
            if color.get(dependency, 0) == 0:
                visit(dependency)
            elif color.get(dependency) == 1:
                cycle = stack[stack.index(dependency):] + [dependency]
                if cycle not in cycles:
                    cycles.append(cycle)
        stack.pop()
        color[node] = 2

    for node in sorted(graph):
        if node and color.get(node, 0) == 0:
            visit(node)
    errors.extend("DEPENDENCY_CYCLE:" + "->".join(cycle) for cycle in cycles)
    return {"status": "PASS" if not errors else "FAIL", "errors": sorted(set(errors)), "cycles": cycles}


def _evidence_fresh(state: dict[str, Any], *, now: datetime) -> bool:
    main = state.get("main") or {}
    snapshot = state.get("snapshot") or {}
    head = str(main.get("head") or "")
    observed = _utc(snapshot.get("observed_at"))
    valid_until = _utc(snapshot.get("valid_until"))
    return bool(
        re.fullmatch(r"[0-9a-f]{40}", head)
        and main.get("health") == "PASS"
        and snapshot.get("source_main_sha") == head
        and observed is not None
        and valid_until is not None
        and observed <= now <= valid_until
        and (state.get("health") or {}).get("adwf") in SAFE_ADWF
    )


def derive_verified_progress(
    tasks: list[dict[str, Any]], state: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Derive three progress axes; DONE alone is never verification evidence."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    total = len(tasks)
    evidence_fresh = _evidence_fresh(state, now=now)
    implemented = sum(1 for item in tasks if str(item.get("state") or "PLANNED") in IMPLEMENTED)
    verified_ids = {
        str(item.get("roadmap_id") or item.get("id") or "")
        for item in tasks
        if evidence_fresh and str(item.get("state") or "") == "DONE"
    }
    product_tasks = [item for item in tasks if item.get("product_impact", True) is True]
    product_healthy = (state.get("health") or {}).get("product") in SAFE_PRODUCT
    outcome_ids = {
        str(item.get("roadmap_id") or item.get("id") or "")
        for item in product_tasks
        if product_healthy and str(item.get("roadmap_id") or item.get("id") or "") in verified_ids
    }

    def ratio(value: int, denominator: int) -> float:
        return round(value / denominator, 3) if denominator else 0.0

    implementation = ratio(implemented, total)
    verification = ratio(len(verified_ids), total)
    outcome = ratio(len(outcome_ids), len(product_tasks))
    return {
        "implementation": implementation,
        "verification": verification,
        "outcome_readiness": outcome,
        "product_readiness": outcome,
        "verification_gap": round(max(0.0, implementation - verification), 3),
        "evidence_fresh": evidence_fresh,
        "product_health_verified": product_healthy,
        "verified_ids": sorted(verified_ids),
        "outcome_ids": sorted(outcome_ids),
        "false_progress": bool(implementation >= 1.0 and (verification < 1.0 or (product_tasks and outcome < 1.0))),
    }


def critical_path_scores(tasks: list[dict[str, Any]], verified_ids: set[str] | None = None) -> dict[str, int]:
    """Return deterministic downstream depth used by the existing queue sorter."""
    verified_ids = verified_ids or set()
    graph_check = validate_roadmap_graph(tasks)
    if graph_check["status"] != "PASS":
        return {str(item.get("roadmap_id") or item.get("id") or ""): 0 for item in tasks}
    children: dict[str, list[str]] = {}
    ids = {str(item.get("roadmap_id") or item.get("id") or "") for item in tasks}
    for value in ids:
        children[value] = []
    for item in tasks:
        node = str(item.get("roadmap_id") or item.get("id") or "")
        for dependency in item.get("dependencies") or []:
            children[str(dependency)].append(node)
    memo: dict[str, int] = {}

    def score(node: str) -> int:
        if node in memo:
            return memo[node]
        downstream = [score(child) for child in children.get(node, []) if child not in verified_ids]
        memo[node] = 0 if node in verified_ids else (1 + max(downstream, default=0))
        return memo[node]

    return {node: score(node) for node in sorted(ids)}


def _critical_path(tasks: list[dict[str, Any]], verified_ids: set[str]) -> list[str]:
    scores = critical_path_scores(tasks, verified_ids)
    by_id = {str(item.get("roadmap_id") or item.get("id") or ""): item for item in tasks}
    children: dict[str, list[str]] = {node: [] for node in by_id}
    for item in tasks:
        node = str(item.get("roadmap_id") or item.get("id") or "")
        for dependency in item.get("dependencies") or []:
            children[str(dependency)].append(node)
    candidates = [node for node in by_id if node not in verified_ids]
    if not candidates:
        return []
    node = sorted(candidates, key=lambda value: (-scores.get(value, 0), value))[0]
    path: list[str] = []
    while node and node not in path:
        path.append(node)
        next_nodes = [child for child in children.get(node, []) if child not in verified_ids]
        node = sorted(next_nodes, key=lambda value: (-scores.get(value, 0), value))[0] if next_nodes else ""
    return path


def build_roadmap_view(
    root: str | Path, state: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    base = Path(root).resolve()
    template = _load_template(base)
    items = state.get("work_items") or []
    by_id = {str(item.get("roadmap_id") or item.get("id")): item for item in items}
    goals: list[dict[str, Any]] = []
    all_tasks: list[dict[str, Any]] = []
    for goal in template.get("goals") or []:
        tasks: list[dict[str, Any]] = []
        for source in goal.get("tasks") or []:
            roadmap_id = str(source.get("roadmap_id") or "")
            live = by_id.get(roadmap_id, {})
            task = {
                "roadmap_id": roadmap_id,
                "title_ru": source.get("title_ru") or live.get("title") or roadmap_id,
                "state": str(live.get("state") or source.get("state") or "PLANNED"),
                "dependencies": list(live.get("dependencies") if "dependencies" in live else source.get("dependencies") or []),
                "issue": live.get("number"),
                "product_impact": live.get("product_impact", source.get("product_impact", True)),
            }
            tasks.append(task)
            all_tasks.append(task)
        goals.append({"id": goal.get("id"), "title_ru": goal.get("title_ru"), "tasks": tasks})
    known = {task["roadmap_id"] for task in all_tasks}
    for live in items:
        roadmap_id = str(live.get("roadmap_id") or live.get("id") or "")
        if roadmap_id and roadmap_id not in known:
            task = {
                "roadmap_id": roadmap_id,
                "title_ru": live.get("title") or roadmap_id,
                "state": str(live.get("state") or "PLANNED"),
                "dependencies": list(live.get("dependencies") or []),
                "issue": live.get("number"),
                "product_impact": live.get("product_impact", True),
            }
            if not goals:
                goals.append({"id": "live", "title_ru": "Текущая дорожная карта", "tasks": []})
            goals[-1]["tasks"].append(task)
            all_tasks.append(task)

    graph = validate_roadmap_graph(all_tasks)
    progress = derive_verified_progress(all_tasks, state, now=now)
    verified_ids = set(progress["verified_ids"])
    scores = critical_path_scores(all_tasks, verified_ids)
    ready_frontier: list[str] = []
    blocked_count = 0
    for task in all_tasks:
        blockers = [str(dep) for dep in task["dependencies"] if str(dep) not in verified_ids]
        resolved = graph["status"] == "PASS" and not blockers
        task["dependencies_resolved"] = resolved
        task["blocked_by"] = blockers
        task["critical_path_score"] = scores.get(task["roadmap_id"], 0)
        task["implemented"] = task["state"] in IMPLEMENTED
        task["verified"] = task["roadmap_id"] in verified_ids
        task["outcome_verified"] = task["roadmap_id"] in set(progress["outcome_ids"])
        task["effective_state"] = "BLOCKED" if task["state"] == "READY" and not resolved else task["state"]
        if task["effective_state"] in BLOCKED_STATES or (task["state"] in PLANNING and blockers):
            blocked_count += 1
        if task["state"] in PLANNING and task["state"] not in {"BLOCKED", "HOLD", "HUMAN_REQUIRED"} and resolved:
            ready_frontier.append(task["roadmap_id"])

    active = next((task for task in all_tasks if task["state"] in {"IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY"}), None)
    critical_path = _critical_path(all_tasks, verified_ids) if graph["status"] == "PASS" else []
    summary = {
        "total": len(all_tasks),
        "implemented": progress["implementation"],
        "verified": progress["verification"],
        "product_done": progress["outcome_readiness"],
        "outcome_ready": progress["outcome_readiness"],
        "verification_gap": progress["verification_gap"],
        "evidence_fresh": progress["evidence_fresh"],
        "product_health_verified": progress["product_health_verified"],
        "false_progress": progress["false_progress"],
        "blocked": blocked_count,
        "active": active,
        "graph_status": graph["status"],
        "graph_errors": graph["errors"],
        "ready_frontier": sorted(ready_frontier),
        "critical_path": critical_path,
    }
    return {"schema_version": 1, "goals": goals, "summary": summary}
