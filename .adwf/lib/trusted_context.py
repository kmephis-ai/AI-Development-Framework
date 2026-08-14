"""Trusted Context Compiler.

Caller input is identifier-only. Positive authorization facts are derived from
Effective Policy, provider readback and AssuranceSnapshot, never accepted from
an agent/Issue/PR payload.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import re

from .assurance import validate_assurance_snapshot
from .policy import DecisionContext
from .policy_runtime import load_effective_policy

ALLOWED_REQUEST_FIELDS = {"request_id", "subject_sha", "preview_digest"}
SHA = re.compile(r"^[0-9a-f]{40}$")


def compile_trusted_context(root: str | Path, *, action: str, risk: str, work_type: str,
                            request: dict[str, Any], assurance_snapshot: dict[str, Any],
                            provider_readback: dict[str, Any]) -> DecisionContext:
    unknown = set(request) - ALLOWED_REQUEST_FIELDS
    if unknown:
        raise ValueError("CALLER_POSITIVE_FACTS_FORBIDDEN:" + ",".join(sorted(unknown)))
    subject_sha = str(request.get("subject_sha") or "")
    if SHA.fullmatch(subject_sha) is None:
        raise ValueError("TRUSTED_CONTEXT_SHA_REQUIRED")
    policy = load_effective_policy(root)
    policy_hash = policy["policy_hash"]
    errors = validate_assurance_snapshot(assurance_snapshot, expected_sha=subject_sha,
                                         expected_policy_hash=policy_hash)
    if errors:
        raise ValueError("ASSURANCE_INVALID:" + ",".join(errors))
    if provider_readback.get("subject_sha") != subject_sha:
        raise ValueError("PROVIDER_READBACK_SHA_MISMATCH")
    if provider_readback.get("facts_readback_verified") is not True:
        raise ValueError("PROVIDER_FACTS_READBACK_NOT_VERIFIED")
    if provider_readback.get("repository_visibility") != "PUBLIC":
        raise ValueError("PUBLIC_PROFILE_VISIBILITY_NOT_VERIFIED")
    runner_actions={"test","review","verify","owner_accept","merge","promote","observe","close_issue"}
    if action in runner_actions and provider_readback.get("runner") != "ubuntu-24.04":
        raise ValueError("STANDARD_RUNNER_NOT_VERIFIED")
    if provider_readback.get("larger_runner") is True:
        raise ValueError("LARGER_RUNNER_BLOCKED")
    # Effective Policy is loaded from the trusted package/default branch and is the
    # only source of autonomy/risk ceiling. Caller fields are never consulted.
    if policy.get("active_autonomy") not in {"A0", "A1", "A2", "A3"}:
        raise ValueError("EFFECTIVE_POLICY_TRUSTED_SOURCE_MISSING")
    if policy.get("max_autonomous_risk") not in {"R0", "R1", "R2", "R3"}:
        raise ValueError("EFFECTIVE_POLICY_TRUSTED_SOURCE_MISSING")
    snapshot_health = assurance_snapshot["health"]
    snapshot_gates = assurance_snapshot["gates"]
    owner = provider_readback.get("owner_decision") if isinstance(provider_readback.get("owner_decision"), dict) else {}
    owner_exact = (
        owner.get("readback_verified") is True
        and owner.get("decision") == "ACCEPTED"
        and owner.get("head_sha") == subject_sha
        and owner.get("preview_digest") == request.get("preview_digest")
        and assurance_snapshot.get("policy_hash") == policy_hash
    )
    return DecisionContext(
        action=action,
        autonomy=str(policy.get("active_autonomy")),
        risk=risk,
        max_autonomous_risk=str(policy.get("max_autonomous_risk")),
        work_type=work_type,
        health=dict(snapshot_health),
        gates=dict(snapshot_gates),
        required_gates=tuple(assurance_snapshot.get("required_gates") or ()),
        exact_sha=True,
        evidence_fresh=assurance_snapshot.get("evidence", {}).get("refs_resolved") is True,
        human_approved=owner_exact,
        destructive=False,
        trust_change=False,
        writer_conflict=False,
        provider_allowed=True,
        provider_potentially_paid=False,
        projected_cost=0.0,
        expected_policy_hash=policy_hash,
        provider_facts_fresh=True,
        owner_acceptance_exact=owner_exact,
    )
