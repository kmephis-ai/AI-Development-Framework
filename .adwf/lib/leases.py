"""Writer leases: TTL, heartbeat, conflict detection и fail-closed claim."""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import copy
import json
import os
import re
import tempfile
import uuid

from .evidence import parse_time

DEFAULT_HEARTBEAT_TIMEOUT_MINUTES = 45


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def lease_active(lease: dict[str, Any], now: datetime | None = None) -> bool:
    """Совместимая проверка занятия lease до reconciliation.

    Она намеренно остаётся консервативной по expiry: протухший heartbeat не
    освобождает conflict domain для второго Writer. Полная пригодность lease к
    продолжению работы проверяется ``lease_fresh``.
    """
    now = (now or utc_now()).astimezone(timezone.utc)
    try:
        return parse_time(str(lease["expires_at"])) > now and lease.get("status", "ACTIVE") == "ACTIVE"
    except (KeyError, TypeError, ValueError):
        return False


def active_leases(leases: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    return [lease for lease in leases if lease_active(lease, now)]


def lease_freshness_errors(
    lease: dict[str, Any],
    now: datetime | None = None,
    *,
    heartbeat_timeout_minutes: int = DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
) -> list[str]:
    """Проверить TTL и heartbeat без возможности молча оживить stale lease."""
    now = (now or utc_now()).astimezone(timezone.utc)
    errors: list[str] = []
    if lease.get("status", "ACTIVE") != "ACTIVE":
        errors.append("LEASE_NOT_ACTIVE")
    try:
        expires = parse_time(str(lease["expires_at"]))
        if expires <= now:
            errors.append("LEASE_EXPIRED")
    except (KeyError, TypeError, ValueError):
        errors.append("LEASE_EXPIRY_INVALID")
    try:
        heartbeat_at = parse_time(str(lease["heartbeat_at"]))
        if heartbeat_at > now:
            errors.append("LEASE_HEARTBEAT_IN_FUTURE")
        elif (now - heartbeat_at).total_seconds() > max(1, int(heartbeat_timeout_minutes)) * 60:
            errors.append("LEASE_HEARTBEAT_STALE")
    except (KeyError, TypeError, ValueError):
        errors.append("LEASE_HEARTBEAT_INVALID")
    return list(dict.fromkeys(errors))


def lease_fresh(
    lease: dict[str, Any],
    now: datetime | None = None,
    *,
    heartbeat_timeout_minutes: int = DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
) -> bool:
    return not lease_freshness_errors(
        lease, now, heartbeat_timeout_minutes=heartbeat_timeout_minutes
    )


def invalid_declared_active_leases(
    leases: list[dict[str, Any]],
    now: datetime | None = None,
    *,
    heartbeat_timeout_minutes: int = DEFAULT_HEARTBEAT_TIMEOUT_MINUTES,
) -> list[dict[str, Any]]:
    return [
        lease
        for lease in leases
        if lease.get("status", "ACTIVE") == "ACTIVE"
        and not lease_fresh(
            lease,
            now,
            heartbeat_timeout_minutes=heartbeat_timeout_minutes,
        )
    ]


def conflict_domains(issue: dict[str, Any], leases: list[dict[str, Any]], now: datetime | None = None) -> list[str]:
    domains = set(issue.get("conflict_domains", []))
    overlap: set[str] = set()
    for lease in active_leases(leases, now):
        overlap.update(domains.intersection(lease.get("conflict_domains", [])))
    return sorted(overlap)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        raise ValueError("WORKSPACE_IDENTITY_INVALID")
    return normalized[:48]


def claim(
    queue: dict[str, Any],
    issue_id: str,
    worker_id: str,
    base_sha: str,
    *,
    now: datetime | None = None,
    ttl_minutes: int = 120,
    one_active_writer: bool = True,
    permission_allowed: bool = False,
    max_ready_age_days: int = 30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = (now or utc_now()).astimezone(timezone.utc)
    result = copy.deepcopy(queue)
    issues = result.get("issues", [])
    matches = [item for item in issues if str(item.get("id")) == str(issue_id)]
    if len(matches) != 1:
        raise ValueError("ISSUE_ID_NOT_UNIQUE")
    issue = matches[0]
    leases = result.setdefault("leases", [])
    if invalid_declared_active_leases(leases, now):
        raise ValueError("LEASE_RECONCILIATION_REQUIRED")
    current = active_leases(leases, now)
    if one_active_writer and current:
        raise ValueError("ACTIVE_WRITER_EXISTS")
    if issue.get("state") != "READY":
        raise ValueError("ISSUE_NOT_READY")
    if issue.get("dependencies_resolved") is not True:
        raise ValueError("DEPENDENCIES_NOT_VERIFIED")
    if permission_allowed is not True:
        raise ValueError("PERMISSION_NOT_ALLOWED")
    if issue.get("human_required") is not False or issue.get("autonomy_allowed") is not True:
        raise ValueError("ISSUE_NOT_AUTONOMOUS")
    roadmap_id = str(issue.get("roadmap_id", ""))
    if not roadmap_id or sum(str(item.get("roadmap_id")) == roadmap_id for item in issues) != 1:
        raise ValueError("ROADMAP_ID_NOT_ONE_TO_ONE")
    try:
        ready_since = parse_time(str(issue["ready_since"]))
        age = now - ready_since
        if age.total_seconds() < 0 or age.total_seconds() > max_ready_age_days * 86400:
            raise ValueError("ISSUE_NOT_FRESH")
    except (KeyError, ValueError):
        raise ValueError("ISSUE_NOT_FRESH") from None
    if any(str(item.get("issue_id")) == str(issue_id) for item in current):
        raise ValueError("ISSUE_ALREADY_LEASED")
    if conflict_domains(issue, leases, now):
        raise ValueError("CONFLICT_DOMAIN_BUSY")
    if not worker_id.strip() or re.fullmatch(r"[0-9a-f]{40}", base_sha.strip()) is None:
        raise ValueError("WORKER_OR_SHA_MISSING")
    expires = now + timedelta(minutes=max(1, min(int(ttl_minutes), 240)))
    lease_id = str(uuid.uuid4())
    workspace_key = f"{_slug(roadmap_id)}-issue-{_slug(str(issue_id))}"
    lease = {
        "lease_id": lease_id,
        "issue_id": str(issue_id),
        "roadmap_id": roadmap_id,
        "worker_id": worker_id,
        "claimed_at": iso(now),
        "heartbeat_at": iso(now),
        "expires_at": iso(expires),
        "base_sha": base_sha,
        "workspace_id": workspace_key,
        "workspace_path": f".adwf-runtime/workspaces/{workspace_key}",
        "branch": f"adwf/{workspace_key}",
        "conflict_domains": list(issue.get("conflict_domains", [])),
        "status": "ACTIVE",
    }
    leases.append(lease)
    issue["state"] = "IN_PROGRESS"
    issue["writer_id"] = worker_id
    issue["lease_id"] = lease["lease_id"]
    issue["workspace_id"] = workspace_key
    issue["heartbeat_at"] = lease["heartbeat_at"]
    issue["expires_at"] = lease["expires_at"]
    return result, lease


def heartbeat(lease: dict[str, Any], worker_id: str, *, now: datetime | None = None, ttl_minutes: int = 120) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    freshness = lease_freshness_errors(lease, now)
    if freshness:
        raise ValueError("LEASE_NOT_RENEWABLE:" + ",".join(freshness))
    if lease.get("worker_id") != worker_id:
        raise ValueError("LEASE_OWNER_MISMATCH")
    updated = dict(lease)
    updated["heartbeat_at"] = iso(now)
    updated["expires_at"] = iso(now + timedelta(minutes=max(1, min(int(ttl_minutes), 240))))
    return updated


def reconcile(queue: dict[str, Any], *, now: datetime | None = None) -> tuple[dict[str, Any], list[str]]:
    now = (now or utc_now()).astimezone(timezone.utc)
    result = copy.deepcopy(queue)
    expired_ids: list[str] = []
    for lease in result.get("leases", []):
        if lease.get("status", "ACTIVE") == "ACTIVE" and not lease_fresh(lease, now):
            lease["status"] = "EXPIRED"
            expired_ids.append(str(lease.get("lease_id", "UNKNOWN")))
            for issue in result.get("issues", []):
                if str(issue.get("id")) == str(lease.get("issue_id")) and issue.get("state") in {"CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION"}:
                    issue["state"] = "RECOVERY"
    return result, expired_ids


def atomic_update(path: str | Path, transform) -> Any:
    """Сериализовать локальную транзакцию lock + fsync + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        current = strict_loads(target.read_text(encoding="utf-8"))
        updated, output = transform(current)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return output
