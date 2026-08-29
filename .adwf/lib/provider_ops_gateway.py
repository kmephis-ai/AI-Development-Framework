"""Trusted provider-hosted repository operations gateway.

Stage 1 implements MATERIALIZE_PROJECTIONS and Stage 2 adds release-only
LEASE_RECONCILE. Issue-comment payloads are strictly bounded data. Authority comes from exact protected BASE code, provider
readback, the current effective policy/rulesets, the live writer lease, and the
BASE trust classifier. Candidate code is never imported or executed.
"""
from __future__ import annotations

from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile

from .github_lease_store import GitHubLeaseStore
from .github_provider import GitHubClient
from .github_rulesets import verify_rulesets, verify_runtime_anchor_ruleset
from .lease_registry import _registry_lease_freshness_errors
from .strict_json import loads as strict_loads
from .trust import classify_diff, is_trust_sensitive_path, normalize_repo_path

PROVIDER_OPS_MARKER = "ADWF-PROVIDER-OPS-REQUEST v1"
PROVIDER_OPS_ROLE = "ADWF_PROVIDER_OPS_REQUEST_V1"
PROVIDER_OPS_SCHEMA_VERSION = 1
MATERIALIZE_PROJECTIONS = "MATERIALIZE_PROJECTIONS"
LEASE_RECONCILE = "LEASE_RECONCILE"
PROJECTION_PATHS = [".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt"]
MATERIALIZE_REQUEST_FIELDS = {
    "schema_version", "role", "request_id", "operation", "issue_id", "roadmap_id",
    "expected_main_sha", "pr_number", "base_sha", "head_sha", "branch", "worker_id",
    "lease_id", "lease_registry_revision", "source_paths", "projection_paths",
    "monetary_budget_usd", "request_digest",
}
LEASE_RECONCILE_REQUEST_FIELDS = {
    "schema_version", "role", "request_id", "operation", "issue_id", "roadmap_id",
    "expected_main_sha", "lease_registry_revision", "lease_id", "lease_base_sha",
    "branch", "worker_id", "pr_number", "pr_head_sha", "monetary_budget_usd",
    "request_digest",
}
# Backward-compatible public alias used by Stage-1 tests/callers.
REQUEST_FIELDS = MATERIALIZE_REQUEST_FIELDS
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
_ROADMAP_ID = re.compile(r"^[A-Z][A-Z0-9_-]{1,80}$")
_BRANCH = re.compile(r"^[A-Za-z0-9_.+\-/]{1,240}$")
_WORKER = re.compile(r"^[A-Za-z0-9_.:@+\-/]{1,240}$")
_LEASE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_OWNER_ATTESTATION = re.compile(r"(?mi)^\s*Owner-Attestation:\s*`?([0-9a-f]{40})`?\s*$")
_SENSITIVE_ENV = re.compile(r"(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY|CREDENTIAL|PRIVATE[_-]?KEY)", re.I)
_ALLOWED_FILE_MODES = {"100644", "100755"}
_MAX_ANCESTRY_COMMITS = 512
_MAX_TREE_ENTRIES = 100_000
_MAX_CHANGED_FILES = 3_000
_MAX_CHANGED_BLOB_BYTES = 8 * 1024 * 1024
_MAX_CHANGED_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_GENERATED_BYTES = 2 * 1024 * 1024
_MAX_FS_FILES = 100_000
_MAX_FS_BYTES = 256 * 1024 * 1024


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest_payload(request: dict[str, Any]) -> str:
    payload = {key: value for key, value in request.items() if key != "request_digest"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("PROVIDER_OPS_PATH_INVALID")
    try:
        normalized = normalize_repo_path(value)
    except ValueError as exc:
        raise ValueError("PROVIDER_OPS_PATH_INVALID") from exc
    if normalized != value or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("PROVIDER_OPS_PATH_INVALID")
    if len(value.encode("utf-8")) > 512:
        raise ValueError("PROVIDER_OPS_PATH_TOO_LONG")
    return value


def _canonical_paths(value: Any, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("PROVIDER_OPS_PATHS_REQUIRED")
    paths = [_path(item) for item in value]
    if len(paths) > _MAX_CHANGED_FILES or len(paths) != len(set(paths)) or paths != sorted(paths):
        raise ValueError("PROVIDER_OPS_PATHS_NOT_CANONICAL")
    return paths


def build_provider_ops_comment(
    *, request_id: str, issue_id: int, roadmap_id: str, expected_main_sha: str,
    pr_number: int, base_sha: str, head_sha: str, branch: str, worker_id: str,
    lease_id: str, lease_registry_revision: int, source_paths: list[str],
) -> str:
    request: dict[str, Any] = {
        "schema_version": PROVIDER_OPS_SCHEMA_VERSION,
        "role": PROVIDER_OPS_ROLE,
        "request_id": request_id,
        "operation": MATERIALIZE_PROJECTIONS,
        "issue_id": issue_id,
        "roadmap_id": roadmap_id,
        "expected_main_sha": expected_main_sha,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "branch": branch,
        "worker_id": worker_id,
        "lease_id": lease_id,
        "lease_registry_revision": lease_registry_revision,
        "source_paths": sorted(source_paths),
        "projection_paths": list(PROJECTION_PATHS),
        "monetary_budget_usd": 0,
    }
    request["request_digest"] = _digest_payload(request)
    return PROVIDER_OPS_MARKER + "\n" + _canonical(request)


def build_lease_reconcile_comment(
    *, request_id: str, issue_id: int, roadmap_id: str, expected_main_sha: str,
    lease_registry_revision: int, lease_id: str, lease_base_sha: str, branch: str,
    worker_id: str, pr_number: int, pr_head_sha: str,
) -> str:
    request: dict[str, Any] = {
        "schema_version": PROVIDER_OPS_SCHEMA_VERSION,
        "role": PROVIDER_OPS_ROLE,
        "request_id": request_id,
        "operation": LEASE_RECONCILE,
        "issue_id": issue_id,
        "roadmap_id": roadmap_id,
        "expected_main_sha": expected_main_sha,
        "lease_registry_revision": lease_registry_revision,
        "lease_id": lease_id,
        "lease_base_sha": lease_base_sha,
        "branch": branch,
        "worker_id": worker_id,
        "pr_number": pr_number,
        "pr_head_sha": pr_head_sha,
        "monetary_budget_usd": 0,
    }
    request["request_digest"] = _digest_payload(request)
    return PROVIDER_OPS_MARKER + "\n" + _canonical(request)


def has_provider_ops_marker(body: Any) -> bool:
    if not isinstance(body, str):
        return False
    normalized = body.replace("\r\n", "\n").strip()
    return normalized == PROVIDER_OPS_MARKER or normalized.startswith(PROVIDER_OPS_MARKER + "\n")


def parse_provider_ops_comment(body: str) -> dict[str, Any]:
    normalized = body.replace("\r\n", "\n").strip()
    lines = normalized.split("\n")
    if len(lines) != 2 or lines[0] != PROVIDER_OPS_MARKER or not lines[1]:
        raise ValueError("PROVIDER_OPS_REQUEST_ENVELOPE_INVALID")
    try:
        request = strict_loads(lines[1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("PROVIDER_OPS_REQUEST_JSON_INVALID") from exc
    if not isinstance(request, dict):
        raise ValueError("PROVIDER_OPS_REQUEST_FIELDS_INVALID")
    if request.get("schema_version") != PROVIDER_OPS_SCHEMA_VERSION or request.get("role") != PROVIDER_OPS_ROLE:
        raise ValueError("PROVIDER_OPS_REQUEST_IDENTITY_INVALID")
    operation = request.get("operation")
    if operation == MATERIALIZE_PROJECTIONS:
        expected_fields = MATERIALIZE_REQUEST_FIELDS
    elif operation == LEASE_RECONCILE:
        expected_fields = LEASE_RECONCILE_REQUEST_FIELDS
    else:
        raise ValueError("PROVIDER_OPS_OPERATION_UNSUPPORTED")
    if set(request) != expected_fields:
        raise ValueError("PROVIDER_OPS_REQUEST_FIELDS_INVALID")
    if not isinstance(request.get("request_id"), str) or _REQUEST_ID.fullmatch(request["request_id"]) is None:
        raise ValueError("PROVIDER_OPS_REQUEST_ID_INVALID")
    if isinstance(request.get("issue_id"), bool) or not isinstance(request.get("issue_id"), int) or request["issue_id"] < 1:
        raise ValueError("PROVIDER_OPS_ISSUE_ID_INVALID")
    if not isinstance(request.get("roadmap_id"), str) or _ROADMAP_ID.fullmatch(request["roadmap_id"]) is None:
        raise ValueError("PROVIDER_OPS_ROADMAP_ID_INVALID")
    if not isinstance(request.get("expected_main_sha"), str) or _SHA40.fullmatch(request["expected_main_sha"]) is None:
        raise ValueError("PROVIDER_OPS_SHA_INVALID")
    if isinstance(request.get("pr_number"), bool) or not isinstance(request.get("pr_number"), int) or request["pr_number"] < 1:
        raise ValueError("PROVIDER_OPS_PR_NUMBER_INVALID")
    branch = request.get("branch")
    if not isinstance(branch, str) or _BRANCH.fullmatch(branch) is None or branch.startswith("-") or ".." in branch or branch.endswith("/"):
        raise ValueError("PROVIDER_OPS_BRANCH_INVALID")
    worker = request.get("worker_id")
    if not isinstance(worker, str) or _WORKER.fullmatch(worker) is None:
        raise ValueError("PROVIDER_OPS_WORKER_ID_INVALID")
    lease_id = request.get("lease_id")
    if not isinstance(lease_id, str) or _LEASE_ID.fullmatch(lease_id) is None:
        raise ValueError("PROVIDER_OPS_LEASE_ID_INVALID")
    revision = request.get("lease_registry_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("PROVIDER_OPS_LEASE_REVISION_INVALID")
    if operation == MATERIALIZE_PROJECTIONS:
        for field in ("base_sha", "head_sha"):
            if not isinstance(request.get(field), str) or _SHA40.fullmatch(request[field]) is None:
                raise ValueError("PROVIDER_OPS_SHA_INVALID")
        if request["base_sha"] != request["expected_main_sha"] or request["head_sha"] == request["base_sha"]:
            raise ValueError("PROVIDER_OPS_BASE_HEAD_INVALID")
        source_paths = _canonical_paths(request.get("source_paths"))
        projections = _canonical_paths(request.get("projection_paths"))
        if projections != PROJECTION_PATHS or set(source_paths) & set(PROJECTION_PATHS):
            raise ValueError("PROVIDER_OPS_PROJECTION_PATHS_INVALID")
    else:
        for field in ("lease_base_sha", "pr_head_sha"):
            if not isinstance(request.get(field), str) or _SHA40.fullmatch(request[field]) is None:
                raise ValueError("PROVIDER_OPS_SHA_INVALID")
    budget = request.get("monetary_budget_usd")
    if isinstance(budget, bool) or budget != 0:
        raise ValueError("PROVIDER_OPS_MONETARY_BUDGET_INVALID")
    digest = request.get("request_digest")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None or digest != _digest_payload(request):
        raise ValueError("PROVIDER_OPS_REQUEST_DIGEST_INVALID")
    if lines[1] != _canonical(request):
        raise ValueError("PROVIDER_OPS_REQUEST_NOT_CANONICAL")
    return request


def _rejected(reason: str, request: dict[str, Any] | None = None, **details: Any) -> dict[str, Any]:
    result = {"status": "REJECTED", "reason": reason, "mutation": False, **details}
    if request:
        result.update({"request_id": request["request_id"], "request_digest": request["request_digest"]})
    return result


def _not_verified(reason: str, request: dict[str, Any] | None = None, **details: Any) -> dict[str, Any]:
    result = {"status": "NOT_VERIFIED", "reason": reason, "mutation": False, **details}
    if request:
        result.update({"request_id": request["request_id"], "request_digest": request["request_digest"]})
    return result


def _local_head(root: Path) -> str:
    process = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False, timeout=10)
    sha = process.stdout.strip()
    if process.returncode != 0 or _SHA40.fullmatch(sha) is None:
        raise ValueError("PROVIDER_OPS_LOCAL_HEAD_NOT_VERIFIED")
    return sha


def _policy_gate(root: Path) -> str | None:
    try:
        policy = strict_loads((root / ".adwf/effective-policy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return "PROVIDER_OPS_EFFECTIVE_POLICY_UNAVAILABLE"
    if not isinstance(policy, dict):
        return "PROVIDER_OPS_EFFECTIVE_POLICY_INVALID"
    ranks = ((policy.get("rules") or {}).get("autonomy_rank") or {})
    active = policy.get("active_autonomy")
    if not isinstance(active, str) or active not in ranks or "A2" not in ranks or ranks[active] < ranks["A2"]:
        return "PROVIDER_OPS_POLICY_AUTONOMY_INSUFFICIENT"
    if policy.get("max_autonomous_risk") not in {"R1", "R2", "R3", "R4"}:
        return "PROVIDER_OPS_POLICY_RISK_INVALID"
    if policy.get("hard_budget_usd") != 0 or policy.get("mandatory_ai_api") is not False:
        return "PROVIDER_OPS_POLICY_FREE_ONLY_INVALID"
    if policy.get("max_parallel_writers") != 1:
        return "PROVIDER_OPS_POLICY_WRITER_CEILING_INVALID"
    return None


def _issue_title_has_roadmap(title: str, roadmap_id: str) -> bool:
    return roadmap_id in re.findall(r"\[([A-Z][A-Z0-9_-]{1,80})\]", title[:240])


def _commit_node(client: GitHubClient, sha: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if _SHA40.fullmatch(sha) is None:
        raise ValueError("PROVIDER_OPS_COMMIT_SHA_INVALID")
    if sha in cache:
        return cache[sha]
    payload = client.git_commit(sha)
    if str(payload.get("sha") or "") != sha:
        raise ValueError("PROVIDER_OPS_COMMIT_READBACK_MISMATCH")
    tree_sha = str((payload.get("tree") or {}).get("sha") or "")
    parents = payload.get("parents")
    if _SHA40.fullmatch(tree_sha) is None or not isinstance(parents, list):
        raise ValueError("PROVIDER_OPS_COMMIT_PAYLOAD_INVALID")
    parent_shas: list[str] = []
    for item in parents:
        value = str((item or {}).get("sha") or "") if isinstance(item, dict) else ""
        if _SHA40.fullmatch(value) is None:
            raise ValueError("PROVIDER_OPS_COMMIT_PARENT_INVALID")
        parent_shas.append(value)
    node = {"sha": sha, "tree_sha": tree_sha, "parents": parent_shas, "message": str(payload.get("message") or "")}
    cache[sha] = node
    return node


def _prove_ancestor(client: GitHubClient, base_sha: str, head_sha: str, cache: dict[str, dict[str, Any]]) -> None:
    pending = [head_sha]
    seen: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current == base_sha:
            _commit_node(client, current, cache)
            return
        if current in seen:
            continue
        if len(seen) >= _MAX_ANCESTRY_COMMITS:
            raise ValueError("PROVIDER_OPS_ANCESTRY_LIMIT")
        seen.add(current)
        pending.extend(parent for parent in _commit_node(client, current, cache)["parents"] if parent not in seen)
    raise ValueError("PROVIDER_OPS_BASE_NOT_ANCESTOR")


def _tree_files(client: GitHubClient, tree_sha: str) -> dict[str, dict[str, Any]]:
    payload = client.git_tree(tree_sha, recursive=True)
    if str(payload.get("sha") or "") != tree_sha or payload.get("truncated") is not False:
        raise ValueError("PROVIDER_OPS_TREE_NOT_COMPLETE")
    rows = payload.get("tree")
    if not isinstance(rows, list) or len(rows) > _MAX_TREE_ENTRIES:
        raise ValueError("PROVIDER_OPS_TREE_PAYLOAD_INVALID")
    files: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("PROVIDER_OPS_TREE_ENTRY_INVALID")
        path = _path(row.get("path"))
        kind, mode = row.get("type"), str(row.get("mode") or "")
        if kind == "tree":
            if mode != "040000":
                raise ValueError("PROVIDER_OPS_TREE_MODE_INVALID")
            continue
        if kind == "commit" or mode == "160000":
            raise ValueError("PROVIDER_OPS_SUBMODULE_FORBIDDEN")
        if kind != "blob" or mode == "120000":
            raise ValueError("PROVIDER_OPS_SYMLINK_OR_SPECIAL_FORBIDDEN")
        if mode not in _ALLOWED_FILE_MODES:
            raise ValueError("PROVIDER_OPS_FILE_MODE_INVALID")
        sha = str(row.get("sha") or "")
        size = row.get("size")
        if _SHA40.fullmatch(sha) is None or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("PROVIDER_OPS_TREE_BLOB_INVALID")
        if path in files:
            raise ValueError("PROVIDER_OPS_TREE_PATH_DUPLICATE")
        files[path] = {"sha": sha, "mode": mode, "size": size}
    return files


def _tree_effect(base: dict[str, dict[str, Any]], head: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if old == new:
            continue
        status = "A" if old is None else "D" if new is None else "M"
        records.append({"path": path, "status": status, "old": old, "new": new})
        if len(records) > _MAX_CHANGED_FILES:
            raise ValueError("PROVIDER_OPS_CHANGED_PATH_LIMIT")
    return records


def _blob_bytes(client: GitHubClient, entry: dict[str, Any]) -> bytes:
    if entry["size"] > _MAX_CHANGED_BLOB_BYTES:
        raise ValueError("PROVIDER_OPS_BLOB_TOO_LARGE")
    payload = client.git_blob(entry["sha"])
    if str(payload.get("sha") or "") != entry["sha"] or payload.get("encoding") != "base64":
        raise ValueError("PROVIDER_OPS_BLOB_READBACK_INVALID")
    encoded = "".join(str(payload.get("content") or "").split())
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("PROVIDER_OPS_BLOB_BASE64_INVALID") from exc
    if len(raw) != entry["size"] or len(raw) > _MAX_CHANGED_BLOB_BYTES:
        raise ValueError("PROVIDER_OPS_BLOB_SIZE_MISMATCH")
    return raw


def _load_trust_policy(root: Path) -> dict[str, Any]:
    try:
        policy = strict_loads((root / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("PROVIDER_OPS_BASE_TRUST_POLICY_INVALID") from exc
    if not isinstance(policy, dict):
        raise ValueError("PROVIDER_OPS_BASE_TRUST_POLICY_INVALID")
    return policy


def _trust_classification(
    root: Path, client: GitHubClient, effect: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = _load_trust_policy(root)
    patterns = policy.get("paths") or []
    records: list[dict[str, Any]] = []
    total = 0
    for row in effect:
        old_text = new_text = None
        if is_trust_sensitive_path(row["path"], patterns):
            try:
                if row["old"] is not None:
                    raw = _blob_bytes(client, row["old"]); total += len(raw); old_text = raw.decode("utf-8", errors="strict")
                if row["new"] is not None:
                    raw = _blob_bytes(client, row["new"]); total += len(raw); new_text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("PROVIDER_OPS_PROTECTED_BLOB_UTF8_INVALID") from exc
        records.append({"path": row["path"], "status": row["status"], "old_path": None, "old_text": old_text, "new_text": new_text})
    if total > _MAX_CHANGED_TOTAL_BYTES:
        raise ValueError("PROVIDER_OPS_CHANGED_PAYLOAD_TOO_LARGE")
    return classify_diff(records, policy)


def _human_authorization(client: GitHubClient, pr_number: int, pr: dict[str, Any], sha: str) -> dict[str, Any]:
    author = str((pr.get("user") or {}).get("login") or "")
    for review in client.pull_reviews(pr_number):
        if str(review.get("state") or "").upper() != "APPROVED" or str(review.get("commit_id") or "") != sha:
            continue
        login = str((review.get("user") or {}).get("login") or "")
        if not login or login == author:
            continue
        try:
            if str(client.collaborator_permission(login).get("permission") or "").lower() == "admin":
                return {"verified": True, "mode": "ADMIN_REVIEW", "login": login, "commit_id": sha}
        except Exception:
            continue
    matches = _OWNER_ATTESTATION.findall(str(pr.get("body") or ""))
    if matches != [sha] or not author:
        return {"verified": False, "reason": "PROVIDER_OPS_EXACT_HEAD_AUTHORIZATION_REQUIRED"}
    try:
        permission = str(client.collaborator_permission(author).get("permission") or "").lower()
    except Exception:
        return {"verified": False, "reason": "PROVIDER_OPS_OWNER_PERMISSION_NOT_VERIFIED"}
    if permission != "admin":
        return {"verified": False, "reason": "PROVIDER_OPS_OWNER_ADMIN_REQUIRED"}
    return {"verified": True, "mode": "SOLO_MAINTAINER_OWNER_ATTESTATION", "login": author, "commit_id": sha}


def _lease_identity(client: GitHubClient, request: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    registry, anchor = GitHubLeaseStore(client).read(expected_main_sha=request["expected_main_sha"], policy_max_parallel_writers=1)
    if registry.get("revision") != request["lease_registry_revision"] or registry.get("observed_main_sha") != request["expected_main_sha"]:
        raise ValueError("PROVIDER_OPS_LEASE_REVISION_MISMATCH")
    active = [lease for lease in registry.get("leases") or [] if lease.get("status") == "ACTIVE"]
    if len(active) != 1:
        raise ValueError("PROVIDER_OPS_SOLE_ACTIVE_LEASE_REQUIRED")
    lease = active[0]
    expected = {
        "lease_id": request["lease_id"], "worker_id": request["worker_id"], "issue_id": str(request["issue_id"]),
        "roadmap_id": request["roadmap_id"], "base_sha": request["base_sha"], "branch": request["branch"],
        "resources": [{"global": True, "kind": "provider", "scope": "global", "shared": True}],
    }
    for key, value in expected.items():
        if lease.get(key) != value:
            raise ValueError("PROVIDER_OPS_LEASE_IDENTITY_MISMATCH:" + key)
    if _registry_lease_freshness_errors(lease, datetime.now(timezone.utc)):
        raise ValueError("PROVIDER_OPS_LEASE_NOT_FRESH")
    return lease, anchor


def _rulesets(client: GitHubClient) -> None:
    rows = client.rulesets()
    if verify_rulesets(rows, expected_integration_id=15368).get("readback_verified") is not True:
        raise ValueError("PROVIDER_OPS_PROTECTED_MAIN_RULESET_NOT_VERIFIED")
    if verify_runtime_anchor_ruleset(rows).get("readback_verified") is not True:
        raise ValueError("PROVIDER_OPS_IMMUTABLE_ANCHOR_RULESET_NOT_VERIFIED")


def _live_identity(root: Path, client: GitHubClient, request: dict[str, Any], *, require_head: bool = True) -> dict[str, Any]:
    info = client.repo_info(); default = str(info.get("default_branch") or "")
    if not default:
        raise ValueError("PROVIDER_OPS_DEFAULT_BRANCH_MISSING")
    main = str((client.branch(default).get("commit") or {}).get("sha") or "")
    if main != request["expected_main_sha"]:
        raise ValueError("PROVIDER_OPS_MAIN_DRIFT")
    if _local_head(root) != main:
        raise ValueError("PROVIDER_OPS_TRUSTED_CHECKOUT_MAIN_MISMATCH")
    _rulesets(client)
    issue = client.get(f"/repos/{client.repo}/issues/{request['issue_id']}")
    if issue.get("state") != "open" or issue.get("pull_request") is not None or int(issue.get("number") or 0) != request["issue_id"]:
        raise ValueError("PROVIDER_OPS_ISSUE_NOT_OPEN")
    if not _issue_title_has_roadmap(str(issue.get("title") or ""), request["roadmap_id"]):
        raise ValueError("PROVIDER_OPS_ISSUE_ROADMAP_MISMATCH")
    pr = client.pull(request["pr_number"])
    if pr.get("state") != "open" or int(pr.get("number") or 0) != request["pr_number"]:
        raise ValueError("PROVIDER_OPS_PR_NOT_OPEN")
    base = pr.get("base") or {}; head = pr.get("head") or {}; head_repo = head.get("repo") or {}
    if str(base.get("sha") or "") != request["base_sha"] or str(base.get("ref") or "") != default:
        raise ValueError("PROVIDER_OPS_PR_BASE_MISMATCH")
    if str(head.get("ref") or "") != request["branch"] or str(head_repo.get("full_name") or "") != client.repo or head_repo.get("fork") is True:
        raise ValueError("PROVIDER_OPS_PR_HEAD_REPOSITORY_MISMATCH")
    branch_sha = str((client.git_ref(request["branch"]).get("object") or {}).get("sha") or "")
    pr_sha = str(head.get("sha") or "")
    if require_head and (branch_sha != request["head_sha"] or pr_sha != request["head_sha"]):
        raise ValueError("PROVIDER_OPS_HEAD_DRIFT")
    if branch_sha != pr_sha:
        raise ValueError("PROVIDER_OPS_PR_BRANCH_MISMATCH")
    lease, anchor = _lease_identity(client, request)
    return {"main_sha": main, "pr": pr, "branch_sha": branch_sha, "lease": lease, "lease_anchor": anchor}


def _safe_tar_extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r") as tf:
        members = tf.getmembers()
        if len(members) > _MAX_FS_FILES:
            raise ValueError("PROVIDER_OPS_ARCHIVE_FILE_LIMIT")
        total = 0
        for member in members:
            path = _path(member.name.rstrip("/")) if member.name.rstrip("/") else ""
            if not path:
                continue
            destination = (target / path).resolve()
            if os.path.commonpath([str(target.resolve()), str(destination)]) != str(target.resolve()):
                raise ValueError("PROVIDER_OPS_ARCHIVE_PATH_ESCAPE")
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True); continue
            if not member.isfile():
                raise ValueError("PROVIDER_OPS_ARCHIVE_SPECIAL_FILE")
            total += member.size
            if total > _MAX_FS_BYTES:
                raise ValueError("PROVIDER_OPS_ARCHIVE_SIZE_LIMIT")
            source = tf.extractfile(member)
            if source is None:
                raise ValueError("PROVIDER_OPS_ARCHIVE_MEMBER_MISSING")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read())
            destination.chmod(0o755 if member.mode & 0o111 else 0o644)


def _materialize_candidate_root(root: Path, base_sha: str, effect: list[dict[str, Any]], client: GitHubClient, target: Path) -> None:
    archive = target.parent / "trusted-base.tar"
    process = subprocess.run(["git", "archive", "--format=tar", "--output", str(archive), base_sha], cwd=root, text=True, capture_output=True, check=False, timeout=60)
    if process.returncode != 0:
        raise ValueError("PROVIDER_OPS_TRUSTED_BASE_ARCHIVE_FAILED")
    target.mkdir(parents=True, exist_ok=True)
    _safe_tar_extract(archive, target)
    total_changed = 0
    for row in effect:
        destination = target / row["path"]
        if row["status"] == "D":
            if not destination.is_file():
                raise ValueError("PROVIDER_OPS_CANDIDATE_DELETE_MISSING")
            destination.unlink(); continue
        raw = _blob_bytes(client, row["new"]); total_changed += len(raw)
        if total_changed > _MAX_CHANGED_TOTAL_BYTES:
            raise ValueError("PROVIDER_OPS_CHANGED_PAYLOAD_TOO_LARGE")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not destination.is_file():
            raise ValueError("PROVIDER_OPS_CANDIDATE_PATH_NOT_FILE")
        destination.write_bytes(raw)
        destination.chmod(0o755 if row["new"]["mode"] == "100755" else 0o644)


def _snapshot(root: Path, *, excluding: set[str] | None = None) -> dict[str, tuple[str, int]]:
    excluded = excluding or set(); result: dict[str, tuple[str, int]] = {}; total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("PROVIDER_OPS_GENERATOR_SYMLINK_FORBIDDEN")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        raw = path.read_bytes(); total += len(raw)
        if len(result) >= _MAX_FS_FILES or total > _MAX_FS_BYTES:
            raise ValueError("PROVIDER_OPS_GENERATOR_SNAPSHOT_LIMIT")
        result[relative] = (hashlib.sha256(raw).hexdigest(), path.stat().st_mode & 0o777)
    return result


def _sanitized_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if _SENSITIVE_ENV.search(upper) or upper in {"PYTHONPATH", "PYTHONHOME"}:
            continue
        env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_generator(command: list[str], *, root: Path, env: dict[str, str]) -> None:
    try:
        process = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True, check=False, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PROVIDER_OPS_GENERATOR_TIMEOUT") from exc
    if process.returncode != 0:
        raise ValueError("PROVIDER_OPS_GENERATOR_FAILED")


def _generate_projections(trusted_root: Path, candidate_root: Path) -> dict[str, bytes]:
    before = _snapshot(candidate_root, excluding=set(PROJECTION_PATHS))
    env = _sanitized_env()
    commands = [
        [sys.executable, "-I", str(trusted_root / ".adwf/scripts/docs_freshness.py"), "--root", str(candidate_root), "--write"],
        [sys.executable, "-I", str(trusted_root / ".adwf/scripts/generate_manifest.py"), "--root", str(candidate_root)],
    ]
    for command in commands:
        _run_generator(command, root=trusted_root, env=env)
    after = _snapshot(candidate_root, excluding=set(PROJECTION_PATHS))
    if before != after:
        raise ValueError("PROVIDER_OPS_GENERATOR_UNEXPECTED_PATH")
    first: dict[str, bytes] = {}
    for relative in PROJECTION_PATHS:
        path = candidate_root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("PROVIDER_OPS_GENERATED_PROJECTION_MISSING")
        raw = path.read_bytes()
        if len(raw) > _MAX_GENERATED_BYTES:
            raise ValueError("PROVIDER_OPS_GENERATED_PROJECTION_TOO_LARGE")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("PROVIDER_OPS_GENERATED_PROJECTION_UTF8_INVALID") from exc
        first[relative] = raw
    for command in commands:
        _run_generator(command, root=trusted_root, env=env)
    if before != _snapshot(candidate_root, excluding=set(PROJECTION_PATHS)):
        raise ValueError("PROVIDER_OPS_GENERATOR_SECOND_RUN_UNEXPECTED_PATH")
    second = {relative: (candidate_root / relative).read_bytes() for relative in PROJECTION_PATHS}
    if first != second:
        raise ValueError("PROVIDER_OPS_GENERATOR_NOT_IDEMPOTENT")
    return first


def _commit_marker(request: dict[str, Any]) -> str:
    return f"ADWF-Provider-Ops-Digest: {request['request_digest']}"


def _commit_message(request: dict[str, Any]) -> str:
    return (
        f"{request['roadmap_id']}: materialize deterministic projections\n\n"
        f"ADWF-Provider-Ops: {MATERIALIZE_PROJECTIONS}\n"
        f"ADWF-Provider-Ops-Request: {request['request_id']}\n"
        f"{_commit_marker(request)}\n"
        f"ADWF-Provider-Ops-Parent: {request['head_sha']}"
    )


def _verify_replay(client: GitHubClient, request: dict[str, Any], branch_sha: str, pr_sha: str) -> dict[str, Any] | None:
    if branch_sha == request["head_sha"]:
        return None
    if branch_sha != pr_sha or _SHA40.fullmatch(branch_sha) is None:
        return None
    cache: dict[str, dict[str, Any]] = {}
    node = _commit_node(client, branch_sha, cache)
    if node["parents"] != [request["head_sha"]] or _commit_marker(request) not in node["message"]:
        return None
    parent = _commit_node(client, request["head_sha"], cache)
    current_files = _tree_files(client, node["tree_sha"])
    parent_files = _tree_files(client, parent["tree_sha"])
    effect = _tree_effect(parent_files, current_files)
    if [row["path"] for row in effect] != PROJECTION_PATHS:
        return None
    return {
        "status": "ALREADY_APPLIED", "mutation": False, "request_id": request["request_id"],
        "request_digest": request["request_digest"], "head_sha": request["head_sha"], "new_head_sha": branch_sha,
        "changed_paths": PROJECTION_PATHS, "provider_readback": True, "merge_authority": False,
        "issue_close_authority": False, "monetary_cost_usd": 0,
    }


def _reconcile_ref(request: dict[str, Any]) -> str:
    return (
        f"github:provider-ops:{request['request_id']}:{request['request_digest']}:"
        f"pr/{request['pr_number']}@{request['pr_head_sha']}:main@{request['expected_main_sha']}"
    )


def _reconcile_pr_identity(client: GitHubClient, request: dict[str, Any], default: str) -> dict[str, Any]:
    pr = client.pull(request["pr_number"])
    if int(pr.get("number") or 0) != request["pr_number"]:
        raise ValueError("PROVIDER_OPS_PR_IDENTITY_MISMATCH")
    base = pr.get("base") or {}; head = pr.get("head") or {}; head_repo = head.get("repo") or {}
    if str(base.get("ref") or "") != default or str(base.get("sha") or "") != request["lease_base_sha"]:
        raise ValueError("PROVIDER_OPS_PR_BASE_MISMATCH")
    if str(head.get("ref") or "") != request["branch"] or str(head_repo.get("full_name") or "") != client.repo or head_repo.get("fork") is True:
        raise ValueError("PROVIDER_OPS_PR_HEAD_REPOSITORY_MISMATCH")
    if str(head.get("sha") or "") != request["pr_head_sha"]:
        raise ValueError("PROVIDER_OPS_PR_HEAD_DRIFT")
    branch_sha = str((client.git_ref(request["branch"]).get("object") or {}).get("sha") or "")
    if branch_sha != request["pr_head_sha"]:
        raise ValueError("PROVIDER_OPS_BRANCH_HEAD_DRIFT")
    merged = pr.get("merged") is True
    state = str(pr.get("state") or "")
    if merged:
        if state != "closed" or str(pr.get("merge_commit_sha") or "") != request["expected_main_sha"]:
            raise ValueError("PROVIDER_OPS_MERGED_PR_MAIN_IDENTITY_NOT_VERIFIED")
        main_node = _commit_node(client, request["expected_main_sha"], {})
        if main_node["parents"] != [request["lease_base_sha"]]:
            raise ValueError("PROVIDER_OPS_MERGED_MAIN_PARENT_MISMATCH")
    elif state != "open":
        raise ValueError("PROVIDER_OPS_PR_NOT_OPEN_OR_MERGED")
    return {"pr": pr, "branch_sha": branch_sha, "merged": merged}


def _reconcile_registry(client: GitHubClient, request: dict[str, Any]) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    registry, anchor = GitHubLeaseStore(client).read(
        expected_main_sha=request["expected_main_sha"], policy_max_parallel_writers=1,
    )
    matches = [lease for lease in registry.get("leases") or [] if lease.get("lease_id") == request["lease_id"]]
    lease = matches[0] if len(matches) == 1 else None
    return registry, anchor, lease


def _reconcile_replay(request: dict[str, Any], registry: dict[str, Any], lease: dict[str, Any] | None) -> dict[str, Any] | None:
    if lease is None or lease.get("status") != "RELEASED":
        return None
    if lease.get("worker_id") != request["worker_id"] or lease.get("base_sha") != request["lease_base_sha"] or lease.get("branch") != request["branch"]:
        return None
    if lease.get("provider_reconciliation_ref") != _reconcile_ref(request):
        return None
    if registry.get("revision") < request["lease_registry_revision"] + 1 or registry.get("observed_main_sha") != request["expected_main_sha"]:
        return None
    return {
        "status": "ALREADY_APPLIED", "operation": LEASE_RECONCILE, "mutation": False,
        "request_id": request["request_id"], "request_digest": request["request_digest"],
        "lease_id": request["lease_id"], "lease_registry_revision": registry.get("revision"),
        "provider_readback": True, "merge_authority": False, "issue_close_authority": False,
        "monetary_cost_usd": 0,
    }


def _reconcile_live_identity(root: Path, client: GitHubClient, request: dict[str, Any], *, allow_replay: bool) -> dict[str, Any]:
    info = client.repo_info(); default = str(info.get("default_branch") or "")
    if not default:
        raise ValueError("PROVIDER_OPS_DEFAULT_BRANCH_MISSING")
    main = str((client.branch(default).get("commit") or {}).get("sha") or "")
    if main != request["expected_main_sha"]:
        raise ValueError("PROVIDER_OPS_MAIN_DRIFT")
    if _local_head(root) != main:
        raise ValueError("PROVIDER_OPS_TRUSTED_CHECKOUT_MAIN_MISMATCH")
    _rulesets(client)
    issue = client.get(f"/repos/{client.repo}/issues/{request['issue_id']}")
    if issue.get("state") != "open" or issue.get("pull_request") is not None or int(issue.get("number") or 0) != request["issue_id"]:
        raise ValueError("PROVIDER_OPS_ISSUE_NOT_OPEN")
    if not _issue_title_has_roadmap(str(issue.get("title") or ""), request["roadmap_id"]):
        raise ValueError("PROVIDER_OPS_ISSUE_ROADMAP_MISMATCH")
    pr_state = _reconcile_pr_identity(client, request, default)
    registry, anchor, lease = _reconcile_registry(client, request)
    if allow_replay:
        replay = _reconcile_replay(request, registry, lease)
        if replay is not None:
            return {"replay": replay, "main_sha": main, "registry": registry, "lease": lease, **pr_state}
    if registry.get("revision") != request["lease_registry_revision"]:
        raise ValueError("PROVIDER_OPS_LEASE_REVISION_MISMATCH")
    if registry.get("observed_main_sha") != request["lease_base_sha"]:
        raise ValueError("PROVIDER_OPS_LEASE_OBSERVED_MAIN_MISMATCH")
    active = [row for row in registry.get("leases") or [] if row.get("status") == "ACTIVE"]
    if len(active) != 1 or lease is None or active[0].get("lease_id") != request["lease_id"]:
        raise ValueError("PROVIDER_OPS_SOLE_ACTIVE_LEASE_REQUIRED")
    expected = {
        "worker_id": request["worker_id"], "issue_id": str(request["issue_id"]), "roadmap_id": request["roadmap_id"],
        "base_sha": request["lease_base_sha"], "branch": request["branch"],
        "resources": [{"global": True, "kind": "provider", "scope": "global", "shared": True}],
    }
    for key, value in expected.items():
        if lease.get(key) != value:
            raise ValueError("PROVIDER_OPS_LEASE_IDENTITY_MISMATCH:" + key)
    freshness = _registry_lease_freshness_errors(lease, datetime.now(timezone.utc))
    invalid_freshness = [item for item in freshness if item not in {"LEASE_EXPIRED", "LEASE_HEARTBEAT_STALE"}]
    if invalid_freshness:
        raise ValueError("PROVIDER_OPS_LEASE_FRESHNESS_NOT_VERIFIED:" + ",".join(invalid_freshness))
    merged_reconciles_main = pr_state["merged"] and main != request["lease_base_sha"]
    if not freshness and not merged_reconciles_main:
        raise ValueError("PROVIDER_OPS_FRESH_ACTIVE_LEASE_NOT_RELEASABLE")
    if pr_state["merged"] is False and main != request["lease_base_sha"]:
        raise ValueError("PROVIDER_OPS_OPEN_PR_MAIN_DRIFT")
    return {
        "main_sha": main, "registry": registry, "lease": lease, "lease_anchor": anchor,
        "freshness_errors": freshness, "merged_reconciles_main": merged_reconciles_main, **pr_state,
    }


def _process_lease_reconcile(root: Path, event: dict[str, Any], client: GitHubClient, request: dict[str, Any]) -> dict[str, Any]:
    try:
        live = _reconcile_live_identity(root, client, request, allow_replay=True)
    except Exception as exc:
        return _not_verified(str(exc), request)
    if live.get("replay") is not None:
        return live["replay"]
    # Re-read every mutable provider identity immediately before the immutable CAS append.
    try:
        pre = _reconcile_live_identity(root, client, request, allow_replay=False)
        if pre["registry"].get("revision") != live["registry"].get("revision") or pre["branch_sha"] != live["branch_sha"]:
            raise ValueError("PROVIDER_OPS_RECONCILE_PREFLIGHT_DRIFT")
    except Exception as exc:
        return _not_verified(str(exc), request)
    reconciliation_ref = _reconcile_ref(request)
    try:
        persisted, anchor = GitHubLeaseStore(client).release(
            expected_main_sha=request["expected_main_sha"], policy_max_parallel_writers=1,
            lease_id=request["lease_id"], worker_id=request["worker_id"], provider_reconciled=True,
            provider_reconciliation_ref=reconciliation_ref,
        )
    except Exception as exc:
        try:
            winner, _, target = _reconcile_registry(client, request)
            replay = _reconcile_replay(request, winner, target)
            if replay is not None:
                return replay
        except Exception:
            pass
        return _not_verified("PROVIDER_OPS_LEASE_RECONCILE_CAS_FAILED:" + str(exc), request)
    try:
        post = _reconcile_live_identity(root, client, request, allow_replay=True)
        replay = post.get("replay")
        if replay is None:
            raise ValueError("PROVIDER_OPS_LEASE_RECONCILE_POST_READBACK_FAILED")
        if persisted.get("revision") != request["lease_registry_revision"] + 1 or post["registry"].get("revision") != persisted.get("revision"):
            raise ValueError("PROVIDER_OPS_LEASE_RECONCILE_REVISION_INVALID")
        if any(row.get("status") == "ACTIVE" for row in post["registry"].get("leases") or []):
            raise ValueError("PROVIDER_OPS_LEASE_RECONCILE_ACTIVE_WRITER_REMAINS")
        if post["branch_sha"] != request["pr_head_sha"] or post["main_sha"] != request["expected_main_sha"]:
            raise ValueError("PROVIDER_OPS_LEASE_RECONCILE_PROVIDER_IDENTITY_DRIFT")
    except Exception as exc:
        return _not_verified(str(exc), request, mutation=True, lease_anchor=anchor)
    return {
        "status": "PASS", "operation": LEASE_RECONCILE, "mutation": True,
        "request_id": request["request_id"], "request_digest": request["request_digest"],
        "lease_id": request["lease_id"], "released_lease_base_sha": request["lease_base_sha"],
        "lease_registry_revision_before": request["lease_registry_revision"],
        "lease_registry_revision_after": persisted["revision"], "lease_anchor": anchor,
        "provider_reconciliation_ref": reconciliation_ref, "provider_readback": True,
        "merge_authority": False, "issue_close_authority": False, "monetary_cost_usd": 0,
    }


def process_issue_comment_provider_ops(root: Path, event: dict[str, Any], client: GitHubClient) -> dict[str, Any] | None:
    comment = event.get("comment") if isinstance(event, dict) else None
    body = (comment or {}).get("body") if isinstance(comment, dict) else None
    if not has_provider_ops_marker(body):
        return None
    try:
        request = parse_provider_ops_comment(str(body))
    except ValueError as exc:
        return _rejected(str(exc))
    if event.get("action") != "created":
        return _rejected("PROVIDER_OPS_EVENT_ACTION_INVALID", request)
    if str((event.get("repository") or {}).get("full_name") or "") != client.repo:
        return _rejected("PROVIDER_OPS_EVENT_REPOSITORY_MISMATCH", request)
    issue_event = event.get("issue") or {}
    if "pull_request" in issue_event or int(issue_event.get("number") or 0) != request["issue_id"]:
        return _rejected("PROVIDER_OPS_EVENT_ISSUE_MISMATCH", request)
    sender = event.get("sender") or {}; actor = str((comment.get("user") or {}).get("login") or "")
    if not actor or actor != str(sender.get("login") or ""):
        return _rejected("PROVIDER_OPS_EVENT_ACTOR_MISMATCH", request)
    if str(comment.get("author_association") or "") not in {"OWNER", "MEMBER", "COLLABORATOR"}:
        return _rejected("PROVIDER_OPS_ACTOR_ASSOCIATION_FORBIDDEN", request)
    try:
        if str(client.collaborator_permission(actor).get("permission") or "").lower() != "admin":
            return _rejected("PROVIDER_OPS_ACTOR_ADMIN_REQUIRED", request)
    except Exception:
        return _not_verified("PROVIDER_OPS_ACTOR_PERMISSION_READBACK_FAILED", request)
    reason = _policy_gate(root)
    if reason:
        return _rejected(reason, request)
    if request["operation"] == LEASE_RECONCILE:
        return _process_lease_reconcile(root, event, client, request)

    # Read live identities without requiring the original HEAD so a verified
    # replay can return ALREADY_APPLIED without attempting a second mutation.
    try:
        live = _live_identity(root, client, request, require_head=False)
    except Exception as exc:
        return _not_verified(str(exc), request)
    replay = _verify_replay(client, request, live["branch_sha"], str((live["pr"].get("head") or {}).get("sha") or ""))
    if replay is not None:
        return replay
    if live["branch_sha"] != request["head_sha"] or str((live["pr"].get("head") or {}).get("sha") or "") != request["head_sha"]:
        return _rejected("PROVIDER_OPS_HEAD_DRIFT", request)

    try:
        cache: dict[str, dict[str, Any]] = {}
        _prove_ancestor(client, request["base_sha"], request["head_sha"], cache)
        base_node = _commit_node(client, request["base_sha"], cache)
        head_node = _commit_node(client, request["head_sha"], cache)
        base_files = _tree_files(client, base_node["tree_sha"])
        head_files = _tree_files(client, head_node["tree_sha"])
        effect = _tree_effect(base_files, head_files)
        effect_paths = [row["path"] for row in effect]
        if effect_paths != request["source_paths"]:
            return _rejected("PROVIDER_OPS_SOURCE_EFFECT_MISMATCH", request, provider_changed_paths=effect_paths)
        for projection in PROJECTION_PATHS:
            if base_files.get(projection) != head_files.get(projection):
                return _rejected("PROVIDER_OPS_PROJECTION_PREEDIT_FORBIDDEN", request, path=projection)
        classification = _trust_classification(root, client, effect)
        if classification.get("result") == "BLOCK":
            return _rejected("PROVIDER_OPS_BASE_TRUST_CLASSIFICATION_BLOCK", request, trust=classification)
        if classification.get("result") == "HUMAN_REQUIRED":
            authorization = _human_authorization(client, request["pr_number"], live["pr"], request["head_sha"])
            if authorization.get("verified") is not True:
                return _rejected("PROVIDER_OPS_EXACT_HEAD_AUTHORIZATION_REQUIRED", request, trust=classification)
        else:
            authorization = {"verified": True, "mode": classification.get("authorization_mode") or "NORMAL"}
        with TemporaryDirectory(prefix="adwf-provider-ops-") as tmp:
            candidate = Path(tmp) / "candidate"
            _materialize_candidate_root(root, request["base_sha"], effect, client, candidate)
            generated = _generate_projections(root, candidate)
    except Exception as exc:
        return _not_verified(str(exc), request)

    # Create immutable Git objects first. They are unreachable if any later CAS
    # check fails; no branch ref has been mutated yet.
    try:
        entries: list[dict[str, Any]] = []
        for path in PROJECTION_PATHS:
            created = client.create_blob(generated[path]); blob_sha = str(created.get("sha") or "")
            if _SHA40.fullmatch(blob_sha) is None:
                raise ValueError("PROVIDER_OPS_BLOB_CREATE_FAILED")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
        created_tree = client.create_tree(base_tree_sha=head_node["tree_sha"], entries=entries)
        tree_sha = str(created_tree.get("sha") or "")
        if _SHA40.fullmatch(tree_sha) is None:
            raise ValueError("PROVIDER_OPS_TREE_CREATE_FAILED")
        tree_files = _tree_files(client, tree_sha)
        if [row["path"] for row in _tree_effect(head_files, tree_files)] != PROJECTION_PATHS:
            raise ValueError("PROVIDER_OPS_CREATED_TREE_EFFECT_MISMATCH")
        created_commit = client.create_commit(message=_commit_message(request), tree_sha=tree_sha, parent_sha=request["head_sha"])
        new_sha = str(created_commit.get("sha") or "")
        if _SHA40.fullmatch(new_sha) is None:
            raise ValueError("PROVIDER_OPS_COMMIT_CREATE_FAILED")
        new_node = _commit_node(client, new_sha, {})
        if new_node["parents"] != [request["head_sha"]] or new_node["tree_sha"] != tree_sha or _commit_marker(request) not in new_node["message"]:
            raise ValueError("PROVIDER_OPS_COMMIT_READBACK_INVALID")
        _live_identity(root, client, request, require_head=True)
    except Exception as exc:
        return _not_verified(str(exc), request)

    try:
        client.update_branch_ref(request["branch"], new_sha)
    except Exception as exc:
        return _not_verified("PROVIDER_OPS_BRANCH_CAS_FAILED:" + str(exc), request, orphan_commit_sha=new_sha)

    try:
        post = _live_identity(root, client, request, require_head=False)
        post_pr_sha = str((post["pr"].get("head") or {}).get("sha") or "")
        if post["main_sha"] != request["expected_main_sha"] or post["branch_sha"] != new_sha or post_pr_sha != new_sha:
            raise ValueError("PROVIDER_OPS_POST_UPDATE_IDENTITY_MISMATCH")
        post_node = _commit_node(client, new_sha, {})
        post_tree = _tree_files(client, post_node["tree_sha"])
        if post_node["parents"] != [request["head_sha"]] or [row["path"] for row in _tree_effect(head_files, post_tree)] != PROJECTION_PATHS:
            raise ValueError("PROVIDER_OPS_POST_UPDATE_COMMIT_EFFECT_MISMATCH")
    except Exception as exc:
        return _not_verified(str(exc), request, new_head_sha=new_sha, mutation=True)

    return {
        "status": "PASS", "operation": MATERIALIZE_PROJECTIONS, "mutation": True,
        "request_id": request["request_id"], "request_digest": request["request_digest"],
        "base_sha": request["base_sha"], "source_head_sha": request["head_sha"], "new_head_sha": new_sha,
        "changed_paths": list(PROJECTION_PATHS), "source_paths": list(request["source_paths"]),
        "lease_id": request["lease_id"], "lease_registry_revision": request["lease_registry_revision"],
        "provider_readback": True, "trust_authorization": authorization,
        "merge_authority": False, "issue_close_authority": False, "monetary_cost_usd": 0,
    }
