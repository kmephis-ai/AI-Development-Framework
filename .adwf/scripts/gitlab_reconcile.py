#!/usr/bin/env python3
"""Trusted GitLab Free adapter: API facts → тот же exact-SHA snapshot, что и GitHub."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
import argparse
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.cost_guard import evaluate_provider  # noqa: E402
from lib.health import active_state_path  # noqa: E402
from lib.reconciliation import reconcile_snapshot  # noqa: E402
from lib.workspaces import read_registry  # noqa: E402
from lib.http_transport import urllib_transport  # noqa: E402
from lib.provider_contracts import request_value, ProviderContractError  # noqa: E402


def approved_api_base(base: str, registry: dict[str, Any]) -> str:
    normalized = base.rstrip("/")
    host = (urlparse(normalized).hostname or "").lower()
    allowed = set(registry.get("providers", {}).get("gitlab_self_hosted", {}).get("outbound_domains", []))
    if not normalized.startswith("https://") or host not in allowed:
        raise ValueError("GITLAB_API_DOMAIN_NOT_APPROVED")
    return normalized


def _gitlab_headers(token: str, token_kind: str) -> dict[str, str]:
    header = "PRIVATE-TOKEN" if token_kind == "private" else "JOB-TOKEN"
    return {header: token, "Accept": "application/json", "User-Agent": "adwf-v1.6"}


def api_page(url: str, token: str, token_kind: str) -> tuple[Any, str]:
    value, response = request_value(urllib_transport, "GET", url, _gitlab_headers(token, token_kind), timeout=20, max_attempts=2)
    return value, str(response.headers.get("X-Next-Page") or "")


def api(url: str, token: str, token_kind: str) -> Any:
    value, next_page = api_page(url, token, token_kind)
    if next_page:
        raise ValueError("UNEXPECTED_PAGINATED_SINGLETON")
    return value


def paged(url: str, token: str, token_kind: str, *, max_pages: int = 10) -> list:
    output = []
    page = "1"
    separator = "&" if "?" in url else "?"
    for _ in range(max_pages):
        value, next_page = api_page(f"{url}{separator}per_page=100&page={page}", token, token_kind)
        if not isinstance(value, list):
            raise ValueError("PROVIDER_PAGE_SHAPE_INVALID")
        output.extend(value)
        if not next_page:
            return output
        page = next_page
    raise ValueError("PROVIDER_PAGE_LIMIT_EXCEEDED")


def normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {"number": issue.get("iid"), "title": issue.get("title") or "", "body": issue.get("description") or "",
            "state": "closed" if issue.get("state") == "closed" else "open", "labels": issue.get("labels") or [],
            "updated_at": issue.get("updated_at")}


def normalize_merge_request(value: dict[str, Any]) -> dict[str, Any]:
    return {"number": value.get("iid"), "body": value.get("description") or "", "state": value.get("state"),
            "merged": value.get("state") == "merged", "head": {"ref": value.get("source_branch"), "sha": value.get("sha")}}


def normalize_pipeline(value: dict[str, Any]) -> dict[str, Any]:
    conclusion = {"success": "success", "failed": "failure", "canceled": "cancelled", "skipped": "cancelled"}.get(value.get("status"), "cancelled")
    return {"id": value.get("id"), "created_at": value.get("created_at"), "run_started_at": value.get("started_at"),
            "updated_at": value.get("updated_at"), "conclusion": conclusion, "run_attempt": 1}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--apply", action="store_true"); parser.add_argument("--quota-input")
    args = parser.parse_args()
    config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
    if config.get("provider", {}).get("mode") != "gitlab":
        raise SystemExit("CANONICAL_PROVIDER_NOT_GITLAB")
    registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))
    try:
        base = approved_api_base(os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4"), registry)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    project_id = os.environ.get("CI_PROJECT_ID", "")
    token = os.environ.get("ADWF_GITLAB_TOKEN", "")
    if not project_id or not token:
        raise SystemExit("CI_PROJECT_ID/ADWF_GITLAB_TOKEN missing")
    kind = "private"
    project = api(f"{base}/projects/{quote(project_id, safe='')}", token, kind)
    default_branch = project.get("default_branch")
    branch = api(f"{base}/projects/{quote(project_id, safe='')}/repository/branches/{quote(str(default_branch), safe='')}", token, kind)
    issues = paged(f"{base}/projects/{quote(project_id, safe='')}/issues?state=all", token, kind)
    merges = paged(f"{base}/projects/{quote(project_id, safe='')}/merge_requests?scope=all&state=all&order_by=updated_at&sort=desc", token, kind)
    pipelines = paged(f"{base}/projects/{quote(project_id, safe='')}/pipelines?order_by=id&sort=desc", token, kind)
    capability = config.get("cost", {}).get("default_ci_capability")
    request = {"provider": capability, "mandatory_ci": True, "automated": True, "projected_cost": 0, "projected_units": 0}
    if args.quota_input:
        request.update(json.loads(Path(args.quota_input).read_text(encoding="utf-8")))
    cost = evaluate_provider(registry, request, canonical_provider="gitlab")
    previous = json.loads(active_state_path(ROOT).read_text(encoding="utf-8"))
    snapshot = reconcile_snapshot(previous, config, provider="gitlab", main_sha=branch["commit"]["id"],
                                  issues=[normalize_issue(item) for item in issues],
                                  pulls=[normalize_merge_request(item) for item in merges],
                                  runs=[normalize_pipeline(item) for item in pipelines], cost=cost,
                                  workspace_registry=read_registry(ROOT))
    if args.apply:
        atomic_json(ROOT / ".adwf-runtime/project-state.json", snapshot)
        print("GITLAB RECONCILIATION: APPLIED")
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if snapshot.get("health", {}).get("adwf") == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
