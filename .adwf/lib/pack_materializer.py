"""Materialize a detected Project Pack into a consumer-owned profile overlay."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .consumer_profile import (
    ConsumerProfileError,
    apply_consumer_profile,
    load_consumer_profile,
    load_effective_config,
    plan_consumer_profile,
)
from .project_packs import commands_for_pack
from .strict_json import loads as strict_loads


def _identity(
    project: Path,
    framework: Path,
    *,
    product_name: str | None,
    default_branch: str | None,
    repository_visibility: str | None,
) -> tuple[str, str, str] | None:
    if product_name and default_branch and repository_visibility:
        return str(product_name), str(default_branch), str(repository_visibility)
    try:
        existing = load_consumer_profile(project, framework, required=False)
    except ConsumerProfileError:
        existing = None
    if existing is not None:
        p = existing["project"]
        return str(p["name"]), str(p["default_branch"]), str(p["repository_visibility"])
    try:
        base = strict_loads((framework / ".adwf/config.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    p = base.get("project") if isinstance(base, dict) else None
    if isinstance(p, dict) and p.get("type") != "framework" and p.get("runtime_product") is True:
        return str(p.get("name") or ""), str(p.get("default_branch") or ""), str(p.get("repository_visibility") or "")
    return None


def materialize_project_pack(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    apply: bool = False,
    product_name: str | None = None,
    default_branch: str | None = None,
    repository_visibility: str | None = None,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    detected = commands_for_pack(project, framework)
    if not detected.get("pack"):
        return {"status": "HUMAN_REQUIRED", "reason": "PROJECT_PACK_NOT_DETECTED", "write_performed": False}
    identity = _identity(
        project,
        framework,
        product_name=product_name,
        default_branch=default_branch,
        repository_visibility=repository_visibility,
    )
    if identity is None or not all(identity):
        return {
            "status": "HUMAN_REQUIRED",
            "reason": "CONSUMER_PROFILE_IDENTITY_REQUIRED",
            "pack": detected.get("pack"),
            "pack_digest": detected.get("pack_digest"),
            "write_performed": False,
        }
    name, branch, visibility = identity
    try:
        result = (
            apply_consumer_profile(
                project,
                framework,
                product_name=name,
                default_branch=branch,
                repository_visibility=visibility,
            )
            if apply
            else plan_consumer_profile(
                project,
                framework,
                product_name=name,
                default_branch=branch,
                repository_visibility=visibility,
            )
        )
        if result.get("status") in {"READY_TO_APPLY", "ALREADY_MATERIALIZED", "APPLIED"}:
            result["preview"] = detected.get("preview") or {}
            result["safety"] = detected.get("safety") or {}
            if result.get("status") != "READY_TO_APPLY":
                result["effective_config"] = load_effective_config(project, framework)
        return result
    except ConsumerProfileError as exc:
        return {"status": "BLOCK", "reason": str(exc).split(":", 1)[0], "write_performed": False}
