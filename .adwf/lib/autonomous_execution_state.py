"""Provider-neutral durable autonomous execution state for ADWF.

The projection records public-safe observations needed by orchestration. It never
becomes work authority: provider facts, Roadmap contracts and writer leases remain
canonical and must be re-read before mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import copy
import hashlib
import json
import re

SCHEMA_VERSION = 1
EXECUTION_STATES = {
    "RUNNING", "WAITING_CI", "SUSPENDED", "HUMAN_REQUIRED", "RECOVERY", "COMPLETE"
}
LEASE_STATES = {"ACTIVE", "SUSPENDED", "RELEASED", "EXPIRED", "NONE"}
BOUNDARY_TYPES = {
    "NONE", "WAITING_EXTERNAL", "HUMAN_REQUIRED", "UNAVAILABLE_CAPABILITY",
    "AUTHORITY_EXHAUSTED", "ROADMAP_END", "EXECUTOR_LIMIT"
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
FORBIDDEN_MARKERS = (
    "chain-of-thought", "chain of thought", "private reasoning", "private_reasoning",
    "authorization: bearer", "api_key", "access_token", "refresh_token", "client_secret",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _contains_forbidden(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    return any(marker in text for marker in FORBIDDEN_MARKERS)


def _valid_sha(value: Any, *, nullable: bool = True) -> bool:
    if value is None:
        return nullable
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _normalize_domains(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("CONFLICT_DOMAINS_REQUIRED")
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip() or len(item) > 240:
            raise ValueError("CONFLICT_DOMAIN_INVALID")
        value = item.strip()
        if value in normalized:
            raise ValueError("CONFLICT_DOMAIN_DUPLICATE")
        normalized.append(value)
    return sorted(normalized)


def _integrity_payload(value: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(value)
    payload.pop("integrity_digest", None)
    return payload


def validate_state(value: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    required = {
        "$schema", "schema_version", "project_identity", "work_identity", "writer",
        "conflict_domains", "provider_observation", "execution_state", "boundary_type",
        "blockers", "last_verified_transition", "evidence_refs", "next_permitted_action",
        "revision", "integrity_digest",
    }
    allowed = set(required) | {"executor_audit"}
    if set(value) - allowed:
        findings.append("UNKNOWN_FIELDS")
    if required - set(value):
        findings.append("REQUIRED_FIELDS")
    if value.get("schema_version") != SCHEMA_VERSION:
        findings.append("SCHEMA_VERSION")
    if value.get("$schema") != ".adwf/schemas/autonomous-execution-state.schema.json":
        findings.append("SCHEMA_REF")

    project = value.get("project_identity")
    if not isinstance(project, dict) or not isinstance(project.get("repository"), str) or "/" not in project.get("repository", ""):
        findings.append("PROJECT_IDENTITY")

    work = value.get("work_identity")
    if not isinstance(work, dict) or not all(isinstance(work.get(k), str) and work.get(k) for k in ("roadmap_id", "issue_id")):
        findings.append("WORK_IDENTITY")

    writer = value.get("writer")
    if not isinstance(writer, dict) or writer.get("lease_state") not in LEASE_STATES:
        findings.append("WRITER")
    else:
        lease_id = writer.get("lease_id")
        if writer.get("lease_state") == "NONE":
            if lease_id is not None:
                findings.append("LEASE_NONE_WITH_ID")
        elif writer.get("lease_state") in {"ACTIVE", "SUSPENDED"}:
            if not isinstance(lease_id, str) or UUID_RE.fullmatch(lease_id) is None:
                findings.append("LEASE_ID")

    try:
        if _normalize_domains(value.get("conflict_domains")) != value.get("conflict_domains"):
            findings.append("CONFLICT_DOMAIN_ORDER")
    except ValueError as exc:
        findings.append(str(exc))

    observed = value.get("provider_observation")
    if not isinstance(observed, dict):
        findings.append("PROVIDER_OBSERVATION")
    else:
        if not _valid_sha(observed.get("main_sha"), nullable=False):
            findings.append("MAIN_SHA")
        if not _valid_sha(observed.get("head_sha")):
            findings.append("HEAD_SHA")
        if observed.get("pr_number") is not None and (not isinstance(observed.get("pr_number"), int) or observed["pr_number"] < 1):
            findings.append("PR_NUMBER")
        if observed.get("branch") is not None and not isinstance(observed.get("branch"), str):
            findings.append("BRANCH")

    execution_state = value.get("execution_state")
    boundary = value.get("boundary_type")
    if execution_state not in EXECUTION_STATES:
        findings.append("EXECUTION_STATE")
    if boundary not in BOUNDARY_TYPES:
        findings.append("BOUNDARY_TYPE")
    if execution_state == "RUNNING" and boundary != "NONE":
        findings.append("RUNNING_BOUNDARY_CONFLICT")
    if execution_state == "WAITING_CI" and boundary != "WAITING_EXTERNAL":
        findings.append("WAITING_CI_BOUNDARY")
    if execution_state == "HUMAN_REQUIRED" and boundary != "HUMAN_REQUIRED":
        findings.append("HUMAN_REQUIRED_BOUNDARY")
    if execution_state == "COMPLETE" and value.get("next_permitted_action") not in {"RECONCILE_NEXT", "NONE"}:
        findings.append("COMPLETE_NEXT_ACTION")

    if not isinstance(value.get("blockers"), list) or not all(isinstance(x, str) and len(x) <= 240 for x in value.get("blockers", [])):
        findings.append("BLOCKERS")
    if not isinstance(value.get("evidence_refs"), list) or not all(isinstance(x, str) and len(x) <= 500 for x in value.get("evidence_refs", [])):
        findings.append("EVIDENCE_REFS")
    if not isinstance(value.get("last_verified_transition"), str) or not value.get("last_verified_transition"):
        findings.append("LAST_VERIFIED_TRANSITION")
    if not isinstance(value.get("next_permitted_action"), str) or not value.get("next_permitted_action"):
        findings.append("NEXT_PERMITTED_ACTION")
    if not isinstance(value.get("revision"), int) or value.get("revision", -1) < 0:
        findings.append("REVISION")
    if _contains_forbidden(value):
        findings.append("FORBIDDEN_CONTENT")
    if value.get("integrity_digest") != _digest(_integrity_payload(value)):
        findings.append("INTEGRITY_DIGEST")
    return findings


def build_state(
    *,
    repository: str,
    roadmap_id: str,
    issue_id: str,
    lease_id: str | None,
    lease_state: str,
    conflict_domains: list[str],
    main_sha: str,
    head_sha: str | None = None,
    pr_number: int | None = None,
    branch: str | None = None,
    execution_state: str = "RUNNING",
    boundary_type: str = "NONE",
    blockers: list[str] | None = None,
    last_verified_transition: str = "FRESH_RECONCILE",
    evidence_refs: list[str] | None = None,
    next_permitted_action: str = "CONTINUE",
    revision: int = 0,
    executor_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "$schema": ".adwf/schemas/autonomous-execution-state.schema.json",
        "schema_version": SCHEMA_VERSION,
        "project_identity": {"repository": repository},
        "work_identity": {"roadmap_id": roadmap_id, "issue_id": str(issue_id)},
        "writer": {"lease_id": lease_id, "lease_state": lease_state},
        "conflict_domains": _normalize_domains(conflict_domains),
        "provider_observation": {
            "main_sha": main_sha,
            "head_sha": head_sha,
            "pr_number": pr_number,
            "branch": branch,
        },
        "execution_state": execution_state,
        "boundary_type": boundary_type,
        "blockers": list(blockers or []),
        "last_verified_transition": last_verified_transition,
        "evidence_refs": list(evidence_refs or []),
        "next_permitted_action": next_permitted_action,
        "revision": int(revision),
        "integrity_digest": "",
    }
    if executor_audit is not None:
        value["executor_audit"] = copy.deepcopy(executor_audit)
    value["integrity_digest"] = _digest(_integrity_payload(value))
    findings = validate_state(value)
    if findings:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_INVALID:" + ",".join(findings))
    return value


def reconcile_provider_observation(
    state: dict[str, Any], *, main_sha: str, head_sha: str | None, pr_number: int | None, branch: str | None
) -> dict[str, Any]:
    findings = validate_state(state)
    if findings:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_INVALID:" + ",".join(findings))
    current = state["provider_observation"]
    stale = (
        current.get("main_sha") != main_sha
        or current.get("head_sha") != head_sha
        or current.get("pr_number") != pr_number
        or current.get("branch") != branch
    )
    return {
        "stale": stale,
        "observed": copy.deepcopy(current),
        "fresh": {"main_sha": main_sha, "head_sha": head_sha, "pr_number": pr_number, "branch": branch},
        "write_authorized": not stale,
    }


def cas_update(state: dict[str, Any], *, expected_revision: int, changes: dict[str, Any]) -> dict[str, Any]:
    findings = validate_state(state)
    if findings:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_INVALID:" + ",".join(findings))
    if state["revision"] != expected_revision:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_REVISION_CONFLICT")
    forbidden = {"schema_version", "$schema", "project_identity", "work_identity"}
    if forbidden & set(changes):
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_IDENTITY_IMMUTABLE")
    updated = copy.deepcopy(state)
    for key, value in changes.items():
        if key not in updated or key == "integrity_digest":
            raise ValueError("AUTONOMOUS_EXECUTION_STATE_CHANGE_INVALID")
        updated[key] = copy.deepcopy(value)
    updated["revision"] = expected_revision + 1
    updated["integrity_digest"] = _digest(_integrity_payload(updated))
    findings = validate_state(updated)
    if findings:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_INVALID:" + ",".join(findings))
    return updated


def owner_projection(state: dict[str, Any]) -> dict[str, Any]:
    findings = validate_state(state)
    if findings:
        raise ValueError("AUTONOMOUS_EXECUTION_STATE_INVALID:" + ",".join(findings))
    return {
        "project": state["project_identity"]["repository"],
        "work_item": state["work_identity"]["roadmap_id"],
        "issue": state["work_identity"]["issue_id"],
        "writer_lease_state": state["writer"]["lease_state"],
        "execution_state": state["execution_state"],
        "boundary": state["boundary_type"],
        "blocker": state["blockers"][0] if state["blockers"] else None,
        "last_verified_transition": state["last_verified_transition"],
        "next_allowed_action": state["next_permitted_action"],
    }
