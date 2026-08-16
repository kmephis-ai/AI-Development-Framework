"""Consumer-owned profile overlay that preserves immutable ADWF package identity.

The canonical `.adwf/config.json` remains framework package truth. A consumer
repository may add `.adwf-consumer/profile.json`, which is deliberately outside
MANIFEST/Managed Surface authority. Only project identity and validated Project
Pack projections may overlay canonical config; governance/provider/trust/cost
settings are never accepted from the consumer profile.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import os

from .contracts import validate
from .project_packs import commands_for_pack
from .strict_json import loads as strict_loads

PROFILE_REL = ".adwf-consumer/profile.json"
CONFIG_COMMANDS = ("lint", "unit", "integration", "build", "smoke", "golden_paths", "e2e")
RUNTIME_COMMANDS = ("install", "start")
VISIBILITY = {"PUBLIC", "PRIVATE", "INTERNAL"}


class ConsumerProfileError(ValueError):
    """Deterministic fail-closed consumer profile error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConsumerProfileError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerProfileError(code + ":OBJECT_REQUIRED")
    return value


def _validate_config(config: dict[str, Any], framework_root: Path) -> None:
    schema = _object(framework_root / ".adwf/schemas/config.schema.json", "CONSUMER_CONFIG_SCHEMA_INVALID")
    findings = validate(config, schema)
    if findings:
        raise ConsumerProfileError("CONSUMER_EFFECTIVE_CONFIG_SCHEMA_MISMATCH")


