"""Generic ADWF session continuity primitives.

Continuity checkpoints contain bounded public-safe facts for handover/resume.
They are never work authority and never override fresh provider truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import re

from .contracts import validate as validate_contract

SCHEMA_VERSION = 1
BOUNDARY_TYPES = {
    "HUMAN_REQUIRED",
    "OWNER_ATTESTATION_REQUIRED",
    "UAT_REQUIRED",
    "SECURITY_BOUNDARY",
    "DESTRUCTIVE_BOUNDARY",
    "SECRETS_BOUNDARY",
    "CAPABILITY_UNAVAILABLE",
    "EXTERNAL_WAIT",
    "AUTHORITY_EXHAUSTED",
    "ROADMAP_END",
    "EXECUTOR_LIMIT",
}
NON_BOUNDARIES = {"COMMIT", "PR_CREATED", "PR_OPENED", "MERGE", "MERGED", "TEST_PASS", "CI_PASS"}
CONTINUATION_CAPABILITIES = {"GENERIC_PROVIDER_RECONCILE", "PRIVATE_SESSION_ACCELERATOR_AVAILABLE"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Public durable state must not become model-private memory or a secret/session dump.
FORBIDDEN_KEY_FRAGMENTS = {
    "chain_of_thought", "reasoning_trace", "private_reasoning", "raw_prompt",
    "chat_transcript", "access_token", "refresh_token", "api_key", "password",
    "secret", "credential", "conversation_id", "session_token",
}
FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
FORBIDDEN_PRIVATE_TEXT_PATTERNS = (
    re.compile(r"\bchain[ _-]*of[ _-]*thought\b", re.IGNORECASE),
    re.compile(r"\b(?:private|hidden|internal)[ _-]+reasoning\b", re.IGNORECASE),
    re.compile(r"\breasoning[ _-]+trace\b", re.IGNORECASE),
    re.compile(r"\braw[ _-]+prompt\b", re.IGNORECASE),
    re.compile(r"\bchat[ _-]+transcript\b", re.IGNORECASE),
    re.compile(
        r"\b(?:conversation|session|chat)[ _-]*(?:id|identifier|token)\b\s*(?::|=)\s*\S+",
        re.IGNORECASE,
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def checkpoint_digest(value: dict[str, Any]) -> str:
    body = {k: v for k, v in value.items() if k != "checkpoint_digest"}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def _checkpoint_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / "session-continuity-checkpoint.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(value: dict[str, Any]) -> list[str]:
    return [f"CONTINUITY_SCHEMA:{finding.path}:{finding.code}" for finding in validate_contract(value, _checkpoint_schema())]


def _scan_public_safe(value: Any, *, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                errors.append(f"CONTINUITY_FORBIDDEN_FIELD:{path}.{key}")
            errors.extend(_scan_public_safe(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_scan_public_safe(child, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(value):
                errors.append(f"CONTINUITY_FORBIDDEN_SECRET_LIKE_TEXT:{path}")
                break
        for pattern in FORBIDDEN_PRIVATE_TEXT_PATTERNS:
            if pattern.search(value):
                errors.append(f"CONTINUITY_FORBIDDEN_PRIVATE_TEXT:{path}")
                break
    return errors


def validate_checkpoint(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["CONTINUITY_CHECKPOINT_NOT_OBJECT"]
    errors.extend(_schema_errors(value))
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("CONTINUITY_SCHEMA_VERSION")
    if not str(value.get("checkpoint_id") or "").strip():
        errors.append("CONTINUITY_CHECKPOINT_ID")
    if not isinstance(value.get("checkpoint_revision"), int) or int(value.get("checkpoint_revision", -1)) < 0:
        errors.append("CONTINUITY_CHECKPOINT_REVISION")
    if not str(value.get("project_identity") or "").strip():
        errors.append("CONTINUITY_PROJECT_IDENTITY")

    work = value.get("work_identity")
    if not isinstance(work, dict) or not str(work.get("roadmap_id") or "").strip():
        errors.append("CONTINUITY_WORK_IDENTITY")

    domains = value.get("conflict_domains")
    if not isinstance(domains, list) or any(not str(item).strip() for item in domains):
        errors.append("CONTINUITY_CONFLICT_DOMAINS")

    observed = value.get("observed_provider_state")
    if not isinstance(observed, dict) or not SHA_RE.fullmatch(str(observed.get("main_sha") or "")):
        errors.append("CONTINUITY_OBSERVED_MAIN_SHA")
    elif observed.get("head_sha") is not None and not SHA_RE.fullmatch(str(observed.get("head_sha"))):
        errors.append("CONTINUITY_OBSERVED_HEAD_SHA")

    boundary = str(value.get("boundary_type") or "")
    if boundary in NON_BOUNDARIES:
        errors.append("CONTINUITY_NOT_NATURAL_BOUNDARY")
    elif boundary not in BOUNDARY_TYPES:
        errors.append("CONTINUITY_BOUNDARY_TYPE")

    if value.get("continuation_capability") not in CONTINUATION_CAPABILITIES:
        errors.append("CONTINUITY_CAPABILITY")
    if not str(value.get("next_permitted_action") or "").strip():
        errors.append("CONTINUITY_NEXT_ACTION")
    summary = str(value.get("safe_handover_summary") or "").strip()
    if not summary or len(summary) > 2000:
        errors.append("CONTINUITY_SAFE_SUMMARY")

    errors.extend(_scan_public_safe(value))
    expected = checkpoint_digest(value)
    if value.get("checkpoint_digest") != expected:
        errors.append("CONTINUITY_DIGEST")
    return sorted(set(errors))


def build_checkpoint(
    *,
    checkpoint_id: str,
    checkpoint_revision: int,
    project_identity: str,
    roadmap_id: str,
    issue_id: str | None,
    main_sha: str,
    boundary_type: str,
    next_permitted_action: str,
    safe_handover_summary: str,
    lease_identity: str | None = None,
    conflict_domains: list[str] | None = None,
    pr_number: int | None = None,
    head_sha: str | None = None,
    branch: str | None = None,
    last_verified_transition: str = "FRESH_PROVIDER_RECONCILIATION",
    evidence_refs: list[str] | None = None,
    pending_external: dict[str, str | None] | None = None,
    continuation_capability: str = "GENERIC_PROVIDER_RECONCILE",
    ai_work_package_digest: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    created = created_at or _now()
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_id": str(checkpoint_id),
        "checkpoint_revision": int(checkpoint_revision),
        "project_identity": str(project_identity),
        "work_identity": {
            "roadmap_id": str(roadmap_id),
            "issue_id": None if issue_id is None else str(issue_id),
            "ai_work_package_digest": ai_work_package_digest,
        },
        "lease_identity": lease_identity,
        "conflict_domains": sorted(set(conflict_domains or [])),
        "observed_provider_state": {
            "main_sha": str(main_sha),
            "pr_number": pr_number,
            "head_sha": head_sha,
            "branch": branch,
        },
        "last_verified_transition": str(last_verified_transition),
        "evidence_refs": list(evidence_refs or []),
        "boundary_type": str(boundary_type),
        "pending_external": copy.deepcopy(pending_external or {"provider": None, "object_ref": None, "status": None}),
        "next_permitted_action": str(next_permitted_action),
        "safe_handover_summary": str(safe_handover_summary),
        "continuation_capability": str(continuation_capability),
        "created_at": created,
        "updated_at": updated_at or created,
    }
    value["checkpoint_digest"] = checkpoint_digest(value)
    errors = validate_checkpoint(value)
    if errors:
        raise ValueError("SESSION_CONTINUITY_INVALID:" + ",".join(errors))
    return value


def reconcile_checkpoint(checkpoint: dict[str, Any], *, actual_main_sha: str, actual_head_sha: str | None = None) -> dict[str, Any]:
    """Return a non-authoritative reconciliation projection.

    A stale checkpoint is useful context, never authority. Callers must acquire/resolve
    work authority separately before any mutation.
    """
    errors = validate_checkpoint(checkpoint)
    if errors:
        raise ValueError("SESSION_CONTINUITY_INVALID:" + ",".join(errors))
    if not SHA_RE.fullmatch(str(actual_main_sha)):
        raise ValueError("SESSION_CONTINUITY_ACTUAL_MAIN_SHA_INVALID")
    if actual_head_sha is not None and not SHA_RE.fullmatch(str(actual_head_sha)):
        raise ValueError("SESSION_CONTINUITY_ACTUAL_HEAD_SHA_INVALID")
    observed = checkpoint["observed_provider_state"]
    stale_main = observed["main_sha"] != actual_main_sha
    stale_head = observed.get("head_sha") is not None and observed.get("head_sha") != actual_head_sha
    return {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_revision": checkpoint["checkpoint_revision"],
        "provider_authority": False,
        "stale": bool(stale_main or stale_head),
        "stale_main": stale_main,
        "stale_head": stale_head,
        "actual_main_sha": actual_main_sha,
        "actual_head_sha": actual_head_sha,
        "next_step": "FRESH_AUTHORITY_RESOLUTION_REQUIRED" if stale_main or stale_head else "RESUME_CONTEXT_ONLY",
    }
