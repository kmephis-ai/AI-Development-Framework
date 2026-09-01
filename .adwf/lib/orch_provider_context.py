"""Fresh, provider-read-only context compiler for ORCH_CONTEXT-001.

The durable orchestration journal remains local execution authority. This module
only compiles bounded observations for ``orch_resume.decide_resume`` and never
persists projections or mutates provider state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import copy
import os
import re

from .autonomous_execution_state import build_state
from .github_lease_store import GitHubLeaseStore
from .github_provider import GitHubClient
from .github_runtime_store import GitHubRuntimeStore
from .lease_registry import _registry_lease_freshness_errors, validate_lease_registry
from .session_continuity import validate_checkpoint

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_EXTERNAL_PENDING = {"queued", "in_progress", "pending", "waiting", "requested"}
_EXTERNAL_SUCCESS = {"success", "neutral", "skipped"}


class ProviderContextError(ValueError):
    """Public-safe fail-closed compiler error."""


def _fail(code: str) -> None:
    raise ProviderContextError(code)


def _sha(value: Any, code: str) -> str:
    result = str(value or "")
    if _SHA40_RE.fullmatch(result) is None:
        _fail(code)
    return result


def _issue(client: Any, repository: str, issue_id: str) -> dict[str, Any]:
    try:
        value = client.get(f"/repos/{repository}/issues/{int(issue_id)}")
    except Exception:
        _fail("PROVIDER_ISSUE_READ_FAILED")
    if not isinstance(value, dict) or str(value.get("number") or "") != issue_id:
        _fail("PROVIDER_ISSUE_READBACK_INVALID")
    return value


def _pull(client: Any, number: int) -> dict[str, Any]:
    try:
        value = client.pull(number)
    except Exception:
        _fail("PROVIDER_PULL_REQUEST_READ_FAILED")
    if not isinstance(value, dict) or value.get("number") != number:
        _fail("PROVIDER_PULL_REQUEST_READBACK_INVALID")
    return value


def _external_status(client: Any, head_sha: str) -> str:
    try:
        rows = client.check_runs(head_sha)
    except Exception:
        _fail("PROVIDER_EXTERNAL_STATUS_READ_FAILED")
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        _fail("PROVIDER_EXTERNAL_STATUS_INVALID")
    if not rows:
        return "pending"
    statuses = {str(item.get("status") or "").strip().lower() for item in rows}
    if statuses & _EXTERNAL_PENDING or any(status != "completed" for status in statuses):
        return "in_progress"
    conclusions = {str(item.get("conclusion") or "").strip().lower() for item in rows}
    if conclusions and conclusions <= _EXTERNAL_SUCCESS:
        return "success"
    return "failure"


def _execution_boundary(durable: dict[str, Any]) -> tuple[str, str, bool, bool]:
    status = str(durable.get("status") or "")
    phase = str(durable.get("phase") or "")
    if status == "HUMAN_REQUIRED":
        return "HUMAN_REQUIRED", "HUMAN_REQUIRED", True, False
    if status == "RECOVERY":
        return "RECOVERY", "NONE", False, False
    if status == "COMPLETE":
        return "COMPLETE", "NONE", False, False
    external_wait = status == "RETRY_WAIT" and phase == "CI"
    if external_wait:
        return "WAITING_CI", "WAITING_EXTERNAL", False, True
    return "RUNNING", "NONE", False, False


def _fresh_compatible_lease(
    registry: dict[str, Any],
    durable: dict[str, Any],
    *,
    branch: str,
    main_sha: str,
    now: datetime,
) -> dict[str, Any] | None:
    compatible = []
    for lease in registry.get("leases") or []:
        if lease.get("status") != "ACTIVE":
            continue
        if lease.get("roadmap_id") != str(durable.get("roadmap_id") or ""):
            continue
        if lease.get("issue_id") != str(durable.get("issue_id") or ""):
            continue
        if lease.get("branch") != branch or lease.get("base_sha") != main_sha:
            continue
        if _registry_lease_freshness_errors(lease, now):
            continue
        compatible.append(lease)
    if len(compatible) > 1:
        _fail("PROVIDER_COMPATIBLE_LEASE_AMBIGUOUS")
    return compatible[0] if compatible else None


def _checkpoint(
    runtime_store: Any,
    *,
    repository: str,
    durable: dict[str, Any],
    main_sha: str,
    head_sha: str | None,
) -> dict[str, Any] | None:
    try:
        restored = runtime_store.restore_latest_session_continuity(
            actual_main_sha=main_sha,
            actual_head_sha=head_sha,
        )
    except Exception:
        _fail("PROVIDER_SESSION_CONTINUITY_READ_FAILED")
    if restored is None:
        return None
    checkpoint = restored.get("checkpoint") if isinstance(restored, dict) else None
    if not isinstance(checkpoint, dict) or validate_checkpoint(checkpoint):
        _fail("PROVIDER_SESSION_CONTINUITY_INVALID")
    work = checkpoint.get("work_identity") or {}
    if checkpoint.get("project_identity") != repository:
        _fail("PROVIDER_SESSION_CONTINUITY_PROJECT_MISMATCH")
    if str(work.get("roadmap_id") or "") != str(durable.get("roadmap_id") or ""):
        _fail("PROVIDER_SESSION_CONTINUITY_ROADMAP_MISMATCH")
    if str(work.get("issue_id") or "") != str(durable.get("issue_id") or ""):
        _fail("PROVIDER_SESSION_CONTINUITY_ISSUE_MISMATCH")
    return copy.deepcopy(checkpoint)


def compile_provider_context(
    durable: dict[str, Any],
    *,
    client: Any = None,
    lease_store: Any = None,
    runtime_store: Any = None,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile validated fresh facts accepted by ``decide_resume``.

    Fakes may be injected for unit tests. The production path requires the
    explicit GitHub repository and token environment variables.
    """
    if not isinstance(durable, dict):
        _fail("DURABLE_RUN_INVALID")
    run_id = str(durable.get("run_id") or "")
    roadmap_id = str(durable.get("roadmap_id") or "")
    issue_id = str(durable.get("issue_id") or "")
    if not run_id or not roadmap_id or not issue_id.isdigit():
        _fail("DURABLE_WORK_IDENTITY_INVALID")

    environment = os.environ if environ is None else environ
    repository = str(environment.get("GITHUB_REPOSITORY") or "")
    token = str(environment.get("GITHUB_TOKEN") or "")
    if client is None:
        if _REPOSITORY_RE.fullmatch(repository) is None or not token:
            _fail("GITHUB_REPOSITORY_OR_TOKEN_MISSING")
        client = GitHubClient(repository, token)
    else:
        repository = str(getattr(client, "repo", repository) or "")
        if _REPOSITORY_RE.fullmatch(repository) is None:
            _fail("PROVIDER_REPOSITORY_INVALID")

    try:
        info = client.repo_info()
        default_branch = str((info or {}).get("default_branch") or "")
        if not default_branch:
            _fail("PROVIDER_DEFAULT_BRANCH_INVALID")
        main = client.branch(default_branch)
        main_sha = _sha(((main or {}).get("commit") or {}).get("sha"), "PROVIDER_MAIN_SHA_INVALID")
    except ProviderContextError:
        raise
    except Exception:
        _fail("PROVIDER_REPOSITORY_READ_FAILED")

    issue = _issue(client, repository, issue_id)
    work_terminal = str(issue.get("state") or "").lower() != "open"

    raw_pr = durable.get("pull_request_number")
    pr_number = None if raw_pr is None else int(raw_pr)
    durable_branch = str(durable.get("work_branch") or "")
    branch = durable_branch or default_branch
    head_sha: str | None = None
    if pr_number is not None:
        if pr_number < 1:
            _fail("PROVIDER_PULL_REQUEST_NUMBER_INVALID")
        pull = _pull(client, pr_number)
        head = pull.get("head") or {}
        base = pull.get("base") or {}
        head_sha = _sha(head.get("sha"), "PROVIDER_HEAD_SHA_INVALID")
        provider_branch = str(head.get("ref") or "")
        if not provider_branch:
            _fail("PROVIDER_HEAD_BRANCH_INVALID")
        if durable_branch and provider_branch != durable_branch:
            _fail("PROVIDER_PULL_REQUEST_BRANCH_MISMATCH")
        branch = provider_branch
        if str(base.get("ref") or "") != default_branch:
            _fail("PROVIDER_PULL_REQUEST_BASE_BRANCH_MISMATCH")
    elif durable_branch:
        try:
            branch_readback = client.branch(durable_branch)
            head_sha = _sha(
                ((branch_readback or {}).get("commit") or {}).get("sha"),
                "PROVIDER_HEAD_SHA_INVALID",
            )
        except ProviderContextError:
            raise
        except Exception:
            _fail("PROVIDER_HEAD_BRANCH_READ_FAILED")
    else:
        head_sha = main_sha

    lease_store = lease_store if lease_store is not None else GitHubLeaseStore(client)
    try:
        lease_registry, _anchor = lease_store.read(
            expected_main_sha=main_sha,
            policy_max_parallel_writers=1,
        )
    except Exception:
        _fail("PROVIDER_LEASE_READ_FAILED")
    findings = validate_lease_registry(lease_registry)
    if findings:
        _fail("PROVIDER_LEASE_REGISTRY_INVALID")
    if lease_registry.get("repository") != repository:
        _fail("PROVIDER_LEASE_REPOSITORY_MISMATCH")

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    compatible = _fresh_compatible_lease(
        lease_registry,
        durable,
        branch=branch,
        main_sha=main_sha,
        now=observed_at,
    )
    lease_id = str(compatible["lease_id"]) if compatible is not None else None
    lease_state = "ACTIVE" if compatible is not None else "NONE"
    domains = [
        f"{item['kind']}:{item['scope']}"
        for item in ((compatible or {}).get("resources") or [])
    ] or ["provider:global"]

    execution_state, boundary_type, human_boundary, external_wait = _execution_boundary(durable)
    projection = build_state(
        repository=repository,
        roadmap_id=roadmap_id,
        issue_id=issue_id,
        lease_id=lease_id,
        lease_state=lease_state,
        conflict_domains=domains,
        main_sha=main_sha,
        head_sha=head_sha,
        pr_number=pr_number,
        branch=branch,
        execution_state=execution_state,
        boundary_type=boundary_type,
        blockers=[str(item)[:240] for item in (durable.get("blockers") or [])],
        last_verified_transition="ORCH_CONTEXT_FRESH_PROVIDER_READ",
        evidence_refs=[],
        next_permitted_action="BLOCK" if human_boundary else "RECONCILE_OR_RESUME",
        revision=int(durable.get("revision") or 0),
    )

    runtime_store = runtime_store if runtime_store is not None else GitHubRuntimeStore(client)
    checkpoint = _checkpoint(
        runtime_store,
        repository=repository,
        durable=durable,
        main_sha=main_sha,
        head_sha=head_sha,
    )
    external_status = _external_status(client, head_sha) if external_wait and head_sha else None

    return {
        "repository": repository,
        "main_sha": main_sha,
        "head_sha": head_sha,
        "pr_number": pr_number,
        "branch": branch,
        "roadmap_id": roadmap_id,
        "issue_id": issue_id,
        "expected_base_sha": main_sha,
        "current_worker_id": "adwf-runtime:" + run_id,
        "policy_max_parallel_writers": 1,
        "lease_registry": copy.deepcopy(lease_registry),
        "execution_state": projection,
        "checkpoint": checkpoint,
        "work_terminal": work_terminal,
        "next_work_authorized": False,
        "external_status": external_status,
        "human_boundary_active": human_boundary,
        "session_accelerator_present": checkpoint is not None,
        "now": observed_at,
    }