def _project_projection(pack: dict[str, Any], base_config: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    pack_commands = pack.get("commands") or {}
    base_commands = base_config.get("commands") or {}
    for name in CONFIG_COMMANDS:
        entry = pack_commands.get(name) or {}
        if entry.get("available") is True and entry.get("command"):
            output[name] = {
                "required": True,
                "command": copy.deepcopy(entry["command"]),
                "phases": copy.deepcopy(entry.get("phases") or ["pr"]),
            }
        else:
            fallback = base_commands.get(name) or {"required": False, "command": [], "phases": ["pr"]}
            output[name] = {
                "required": False,
                "command": [],
                "phases": copy.deepcopy(fallback.get("phases") or ["pr"]),
            }
    return output


def _pack_projection(pack: dict[str, Any]) -> dict[str, Any]:
    commands = pack.get("commands") or {}
    return {
        "selected": pack["pack"],
        "selected_digest": pack["pack_digest"],
        "materialized": True,
        "runtime_commands": {
            name: {
                "command": copy.deepcopy((commands.get(name) or {}).get("command")),
                "available": (commands.get(name) or {}).get("available") is True,
                "phases": copy.deepcopy((commands.get(name) or {}).get("phases") or []),
            }
            for name in RUNTIME_COMMANDS
            if name in commands
        },
        "preview": copy.deepcopy(pack.get("preview") or {}),
        "safety": copy.deepcopy(pack.get("safety") or {}),
    }


def seal_profile(profile: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(profile)
    value["profile_sha256"] = _digest({k: v for k, v in value.items() if k != "profile_sha256"})
    return value


def build_consumer_profile(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    product_name: str,
    default_branch: str,
    repository_visibility: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    name = str(product_name or "").strip()
    branch = str(default_branch or "").strip()
    visibility = str(repository_visibility or "").strip().upper()
    if len(name) < 2:
        raise ConsumerProfileError("CONSUMER_PRODUCT_NAME_REQUIRED")
    if not branch or any(ch in branch for ch in "\r\n\x00"):
        raise ConsumerProfileError("CONSUMER_DEFAULT_BRANCH_INVALID")
    if visibility not in VISIBILITY:
        raise ConsumerProfileError("CONSUMER_REPOSITORY_VISIBILITY_INVALID")
    config_path = framework / ".adwf/config.json"
    base = _object(config_path, "CONSUMER_BASE_CONFIG_INVALID")
    _validate_config(base, framework)
    pack = commands_for_pack(project, framework)
    if not pack.get("pack") or not pack.get("pack_digest"):
        raise ConsumerProfileError("PROJECT_PACK_NOT_DETECTED")
    profile = {
        "$schema": ".adwf/schemas/consumer-profile.schema.json",
        "schema_version": 1,
        "role": "CONSUMER_PROJECT_PROFILE",
        "framework_config_sha256": _file_digest(config_path),
        "project_pack_digest": pack["pack_digest"],
        "project": {
            "name": name,
            "default_branch": branch,
            "type": pack["pack"],
            "runtime_product": True,
            "repository_visibility": visibility,
        },
        "commands": _project_projection(pack, base),
        "project_packs": _pack_projection(pack),
    }
    sealed = seal_profile(profile)
    validate_consumer_profile(sealed, project, framework)
    return sealed


def validate_consumer_profile(profile: dict[str, Any], project_root: str | Path, framework_root: str | Path) -> None:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    schema = _object(framework / ".adwf/schemas/consumer-profile.schema.json", "CONSUMER_PROFILE_SCHEMA_INVALID")
    findings = validate(profile, schema)
    if findings:
        raise ConsumerProfileError("CONSUMER_PROFILE_SCHEMA_MISMATCH")
    expected_seal = _digest({k: v for k, v in profile.items() if k != "profile_sha256"})
    if profile.get("profile_sha256") != expected_seal:
        raise ConsumerProfileError("CONSUMER_PROFILE_DIGEST_MISMATCH")
    config_path = framework / ".adwf/config.json"
    if profile.get("framework_config_sha256") != _file_digest(config_path):
        raise ConsumerProfileError("CONSUMER_PROFILE_FRAMEWORK_CONFIG_STALE")
    base = _object(config_path, "CONSUMER_BASE_CONFIG_INVALID")
    _validate_config(base, framework)
    pack = commands_for_pack(project, framework)
    if not pack.get("pack") or not pack.get("pack_digest"):
        raise ConsumerProfileError("CONSUMER_PROFILE_PROJECT_PACK_NOT_DETECTED")
    if profile.get("project_pack_digest") != pack.get("pack_digest"):
        raise ConsumerProfileError("CONSUMER_PROFILE_PACK_DIGEST_MISMATCH")
    if profile.get("project", {}).get("type") != pack.get("pack"):
        raise ConsumerProfileError("CONSUMER_PROFILE_PROJECT_TYPE_MISMATCH")
    expected_commands = _project_projection(pack, base)
    expected_pack = _pack_projection(pack)
    if profile.get("commands") != expected_commands:
        raise ConsumerProfileError("CONSUMER_PROFILE_COMMAND_PROJECTION_MISMATCH")
    if profile.get("project_packs") != expected_pack:
        raise ConsumerProfileError("CONSUMER_PROFILE_PACK_PROJECTION_MISMATCH")


def load_consumer_profile(project_root: str | Path, framework_root: str | Path, *, required: bool = False) -> dict[str, Any] | None:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    path = project / PROFILE_REL
    if path.is_symlink():
        raise ConsumerProfileError("CONSUMER_PROFILE_SYMLINK_FORBIDDEN")
    if not path.exists():
        if required:
            raise ConsumerProfileError("CONSUMER_PROFILE_REQUIRED")
        return None
    if not path.is_file():
        raise ConsumerProfileError("CONSUMER_PROFILE_NON_FILE")
    profile = _object(path, "CONSUMER_PROFILE_INVALID")
    validate_consumer_profile(profile, project, framework)
    return profile


def load_effective_config(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    base = _object(framework / ".adwf/config.json", "CONSUMER_BASE_CONFIG_INVALID")
    # Preserve the pre-overlay self-host/template path: when no consumer profile
    # exists, callers receive canonical config exactly as before and do not gain a
    # new dependency on consumer-profile/config schemas. A real overlay is the
    # point at which strict base/profile/effective validation becomes mandatory.
    profile = load_consumer_profile(project, framework, required=False)
    if profile is None:
        return base
    _validate_config(base, framework)
    effective = copy.deepcopy(base)
    effective["project"] = copy.deepcopy(profile["project"])
    effective["commands"] = copy.deepcopy(profile["commands"])
    pp = effective.setdefault("project_packs", {})
    for key, value in profile["project_packs"].items():
        pp[key] = copy.deepcopy(value)
    _validate_config(effective, framework)
    return effective


def plan_consumer_profile(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    product_name: str,
    default_branch: str,
    repository_visibility: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    desired = build_consumer_profile(
        project, framework, product_name=product_name, default_branch=default_branch,
        repository_visibility=repository_visibility,
    )
    path = project / PROFILE_REL
    if path.is_symlink():
        return {"status": "BLOCK", "reason": "CONSUMER_PROFILE_SYMLINK_FORBIDDEN", "write_performed": False}
    if path.exists():
        if not path.is_file():
            return {"status": "BLOCK", "reason": "CONSUMER_PROFILE_NON_FILE", "write_performed": False}
        try:
            existing = _object(path, "CONSUMER_PROFILE_INVALID")
            validate_consumer_profile(existing, project, framework)
        except ConsumerProfileError as exc:
            return {"status": "HUMAN_REQUIRED", "reason": str(exc).split(":", 1)[0], "write_performed": False}
        if existing == desired:
            return {
                "status": "ALREADY_MATERIALIZED", "profile_path": PROFILE_REL,
                "pack": desired["project_packs"]["selected"], "pack_digest": desired["project_pack_digest"],
                "write_performed": False, "desired_profile": desired,
            }
        return {"status": "HUMAN_REQUIRED", "reason": "CONSUMER_PROFILE_EXISTS_DIFFERENT", "write_performed": False}
    return {
        "status": "READY_TO_APPLY", "profile_path": PROFILE_REL,
        "pack": desired["project_packs"]["selected"], "pack_digest": desired["project_pack_digest"],
        "write_performed": False, "desired_profile": desired,
    }


def _safe_profile_parent(project: Path) -> Path:
    parent = project / ".adwf-consumer"
    if parent.is_symlink():
        raise ConsumerProfileError("CONSUMER_PROFILE_PARENT_SYMLINK_FORBIDDEN")
    if parent.exists() and not parent.is_dir():
        raise ConsumerProfileError("CONSUMER_PROFILE_PARENT_NON_DIRECTORY")
    if not parent.exists():
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if parent.is_symlink() or not parent.is_dir():
        raise ConsumerProfileError("CONSUMER_PROFILE_PARENT_UNSAFE")
    return parent


def apply_consumer_profile(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    product_name: str,
    default_branch: str,
    repository_visibility: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    plan = plan_consumer_profile(
        project, framework, product_name=product_name, default_branch=default_branch,
        repository_visibility=repository_visibility,
    )
    if plan["status"] == "ALREADY_MATERIALIZED":
        return plan
    if plan["status"] != "READY_TO_APPLY":
        return plan
    parent = _safe_profile_parent(project)
    path = parent / "profile.json"
    if path.exists() or path.is_symlink():
        return {"status": "HUMAN_REQUIRED", "reason": "CONSUMER_PROFILE_CONCURRENT_COLLISION", "write_performed": False}
    payload = json.dumps(plan["desired_profile"], ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return {"status": "HUMAN_REQUIRED", "reason": "CONSUMER_PROFILE_CONCURRENT_COLLISION", "write_performed": False}
    try:
        readback = load_consumer_profile(project, framework, required=True)
        effective = load_effective_config(project, framework)
    except Exception:
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == payload:
                path.unlink()
        finally:
            raise
    return {
        "status": "APPLIED", "profile_path": PROFILE_REL,
        "pack": readback["project_packs"]["selected"], "pack_digest": readback["project_pack_digest"],
        "write_performed": True, "effective_config": effective,
    }
