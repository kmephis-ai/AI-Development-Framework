"""Strict GitHub issue-comment gateway for the first provider-durable writer CLAIM.

Issue-comment text is request data only. Authority comes from trusted code, the
current effective policy, provider admin identity, protected rulesets, exact
main readback, and the canonical GitHubLeaseStore/claim_executor path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import re
import subprocess

from .action_executors import ExecutorWait, _stage1_resources, _writer_branch, _writer_id, claim_executor
from .github_lease_store import GitHubLeaseStore
from .github_provider import GitHubClient
from .github_rulesets import verify_rulesets, verify_runtime_anchor_ruleset
from .issue_contract import ROADMAP_ID
from .provider_contracts import ProviderContractError
from .strict_json import loads as strict_loads

CLAIM_MARKER = "ADWF-CLAIM-REQUEST v1"
CLAIM_ROLE = "ADWF_PROVIDER_CLAIM_V1"
CLAIM_SCHEMA_VERSION = 1
REQUEST_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RISK_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
REQUEST_FIELDS = {
    "schema_version",
    "role",
    "request_id",
    "issue_id",
    "roadmap_id",
    "expected_main_sha",
    "risk",
    "monetary_budget_usd",
    "request_digest",
}


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest_payload(request: dict[str, Any]) -> str:
    payload = {k: v for k, v in request.items() if k != "request_digest"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def build_claim_comment(
    *,
    request_id: str,
    issue_id: int,
    roadmap_id: str,
    expected_main_sha: str,
    risk: str = "R1",
) -> str:
    """Build canonical request text. Useful to trusted planners and tests."""
    request: dict[str, Any] = {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "role": CLAIM_ROLE,
        "request_id": request_id,
        "issue_id": issue_id,
        "roadmap_id": roadmap_id,
        "expected_main_sha": expected_main_sha,
        "risk": risk,
        "monetary_budget_usd": 0,
    }
    request["request_digest"] = _digest_payload(request)
    return CLAIM_MARKER + "\n" + _canonical(request)


def has_claim_marker(body: Any) -> bool:
    if not isinstance(body, str):
        return False
    normalized = body.replace("\r\n", "\n").strip()
    return normalized == CLAIM_MARKER or normalized.startswith(CLAIM_MARKER + "\n")


def parse_claim_comment(body: str) -> dict[str, Any]:
    normalized = body.replace("\r\n", "\n").strip()
    lines = normalized.split("\n")
    if len(lines) != 2 or lines[0] != CLAIM_MARKER or not lines[1]:
        raise ValueError("CLAIM_REQUEST_ENVELOPE_INVALID")
    try:
        request = strict_loads(lines[1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("CLAIM_REQUEST_JSON_INVALID") from exc
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("CLAIM_REQUEST_FIELDS_INVALID")
    if request.get("schema_version") != CLAIM_SCHEMA_VERSION or request.get("role") != CLAIM_ROLE:
        raise ValueError("CLAIM_REQUEST_IDENTITY_INVALID")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
        raise ValueError("CLAIM_REQUEST_ID_INVALID")
    issue_id = request.get("issue_id")
    if isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id < 1:
        raise ValueError("CLAIM_REQUEST_ISSUE_ID_INVALID")
    roadmap_id = request.get("roadmap_id")
    if not isinstance(roadmap_id, str) or ROADMAP_ID.fullmatch(roadmap_id) is None:
        raise ValueError("CLAIM_REQUEST_ROADMAP_ID_INVALID")
    expected_main = request.get("expected_main_sha")
    if not isinstance(expected_main, str) or SHA40.fullmatch(expected_main) is None:
        raise ValueError("CLAIM_REQUEST_MAIN_SHA_INVALID")
    risk = request.get("risk")
    if risk not in {"R0", "R1"}:
        raise ValueError("CLAIM_REQUEST_RISK_UNSUPPORTED")
    budget = request.get("monetary_budget_usd")
    if isinstance(budget, bool) or budget != 0:
        raise ValueError("CLAIM_REQUEST_MONETARY_BUDGET_INVALID")
    digest = request.get("request_digest")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None or digest != _digest_payload(request):
        raise ValueError("CLAIM_REQUEST_DIGEST_INVALID")
    if lines[1] != _canonical(request):
        raise ValueError("CLAIM_REQUEST_NOT_CANONICAL")
    return request


def _local_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    value = proc.stdout.strip()
    if proc.returncode != 0 or SHA40.fullmatch(value) is None:
        raise ValueError("CLAIM_LOCAL_HEAD_NOT_VERIFIED")
    return value


def _policy_gate(root: Path, request: dict[str, Any]) -> str | None:
    try:
        policy = strict_loads((root / ".adwf/effective-policy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return "CLAIM_EFFECTIVE_POLICY_UNAVAILABLE"
    if not isinstance(policy, dict):
        return "CLAIM_EFFECTIVE_POLICY_INVALID"
    ranks = ((policy.get("rules") or {}).get("autonomy_rank") or {})
    action_min = ((policy.get("rules") or {}).get("action_min_autonomy") or {})
    active = policy.get("active_autonomy")
    claim_min = action_min.get("claim")
    if not isinstance(active, str) or not isinstance(claim_min, str):
        return "CLAIM_POLICY_AUTONOMY_INVALID"
    if active not in ranks or claim_min not in ranks or ranks[active] < ranks[claim_min]:
        return "CLAIM_POLICY_AUTONOMY_INSUFFICIENT"
    max_risk = policy.get("max_autonomous_risk")
    risk = request["risk"]
    if max_risk not in RISK_RANK or RISK_RANK[risk] > RISK_RANK[max_risk]:
        return "CLAIM_POLICY_RISK_EXCEEDED"
    if policy.get("hard_budget_usd") != 0 or policy.get("mandatory_ai_api") is not False:
        return "CLAIM_POLICY_FREE_ONLY_INVALID"
    if policy.get("max_parallel_writers") != 1:
        return "CLAIM_POLICY_WRITER_CEILING_INVALID"
    return None


def _rejected(reason: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "REJECTED", "reason": reason, "mutation": False}
    if request is not None:
        result.update({"request_id": request["request_id"], "request_digest": request["request_digest"]})
    return result


def _not_verified(reason: str, request: dict[str, Any] | None = None, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "NOT_VERIFIED", "reason": reason, "mutation": False, **details}
    if request is not None:
        result.update({"request_id": request["request_id"], "request_digest": request["request_digest"]})
    return result


def _issue_title_has_roadmap(title: str, roadmap_id: str) -> bool:
    tokens = re.findall(r"\[([A-Z][A-Z0-9_-]{1,80})\]", title[:200])
    return roadmap_id in tokens


def process_issue_comment_claim(root: Path, event: dict[str, Any], client: GitHubClient) -> dict[str, Any] | None:
    """Consume one strict issue-comment claim request, or return None if unrelated."""
    comment = event.get("comment") if isinstance(event, dict) else None
    body = (comment or {}).get("body") if isinstance(comment, dict) else None
    if not has_claim_marker(body):
        return None
    try:
        request = parse_claim_comment(str(body))
    except ValueError as exc:
        return _rejected(str(exc))

    if event.get("action") != "created":
        return _rejected("CLAIM_EVENT_ACTION_INVALID", request)
    repository = event.get("repository") or {}
    if str(repository.get("full_name") or "") != client.repo:
        return _rejected("CLAIM_EVENT_REPOSITORY_MISMATCH", request)
    issue_event = event.get("issue") or {}
    if "pull_request" in issue_event or int(issue_event.get("number") or 0) != request["issue_id"]:
        return _rejected("CLAIM_EVENT_ISSUE_MISMATCH", request)
    sender = event.get("sender") or {}
    comment_user = comment.get("user") or {}
    actor = str(comment_user.get("login") or "")
    if not actor or actor != str(sender.get("login") or ""):
        return _rejected("CLAIM_EVENT_ACTOR_MISMATCH", request)
    association = str(comment.get("author_association") or "")
    if association not in {"OWNER", "MEMBER", "COLLABORATOR"}:
        return _rejected("CLAIM_ACTOR_ASSOCIATION_FORBIDDEN", request)
    try:
        permission = client.collaborator_permission(actor)
    except Exception:
        return _not_verified("CLAIM_ACTOR_PERMISSION_READBACK_FAILED", request)
    if str(permission.get("permission") or "") != "admin":
        return _rejected("CLAIM_ACTOR_ADMIN_REQUIRED", request)

    try:
        repo_info = client.repo_info()
        default_branch = str(repo_info.get("default_branch") or "")
        if not default_branch:
            return _not_verified("CLAIM_DEFAULT_BRANCH_MISSING", request)
        provider_main = str((client.branch(default_branch).get("commit") or {}).get("sha") or "")
    except Exception:
        return _not_verified("CLAIM_MAIN_READBACK_FAILED", request)
    if SHA40.fullmatch(provider_main) is None:
        return _not_verified("CLAIM_MAIN_SHA_NOT_VERIFIED", request)
    if provider_main != request["expected_main_sha"]:
        return _rejected("CLAIM_REQUEST_STALE_BASE", request)
    try:
        if _local_head(root) != provider_main:
            return _not_verified("CLAIM_TRUSTED_CHECKOUT_MAIN_MISMATCH", request)
    except ValueError as exc:
        return _not_verified(str(exc), request)

    policy_reason = _policy_gate(root, request)
    if policy_reason:
        return _rejected(policy_reason, request)

    try:
        issue = client.get(f"/repos/{client.repo}/issues/{request['issue_id']}")
    except Exception:
        return _not_verified("CLAIM_ISSUE_READBACK_FAILED", request)
    if issue.get("state") != "open" or issue.get("pull_request") is not None:
        return _rejected("CLAIM_ISSUE_NOT_OPEN_WORK_ITEM", request)
    if int(issue.get("number") or 0) != request["issue_id"]:
        return _not_verified("CLAIM_ISSUE_PROVIDER_ID_MISMATCH", request)
    if not _issue_title_has_roadmap(str(issue.get("title") or ""), request["roadmap_id"]):
        return _rejected("CLAIM_ISSUE_ROADMAP_ID_MISMATCH", request)

    try:
        rulesets = client.rulesets()
        main_rules = verify_rulesets(rulesets, expected_integration_id=15368)
        anchor_rules = verify_runtime_anchor_ruleset(rulesets)
    except Exception:
        return _not_verified("CLAIM_RULESET_READBACK_FAILED", request)
    if main_rules.get("readback_verified") is not True:
        return _rejected("CLAIM_PROTECTED_MAIN_RULESET_NOT_VERIFIED", request)
    if anchor_rules.get("readback_verified") is not True:
        return _rejected("CLAIM_IMMUTABLE_ANCHOR_RULESET_NOT_VERIFIED", request)

    run_id = "claim-" + request["request_id"]
    state = {
        "phase": "CLAIM",
        "run_id": run_id,
        "roadmap_id": request["roadmap_id"],
        "issue_id": str(request["issue_id"]),
        "subject_sha": provider_main,
        "risk": request["risk"],
    }
    worker = _writer_id(state)
    branch = _writer_branch(state)
    resources = _stage1_resources()
    store = GitHubLeaseStore(client)
    try:
        registry, _ = store.read(expected_main_sha=provider_main, policy_max_parallel_writers=1)
    except Exception:
        return _not_verified("CLAIM_LEASE_READBACK_FAILED", request)
    history = [lease for lease in registry.get("leases") or [] if lease.get("worker_id") == worker]
    for lease in history:
        exact_identity = (
            lease.get("issue_id") == str(request["issue_id"])
            and lease.get("roadmap_id") == request["roadmap_id"]
            and lease.get("base_sha") == provider_main
            and lease.get("branch") == branch
            and lease.get("resources") == resources
        )
        if not exact_identity:
            return _rejected("CLAIM_REQUEST_ID_REUSE_CONFLICT", request)
        if lease.get("status") == "RELEASED":
            return _rejected("CLAIM_REQUEST_REPLAY_RELEASED", request)

    try:
        claim = claim_executor(root, state, request["request_digest"], {})
    except Exception:
        return _not_verified("CLAIM_EXECUTOR_UNEXPECTED_FAILURE", request)
    if isinstance(claim, ExecutorWait):
        return _not_verified("CLAIM_EXECUTOR_WAIT", request, executor_reason=claim.reason)
    if claim.get("outcome") != "PASS":
        return _not_verified("CLAIM_EXECUTOR_NOT_PASS", request)
    meta = claim.get("metadata") or {}
    if meta.get("lease_model") != "PROVIDER_DURABLE_CAS":
        return _not_verified("CLAIM_EXECUTOR_LEASE_MODEL_INVALID", request)
    lease_id = str(meta.get("lease_id") or "")
    lease_anchor = str(meta.get("lease_anchor") or "")
    revision = meta.get("lease_registry_revision")
    if not lease_id or not lease_anchor or isinstance(revision, bool) or not isinstance(revision, int):
        return _not_verified("CLAIM_EXECUTOR_METADATA_INVALID", request)

    try:
        try:
            branch_ref = client.git_ref(branch)
        except ProviderContractError as exc:
            if str(exc) != "PROVIDER_HTTP_404":
                raise
            client.create_ref(branch, provider_main)
            branch_ref = client.git_ref(branch)
        branch_sha = str((branch_ref.get("object") or {}).get("sha") or "")
        if branch_sha != provider_main:
            return _not_verified("CLAIM_SOURCE_BRANCH_SHA_MISMATCH", request, branch=branch)
        reread, reread_anchor = store.read(expected_main_sha=provider_main, policy_max_parallel_writers=1)
        matches = [
            lease
            for lease in reread.get("leases") or []
            if lease.get("status") == "ACTIVE"
            and lease.get("lease_id") == lease_id
            and lease.get("worker_id") == worker
            and lease.get("issue_id") == str(request["issue_id"])
            and lease.get("roadmap_id") == request["roadmap_id"]
            and lease.get("base_sha") == provider_main
            and lease.get("branch") == branch
            and lease.get("resources") == resources
        ]
        latest_main = str((client.branch(default_branch).get("commit") or {}).get("sha") or "")
        latest_issue = client.get(f"/repos/{client.repo}/issues/{request['issue_id']}")
    except Exception:
        return _not_verified("CLAIM_PROVIDER_READBACK_FAILED", request, lease_id=lease_id, branch=branch)
    if len(matches) != 1 or reread_anchor != lease_anchor or reread.get("revision") != revision:
        return _not_verified("CLAIM_LEASE_READBACK_MISMATCH", request, lease_id=lease_id, branch=branch)
    if reread.get("observed_main_sha") != provider_main or latest_main != provider_main:
        return _not_verified("CLAIM_MAIN_CHANGED_AFTER_MUTATION", request, lease_id=lease_id, branch=branch)
    if latest_issue.get("state") != "open":
        return _not_verified("CLAIM_ISSUE_CHANGED_AFTER_MUTATION", request, lease_id=lease_id, branch=branch)

    resumed = meta.get("resumed_existing") is True
    return {
        "status": "ALREADY_APPLIED" if resumed else "PASS",
        "reason": "CLAIM_ALREADY_APPLIED" if resumed else "CLAIM_PROVIDER_DURABLE",
        "mutation": not resumed,
        "request_id": request["request_id"],
        "request_digest": request["request_digest"],
        "issue_id": request["issue_id"],
        "roadmap_id": request["roadmap_id"],
        "worker_id": worker,
        "branch": branch,
        "branch_sha": branch_sha,
        "lease_id": lease_id,
        "lease_registry_revision": revision,
        "lease_anchor": lease_anchor,
        "observed_main_sha": provider_main,
        "monetary_cost_usd": 0,
        "merge_authority": False,
    }
