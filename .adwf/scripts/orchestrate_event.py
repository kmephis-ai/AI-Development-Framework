#!/usr/bin/env python3
"""Trusted GitHub controller.

Скрипт должен выполняться только из default branch. Он читает PR как недоверенные
данные, никогда не исполняет PR-код и не переводит READY в IN_PROGRESS без lease.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import base64
import binascii
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.evidence import parse_time  # noqa: E402
from lib.http_transport import urllib_transport  # noqa: E402
from lib.provider_contracts import request_value, ProviderContractError  # noqa: E402
from lib.strict_json import loads as strict_loads  # noqa: E402
from lib.issue_contract import parse_issue_marker, parse_pr_contract, pr_attestations, replace_issue_marker_state  # noqa: E402
from lib.state_engine import evaluate_transition  # noqa: E402
from lib.policy_runtime import load_effective_policy  # noqa: E402
from lib.remote_saga import RemoteResponse, SagaError, make_transition_plan, run_transition  # noqa: E402
from lib.trust import (  # noqa: E402
    classify_diff,
    is_protected_path,
    normalize_repo_path,
    validate_check_provenance,
    validate_review_provenance,
)

ROADMAP_LABELS = {
    "roadmap:ready", "roadmap:in-progress", "roadmap:review", "roadmap:verification", "roadmap:blocked",
    "roadmap:hold", "roadmap:needs-spec", "roadmap:needs-split", "roadmap:stale", "recovery:active",
}
TARGET_STATE = {"roadmap:review": "REVIEW", "roadmap:verification": "VERIFICATION", "recovery:active": "RECOVERY"}
SHA = re.compile(r"^[0-9a-f]{40}$")
HUMAN_GATE_TEXT = "Trust change вынесен в отдельный GOV PR, классифицирован R4 и human-gated"


def _github_headers(token: str, *, etag: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "adwf-v1.6",
    }
    if etag:
        headers["If-Match"] = etag
    return headers


def api(method: str, url: str, token: str, data=None):
    value, _ = request_value(urllib_transport, method, url, _github_headers(token), data, timeout=20, max_attempts=2 if method == "GET" else 1)
    return value


def api_with_meta(method: str, url: str, token: str, data=None, *, etag: str | None = None) -> RemoteResponse:
    try:
        value, response = request_value(urllib_transport, method, url, _github_headers(token, etag=etag), data, timeout=20, max_attempts=2 if method == "GET" else 1)
    except ProviderContractError as exc:
        if str(exc) in {"PROVIDER_HTTP_409", "PROVIDER_HTTP_412"}:
            raise ValueError("REMOTE_CAS_FAILED") from exc
        raise
    return RemoteResponse(value, response.headers.get("ETag"), int(response.status))


class GithubIssueTransport:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, payload=None, *, etag: str | None = None) -> RemoteResponse:
        return api_with_meta(method, "https://api.github.com" + path, self.token, payload, etag=etag)


def _exact_sha(value: object, code: str) -> str:
    sha = str(value or "").lower()
    if SHA.fullmatch(sha) is None:
        raise ValueError(code)
    return sha


def _github_blob(repo: str, path: str, sha: str, token: str) -> str:
    """Read a bounded UTF-8 blob through the provider API without checkout."""
    normalized = normalize_repo_path(path)
    quoted = urllib.parse.quote(normalized, safe="/")
    payload = api(
        "GET",
        f"https://api.github.com/repos/{repo}/contents/{quoted}?ref={_exact_sha(sha, 'BLOB_SHA_INVALID')}",
        token,
    )
    if not isinstance(payload, dict) or payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise ValueError("GITHUB_BLOB_NOT_INLINE")
    try:
        raw = base64.b64decode(str(payload.get("content") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("GITHUB_BLOB_BASE64_INVALID") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("GITHUB_BLOB_INSPECTION_LIMIT")
    return raw.decode("utf-8", errors="replace")


def github_trust_classification(repo: str, pr: dict, token: str) -> dict:
    """Classify exact provider base..head facts using policy from the base SHA."""
    number = int(pr.get("number", 0))
    if number < 1:
        raise ValueError("PR_NUMBER_INVALID")
    base_sha = _exact_sha((pr.get("base") or {}).get("sha"), "PR_BASE_SHA_INVALID")
    head_sha = _exact_sha((pr.get("head") or {}).get("sha"), "PR_HEAD_SHA_INVALID")
    policy = strict_loads(_github_blob(repo, ".adwf/policies/trust-boundary.json", base_sha, token))
    patterns = policy.get("paths") if isinstance(policy, dict) else None
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("BASE_TRUST_POLICY_INVALID")

    files: list[dict] = []
    for page in range(1, 32):
        batch = api(
            "GET",
            f"https://api.github.com/repos/{repo}/pulls/{number}/files?per_page=100&page={page}",
            token,
        )
        if not isinstance(batch, list):
            raise ValueError("PR_FILES_RESPONSE_INVALID")
        files.extend(batch)
        if len(files) > 3000:
            raise ValueError("PR_DIFF_INSPECTION_LIMIT")
        if len(batch) < 100:
            break
    else:
        raise ValueError("PR_DIFF_PAGINATION_INCONCLUSIVE")
    status_map = {"added": "A", "removed": "D", "modified": "M", "renamed": "R", "copied": "C"}
    records: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("PR_FILE_RECORD_INVALID")
        path = normalize_repo_path(str(item.get("filename") or ""))
        old_path = normalize_repo_path(str(item.get("previous_filename"))) if item.get("previous_filename") else None
        status = status_map.get(str(item.get("status") or ""))
        if status is None:
            raise ValueError("PR_FILE_STATUS_UNKNOWN")
        inspect = any(is_protected_path(candidate, patterns) for candidate in (path, old_path) if candidate)
        old_text = None
        new_text = None
        if inspect:
            if status != "A":
                old_text = _github_blob(repo, old_path or path, base_sha, token)
            if status != "D":
                new_text = _github_blob(repo, path, head_sha, token)
        records.append({
            "path": path,
            "old_path": old_path,
            "status": status,
            "old_text": old_text,
            "new_text": new_text,
        })
    result = classify_diff(records, policy)
    result.update({"base_sha": base_sha, "head_sha": head_sha, "source": "GITHUB_PROVIDER_API"})
    return result


def _trusted_reviews_config(config: dict) -> dict:
    value = config.get("github", {}).get("trust", {})
    return value if isinstance(value, dict) else {}


def extract_pr_number(event: dict) -> int | None:
    run = event.get("workflow_run") or {}
    prs = run.get("pull_requests") or []
    if len(prs) != 1:
        return None
    return int(prs[0]["number"])


def workflow_sha_valid(pr: dict, run_sha: str | None) -> bool:
    """Open PR run должен совпасть с HEAD; closed run может указывать merge SHA."""
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if pr.get("state") != "closed":
        return bool(head_sha) and run_sha == head_sha
    allowed = {head_sha, str(pr.get("merge_commit_sha") or "")}
    allowed.discard("")
    return bool(run_sha) and run_sha in allowed


def exact_ci_valid(checks: list[dict], head_sha: str, *, now: datetime | None = None, ttl_hours: int = 24) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for item in checks:
        if item.get("name") != "fast-feedback" or item.get("head_sha") != head_sha or item.get("conclusion") != "success":
            continue
        try:
            completed = parse_time(item["completed_at"])
            if completed <= now <= completed + timedelta(hours=ttl_hours):
                return True
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
    return False


def exact_review_valid(reviews: list[dict], head_sha: str, author: str, *, now: datetime | None = None, ttl_hours: int = 24) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    latest: dict[str, dict] = {}
    for item in reviews:
        user = str((item.get("user") or {}).get("login") or "")
        if not user or user == author or item.get("commit_id") != head_sha:
            continue
        try:
            submitted = parse_time(item["submitted_at"])
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
        if submitted > now or now > submitted + timedelta(hours=ttl_hours):
            continue
        if user not in latest or str(item.get("submitted_at")) > str(latest[user].get("submitted_at")):
            latest[user] = item
    return bool(latest) and any(item.get("state") == "APPROVED" for item in latest.values()) and not any(item.get("state") == "CHANGES_REQUESTED" for item in latest.values())


def lease_times_valid(marker: dict, *, now: datetime | None = None, stall_timeout_minutes: int = 45) -> bool:
    """Lease должен быть не истёкшим, а heartbeat — свежим и не из будущего."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        heartbeat = parse_time(marker["heartbeat_at"])
        expires = parse_time(marker["expires_at"])
    except (KeyError, AttributeError, TypeError, ValueError):
        return False
    return heartbeat <= now < expires and (now - heartbeat) <= timedelta(minutes=stall_timeout_minutes)


