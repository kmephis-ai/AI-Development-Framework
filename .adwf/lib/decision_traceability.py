"""Durable requirement/decision traceability without creating a second SSOT.

The graph stores rationale records and typed references only. Capability Truth,
Roadmap/AIWorkPackage and Evidence Graph remain authoritative for their own
payloads. Runtime Work Memory and chat are never long-term decision truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import re
import subprocess

from .contracts import validate
from .evidence import parse_time, read_evidence_graph
from .strict_json import loads as strict_loads

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
AIWP_RE = re.compile(r"^AIWP-[0-9a-f]{24}$")
RECORD_PREFIX = {
    "OWNER_INTENT_REF": "INTENT-",
    "REQUIREMENT": "REQ-",
    "DECISION": "DEC-",
}
RECORD_STATUS = {
    "OWNER_INTENT_REF": {"ACTIVE"},
    "REQUIREMENT": {"ACTIVE", "WITHDRAWN"},
    "DECISION": {"ACCEPTED", "REJECTED", "DEFERRED"},
}
EDGE_ENDPOINTS = {
    "INTENT_TO_REQUIREMENT": ("OWNER_INTENT_REF", "REQUIREMENT"),
    "REQUIREMENT_TO_DECISION": ("REQUIREMENT", "DECISION"),
    "DECISION_TO_CAPABILITY": ("DECISION", "CAPABILITY_REF"),
    "CAPABILITY_TO_WORK": ("CAPABILITY_REF", "WORK_UNIT_REF"),
    "WORK_TO_EVIDENCE": ("WORK_UNIT_REF", "EVIDENCE_REF"),
    "REQUIREMENT_SUPERSEDES_REQUIREMENT": ("REQUIREMENT", "REQUIREMENT"),
    "DECISION_SUPERSEDES_DECISION": ("DECISION", "DECISION"),
}
SUPERSESSION_EDGES = {"REQUIREMENT_SUPERSEDES_REQUIREMENT", "DECISION_SUPERSEDES_DECISION"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _without(value: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in names}


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value and not path.is_absolute() and "\\" not in value and all(part not in {"", ".", ".."} for part in path.parts))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal_record(record: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(record)
    value["record_sha256"] = _digest(_without(value, "record_sha256"))
    return value


def seal_reference(reference: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(reference)
    value["ref_sha256"] = _digest(_without(value, "ref_sha256"))
    return value


def seal_edge(edge: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(edge)
    value["edge_sha256"] = _digest(_without(value, "edge_sha256"))
    return value


def seal_graph(graph: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(graph)
    value["records"] = [seal_record(item) for item in value.get("records", [])]
    for field in ("capability_refs", "work_unit_refs", "evidence_refs"):
        value[field] = [seal_reference(item) for item in value.get(field, [])]
    value["edges"] = [seal_edge(item) for item in value.get("edges", [])]
    value["graph_sha256"] = _digest(_without(value, "graph_sha256"))
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = strict_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("TRACE_JSON_NOT_OBJECT")
    return value


def _roadmap_ids(root: Path) -> set[str]:
    roadmap = _load_json(root / ".adwf/roadmap.json")
    return {
        str(task.get("roadmap_id") or "")
        for goal in roadmap.get("goals") or []
        for task in goal.get("tasks") or []
        if task.get("roadmap_id")
    }


def _capability_ids(root: Path) -> set[str]:
    truth = _load_json(root / ".adwf/capability-traceability.json")
    return {str(item.get("id") or "") for item in truth.get("capabilities") or [] if item.get("id")}


def _node_index(graph: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    nodes: dict[str, str] = {}
    errors: list[str] = []
    groups = [
        ("records", None),
        ("capability_refs", "CAPABILITY_REF"),
        ("work_unit_refs", "WORK_UNIT_REF"),
        ("evidence_refs", "EVIDENCE_REF"),
    ]
    for field, fixed_kind in groups:
        for item in graph.get(field) or []:
            node_id = str(item.get("id") or "")
            kind = fixed_kind or str(item.get("kind") or "")
            if not node_id:
                errors.append(f"TRACE_NODE_ID_MISSING:{field}")
            elif node_id in nodes:
                errors.append(f"TRACE_NODE_DUPLICATE:{node_id}")
            else:
                nodes[node_id] = kind
    return nodes, errors


def _validate_digests(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for record in graph.get("records") or []:
        if record.get("record_sha256") != _digest(_without(record, "record_sha256")):
            errors.append("TRACE_RECORD_DIGEST_MISMATCH:" + str(record.get("id") or "?"))
    for field in ("capability_refs", "work_unit_refs", "evidence_refs"):
        for ref in graph.get(field) or []:
            if ref.get("ref_sha256") != _digest(_without(ref, "ref_sha256")):
                errors.append("TRACE_REF_DIGEST_MISMATCH:" + str(ref.get("id") or "?"))
    for edge in graph.get("edges") or []:
        if edge.get("edge_sha256") != _digest(_without(edge, "edge_sha256")):
            errors.append("TRACE_EDGE_DIGEST_MISMATCH:" + str(edge.get("id") or "?"))
    if graph.get("graph_sha256") != _digest(_without(graph, "graph_sha256")):
        errors.append("TRACE_GRAPH_DIGEST_MISMATCH")
    return errors


def _validate_records(graph: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    for record in graph.get("records") or []:
        rid = str(record.get("id") or "")
        kind = str(record.get("kind") or "")
        if kind not in RECORD_PREFIX or not rid.startswith(RECORD_PREFIX.get(kind, "!")):
            errors.append(f"TRACE_RECORD_KIND_ID_MISMATCH:{rid}:{kind}")
        if record.get("status") not in RECORD_STATUS.get(kind, set()):
            errors.append(f"TRACE_RECORD_STATUS_INVALID:{rid}")
        if not isinstance(record.get("version"), int) or isinstance(record.get("version"), bool) or int(record.get("version", 0)) < 1:
            errors.append(f"TRACE_RECORD_VERSION_INVALID:{rid}")
        source_path = record.get("source_path")
        source_digest = record.get("source_sha256")
        if kind == "OWNER_INTENT_REF":
            if not isinstance(source_path, str) or not _safe_relative(source_path):
                errors.append(f"TRACE_INTENT_SOURCE_PATH_INVALID:{rid}")
                continue
            path = (root / source_path).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                errors.append(f"TRACE_INTENT_SOURCE_ESCAPES_ROOT:{rid}")
                continue
            if not path.is_file():
                errors.append(f"TRACE_INTENT_SOURCE_MISSING:{rid}:{source_path}")
            elif source_digest != _sha256_file(path):
                errors.append(f"TRACE_INTENT_SOURCE_DIGEST_MISMATCH:{rid}")
        elif source_path is not None or source_digest is not None:
            errors.append(f"TRACE_NON_INTENT_SOURCE_FIELDS_FORBIDDEN:{rid}")
    return errors


def _validate_external_refs(graph: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    try:
        capabilities = _capability_ids(root)
    except (OSError, ValueError, json.JSONDecodeError):
        capabilities = set()
        errors.append("TRACE_CAPABILITY_TRUTH_UNREADABLE")
    for ref in graph.get("capability_refs") or []:
        if str(ref.get("capability_id") or "") not in capabilities:
            errors.append("TRACE_CAPABILITY_REF_UNKNOWN:" + str(ref.get("id") or "?"))
    try:
        roadmap = _roadmap_ids(root)
    except (OSError, ValueError, json.JSONDecodeError):
        roadmap = set()
        errors.append("TRACE_ROADMAP_UNREADABLE")
    for ref in graph.get("work_unit_refs") or []:
        if str(ref.get("roadmap_id") or "") not in roadmap:
            errors.append("TRACE_WORK_REF_UNKNOWN_ROADMAP:" + str(ref.get("id") or "?"))
        issue = ref.get("issue_number")
        if issue is not None and (not isinstance(issue, int) or isinstance(issue, bool) or issue < 1):
            errors.append("TRACE_WORK_REF_ISSUE_INVALID:" + str(ref.get("id") or "?"))
        package = ref.get("ai_work_package_id")
        if package is not None and AIWP_RE.fullmatch(str(package)) is None:
            errors.append("TRACE_WORK_REF_PACKAGE_INVALID:" + str(ref.get("id") or "?"))
    return errors


def _validate_edges(graph: dict[str, Any], nodes: dict[str, str]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_tuples: set[tuple[str, str, str]] = set()
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    superseded_by: dict[str, list[str]] = {}
    for edge in graph.get("edges") or []:
        eid = str(edge.get("id") or "")
        etype = str(edge.get("type") or "")
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if eid in seen_ids:
            errors.append("TRACE_EDGE_ID_DUPLICATE:" + eid)
        seen_ids.add(eid)
        triple = (etype, source, target)
        if triple in seen_tuples:
            errors.append("TRACE_EDGE_DUPLICATE:" + eid)
        seen_tuples.add(triple)
        if source == target:
            errors.append("TRACE_EDGE_SELF_REFERENCE:" + eid)
        if source not in nodes:
            errors.append("TRACE_EDGE_DANGLING_FROM:" + eid + ":" + source)
        if target not in nodes:
            errors.append("TRACE_EDGE_DANGLING_TO:" + eid + ":" + target)
        expected = EDGE_ENDPOINTS.get(etype)
        if expected is None:
            errors.append("TRACE_EDGE_TYPE_INVALID:" + eid)
        elif source in nodes and target in nodes and (nodes[source], nodes[target]) != expected:
            errors.append(f"TRACE_EDGE_ENDPOINT_TYPE_INVALID:{eid}:{nodes[source]}->{nodes[target]}")
        if source in adjacency and target in nodes:
            adjacency[source].append(target)
        if etype in SUPERSESSION_EDGES and source in nodes and target in nodes:
            superseded_by.setdefault(target, []).append(source)
            source_record = next((item for item in graph.get("records") or [] if item.get("id") == source), None)
            target_record = next((item for item in graph.get("records") or [] if item.get("id") == target), None)
            if source_record and target_record and int(source_record.get("version", 0)) <= int(target_record.get("version", 0)):
                errors.append("TRACE_SUPERSESSION_VERSION_NOT_INCREASING:" + eid)
    for target, replacements in superseded_by.items():
        if len(replacements) > 1:
            errors.append("TRACE_SUPERSESSION_AMBIGUOUS:" + target)

    color: dict[str, int] = {}
    stack: list[str] = []
    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in adjacency.get(node, []):
            if color.get(nxt, 0) == 0:
                visit(nxt)
            elif color.get(nxt) == 1:
                errors.append("TRACE_CYCLE:" + "->".join(stack[stack.index(nxt):] + [nxt]))
        stack.pop()
        color[node] = 2
    for node in sorted(nodes):
        if color.get(node, 0) == 0:
            visit(node)
    return errors


def _evidence_status(graph: dict[str, Any], root: Path, *, now: datetime) -> tuple[set[str], list[str]]:
    refs = graph.get("evidence_refs") or []
    if not refs:
        return set(), []
    evidence_root = root
    try:
        evidence_graph = read_evidence_graph(evidence_root, now=now)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return set(), ["TRACE_EVIDENCE_GRAPH_UNREADABLE:" + type(exc).__name__]
    if not evidence_graph.get("valid"):
        return set(), ["TRACE_EVIDENCE_GRAPH_INVALID:" + item for item in evidence_graph.get("errors") or []]
    events = {str(item.get("id") or ""): item for item in evidence_graph.get("events") or []}
    verified: set[str] = set()
    errors: list[str] = []
    for ref in refs:
        rid = str(ref.get("id") or "")
        event = events.get(str(ref.get("evidence_id") or ""))
        if event is None:
            errors.append("TRACE_EVIDENCE_REF_MISSING:" + rid)
            continue
        if event.get("subject") != ref.get("subject") or event.get("sha") != ref.get("sha"):
            errors.append("TRACE_EVIDENCE_REF_BINDING_MISMATCH:" + rid)
            continue
        if event.get("status") != "PASS":
            errors.append("TRACE_EVIDENCE_REF_NOT_PASS:" + rid)
            continue
        try:
            if parse_time(str(event.get("expires_at") or "")) <= now:
                errors.append("TRACE_EVIDENCE_REF_STALE:" + rid)
                continue
        except ValueError:
            errors.append("TRACE_EVIDENCE_REF_TIME_INVALID:" + rid)
            continue
        verified.add(rid)
    return verified, errors


def project_traceability(graph: dict[str, Any], root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Project stable coverage and live evidence verification from canonical links."""
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    nodes, node_errors = _node_index(graph)
    edges = graph.get("edges") or []
    incoming: dict[str, list[dict[str, Any]]] = {node: [] for node in nodes}
    outgoing: dict[str, list[dict[str, Any]]] = {node: [] for node in nodes}
    for edge in edges:
        source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
        if source in outgoing:
            outgoing[source].append(edge)
        if target in incoming:
            incoming[target].append(edge)
    records = {str(item.get("id") or ""): item for item in graph.get("records") or []}
    requirements = sorted(rid for rid, item in records.items() if item.get("kind") == "REQUIREMENT")
    decisions = sorted(rid for rid, item in records.items() if item.get("kind") == "DECISION")
    work_ids = sorted(str(item.get("id") or "") for item in graph.get("work_unit_refs") or [])
    evidence_ids = sorted(str(item.get("id") or "") for item in graph.get("evidence_refs") or [])

    orphan_requirements = sorted(rid for rid in requirements if not any(e.get("type") == "INTENT_TO_REQUIREMENT" for e in incoming.get(rid, [])))
    orphan_decisions = sorted(rid for rid in decisions if not any(e.get("type") == "REQUIREMENT_TO_DECISION" for e in incoming.get(rid, [])))
    missing_upstream = sorted(wid for wid in work_ids if not any(e.get("type") == "CAPABILITY_TO_WORK" for e in incoming.get(wid, [])))
    missing_downstream = sorted(wid for wid in work_ids if not any(e.get("type") == "WORK_TO_EVIDENCE" for e in outgoing.get(wid, [])))
    verified_evidence, evidence_errors = _evidence_status(graph, base, now=now)
    unverified_evidence = sorted(set(evidence_ids) - verified_evidence)

    structural_incomplete = bool(node_errors or orphan_requirements or orphan_decisions or missing_upstream or missing_downstream)
    if structural_incomplete:
        status = "INCOMPLETE"
    elif evidence_errors or unverified_evidence:
        status = "STRUCTURED_NOT_VERIFIED"
    else:
        status = "VERIFIED"
    return {
        "status": status,
        "orphan_requirements": orphan_requirements,
        "orphan_decisions": orphan_decisions,
        "orphan_work_units": missing_upstream,
        "missing_upstream_rationale": missing_upstream,
        "missing_downstream_evidence": missing_downstream,
        "unverified_evidence_refs": unverified_evidence,
        "verified_evidence_refs": sorted(verified_evidence),
        "evidence_errors": evidence_errors,
    }


