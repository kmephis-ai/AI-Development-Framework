"""Typed provider-neutral writer lease registry and conflict-resource arbitration.

ORCH_LEASE-001 keeps this contract separate from the legacy local queue lease API.
Provider adapters persist this strict registry via compare-and-set.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import copy
import hashlib as _hashlib
import json
import re
import uuid

from .evidence import parse_time
from .leases import DEFAULT_HEARTBEAT_TIMEOUT_MINUTES, iso, utc_now

LEASE_REGISTRY_SCHEMA = ".adwf/schemas/writer-lease-registry.schema.json"
LEASE_REGISTRY_VERSION = 1
LEASE_RESOURCE_KINDS = {
    "source", "projection", "runtime", "data", "provider", "release", "governance"
}
LEASE_REGISTRY_STATUSES = {"ACTIVE", "RELEASED"}
_RESOURCE_SCOPE_RE = re.compile(r"^[A-Za-z0-9_.:@+\-]+(?:/[A-Za-z0-9_.:@+\-]+)*$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_ROADMAP_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-/#]{1,240}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+\-]{0,239}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9_.:@+\-/#]{1,500}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _registry_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("integrity_digest", None)
    return _hashlib.sha256(_canonical_json(payload)).hexdigest()


def _validate_conflict_resource(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, dict) or set(resource) != {"kind", "scope", "shared", "global"}:
        raise ValueError("CONFLICT_RESOURCE_FIELDS_INVALID")
    kind = resource.get("kind")
    scope = resource.get("scope")
    shared = resource.get("shared")
    global_resource = resource.get("global")
    if kind not in LEASE_RESOURCE_KINDS:
        raise ValueError("CONFLICT_RESOURCE_KIND_INVALID")
    if not isinstance(scope, str) or len(scope) > 240 or _RESOURCE_SCOPE_RE.fullmatch(scope) is None:
        raise ValueError("CONFLICT_RESOURCE_SCOPE_INVALID")
    if any(part in {".", ".."} for part in scope.split("/")):
        raise ValueError("CONFLICT_RESOURCE_SCOPE_AMBIGUOUS")
    if not isinstance(shared, bool) or not isinstance(global_resource, bool):
        raise ValueError("CONFLICT_RESOURCE_FLAGS_INVALID")
    if global_resource and scope != "global":
        raise ValueError("CONFLICT_RESOURCE_GLOBAL_SCOPE_INVALID")
    if global_resource and shared is not True:
        raise ValueError("CONFLICT_RESOURCE_GLOBAL_MUST_BE_SHARED")
    if not global_resource and scope == "global":
        raise ValueError("CONFLICT_RESOURCE_SCOPE_AMBIGUOUS")
    return {"kind": kind, "scope": scope, "shared": shared, "global": global_resource}


def canonical_conflict_resources(resources: Any) -> list[dict[str, Any]]:
    if not isinstance(resources, list) or not resources:
        raise ValueError("CONFLICT_RESOURCES_REQUIRED")
    validated = [_validate_conflict_resource(item) for item in resources]
    # Semantic identity is kind+scope. Flags describe authority semantics and
    # may not create two contradictory declarations of the same resource.
    keys = [(item["kind"], item["scope"]) for item in validated]
    if len(keys) != len(set(keys)):
        raise ValueError("CONFLICT_RESOURCE_DUPLICATE")
    return sorted(validated, key=lambda item: (item["kind"], item["scope"], item["shared"], item["global"]))


def require_canonical_conflict_resources(resources: Any) -> list[dict[str, Any]]:
    canonical = canonical_conflict_resources(resources)
    if canonical != resources:
        raise ValueError("CONFLICT_RESOURCES_NOT_CANONICAL")
    return canonical


def _scope_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def conflicting_resource_keys(left: Any, right: Any) -> list[str]:
    """Return deterministic semantic collisions; malformed/ambiguous inputs fail closed."""
    left_items = require_canonical_conflict_resources(left)
    right_items = require_canonical_conflict_resources(right)
    conflicts: set[str] = set()
    for a in left_items:
        for b in right_items:
            if a["global"] or b["global"]:
                conflicts.add("global:global")
                continue
            if a["kind"] != b["kind"]:
                continue
            if _scope_overlap(a["scope"], b["scope"]):
                scope = a["scope"] if len(a["scope"]) <= len(b["scope"]) else b["scope"]
                conflicts.add(f"{a['kind']}:{scope}")
    return sorted(conflicts)


def _valid_branch(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _BRANCH_RE.fullmatch(value) is not None
        and ".." not in value
        and "//" not in value
        and "@{" not in value
        and not value.endswith("/")
    )


def _bounded_ttl_minutes(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 240:
        raise ValueError("LEASE_TTL_INVALID")
    return value


def _registry_lease_freshness_errors(lease: dict[str, Any], now: datetime) -> list[str]:
    errors: list[str] = []
    try:
        expires = parse_time(str(lease["expires_at"]))
        heartbeat_at = parse_time(str(lease["heartbeat_at"]))
        if expires <= now:
            errors.append("LEASE_EXPIRED")
        if heartbeat_at > now:
            errors.append("LEASE_HEARTBEAT_IN_FUTURE")
        elif (now - heartbeat_at).total_seconds() > DEFAULT_HEARTBEAT_TIMEOUT_MINUTES * 60:
            errors.append("LEASE_HEARTBEAT_STALE")
    except (KeyError, TypeError, ValueError):
        errors.append("LEASE_TIME_INVALID")
    return errors


def validate_lease_registry(value: Any) -> list[str]:
    """Strict runtime validator kept independent from jsonschema for provider readback use."""
    findings: list[str] = []
    required = {
        "$schema", "schema_version", "repository", "revision", "observed_main_sha",
        "max_parallel_writers", "leases", "integrity_digest",
    }
    if not isinstance(value, dict):
        return ["LEASE_REGISTRY_NOT_OBJECT"]
    if set(value) != required:
        findings.append("LEASE_REGISTRY_FIELDS_INVALID")
    if value.get("$schema") != LEASE_REGISTRY_SCHEMA:
        findings.append("LEASE_REGISTRY_SCHEMA_REF")
    if value.get("schema_version") != LEASE_REGISTRY_VERSION:
        findings.append("LEASE_REGISTRY_SCHEMA_VERSION")
    repository = value.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        findings.append("LEASE_REGISTRY_REPOSITORY")
    if not isinstance(value.get("revision"), int) or isinstance(value.get("revision"), bool) or value.get("revision", -1) < 0:
        findings.append("LEASE_REGISTRY_REVISION")
    if not isinstance(value.get("observed_main_sha"), str) or _SHA40_RE.fullmatch(value.get("observed_main_sha", "")) is None:
        findings.append("LEASE_REGISTRY_MAIN_SHA")
    ceiling = value.get("max_parallel_writers")
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or not 1 <= ceiling <= 3:
        findings.append("LEASE_REGISTRY_CEILING")
    leases = value.get("leases")
    if not isinstance(leases, list) or len(leases) > 200:
        findings.append("LEASE_REGISTRY_LEASES")
        leases = []
    seen_ids: set[str] = set()
    seen_generations: set[int] = set()
    active_count = 0
    for index, lease in enumerate(leases):
        prefix = f"LEASE_{index}"
        expected = {
            "lease_id", "generation", "issue_id", "roadmap_id", "worker_id", "base_sha",
            "branch", "resources", "status", "claimed_at", "heartbeat_at", "expires_at",
            "released_at", "provider_reconciled_at", "provider_reconciliation_ref",
        }
        if not isinstance(lease, dict) or set(lease) != expected:
            findings.append(prefix + "_FIELDS_INVALID")
            continue
        lease_id = lease.get("lease_id")
        if not isinstance(lease_id, str) or _UUID_RE.fullmatch(lease_id) is None or lease_id in seen_ids:
            findings.append(prefix + "_ID")
        else:
            seen_ids.add(lease_id)
        generation = lease.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1 or generation in seen_generations:
            findings.append(prefix + "_GENERATION")
        else:
            seen_generations.add(generation)
        issue_id = lease.get("issue_id")
        roadmap_id = lease.get("roadmap_id")
        worker_id = lease.get("worker_id")
        branch = lease.get("branch")
        if not isinstance(issue_id, str) or re.fullmatch(r"[0-9]+", issue_id) is None:
            findings.append(prefix + "_ISSUE_ID")
        if not isinstance(roadmap_id, str) or _ROADMAP_ID_RE.fullmatch(roadmap_id) is None:
            findings.append(prefix + "_ROADMAP_ID")
        if not isinstance(worker_id, str) or _SAFE_ID_RE.fullmatch(worker_id) is None:
            findings.append(prefix + "_WORKER_ID")
        if not _valid_branch(branch):
            findings.append(prefix + "_BRANCH")
        if not isinstance(lease.get("base_sha"), str) or _SHA40_RE.fullmatch(lease.get("base_sha", "")) is None:
            findings.append(prefix + "_BASE_SHA")
        try:
            require_canonical_conflict_resources(lease.get("resources"))
        except ValueError as exc:
            findings.append(prefix + "_" + str(exc))
        status = lease.get("status")
        if status not in LEASE_REGISTRY_STATUSES:
            findings.append(prefix + "_STATUS")
        if status == "ACTIVE":
            active_count += 1
            if any(lease.get(key) is not None for key in ("released_at", "provider_reconciled_at", "provider_reconciliation_ref")):
                findings.append(prefix + "_ACTIVE_RELEASE_FIELDS")
        if status == "RELEASED":
            for key in ("released_at", "provider_reconciled_at", "provider_reconciliation_ref"):
                if not isinstance(lease.get(key), str) or not lease.get(key):
                    findings.append(prefix + "_RELEASE_EVIDENCE")
        try:
            claimed = parse_time(str(lease.get("claimed_at")))
            heartbeat_at = parse_time(str(lease.get("heartbeat_at")))
            expires_at = parse_time(str(lease.get("expires_at")))
            if heartbeat_at < claimed or expires_at <= heartbeat_at:
                findings.append(prefix + "_TIME_ORDER")
            if status == "RELEASED":
                released_at = parse_time(str(lease.get("released_at")))
                reconciled_at = parse_time(str(lease.get("provider_reconciled_at")))
                if released_at < heartbeat_at or reconciled_at > released_at:
                    findings.append(prefix + "_RELEASE_TIME_ORDER")
        except (TypeError, ValueError):
            findings.append(prefix + "_TIME_INVALID")
    active_leases = [lease for lease in leases if isinstance(lease, dict) and lease.get("status") == "ACTIVE"]
    if isinstance(ceiling, int) and active_count > ceiling:
        findings.append("LEASE_REGISTRY_ACTIVE_CEILING_EXCEEDED")
    for left_index, left in enumerate(active_leases):
        for right in active_leases[left_index + 1:]:
            if left.get("issue_id") == right.get("issue_id"):
                findings.append("LEASE_REGISTRY_ACTIVE_ISSUE_DUPLICATE")
            if left.get("roadmap_id") == right.get("roadmap_id"):
                findings.append("LEASE_REGISTRY_ACTIVE_ROADMAP_DUPLICATE")
            if left.get("branch") == right.get("branch"):
                findings.append("LEASE_REGISTRY_ACTIVE_BRANCH_DUPLICATE")
            try:
                if conflicting_resource_keys(left.get("resources"), right.get("resources")):
                    findings.append("LEASE_REGISTRY_ACTIVE_RESOURCE_CONFLICT")
            except ValueError:
                findings.append("LEASE_REGISTRY_ACTIVE_RESOURCE_INVALID")
    if isinstance(value.get("integrity_digest"), str):
        if value.get("integrity_digest") != _registry_digest(value):
            findings.append("LEASE_REGISTRY_INTEGRITY")
    else:
        findings.append("LEASE_REGISTRY_INTEGRITY")
    return list(dict.fromkeys(findings))


def empty_lease_registry(repository: str, main_sha: str, *, max_parallel_writers: int = 1) -> dict[str, Any]:
    if not isinstance(repository, str) or re.fullmatch(r"[^/\s]+/[^/\s]+", repository) is None:
        raise ValueError("LEASE_REGISTRY_REPOSITORY_INVALID")
    if _SHA40_RE.fullmatch(str(main_sha)) is None:
        raise ValueError("LEASE_REGISTRY_MAIN_SHA_INVALID")
    if isinstance(max_parallel_writers, bool) or not isinstance(max_parallel_writers, int) or not 1 <= max_parallel_writers <= 3:
        raise ValueError("LEASE_REGISTRY_CEILING_INVALID")
    value = {
        "$schema": LEASE_REGISTRY_SCHEMA,
        "schema_version": LEASE_REGISTRY_VERSION,
        "repository": repository,
        "revision": 0,
        "observed_main_sha": main_sha,
        "max_parallel_writers": max_parallel_writers,
        "leases": [],
        "integrity_digest": "",
    }
    value["integrity_digest"] = _registry_digest(value)
    return value


def _validated_registry_copy(value: dict[str, Any]) -> dict[str, Any]:
    findings = validate_lease_registry(value)
    if findings:
        raise ValueError("LEASE_REGISTRY_INVALID:" + ",".join(findings))
    return copy.deepcopy(value)


def acquire_registry_lease(
    registry: dict[str, Any],
    *,
    expected_revision: int,
    observed_main_sha: str,
    policy_max_parallel_writers: int,
    issue_id: str,
    roadmap_id: str,
    worker_id: str,
    base_sha: str,
    branch: str,
    resources: list[dict[str, Any]],
    now: datetime | None = None,
    ttl_minutes: int = 120,
    lease_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = (now or utc_now()).astimezone(timezone.utc)
    current = _validated_registry_copy(registry)
    if current["revision"] != expected_revision:
        raise ValueError("LEASE_REGISTRY_REVISION_CONFLICT")
    if _SHA40_RE.fullmatch(str(observed_main_sha)) is None or _SHA40_RE.fullmatch(str(base_sha)) is None:
        raise ValueError("LEASE_SHA_INVALID")
    if current["max_parallel_writers"] != policy_max_parallel_writers:
        raise ValueError("LEASE_POLICY_CEILING_MISMATCH")
    if isinstance(policy_max_parallel_writers, bool) or not isinstance(policy_max_parallel_writers, int) or not 1 <= policy_max_parallel_writers <= 3:
        raise ValueError("LEASE_POLICY_CEILING_INVALID")
    canonical_resources = require_canonical_conflict_resources(resources)
    active = [lease for lease in current["leases"] if lease["status"] == "ACTIVE"]
    if current["observed_main_sha"] != observed_main_sha and active:
        raise ValueError("LEASE_RECONCILIATION_REQUIRED")
    if any(_registry_lease_freshness_errors(lease, now) for lease in active):
        raise ValueError("LEASE_RECONCILIATION_REQUIRED")
    if len(active) >= policy_max_parallel_writers:
        raise ValueError("ACTIVE_WRITER_EXISTS")
    for lease in active:
        if lease["issue_id"] == issue_id:
            raise ValueError("ISSUE_ALREADY_LEASED")
        if lease["roadmap_id"] == roadmap_id:
            raise ValueError("ROADMAP_ALREADY_LEASED")
        if lease["branch"] == branch:
            raise ValueError("BRANCH_ALREADY_LEASED")
        if conflicting_resource_keys(canonical_resources, lease["resources"]):
            raise ValueError("CONFLICT_RESOURCE_BUSY")
    if not isinstance(issue_id, str) or re.fullmatch(r"[0-9]+", issue_id) is None:
        raise ValueError("LEASE_ISSUE_ID_INVALID")
    if not isinstance(roadmap_id, str) or _ROADMAP_ID_RE.fullmatch(roadmap_id) is None:
        raise ValueError("LEASE_ROADMAP_ID_INVALID")
    if not isinstance(worker_id, str) or _SAFE_ID_RE.fullmatch(worker_id) is None or not _valid_branch(branch):
        raise ValueError("LEASE_IDENTITY_INVALID")
    candidate_lease_id = lease_id or str(uuid.uuid4())
    if _UUID_RE.fullmatch(candidate_lease_id) is None or any(item["lease_id"] == candidate_lease_id for item in current["leases"]):
        raise ValueError("LEASE_ID_INVALID")
    if len(current["leases"]) >= 200:
        raise ValueError("LEASE_REGISTRY_RETENTION_REQUIRED")
    ttl = _bounded_ttl_minutes(ttl_minutes)
    expires = now + timedelta(minutes=ttl)
    lease = {
        "lease_id": candidate_lease_id,
        "generation": current["revision"] + 1,
        "issue_id": issue_id,
        "roadmap_id": roadmap_id,
        "worker_id": worker_id,
        "base_sha": base_sha,
        "branch": branch,
        "resources": copy.deepcopy(canonical_resources),
        "status": "ACTIVE",
        "claimed_at": iso(now),
        "heartbeat_at": iso(now),
        "expires_at": iso(expires),
        "released_at": None,
        "provider_reconciled_at": None,
        "provider_reconciliation_ref": None,
    }
    current["leases"].append(lease)
    current["revision"] += 1
    current["observed_main_sha"] = observed_main_sha
    current["integrity_digest"] = _registry_digest(current)
    findings = validate_lease_registry(current)
    if findings:
        raise ValueError("LEASE_REGISTRY_INVALID_AFTER_ACQUIRE:" + ",".join(findings))
    return current, copy.deepcopy(lease)


def heartbeat_registry_lease(
    registry: dict[str, Any], *, expected_revision: int, lease_id: str, worker_id: str,
    observed_main_sha: str, now: datetime | None = None, ttl_minutes: int = 120,
) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    current = _validated_registry_copy(registry)
    if current["revision"] != expected_revision:
        raise ValueError("LEASE_REGISTRY_REVISION_CONFLICT")
    matches = [lease for lease in current["leases"] if lease["lease_id"] == lease_id]
    if len(matches) != 1 or matches[0]["status"] != "ACTIVE":
        raise ValueError("LEASE_NOT_ACTIVE")
    lease = matches[0]
    if lease["worker_id"] != worker_id:
        raise ValueError("LEASE_OWNER_MISMATCH")
    if current["observed_main_sha"] != observed_main_sha:
        raise ValueError("LEASE_RECONCILIATION_REQUIRED")
    if _registry_lease_freshness_errors(lease, now):
        raise ValueError("LEASE_RECONCILIATION_REQUIRED")
    if _SHA40_RE.fullmatch(str(observed_main_sha)) is None:
        raise ValueError("LEASE_SHA_INVALID")
    ttl = _bounded_ttl_minutes(ttl_minutes)
    lease["heartbeat_at"] = iso(now)
    lease["expires_at"] = iso(now + timedelta(minutes=ttl))
    current["observed_main_sha"] = observed_main_sha
    current["revision"] += 1
    current["integrity_digest"] = _registry_digest(current)
    findings = validate_lease_registry(current)
    if findings:
        raise ValueError("LEASE_REGISTRY_INVALID_AFTER_HEARTBEAT:" + ",".join(findings))
    return current


def release_registry_lease(
    registry: dict[str, Any], *, expected_revision: int, lease_id: str, worker_id: str,
    observed_main_sha: str, provider_reconciled: bool, provider_reconciliation_ref: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    current = _validated_registry_copy(registry)
    if current["revision"] != expected_revision:
        raise ValueError("LEASE_REGISTRY_REVISION_CONFLICT")
    if provider_reconciled is not True:
        raise ValueError("LEASE_PROVIDER_RECONCILIATION_REQUIRED")
    if not isinstance(provider_reconciliation_ref, str) or _SAFE_REF_RE.fullmatch(provider_reconciliation_ref) is None:
        raise ValueError("LEASE_PROVIDER_RECONCILIATION_REF_INVALID")
    if _SHA40_RE.fullmatch(str(observed_main_sha)) is None:
        raise ValueError("LEASE_SHA_INVALID")
    matches = [lease for lease in current["leases"] if lease["lease_id"] == lease_id]
    if len(matches) != 1 or matches[0]["status"] != "ACTIVE":
        raise ValueError("LEASE_NOT_ACTIVE")
    lease = matches[0]
    if lease["worker_id"] != worker_id:
        raise ValueError("LEASE_OWNER_MISMATCH")
    lease["status"] = "RELEASED"
    lease["released_at"] = iso(now)
    lease["provider_reconciled_at"] = iso(now)
    lease["provider_reconciliation_ref"] = provider_reconciliation_ref.strip()
    current["observed_main_sha"] = observed_main_sha
    current["revision"] += 1
    current["integrity_digest"] = _registry_digest(current)
    findings = validate_lease_registry(current)
    if findings:
        raise ValueError("LEASE_REGISTRY_INVALID_AFTER_RELEASE:" + ",".join(findings))
    return current