def set_label(repo: str, issue: dict, target: str, token: str, apply: bool, predicates: dict | None = None) -> None:
    labels = [item["name"] for item in issue.get("labels", [])]
    current = [label for label in labels if label in ROADMAP_LABELS]
    expected = {"roadmap:in-progress", "roadmap:review", "roadmap:verification", "recovery:active"}
    if len(current) != 1 or current[0] not in expected:
        raise ValueError(f"ISSUE_STATE_NOT_SAFE:{current}")
    if target == "roadmap:review" and current[0] not in {"roadmap:in-progress", "roadmap:review"}:
        raise ValueError("TRANSITION_NOT_ALLOWED")
    if target == "roadmap:verification" and current[0] not in {"roadmap:in-progress", "roadmap:review", "roadmap:verification"}:
        raise ValueError("TRANSITION_NOT_ALLOWED")
    if target == "recovery:active" and current[0] not in {"roadmap:in-progress", "roadmap:review", "recovery:active"}:
        raise ValueError("TRANSITION_NOT_ALLOWED")
    marker = parse_issue_marker(issue.get("body") or "")
    if not marker["valid"]:
        raise ValueError("ISSUE_MARKER_INVALID:" + ",".join(marker["errors"]))
    to_state = TARGET_STATE.get(target)
    if not to_state:
        raise ValueError("TARGET_STATE_UNKNOWN")
    if current[0] == target:
        if marker.get("state") != to_state:
            raise ValueError("LABEL_MARKER_SPLIT_BRAIN")
        print(f"NOOP issue #{issue['number']}: {target}")
        return
    machine = strict_loads((ROOT / ".adwf/state-machine.json").read_text(encoding="utf-8"))
    transition = evaluate_transition({"state": marker["state"]}, to_state, machine, predicates or {}, expected_state=marker["state"])
    if transition.result != "ALLOW":
        raise ValueError("STATE_ENGINE_BLOCK:" + ",".join(transition.reason_codes + transition.missing_preconditions))
    if not apply:
        print(f"DRY_RUN issue #{issue['number']}: {current[0]} -> {target}")
        return
    updated_body = replace_issue_marker_state(issue.get("body") or "", to_state)
    policy = load_effective_policy(ROOT)
    plan = make_transition_plan(
        repo=repo,
        issue=issue,
        lease_id=str(marker.get("lease_id") or ""),
        from_label=current[0],
        target_label=target,
        from_state=str(marker["state"]),
        to_state=to_state,
        desired_body=updated_body,
        policy_hash=policy["policy_hash"],
    )
    result = run_transition(ROOT, plan, GithubIssueTransport(token), apply=True)
    if result.get("status") != "COMMITTED":
        raise SagaError("ISSUE_TRANSITION_NOT_COMMITTED")
    print(f"APPLIED issue #{issue['number']}: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    event = strict_loads(Path(args.event).read_text(encoding="utf-8"))
    run = event.get("workflow_run") or {}
    if run.get("conclusion") != "success":
        print("No mutation: upstream trusted check is not successful.")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or (args.apply and not token):
        raise SystemExit("GITHUB_REPOSITORY/GITHUB_TOKEN missing")
    number = extract_pr_number(event)
    if number is None:
        raise SystemExit("Expected exactly one pull request in workflow_run")
    pr = api("GET", f"https://api.github.com/repos/{repo}/pulls/{number}", token)
    if not workflow_sha_valid(pr, run.get("head_sha")):
        raise SystemExit("STALE_WORKFLOW_SHA")
    try:
        head_sha = _exact_sha((pr.get("head") or {}).get("sha"), "PR_HEAD_SHA_INVALID")
        trust_classification = github_trust_classification(repo, pr, token)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"TRUST_DIFF_UNVERIFIABLE:{exc}") from exc
    if trust_classification.get("result") == "BLOCK":
        raise SystemExit("TRUST_DIFF_BLOCKED:" + ",".join(trust_classification.get("reason_codes", [])))
    contract = parse_pr_contract(pr.get("body") or "")
    if not contract["valid"]:
        raise SystemExit("INVALID_PR_CONTRACT:" + ",".join(contract["errors"]))
    issue = api("GET", f"https://api.github.com/repos/{repo}/issues/{contract['issue_number']}", token)
    marker = parse_issue_marker(issue.get("body") or "")
    if not marker["valid"]:
        raise SystemExit("ISSUE_LEASE_MARKER_INVALID:" + ",".join(marker["errors"]))
    if marker["roadmap_id"] != contract["roadmap_id"] or marker["lease_id"] != contract["writer_lease"].lower():
        raise SystemExit("ISSUE_PR_CONTRACT_MISMATCH")
    if marker["state"] not in {"IN_PROGRESS", "REVIEW"}:
        raise SystemExit("ISSUE_MARKER_STATE_NOT_ACTIVE")
    config = strict_loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
    trust_config = _trusted_reviews_config(config)
    reviews_cache: list[dict] | None = None
    if trust_classification.get("human_required"):
        body = pr.get("body") if isinstance(pr.get("body"), str) else ""
        if contract.get("risk") != "R4" or not str(contract.get("roadmap_id", "")).startswith("GOV-") or HUMAN_GATE_TEXT not in body:
            raise SystemExit("TRUST_CHANGE_REQUIRES_R4_GOV_HUMAN_GATE")
        reviews_cache = api("GET", f"https://api.github.com/repos/{repo}/pulls/{number}/reviews?per_page=100", token)
        if not isinstance(reviews_cache, list) or len(reviews_cache) >= 100:
            raise SystemExit("TRUST_REVIEW_SET_INCONCLUSIVE")
        human_review = validate_review_provenance(
            reviews_cache,
            expected_sha=head_sha,
            author_login=str((pr.get("user") or {}).get("login") or ""),
            trusted_reviewer_logins=trust_config.get("trusted_reviewer_logins", []),
            ttl_hours=int(trust_config.get("review_ttl_hours", 168)),
            required_approvals=1,
        )
        if not human_review["valid"]:
            print("No mutation: protected change requires an allowlisted exact-SHA owner review: " + ",".join(human_review["errors"]))
            return 0
    stall_timeout = int(config.get("workspace", {}).get("stall_timeout_minutes", 45))
    if not lease_times_valid(marker, stall_timeout_minutes=stall_timeout):
        set_label(repo, issue, "recovery:active", token, args.apply)
        return 0
    if pr.get("state") == "closed" and not pr.get("merged"):
        set_label(repo, issue, "recovery:active", token, args.apply)
        return 0
    if pr.get("draft", True) and not pr.get("merged"):
        print("No mutation: draft pull request remains IN_PROGRESS.")
        return 0
    checks = api("GET", f"https://api.github.com/repos/{repo}/commits/{head_sha}/check-runs?per_page=100", token).get("check_runs", [])
    check_provenance = validate_check_provenance(
        checks,
        expected_sha=head_sha,
        expected_names=trust_config.get("required_check_names", ["fast-feedback"]),
        trusted_app_slugs=trust_config.get("trusted_check_app_slugs", ["github-actions"]),
        ttl_hours=int(trust_config.get("check_ttl_hours", 24)),
    ) if isinstance(checks, list) and len(checks) < 100 else {"valid": False, "errors": ["CHECK_SET_INCONCLUSIVE"]}
    if not check_provenance["valid"]:
        set_label(repo, issue, "recovery:active", token, args.apply)
        return 0
    if pr.get("merged"):
        reviews = reviews_cache if reviews_cache is not None else api("GET", f"https://api.github.com/repos/{repo}/pulls/{number}/reviews?per_page=100", token)
        review_provenance = validate_review_provenance(
            reviews if isinstance(reviews, list) and len(reviews) < 100 else [],
            expected_sha=head_sha,
            author_login=str((pr.get("user") or {}).get("login") or ""),
            trusted_reviewer_logins=trust_config.get("trusted_reviewer_logins", []),
            ttl_hours=int(trust_config.get("review_ttl_hours", 168)),
            required_approvals=1,
        )
        if not review_provenance["valid"] or marker["state"] != "REVIEW":
            set_label(repo, issue, "recovery:active", token, args.apply)
            return 0
        predicates = {"review_pass_exact_sha": True, "ci_pass_exact_sha": True, "evidence_fresh": True}
        set_label(repo, issue, "roadmap:verification", token, args.apply, predicates)
        return 0
    attestation_errors = pr_attestations(pr.get("body") or "")
    if attestation_errors:
        raise SystemExit("PR_ATTESTATIONS_INVALID:" + ",".join(attestation_errors))
    predicates = {"scope_gate_pass": True, "tests_executed_or_na": True, "docs_impact_assessed": True, "lease_active": True}
    set_label(repo, issue, "roadmap:review", token, args.apply, predicates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
