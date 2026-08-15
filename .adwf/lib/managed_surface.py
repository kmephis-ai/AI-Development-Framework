"""Fail-closed consumer managed-surface contracts and read-only lifecycle plans.

The release MANIFEST/SHA256SUMS remain the single inventory of framework package
files. This module adds consumer-repository ownership semantics without copying
that inventory and never performs destructive writes.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import hashlib
import json
import re

from .contracts import validate
from .strict_json import load as strict_load

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OWNERSHIP = {"FRAMEWORK_PRIVATE", "SHARED_GUARDED", "CONSUMER_OWNED"}


class ManagedSurfaceError(ValueError):
    """Deterministic fail-closed managed-surface error."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_rel(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ManagedSurfaceError("SURFACE_PATH_INVALID")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("./") or any(part in {"", ".", ".."} for part in pure.parts):
        raise ManagedSurfaceError("SURFACE_PATH_INVALID")
    normalized = pure.as_posix()
    if normalized != value:
        raise ManagedSurfaceError("SURFACE_PATH_NOT_CANONICAL")
    return normalized


def _load_json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_load(path)
    except Exception as exc:
        raise ManagedSurfaceError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ManagedSurfaceError(code + ":OBJECT_REQUIRED")
    return value


def load_policy(framework_root: str | Path) -> dict[str, Any]:
    root = Path(framework_root).resolve()
    policy_path = root / ".adwf/managed-surface-policy.json"
    schema_path = root / ".adwf/schemas/managed-surface-policy.schema.json"
    policy = _load_json_object(policy_path, "SURFACE_POLICY_INVALID")
    schema = _load_json_object(schema_path, "SURFACE_POLICY_SCHEMA_INVALID")
    findings = validate(policy, schema)
    if findings:
        raise ManagedSurfaceError(
            "SURFACE_POLICY_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    if policy.get("role") != "MANAGED_SURFACE_POLICY":
        raise ManagedSurfaceError("SURFACE_POLICY_ROLE_INVALID")
    return policy


def _parse_sums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw:
            continue
        if "  " not in raw:
            raise ManagedSurfaceError("SOURCE_CHECKSUM_LINE_INVALID")
        digest, rel = raw.split("  ", 1)
        rel = _safe_rel(rel)
        if not SHA256.fullmatch(digest):
            raise ManagedSurfaceError("SOURCE_CHECKSUM_DIGEST_INVALID:" + rel)
        if rel in result:
            raise ManagedSurfaceError("SOURCE_CHECKSUM_DUPLICATE:" + rel)
        result[rel] = digest
    return result


def load_source_inventory(framework_root: str | Path) -> dict[str, Any]:
    """Load and cryptographically verify package inventory without inventing files."""
    root = Path(framework_root).resolve()
    policy = load_policy(root)
    manifest_rel = _safe_rel(str(policy["source_inventory"]))
    sums_rel = _safe_rel(str(policy["source_checksums"]))
    manifest_path = root / manifest_rel
    sums_path = root / sums_rel
    if manifest_path.is_symlink() or sums_path.is_symlink():
        raise ManagedSurfaceError("SOURCE_CONTROL_SYMLINK_FORBIDDEN")
    manifest = _load_json_object(manifest_path, "SOURCE_MANIFEST_INVALID")
    if manifest.get("scope") != "FRAMEWORK_OWNED_TRUST_BOUNDARY":
        raise ManagedSurfaceError("SOURCE_MANIFEST_SCOPE_INVALID")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ManagedSurfaceError("SOURCE_MANIFEST_FILES_EMPTY")
    canonical: list[str] = []
    seen: set[str] = set()
    for value in files:
        rel = _safe_rel(str(value))
        if rel in seen:
            raise ManagedSurfaceError("SOURCE_MANIFEST_DUPLICATE:" + rel)
        seen.add(rel)
        canonical.append(rel)
    if canonical != sorted(canonical):
        raise ManagedSurfaceError("SOURCE_MANIFEST_ORDER_INVALID")
    try:
        sums = _parse_sums(sums_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManagedSurfaceError("SOURCE_CHECKSUMS_UNREADABLE:" + type(exc).__name__) from exc
    expected_sum_paths = set(canonical) | {manifest_rel}
    if set(sums) != expected_sum_paths:
        missing = sorted(expected_sum_paths - set(sums))
        extra = sorted(set(sums) - expected_sum_paths)
        raise ManagedSurfaceError(
            "SOURCE_CHECKSUM_SET_MISMATCH:missing=" + ",".join(missing) + ";extra=" + ",".join(extra)
        )
    for rel in canonical:
        path = root / rel
        if path.is_symlink():
            raise ManagedSurfaceError("SOURCE_SYMLINK_FORBIDDEN:" + rel)
        if not path.is_file():
            raise ManagedSurfaceError("SOURCE_FILE_MISSING:" + rel)
        if _digest(path) != sums[rel]:
            raise ManagedSurfaceError("SOURCE_FILE_DIGEST_MISMATCH:" + rel)
    if _digest(manifest_path) != sums[manifest_rel]:
        raise ManagedSurfaceError("SOURCE_MANIFEST_DIGEST_MISMATCH")
    shared = [_safe_rel(str(item)) for item in policy.get("shared_guarded_paths") or []]
    if len(shared) != len(set(shared)):
        raise ManagedSurfaceError("SURFACE_SHARED_DUPLICATE")
    unknown_shared = sorted(set(shared) - set(canonical))
    if unknown_shared:
        raise ManagedSurfaceError("SURFACE_SHARED_NOT_IN_MANIFEST:" + ",".join(unknown_shared))
    return {
        "root": root,
        "policy": policy,
        "manifest": manifest,
        "manifest_sha256": _digest(manifest_path),
        "files": canonical,
        "sums": sums,
        "shared": set(shared),
    }


def ownership_for(rel: str, inventory: dict[str, Any]) -> str:
    path = _safe_rel(rel)
    if path not in set(inventory["files"]):
        return "CONSUMER_OWNED"
    if path in inventory["shared"]:
        return "SHARED_GUARDED"
    return str(inventory["policy"].get("default_manifest_ownership") or "FRAMEWORK_PRIVATE")


def _target_state(target: Path, expected_sha: str) -> tuple[str, str | None]:
    if target.is_symlink():
        return "SYMLINK", None
    if not target.exists():
        return "ABSENT", None
    if not target.is_file():
        return "NON_FILE", None
    digest = _digest(target)
    return ("EXACT" if digest == expected_sha else "COLLISION"), digest


def plan_adoption(
    framework_root: str | Path,
    consumer_root: str | Path,
    *,
    source_revision: str,
) -> dict[str, Any]:
    """Build a read-only adoption plan. No existing non-exact path is overwritten."""
    if not SHA40.fullmatch(str(source_revision)):
        raise ManagedSurfaceError("SOURCE_REVISION_INVALID")
    inventory = load_source_inventory(framework_root)
    target_root = Path(consumer_root).resolve()
    entries: list[dict[str, Any]] = []
    blockers: list[str] = []
    for rel in inventory["files"]:
        expected = inventory["sums"][rel]
        ownership = ownership_for(rel, inventory)
        state, current = _target_state(target_root / rel, expected)
        if state == "ABSENT":
            action = "CREATE_PLANNED"
        elif state == "EXACT":
            action = "KEEP_EXACT"
        elif state == "SYMLINK":
            action = "BLOCK"
            blockers.append("TARGET_SYMLINK_FORBIDDEN:" + rel)
        elif state == "NON_FILE":
            action = "BLOCK"
            blockers.append("TARGET_NON_FILE_COLLISION:" + rel)
        else:
            action = "BLOCK"
            blockers.append("TARGET_CONTENT_COLLISION:" + rel)
        entries.append(
            {
                "path": rel,
                "ownership": ownership,
                "source_sha256": expected,
                "target_state": state,
                "target_sha256": current,
                "action": action,
            }
        )
    plan = {
        "$schema": ".adwf/schemas/managed-surface-plan.schema.json",
        "schema_version": 1,
        "kind": "ADOPTION",
        "status": "BLOCK" if blockers else "READY",
        "source_revision": source_revision,
        "source_manifest_sha256": inventory["manifest_sha256"],
        "entries": entries,
        "blockers": blockers,
        "write_performed": False,
    }
    _validate_plan(plan, inventory["root"])
    return plan


def snapshot_from_adoption_plan(plan: dict[str, Any], framework_root: str | Path) -> dict[str, Any]:
    """Create expected post-adoption ownership snapshot from a non-blocked plan.

    Only paths that were absent before adoption become ADWF-managed. Pre-existing
    exact files are conservatively preserved on detach because provenance is unknown.
    """
    root = Path(framework_root).resolve()
    _validate_plan(plan, root)
    if plan.get("kind") != "ADOPTION" or plan.get("status") != "READY":
        raise ManagedSurfaceError("SNAPSHOT_REQUIRES_READY_ADOPTION_PLAN")
    entries = []
    for item in plan["entries"]:
        entries.append(
            {
                "path": item["path"],
                "ownership": item["ownership"],
                "installed_sha256": item["source_sha256"],
                "managed_by_adwf": item["target_state"] == "ABSENT",
            }
        )
    snapshot = {
        "$schema": ".adwf/schemas/managed-surface-snapshot.schema.json",
        "schema_version": 1,
        "role": "MANAGED_SURFACE_SNAPSHOT",
        "source_revision": plan["source_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "entries": entries,
    }
    _validate_snapshot(snapshot, root)
    return snapshot


def plan_detach(
    consumer_root: str | Path,
    snapshot: dict[str, Any],
    *,
    framework_root: str | Path,
) -> dict[str, Any]:
    """Build a destructive-detach eligibility plan but never delete anything."""
    root = Path(framework_root).resolve()
    _validate_snapshot(snapshot, root)
    target_root = Path(consumer_root).resolve()
    entries: list[dict[str, Any]] = []
    blockers: list[str] = []
    for item in snapshot["entries"]:
        rel = _safe_rel(item["path"])
        target = target_root / rel
        ownership = item["ownership"]
        managed = item["managed_by_adwf"] is True
        state, current = _target_state(target, item["installed_sha256"])
        if not managed:
            action = "PRESERVE_PREEXISTING"
        elif ownership == "SHARED_GUARDED":
            action = "PRESERVE_SHARED"
        elif ownership != "FRAMEWORK_PRIVATE":
            action = "PRESERVE_CONSUMER"
        elif state == "ABSENT":
            action = "ALREADY_ABSENT"
        elif state == "EXACT":
            action = "REMOVE_ELIGIBLE"
        elif state == "SYMLINK":
            action = "PRESERVE_BLOCK"
            blockers.append("DETACH_SYMLINK_FORBIDDEN:" + rel)
        elif state == "NON_FILE":
            action = "PRESERVE_BLOCK"
            blockers.append("DETACH_NON_FILE_DRIFT:" + rel)
        else:
            action = "PRESERVE_BLOCK"
            blockers.append("DETACH_CONTENT_DRIFT:" + rel)
        entries.append(
            {
                "path": rel,
                "ownership": ownership,
                "installed_sha256": item["installed_sha256"],
                "managed_by_adwf": managed,
                "target_state": state,
                "target_sha256": current,
                "action": action,
            }
        )
    plan = {
        "$schema": ".adwf/schemas/managed-surface-plan.schema.json",
        "schema_version": 1,
        "kind": "DETACH",
        "status": "BLOCK" if blockers else "READY",
        "source_revision": snapshot["source_revision"],
        "source_manifest_sha256": snapshot["source_manifest_sha256"],
        "entries": entries,
        "blockers": blockers,
        "write_performed": False,
    }
    _validate_plan(plan, root)
    return plan


def _validate_snapshot(value: dict[str, Any], root: Path) -> None:
    schema = _load_json_object(root / ".adwf/schemas/managed-surface-snapshot.schema.json", "SNAPSHOT_SCHEMA_INVALID")
    findings = validate(value, schema)
    if findings:
        raise ManagedSurfaceError(
            "SNAPSHOT_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    paths = [str(item.get("path") or "") for item in value.get("entries") or []]
    if len(paths) != len(set(paths)):
        raise ManagedSurfaceError("SNAPSHOT_PATH_DUPLICATE")
    for rel in paths:
        _safe_rel(rel)
    inventory = load_source_inventory(root)
    if value.get("source_manifest_sha256") != inventory["manifest_sha256"]:
        raise ManagedSurfaceError("SNAPSHOT_SOURCE_MANIFEST_MISMATCH")
    if set(paths) != set(inventory["files"]):
        raise ManagedSurfaceError("SNAPSHOT_INVENTORY_SET_MISMATCH")
    by_path = {item["path"]: item for item in value["entries"]}
    for rel in inventory["files"]:
        item = by_path[rel]
        if item["ownership"] != ownership_for(rel, inventory):
            raise ManagedSurfaceError("SNAPSHOT_OWNERSHIP_MISMATCH:" + rel)
        if item["installed_sha256"] != inventory["sums"][rel]:
            raise ManagedSurfaceError("SNAPSHOT_DIGEST_MISMATCH:" + rel)


def _validate_plan(value: dict[str, Any], root: Path) -> None:
    schema = _load_json_object(root / ".adwf/schemas/managed-surface-plan.schema.json", "PLAN_SCHEMA_INVALID")
    findings = validate(value, schema)
    if findings:
        raise ManagedSurfaceError(
            "PLAN_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    paths = [str(item.get("path") or "") for item in value.get("entries") or []]
    if len(paths) != len(set(paths)):
        raise ManagedSurfaceError("PLAN_PATH_DUPLICATE")
    for rel in paths:
        _safe_rel(rel)
    if bool(value.get("write_performed")):
        raise ManagedSurfaceError("MANAGED_SURFACE_V1_WRITE_FORBIDDEN")


def validate_canonical_contract(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    inventory = load_source_inventory(base)
    policy = inventory["policy"]
    if policy.get("default_manifest_ownership") != "FRAMEWORK_PRIVATE":
        raise ManagedSurfaceError("SURFACE_DEFAULT_MANIFEST_OWNERSHIP_INVALID")
    if policy.get("default_unlisted_ownership") != "CONSUMER_OWNED":
        raise ManagedSurfaceError("SURFACE_DEFAULT_UNLISTED_OWNERSHIP_INVALID")
    required = {
        "PROJECT_MUST_OUTLIVE_FRAMEWORK",
        "READ_ONLY_PLANNER_V1",
        "DRIFT_BLOCKS_DESTRUCTIVE_DETACH",
        "SHARED_GUARDED_PRESERVED",
    }
    invariants = set(policy.get("invariants") or [])
    missing = sorted(required - invariants)
    if missing:
        raise ManagedSurfaceError("SURFACE_INVARIANT_MISSING:" + ",".join(missing))
    return {
        "status": "PASS",
        "framework_files": len(inventory["files"]),
        "shared_guarded": len(inventory["shared"]),
        "manifest_sha256": inventory["manifest_sha256"],
    }
