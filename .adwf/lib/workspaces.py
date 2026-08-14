"""Изолированные git worktree для AI Writer: lifecycle, stall и retry/backoff."""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
import copy
import json
import os
import subprocess
import tempfile

from .evidence import parse_time

OCCUPYING = {"ACTIVE", "STALLED", "RETRY_WAIT", "RETRY_READY", "RECOVERY"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if check and process.returncode:
        raise ValueError(f"GIT_COMMAND_FAILED:{args[0]}:{process.stderr.strip()[:200]}")
    return process


def registry_path(root: str | Path) -> Path:
    return Path(root).resolve() / ".adwf-runtime/workspaces.json"


def read_registry(root: str | Path) -> dict[str, Any]:
    path = registry_path(root)
    if not path.is_file():
        return {"schema_version": 1, "workspaces": []}
    value = strict_loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("workspaces"), list):
        raise ValueError("WORKSPACE_REGISTRY_INVALID")
    return value


def atomic_registry_update(root: str | Path, transform: Callable[[dict[str, Any]], tuple[dict[str, Any], Any]]) -> Any:
    target = registry_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(".lock")
    with exclusive_file_lock(lock_path):
        current = read_registry(root)
        updated, output = transform(current)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return output


def _workspace_target(root: Path, lease: dict[str, Any], config: dict[str, Any]) -> Path:
    configured = str(config.get("root", ""))
    relative = str(lease.get("workspace_path", ""))
    if configured != ".adwf-runtime/workspaces" or not relative.startswith(configured + "/"):
        raise ValueError("WORKSPACE_PATH_NOT_ALLOWED")
    target = (root / relative).resolve()
    allowed = (root / configured).resolve()
    if allowed not in target.parents:
        raise ValueError("WORKSPACE_PATH_ESCAPE")
    return target


def _validate_lease(lease: dict[str, Any]) -> None:
    required = ("lease_id", "issue_id", "roadmap_id", "worker_id", "base_sha", "workspace_id", "workspace_path", "branch", "heartbeat_at", "expires_at")
    missing = [name for name in required if not lease.get(name)]
    if missing or lease.get("status") != "ACTIVE":
        raise ValueError("ACTIVE_LEASE_REQUIRED:" + ",".join(missing))
    if not str(lease["branch"]).startswith("adwf/"):
        raise ValueError("WORKSPACE_BRANCH_NOT_ALLOWED")


