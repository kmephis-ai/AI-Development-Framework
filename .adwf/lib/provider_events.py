"""Нейтральный контракт событий GitHub/GitLab для parity tests."""
from __future__ import annotations

from typing import Any


def normalize_github(event: dict[str, Any]) -> dict[str, Any]:
    pr = event.get("pull_request") or {}
    workflow = event.get("workflow_run") or {}
    workflow_prs = workflow.get("pull_requests") or []
    number = pr.get("number") or (workflow_prs[0].get("number") if len(workflow_prs) == 1 else None)
    return {
        "provider": "github",
        "event_kind": "merge_request" if number else "pipeline",
        "change_number": number,
        "head_sha": pr.get("head", {}).get("sha") or workflow.get("head_sha"),
        "default_branch": event.get("repository", {}).get("default_branch"),
        "trusted_control": bool(workflow),
    }


def normalize_gitlab(event: dict[str, Any]) -> dict[str, Any]:
    attrs = event.get("object_attributes") or {}
    project = event.get("project") or {}
    return {
        "provider": "gitlab",
        "event_kind": "merge_request" if attrs.get("iid") else "pipeline",
        "change_number": attrs.get("iid"),
        "head_sha": attrs.get("last_commit", {}).get("id") or attrs.get("sha"),
        "default_branch": project.get("default_branch"),
        "trusted_control": event.get("adwf_trusted_control") is True,
    }


def assert_contract(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for name in ("provider", "event_kind", "head_sha", "default_branch", "trusted_control"):
        if value.get(name) is None:
            errors.append(f"MISSING:{name}")
    if value.get("provider") not in {"github", "gitlab"}:
        errors.append("UNKNOWN_PROVIDER")
    if not isinstance(value.get("trusted_control"), bool):
        errors.append("TRUST_FLAG_NOT_BOOLEAN")
    return errors

