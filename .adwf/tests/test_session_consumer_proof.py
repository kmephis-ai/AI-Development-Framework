from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.session_consumer_proof import evaluate_handover_proof
from lib.session_continuity import build_checkpoint

MAIN_A = "a" * 40
MAIN_B = "b" * 40
HEAD_A = "c" * 40
HEAD_B = "d" * 40
LEASE = "lease-001"
DOMAINS = ["consumer-runtime", "session-continuity"]
BINDING = {
    "inherits_framework_core": True,
    "provider_authority": False,
    "runtime_evidence_mode": "PROVIDER_FACTS_ONLY",
    "resumable_commands": [],
    "safety_boundaries": ["FRESH_RECONCILE_BEFORE_WRITE"],
}


def checkpoint(*, main_sha: str = MAIN_A, head_sha: str = HEAD_A, lease: str | None = LEASE, domains: list[str] | None = None):
    return build_checkpoint(
        checkpoint_id="cp-1",
        checkpoint_revision=1,
        project_identity="consumer/example",
        roadmap_id="SESSION-CONSUMER-PROOF-001",
        issue_id="150",
        main_sha=main_sha,
        head_sha=head_sha,
        boundary_type="EXTERNAL_WAIT",
        next_permitted_action="reconcile provider and resume same writer",
        safe_handover_summary="Writer is waiting on external provider evidence.",
        lease_identity=lease,
        conflict_domains=domains or DOMAINS,
        pr_number=151,
        branch="agent/session-consumer-proof-001",
    )


def test_independent_executor_resumes_same_writer_without_duplicate() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity=LEASE,
        active_conflict_domains=DOMAINS,
        session_continuity_binding=BINDING,
        independent_executor=True,
    )
    assert proof["proof_state"] == "RESUME_CONTEXT_VERIFIED"
    assert proof["resume_context_allowed"] is True
    assert proof["provider_authority"] is False
    assert proof["duplicate_writer_required"] is False


def test_stale_checkpoint_is_reconciled_from_fresh_provider_truth() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_B,
        actual_head_sha=HEAD_B,
        active_lease_identity=LEASE,
        active_conflict_domains=DOMAINS,
        session_continuity_binding=BINDING,
        independent_executor=True,
    )
    assert proof["proof_state"] == "STALE_CHECKPOINT_RECONCILED"
    assert proof["checkpoint_stale"] is True
    assert proof["resume_context_allowed"] is True
    assert proof["next_step"] == "RESUME_SAME_WRITER_AFTER_POLICY_RECHECK"


def test_crash_after_mutation_does_not_require_duplicate_writer() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity=LEASE,
        active_conflict_domains=DOMAINS,
        session_continuity_binding=BINDING,
        independent_executor=True,
        provider_mutation_discovered_after_checkpoint=True,
    )
    assert proof["proof_state"] == "STALE_CHECKPOINT_RECONCILED"
    assert proof["provider_mutation_discovered_after_checkpoint"] is True
    assert proof["duplicate_writer_required"] is False


def test_changed_lease_fails_closed_and_requires_authority_resolution() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity="lease-002",
        active_conflict_domains=DOMAINS,
        session_continuity_binding=BINDING,
        independent_executor=True,
    )
    assert proof["proof_state"] == "LEASE_RECONCILIATION_REQUIRED"
    assert proof["resume_context_allowed"] is False
    assert proof["duplicate_writer_required"] is True
    assert proof["next_step"] == "FRESH_AUTHORITY_RESOLUTION_REQUIRED"


def test_changed_conflict_domains_fail_closed() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity=LEASE,
        active_conflict_domains=["different-domain"],
        session_continuity_binding=BINDING,
        independent_executor=True,
    )
    assert proof["proof_state"] == "CONFLICT_DOMAIN_RECONCILIATION_REQUIRED"
    assert proof["resume_context_allowed"] is False


def test_consumer_binding_cannot_claim_provider_authority() -> None:
    bad_binding = dict(BINDING)
    bad_binding["provider_authority"] = True
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity=LEASE,
        active_conflict_domains=DOMAINS,
        session_continuity_binding=bad_binding,
        independent_executor=True,
    )
    assert proof["proof_state"] == "INVALID_CONSUMER_BINDING"
    assert proof["resume_context_allowed"] is False


def test_same_executor_does_not_satisfy_cross_session_proof() -> None:
    proof = evaluate_handover_proof(
        checkpoint=checkpoint(),
        actual_main_sha=MAIN_A,
        actual_head_sha=HEAD_A,
        active_lease_identity=LEASE,
        active_conflict_domains=DOMAINS,
        session_continuity_binding=BINDING,
        independent_executor=False,
    )
    assert proof["proof_state"] == "NOT_INDEPENDENT_EXECUTOR"
    assert proof["resume_context_allowed"] is False
