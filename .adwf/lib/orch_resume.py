"""Pure executor-neutral acquire/resume/yield decision coordination.

ORCH_RESUME-001 composes fresh provider facts with existing durable
execution, session-continuity, and provider-lease projections.  It never
mutates provider state and never grants write authority by itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re

from .autonomous_execution_state import reconcile_provider_observation, validate_state
from .lease_registry import _registry_lease_freshness_errors, validate_lease_registry
from .leases import utc_now
from .session_continuity import reconcile_checkpoint, validate_checkpoint

DECISIONS = {"RESUME_EXISTING", "RECONCILE", "ACQUIRE_NEW", "YIELD", "BLOCK"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ROADMAP_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_ISSUE_RE = re.compile(r"^[0-9]+$")
_REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_PENDING_EXTERNAL = {"queued", "in_progress", "pending", "waiting", "requested"}
_BLOCKING_BOUNDARIES = {"UNAVAILABLE_CAPABILITY", "AUTHORITY_EXHAUSTED", "ROADMAP_END"}


def _result(decision: str, reason: str, *, lease_id: str | None = None, findings: list[str] | None = None) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError("ORCH_RESUME_DECISION_INVALID")
    value: dict[str, Any] = {
        "decision": decision,
        "reason": reason,
        "lease_id": lease_id,
        # Authority remains in the provider-backed lease/store path.
        "provider_write_authorized": False,
    }
    if findings:
        value["findings"] = sorted(set(str(item) for item in findings))
    return value


def _valid_provider_facts(
    *, repository: Any, main_sha: Any, head_sha: Any, pr_number: Any,
    branch: Any, roadmap_id: Any, issue_id: Any, expected_base_sha: Any,
    current_worker_id: Any, policy_max_parallel_writers: Any,
    work_terminal: Any, next_work_authorized: Any, human_boundary_active: Any,
) -> list[str]:
    findings: list[str] = []
    if not isinstance(repository, str) or _REPOSITORY_RE.fullmatch(repository) is None:
        findings.append("PROVIDER_REPOSITORY_INVALID")
    if not isinstance(main_sha, str) or _SHA_RE.fullmatch(main_sha) is None:
        findings.append("PROVIDER_MAIN_SHA_INVALID")
    if head_sha is not None and (not isinstance(head_sha, str) or _SHA_RE.fullmatch(head_sha) is None):
        findings.append("PROVIDER_HEAD_SHA_INVALID")
    if pr_number is not None and (isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1):
        findings.append("PROVIDER_PR_NUMBER_INVALID")
    if not isinstance(branch, str) or not branch.strip():
        findings.append("PROVIDER_BRANCH_INVALID")
    if not isinstance(roadmap_id, str) or _ROADMAP_RE.fullmatch(roadmap_id) is None:
        findings.append("PROVIDER_ROADMAP_ID_INVALID")
    if not isinstance(issue_id, str) or _ISSUE_RE.fullmatch(issue_id) is None:
        findings.append("PROVIDER_ISSUE_ID_INVALID")
    if not isinstance(expected_base_sha, str) or _SHA_RE.fullmatch(expected_base_sha) is None:
        findings.append("PROVIDER_BASE_SHA_INVALID")
    if not isinstance(current_worker_id, str) or not current_worker_id.strip():
        findings.append("PROVIDER_WORKER_ID_INVALID")
    if isinstance(policy_max_parallel_writers, bool) or not isinstance(policy_max_parallel_writers, int):
        findings.append("POLICY_WRITER_CEILING_INVALID")
    if not isinstance(work_terminal, bool):
        findings.append("PROVIDER_WORK_TERMINAL_INVALID")
    if not isinstance(next_work_authorized, bool):
        findings.append("NEXT_WORK_AUTHORIZATION_INVALID")
    if not isinstance(human_boundary_active, bool):
        findings.append("HUMAN_BOUNDARY_FACT_INVALID")
    return findings


def decide_resume(
    *,
    repository: str,
    main_sha: str,
    head_sha: str | None,
    pr_number: int | None,
    branch: str,
    roadmap_id: str,
    issue_id: str,
    expected_base_sha: str,
    current_worker_id: str,
    policy_max_parallel_writers: int,
    lease_registry: dict[str, Any],
    execution_state: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    work_terminal: bool = False,
    next_work_authorized: bool = False,
    external_status: str | None = None,
    human_boundary_active: bool = False,
    session_accelerator_present: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a side-effect-free resume decision.

    `session_accelerator_present` is accepted only to make its non-authority
    explicit.  It intentionally has no effect on the decision.
    """
    del session_accelerator_present
    findings = _valid_provider_facts(
        repository=repository, main_sha=main_sha, head_sha=head_sha,
        pr_number=pr_number, branch=branch, roadmap_id=roadmap_id,
        issue_id=issue_id, expected_base_sha=expected_base_sha,
        current_worker_id=current_worker_id,
        policy_max_parallel_writers=policy_max_parallel_writers,
        work_terminal=work_terminal, next_work_authorized=next_work_authorized,
        human_boundary_active=human_boundary_active,
    )
    if findings:
        return _result("BLOCK", "PROVIDER_FACTS_INVALID", findings=findings)
    if policy_max_parallel_writers != 1:
        return _result("BLOCK", "STAGE1_REQUIRES_SINGLETON_WRITER_CEILING")

    registry_findings = validate_lease_registry(lease_registry)
    if registry_findings:
        return _result("BLOCK", "LEASE_REGISTRY_INVALID", findings=registry_findings)
    if lease_registry["repository"] != repository:
        return _result("BLOCK", "LEASE_REGISTRY_REPOSITORY_MISMATCH")
    if lease_registry["max_parallel_writers"] != policy_max_parallel_writers:
        return _result("BLOCK", "LEASE_REGISTRY_POLICY_CEILING_MISMATCH")

    if execution_state is not None:
        state_findings = validate_state(execution_state)
        if state_findings:
            return _result("BLOCK", "EXECUTION_STATE_INVALID", findings=state_findings)
    if checkpoint is not None:
        checkpoint_findings = validate_checkpoint(checkpoint)
        if checkpoint_findings:
            return _result("BLOCK", "SESSION_CHECKPOINT_INVALID", findings=checkpoint_findings)

    active = [item for item in lease_registry["leases"] if item["status"] == "ACTIVE"]
    if len(active) > 1:
        return _result("BLOCK", "MULTIPLE_ACTIVE_LEASES_UNSUPPORTED_STAGE1")

    if work_terminal:
        return _result("RECONCILE", "WORK_TERMINAL_RECONCILE_NEXT")

    if not active:
        if execution_state is not None or checkpoint is not None:
            return _result("RECONCILE", "RESUME_CONTEXT_WITHOUT_ACTIVE_LEASE")
        if not next_work_authorized:
            return _result("BLOCK", "NEXT_WORK_NOT_AUTHORIZED")
        return _result("ACQUIRE_NEW", "NO_ACTIVE_LEASE_AND_NEXT_WORK_AUTHORIZED")

    lease = active[0]
    lease_id = lease["lease_id"]
    current_time = (now or utc_now()).astimezone(timezone.utc)
    if lease_registry["observed_main_sha"] != main_sha:
        return _result("RECONCILE", "ACTIVE_LEASE_REGISTRY_MAIN_STALE", lease_id=lease_id)
    freshness = _registry_lease_freshness_errors(lease, current_time)
    if freshness:
        return _result("RECONCILE", "ACTIVE_LEASE_STALE_OR_EXPIRED", lease_id=lease_id, findings=freshness)

    identity_mismatch = []
    if lease["roadmap_id"] != roadmap_id:
        identity_mismatch.append("ROADMAP_ID")
    if lease["issue_id"] != issue_id:
        identity_mismatch.append("ISSUE_ID")
    if lease["branch"] != branch:
        identity_mismatch.append("BRANCH")
    if lease["base_sha"] != expected_base_sha:
        identity_mismatch.append("BASE_SHA")
    if identity_mismatch:
        return _result("BLOCK", "LEASE_WORK_IDENTITY_MISMATCH", lease_id=lease_id, findings=identity_mismatch)
    if lease["worker_id"] != current_worker_id:
        return _result("RECONCILE", "LEASE_OWNER_HANDOFF_REQUIRED", lease_id=lease_id)

    if execution_state is None:
        return _result("RECONCILE", "ACTIVE_LEASE_WITHOUT_EXECUTION_STATE", lease_id=lease_id)
    state_work = execution_state["work_identity"]
    if execution_state["project_identity"]["repository"] != repository:
        return _result("BLOCK", "EXECUTION_PROJECT_IDENTITY_MISMATCH", lease_id=lease_id)
    if state_work["roadmap_id"] != roadmap_id or state_work["issue_id"] != issue_id:
        return _result("BLOCK", "EXECUTION_WORK_IDENTITY_MISMATCH", lease_id=lease_id)
    state_writer = execution_state["writer"]
    if state_writer.get("lease_id") != lease_id:
        return _result("BLOCK", "EXECUTION_LEASE_IDENTITY_MISMATCH", lease_id=lease_id)
    if state_writer.get("lease_state") not in {"ACTIVE", "SUSPENDED"}:
        return _result("RECONCILE", "EXECUTION_LEASE_STATE_NOT_RESUMABLE", lease_id=lease_id)

    state_reconcile = reconcile_provider_observation(
        execution_state, main_sha=main_sha, head_sha=head_sha,
        pr_number=pr_number, branch=branch,
    )
    if state_reconcile["stale"]:
        return _result("RECONCILE", "EXECUTION_PROVIDER_OBSERVATION_STALE", lease_id=lease_id)

    if checkpoint is not None:
        if checkpoint["project_identity"] != repository:
            return _result("BLOCK", "CHECKPOINT_PROJECT_IDENTITY_MISMATCH", lease_id=lease_id)
        checkpoint_work = checkpoint["work_identity"]
        if checkpoint_work.get("roadmap_id") != roadmap_id or str(checkpoint_work.get("issue_id")) != issue_id:
            return _result("BLOCK", "CHECKPOINT_WORK_IDENTITY_MISMATCH", lease_id=lease_id)
        checkpoint_lease = checkpoint.get("lease_identity")
        if checkpoint_lease is not None and checkpoint_lease != lease_id:
            return _result("BLOCK", "CHECKPOINT_LEASE_IDENTITY_MISMATCH", lease_id=lease_id)
        checkpoint_observed = checkpoint["observed_provider_state"]
        if checkpoint_observed.get("pr_number") != pr_number or checkpoint_observed.get("branch") != branch:
            return _result("RECONCILE", "CHECKPOINT_PROVIDER_OBSERVATION_STALE", lease_id=lease_id)
        checkpoint_reconcile = reconcile_checkpoint(
            checkpoint, actual_main_sha=main_sha, actual_head_sha=head_sha,
        )
        if checkpoint_reconcile["stale"]:
            return _result("RECONCILE", "CHECKPOINT_PROVIDER_OBSERVATION_STALE", lease_id=lease_id)

    if execution_state["execution_state"] == "COMPLETE":
        return _result("RECONCILE", "EXECUTION_COMPLETE_RECONCILE_NEXT", lease_id=lease_id)
    if execution_state["execution_state"] == "RECOVERY":
        return _result("RECONCILE", "EXECUTION_RECOVERY_REQUIRED", lease_id=lease_id)
    if human_boundary_active or execution_state["execution_state"] == "HUMAN_REQUIRED":
        return _result("BLOCK", "HUMAN_BOUNDARY_ACTIVE", lease_id=lease_id)

    boundary = execution_state["boundary_type"]
    if execution_state["execution_state"] == "SUSPENDED" and boundary in _BLOCKING_BOUNDARIES:
        return _result("BLOCK", "SUSPENDED_BOUNDARY_BLOCKS_RESUME", lease_id=lease_id, findings=[boundary])
    if execution_state["execution_state"] == "SUSPENDED" and boundary == "NONE":
        return _result("RECONCILE", "SUSPENDED_BOUNDARY_AMBIGUOUS", lease_id=lease_id)

    waiting_declared = (
        execution_state["execution_state"] == "WAITING_CI"
        or boundary == "WAITING_EXTERNAL"
        or (checkpoint is not None and checkpoint.get("boundary_type") == "EXTERNAL_WAIT")
    )
    if waiting_declared:
        if external_status is None:
            return _result("RECONCILE", "EXTERNAL_WAIT_STATUS_REQUIRED", lease_id=lease_id)
        if str(external_status).strip().lower() in _PENDING_EXTERNAL:
            return _result("YIELD", "EXTERNAL_WAIT_IN_PROGRESS", lease_id=lease_id)

    return _result("RESUME_EXISTING", "ACTIVE_LEASE_AND_PROVIDER_FACTS_MATCH", lease_id=lease_id)
