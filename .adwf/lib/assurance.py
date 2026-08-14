"""Immutable assurance snapshot consumed by trusted authorization and CEO UI."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import hashlib, json, re

SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE = {"VERIFIED", "HEALTHY"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    body = {k: v for k, v in snapshot.items() if k != "snapshot_digest"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def validate_assurance_snapshot(snapshot: dict[str, Any], *, expected_sha: str | None = None,
                                expected_policy_hash: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(snapshot, dict):
        return ["ASSURANCE_NOT_OBJECT"]
    if snapshot.get("schema_version") != 1:
        errors.append("ASSURANCE_SCHEMA_VERSION")
    sha = snapshot.get("subject_sha")
    if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        errors.append("ASSURANCE_SHA_INVALID")
    elif expected_sha is not None and sha != expected_sha:
        errors.append("ASSURANCE_SHA_MISMATCH")
    policy_hash = snapshot.get("policy_hash")
    if not isinstance(policy_hash, str) or SHA256.fullmatch(policy_hash) is None:
        errors.append("ASSURANCE_POLICY_HASH_INVALID")
    elif expected_policy_hash is not None and policy_hash != expected_policy_hash:
        errors.append("ASSURANCE_POLICY_HASH_MISMATCH")
    health = snapshot.get("health")
    gates = snapshot.get("gates")
    cost = snapshot.get("cost")
    evidence = snapshot.get("evidence")
    provider = snapshot.get("provider")
    if not isinstance(health, dict): errors.append("ASSURANCE_HEALTH_INVALID")
    if not isinstance(gates, dict): errors.append("ASSURANCE_GATES_INVALID")
    if not isinstance(cost, dict): errors.append("ASSURANCE_COST_INVALID")
    if not isinstance(evidence, dict): errors.append("ASSURANCE_EVIDENCE_INVALID")
    if not isinstance(provider, dict): errors.append("ASSURANCE_PROVIDER_INVALID")
    digest = snapshot.get("snapshot_digest")
    if not isinstance(digest, str) or digest != snapshot_digest(snapshot):
        errors.append("ASSURANCE_DIGEST_INVALID")
    try:
        verified = datetime.fromisoformat(str(snapshot.get("verified_at", "")).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(snapshot.get("expires_at", "")).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if verified.tzinfo is None or expires.tzinfo is None or expires <= verified:
            errors.append("ASSURANCE_TIME_INVALID")
        if expires <= now:
            errors.append("ASSURANCE_STALE")
    except ValueError:
        errors.append("ASSURANCE_TIME_INVALID")
    return list(dict.fromkeys(errors))


def machine_verified(snapshot: dict[str, Any] | None, *, expected_sha: str | None = None) -> str:
    if not snapshot:
        return "NOT_VERIFIED"
    errors = validate_assurance_snapshot(snapshot, expected_sha=expected_sha)
    if errors:
        return "STALE" if errors == ["ASSURANCE_STALE"] else "NOT_VERIFIED"
    health = snapshot["health"]
    required_health = ("package_integrity", "config_health", "control_plane_health", "product_health")
    if any(health.get(name) not in SAFE for name in required_health):
        return "NOT_VERIFIED"
    gates = snapshot["gates"]
    required_gates = tuple(snapshot.get("required_gates") or ())
    if any(gates.get(name) != "PASS" for name in required_gates):
        return "NOT_VERIFIED"
    if snapshot["evidence"].get("refs_resolved") is not True:
        return "NOT_VERIFIED"
    if snapshot["provider"].get("readback_verified") is not True:
        return "NOT_VERIFIED"
    if snapshot["cost"].get("status") != "VERIFIED_ZERO" or snapshot["cost"].get("projected_cost_usd") != 0:
        return "NOT_VERIFIED"
    return "VERIFIED"