def validate_revision_transition(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Existing records/references/edges are immutable; supersession is additive."""
    if current == previous:
        return []
    errors: list[str] = []
    if int(current.get("revision", 0)) <= int(previous.get("revision", 0)):
        errors.append("TRACE_REVISION_NOT_INCREASING")
    for field in ("records", "capability_refs", "work_unit_refs", "evidence_refs", "edges"):
        old = {str(item.get("id") or ""): item for item in previous.get(field) or []}
        new = {str(item.get("id") or ""): item for item in current.get(field) or []}
        for item_id, old_value in old.items():
            if item_id not in new:
                errors.append(f"TRACE_IMMUTABLE_ITEM_REMOVED:{field}:{item_id}")
            elif new[item_id] != old_value:
                errors.append(f"TRACE_IMMUTABLE_ITEM_CHANGED:{field}:{item_id}")
    return errors


def _git_previous_graph(root: Path) -> dict[str, Any] | None:
    """Read the preceding graph revision; current HEAD is never its own predecessor."""
    candidates: list[str] = []
    head: str | None = None
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, timeout=5
        )
        if process.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", process.stdout.strip()):
            head = process.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    def add_candidate(value: str | None) -> None:
        if value and re.fullmatch(r"[0-9a-f]{40}", value) and value != head:
            candidates.append(value)

    env_base = __import__("os").environ.get("ADWF_BASE_SHA")
    add_candidate(env_base)
    try:
        process = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"], cwd=root, capture_output=True, text=True, check=False, timeout=5
        )
        if process.returncode == 0:
            add_candidate(process.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD^"], cwd=root, capture_output=True, text=True, check=False, timeout=5
        )
        if process.returncode == 0:
            add_candidate(process.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for revision in dict.fromkeys(candidates):
        process = subprocess.run(
            ["git", "show", f"{revision}:.adwf/decision-requirement-traceability.json"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if process.returncode == 0:
            value = strict_loads(process.stdout)
            return value if isinstance(value, dict) else None
    return None


def validate_traceability_graph(
    graph: dict[str, Any], *, root: str | Path, schema: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []
    if schema is not None:
        errors.extend(f"SCHEMA:{item.path}:{item.code}" for item in validate(graph, schema))
    if graph.get("schema_version") != 1 or graph.get("role") != "CANONICAL_DECISION_REQUIREMENT_TRACEABILITY":
        errors.append("TRACE_GRAPH_ROLE_OR_VERSION_INVALID")
    nodes, node_errors = _node_index(graph)
    errors.extend(node_errors)
    errors.extend(_validate_digests(graph))
    errors.extend(_validate_records(graph, base))
    errors.extend(_validate_external_refs(graph, base))
    errors.extend(_validate_edges(graph, nodes))
    if previous is not None:
        errors.extend(validate_revision_transition(previous, graph))
    projection = project_traceability(graph, base, now=now)
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "projection": projection}


def validate_repository_traceability(root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    base = Path(root).resolve()
    graph = _load_json(base / ".adwf/decision-requirement-traceability.json")
    schema = _load_json(base / ".adwf/schemas/decision-requirement-traceability.schema.json")
    previous = _git_previous_graph(base)
    return validate_traceability_graph(graph, root=base, schema=schema, previous=previous, now=now)