def plan_workspace(root: str | Path, lease: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    base = Path(root).resolve()
    _validate_lease(lease)
    target = _workspace_target(base, lease, config)
    return {
        "workspace_id": lease["workspace_id"], "lease_id": lease["lease_id"],
        "issue_id": str(lease["issue_id"]), "roadmap_id": lease["roadmap_id"],
        "worker_id": lease["worker_id"], "branch": lease["branch"],
        "path": str(target.relative_to(base)), "absolute_path": str(target), "base_sha": lease["base_sha"],
    }


def create_workspace(root: str | Path, lease: dict[str, Any], config: dict[str, Any], *, apply: bool = False, now: datetime | None = None) -> dict[str, Any]:
    base = Path(root).resolve()
    now = (now or _now()).astimezone(timezone.utc)
    plan = plan_workspace(base, lease, config)
    top = Path(_git(base, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != base:
        raise ValueError("ROOT_NOT_GIT_TOPLEVEL")
    _git(base, "cat-file", "-e", f"{lease['base_sha']}^{{commit}}")
    if not apply:
        return {"result": "DRY_RUN", **plan}

    def transform(registry: dict[str, Any]):
        items = registry.setdefault("workspaces", [])
        same = [item for item in items if item.get("workspace_id") == plan["workspace_id"] and item.get("status") != "CLEANED"]
        if same:
            if len(same) == 1 and same[0].get("lease_id") == lease["lease_id"]:
                return registry, {"result": "ALREADY_ACTIVE", **same[0]}
            raise ValueError("WORKSPACE_ALREADY_BOUND")
        active = [item for item in items if item.get("status") in OCCUPYING]
        if len(active) >= int(config.get("max_active", 1)):
            raise ValueError("WORKSPACE_CONCURRENCY_LIMIT")
        target = Path(plan["absolute_path"])
        if target.exists():
            raise ValueError("UNTRACKED_WORKSPACE_PATH_EXISTS")
        branch_exists = _git(base, "show-ref", "--verify", "--quiet", f"refs/heads/{lease['branch']}", check=False).returncode == 0
        args = ("worktree", "add", str(target), lease["branch"]) if branch_exists else ("worktree", "add", "-b", lease["branch"], str(target), lease["base_sha"])
        _git(base, *args)
        record = {
            "workspace_id": plan["workspace_id"], "lease_id": lease["lease_id"],
            "issue_id": str(lease["issue_id"]), "roadmap_id": lease["roadmap_id"], "worker_id": lease["worker_id"],
            "branch": lease["branch"], "path": plan["path"], "base_sha": lease["base_sha"],
            "status": "ACTIVE", "created_at": _iso(now), "heartbeat_at": _iso(now),
            "expires_at": lease["expires_at"], "retry_count": 0, "next_retry_at": None,
            "last_error": None, "completed_sha": None,
        }
        items.append(record)
        return registry, {"result": "CREATED", **record}

    return atomic_registry_update(base, transform)


def heartbeat_workspace(root: str | Path, workspace_id: str, worker_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(timezone.utc)

    def transform(registry: dict[str, Any]):
        matches = [item for item in registry.get("workspaces", []) if item.get("workspace_id") == workspace_id and item.get("status") != "CLEANED"]
        if len(matches) != 1:
            raise ValueError("WORKSPACE_NOT_UNIQUE")
        item = matches[0]
        if item.get("worker_id") != worker_id:
            raise ValueError("WORKSPACE_OWNER_MISMATCH")
        if item.get("status") not in {"ACTIVE", "RETRY_READY"}:
            raise ValueError("WORKSPACE_NOT_HEARTBEATABLE")
        item["status"] = "ACTIVE"
        item["heartbeat_at"] = _iso(now)
        item["next_retry_at"] = None
        return registry, copy.deepcopy(item)

    return atomic_registry_update(root, transform)


def reconcile_workspaces(root: str | Path, config: dict[str, Any], *, now: datetime | None = None, apply: bool = False) -> dict[str, Any]:
    base = Path(root).resolve()
    now = (now or _now()).astimezone(timezone.utc)

    def transform(registry: dict[str, Any]):
        findings: list[str] = []
        for item in registry.get("workspaces", []):
            if item.get("status") == "ACTIVE":
                path = (base / str(item.get("path", ""))).resolve()
                if (base / ".adwf-runtime/workspaces").resolve() not in path.parents or not path.is_dir():
                    item["status"] = "RECOVERY"
                    item["last_error"] = "WORKSPACE_MISSING_OR_OUTSIDE_ROOT"
                    findings.append(f"{item.get('workspace_id')}:RECOVERY")
                    continue
                try:
                    heartbeat_at = parse_time(str(item["heartbeat_at"]))
                    expires_at = parse_time(str(item["expires_at"]))
                    stalled = (now - heartbeat_at).total_seconds() > int(config.get("stall_timeout_minutes", 45)) * 60 or expires_at <= now
                except (KeyError, ValueError):
                    stalled = True
                if stalled:
                    item["status"] = "STALLED"
                    item["last_error"] = "HEARTBEAT_OR_LEASE_STALE"
                    findings.append(f"{item.get('workspace_id')}:STALLED")
            elif item.get("status") == "RETRY_WAIT" and item.get("next_retry_at"):
                try:
                    if parse_time(item["next_retry_at"]) <= now:
                        item["status"] = "RETRY_READY"
                        findings.append(f"{item.get('workspace_id')}:RETRY_READY")
                except ValueError:
                    item["status"] = "RECOVERY"
                    item["last_error"] = "RETRY_TIME_INVALID"
        return registry, {"result": "APPLIED" if apply else "DRY_RUN", "findings": findings, "registry": copy.deepcopy(registry)}

    if apply:
        return atomic_registry_update(base, transform)
    return transform(read_registry(base))[1]


def schedule_retry(root: str | Path, workspace_id: str, error: str, config: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or _now()).astimezone(timezone.utc)

    def transform(registry: dict[str, Any]):
        matches = [item for item in registry.get("workspaces", []) if item.get("workspace_id") == workspace_id and item.get("status") != "CLEANED"]
        if len(matches) != 1:
            raise ValueError("WORKSPACE_NOT_UNIQUE")
        item = matches[0]
        if item.get("status") not in {"STALLED", "RECOVERY"}:
            raise ValueError("WORKSPACE_RETRY_NOT_ALLOWED")
        count = int(item.get("retry_count", 0))
        if count >= int(config.get("max_retries", 3)):
            item["status"] = "RECOVERY"
            item["last_error"] = "RETRY_LIMIT_REACHED:" + error[:200]
            return registry, copy.deepcopy(item)
        delay = min(int(config.get("retry_base_seconds", 30)) * (2 ** count), int(config.get("retry_max_seconds", 900)))
        item["retry_count"] = count + 1
        item["status"] = "RETRY_WAIT"
        item["next_retry_at"] = _iso(now + timedelta(seconds=delay))
        item["last_error"] = error[:200]
        return registry, copy.deepcopy(item)

    return atomic_registry_update(root, transform)


def complete_workspace(root: str | Path, workspace_id: str) -> dict[str, Any]:
    base = Path(root).resolve()

    def transform(registry: dict[str, Any]):
        matches = [item for item in registry.get("workspaces", []) if item.get("workspace_id") == workspace_id and item.get("status") != "CLEANED"]
        if len(matches) != 1:
            raise ValueError("WORKSPACE_NOT_UNIQUE")
        item = matches[0]
        path = (base / item["path"]).resolve()
        if _git(path, "status", "--porcelain").stdout.strip():
            raise ValueError("WORKSPACE_DIRTY")
        item["completed_sha"] = _git(path, "rev-parse", "HEAD").stdout.strip()
        item["status"] = "COMPLETED"
        return registry, copy.deepcopy(item)

    return atomic_registry_update(base, transform)


def cleanup_workspace(root: str | Path, workspace_id: str, config: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    base = Path(root).resolve()

    def transform(registry: dict[str, Any]):
        matches = [item for item in registry.get("workspaces", []) if item.get("workspace_id") == workspace_id and item.get("status") != "CLEANED"]
        if len(matches) != 1:
            raise ValueError("WORKSPACE_NOT_UNIQUE")
        item = matches[0]
        if item.get("status") not in {"COMPLETED", "STOPPED"}:
            raise ValueError("WORKSPACE_CLEANUP_NOT_ALLOWED")
        path = (base / item["path"]).resolve()
        if config.get("require_clean_cleanup") is not True or _git(path, "status", "--porcelain").stdout.strip():
            raise ValueError("WORKSPACE_DIRTY")
        if not apply:
            return registry, {"result": "DRY_RUN", **copy.deepcopy(item)}
        _git(base, "worktree", "remove", str(path))
        item["status"] = "CLEANED"
        return registry, {"result": "CLEANED", **copy.deepcopy(item)}

    if apply:
        return atomic_registry_update(base, transform)
    return transform(read_registry(base))[1]
