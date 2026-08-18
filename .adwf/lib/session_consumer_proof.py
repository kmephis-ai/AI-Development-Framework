"""Provider-neutral proof evaluator for durable cross-session handover.

This module evaluates evidence; it does not acquire work authority and does not
perform provider mutations. Fresh provider truth and an independently resolved
active writer lease are required inputs.
"""
from __future__ import annotations

from typing import Any

from .session_continuity import reconcile_checkpoint, validate_checkpoint


def evaluate_handover_proof(
    *,
    checkpoint: dict[str, Any],
    actual_main_sha: str,
    actual_head_sha: str | None,
    active_lease_identity: str | None,
    active_conflict_domains: list[str],
    session_continuity_binding: dict[str, Any],
    independent_executor: bool,
    provider_mutation_discovered_after_checkpoint: bool = False,
) -> dict[str, Any]:
    """Evaluate whether a fresh executor may resume the same writer context.

    The result is an evidence projection, never mutation authority. A caller must
    still enforce the repository's current policy and work package before writing.
    A mismatch never authorizes creation of a duplicate writer; it requires fresh
    authority resolution by the existing orchestration/lease layer.
    """
    errors = validate_checkpoint(checkpoint)
    if errors:
        return {
            "proof_state": "INVALID_CHECKPOINT",
            "resume_context_allowed": False,
            "provider_authority": False,
            "duplicate_writer_allowed": False,
            "errors": errors,
        }

    reconciliation = reconcile_checkpoint(
        checkpoint,
        actual_main_sha=actual_main_sha,
        actual_head_sha=actual_head_sha,
    )

    checkpoint_lease = checkpoint.get("lease_identity")
    checkpoint_domains = sorted(set(checkpoint.get("conflict_domains") or []))
    actual_domains = sorted(set(str(item) for item in active_conflict_domains if str(item).strip()))
    binding_inherits_core = bool(session_continuity_binding.get("inherits_framework_core"))
    binding_is_non_authoritative = session_continuity_binding.get("provider_authority") is False

    lease_matches = bool(checkpoint_lease) and checkpoint_lease == active_lease_identity
    domains_match = checkpoint_domains == actual_domains
    same_writer_compatible = lease_matches and domains_match

    if not independent_executor:
        state = "NOT_INDEPENDENT_EXECUTOR"
    elif not binding_inherits_core or not binding_is_non_authoritative:
        state = "INVALID_CONSUMER_BINDING"
    elif not lease_matches:
        state = "LEASE_RECONCILIATION_REQUIRED"
    elif not domains_match:
        state = "CONFLICT_DOMAIN_RECONCILIATION_REQUIRED"
    elif reconciliation["stale"] or provider_mutation_discovered_after_checkpoint:
        state = "STALE_CHECKPOINT_RECONCILED"
    else:
        state = "RESUME_CONTEXT_VERIFIED"

    resume_context_allowed = state in {"STALE_CHECKPOINT_RECONCILED", "RESUME_CONTEXT_VERIFIED"}
    next_step = (
        "FRESH_AUTHORITY_RESOLUTION_REQUIRED"
        if not resume_context_allowed
        else "RESUME_SAME_WRITER_AFTER_POLICY_RECHECK"
    )

    return {
        "proof_state": state,
        "resume_context_allowed": resume_context_allowed,
        "provider_authority": False,
        "duplicate_writer_allowed": False,
        "same_writer_compatible": same_writer_compatible,
        "lease_matches": lease_matches,
        "conflict_domains_match": domains_match,
        "checkpoint_stale": bool(reconciliation["stale"]),
        "provider_mutation_discovered_after_checkpoint": bool(provider_mutation_discovered_after_checkpoint),
        "binding_inherits_framework_core": binding_inherits_core,
        "binding_provider_authority": session_continuity_binding.get("provider_authority"),
        "next_step": next_step,
    }
