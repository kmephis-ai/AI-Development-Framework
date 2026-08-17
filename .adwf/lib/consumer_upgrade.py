"""Fail-closed read-only consumer framework upgrade planning.

UPGRADE-001 composes existing Managed Surface, Consumer Profile, Project Pack and
Skill registry evidence into a deterministic A->B compatibility result and plan.
This module never mutates the consumer or either framework checkout.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import copy
import hashlib
import json
import re
import subprocess

from .consumer_profile import (
    PROFILE_REL, ConsumerProfileError, build_consumer_profile, load_consumer_profile,
)
from .consumer_instructions import (
    ConsumerInstructionError, legacy_preexisting_router_transition_allowed,
    load_consumer_instruction_policy, validate_consumer_instruction_state,
)
from .contracts import validate
from .managed_surface import _validate_snapshot, load_source_inventory
from .strict_json import load as strict_load

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMPAT_SCHEMA = ".adwf/schemas/consumer-upgrade-compatibility.schema.json"
PLAN_SCHEMA = ".adwf/schemas/consumer-upgrade-plan.schema.json"
MIGRATION_REGISTRY = ".adwf/consumer-upgrade-migrations.json"
MIGRATION_SCHEMA = ".adwf/schemas/consumer-upgrade-migrations.schema.json"
SKILL_BINDINGS_SCHEMA_VERSION = 1


class ConsumerUpgradeError(ValueError):
    """Deterministic fail-closed consumer upgrade planning error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _root_sha(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _safe_rel(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ConsumerUpgradeError("UPGRADE_PATH_INVALID")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("./") or any(part in {"", ".", ".."} for part in pure.parts):
        raise ConsumerUpgradeError("UPGRADE_PATH_INVALID")
    if pure.as_posix() != value:
        raise ConsumerUpgradeError("UPGRADE_PATH_NOT_CANONICAL")
    return value


def _json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_load(path)
    except Exception as exc:
        raise ConsumerUpgradeError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerUpgradeError(code + ":OBJECT_REQUIRED")
    return value


def _verify_revision(root: Path, expected: str) -> None:
    if not SHA40.fullmatch(str(expected)):
        raise ConsumerUpgradeError("UPGRADE_REVISION_INVALID")
    if root.is_symlink() or not root.is_dir():
        raise ConsumerUpgradeError("UPGRADE_FRAMEWORK_ROOT_UNSAFE")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=5, check=False,
        )
        state = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConsumerUpgradeError("UPGRADE_REVISION_NOT_VERIFIABLE:" + type(exc).__name__) from exc
    if head.returncode != 0 or head.stdout.strip() != expected:
        raise ConsumerUpgradeError("UPGRADE_REVISION_MISMATCH")
    if state.returncode != 0:
        raise ConsumerUpgradeError("UPGRADE_WORKTREE_NOT_VERIFIABLE")
    if state.stdout.strip():
        raise ConsumerUpgradeError("UPGRADE_WORKTREE_NOT_CLEAN")


def _validate_schema(value: dict[str, Any], root: Path, rel: str, code: str) -> None:
    schema = _json_object(root / rel, code + "_SCHEMA_INVALID")
    findings = validate(value, schema)
    if findings:
        raise ConsumerUpgradeError(
            code + "_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )


def _seal(value: dict[str, Any], field: str) -> dict[str, Any]:
    output = copy.deepcopy(value)
    output[field] = _sha({key: item for key, item in output.items() if key != field})
    return output


def _verify_seal(value: dict[str, Any], field: str, code: str) -> None:
    expected = _sha({key: item for key, item in value.items() if key != field})
    if value.get(field) != expected:
        raise ConsumerUpgradeError(code)


def _consumer_root(value: str | Path) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_CONSUMER_ROOT_SYMLINK_FORBIDDEN")
    root = raw.resolve()
    if not root.is_dir():
        raise ConsumerUpgradeError("UPGRADE_CONSUMER_ROOT_DIRECTORY_REQUIRED")
    return root


def _path_state(root: Path, rel: str) -> tuple[str, str | None]:
    rel = _safe_rel(rel)
    current = root
    parts = PurePosixPath(rel).parts
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            return "SYMLINK", None
        if index < len(parts) - 1 and current.exists() and not current.is_dir():
            return "PARENT_NON_DIRECTORY", None
    if not current.exists():
        return "ABSENT", None
    if not current.is_file():
        return "NON_FILE", None
    return "FILE", _file_sha(current)


def _schema_const(root: Path, rel: str, property_name: str) -> Any:
    schema = _json_object(root / rel, "UPGRADE_CONTRACT_SCHEMA_INVALID")
    properties = schema.get("properties") or {}
    entry = properties.get(property_name) or {}
    if "const" not in entry:
        raise ConsumerUpgradeError("UPGRADE_CONTRACT_SCHEMA_VERSION_UNKNOWN:" + rel)
    return entry["const"]


def _config_identity(root: Path) -> tuple[str, int]:
    config = _json_object(root / ".adwf/config.json", "UPGRADE_CONFIG_INVALID")
    version = config.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise ConsumerUpgradeError("UPGRADE_CONFIG_SCHEMA_VERSION_INVALID")
    return f"schema:{version}", version


def _profile_identity(root: Path) -> tuple[str, int]:
    version = _schema_const(root, ".adwf/schemas/consumer-profile.schema.json", "schema_version")
    if not isinstance(version, int) or version < 1:
        raise ConsumerUpgradeError("UPGRADE_PROFILE_SCHEMA_VERSION_INVALID")
    return f"schema:{version}", version


def _snapshot_identity(root: Path) -> tuple[str, int]:
    version = _schema_const(root, ".adwf/schemas/managed-surface-snapshot.schema.json", "schema_version")
    if not isinstance(version, int) or version < 1:
        raise ConsumerUpgradeError("UPGRADE_SNAPSHOT_SCHEMA_VERSION_INVALID")
    return f"schema:{version}", version


def _load_migrations(target_root: Path) -> dict[str, Any]:
    path = target_root / MIGRATION_REGISTRY
    if path.is_symlink() or not path.is_file():
        raise ConsumerUpgradeError("UPGRADE_MIGRATION_REGISTRY_REQUIRED")
    registry = _json_object(path, "UPGRADE_MIGRATION_REGISTRY_INVALID")
    _validate_schema(registry, target_root, MIGRATION_SCHEMA, "UPGRADE_MIGRATION_REGISTRY")
    seen: set[tuple[str, str, str]] = set()
    for item in registry.get("records") or []:
        key = (str(item.get("contract")), str(item.get("from_identity")), str(item.get("to_identity")))
        if key in seen:
            raise ConsumerUpgradeError("UPGRADE_MIGRATION_RECORD_DUPLICATE")
        seen.add(key)
        if item.get("disposition") == "MIGRATION_REQUIRED" and not item.get("migration_id"):
            raise ConsumerUpgradeError("UPGRADE_MIGRATION_ID_REQUIRED")
        if item.get("disposition") == "COMPATIBLE_NO_MIGRATION" and item.get("migration_id") is not None:
            raise ConsumerUpgradeError("UPGRADE_MIGRATION_ID_FORBIDDEN")
    return registry


def _contract_transition(
    migrations: dict[str, Any], contract: str, source_identity: str, target_identity: str,
) -> dict[str, Any]:
    if source_identity == target_identity:
        return {
            "contract": contract, "source_identity": source_identity, "target_identity": target_identity,
            "status": "PASS", "migration_id": None, "record_id": None,
        }
    matches = [
        item for item in migrations.get("records") or []
        if item.get("contract") == contract
        and item.get("from_identity") == source_identity
        and item.get("to_identity") == target_identity
    ]
    if len(matches) != 1:
        return {
            "contract": contract, "source_identity": source_identity, "target_identity": target_identity,
            "status": "HUMAN_REQUIRED", "migration_id": None, "record_id": None,
        }
    item = matches[0]
    return {
        "contract": contract, "source_identity": source_identity, "target_identity": target_identity,
        "status": "PASS", "migration_id": item.get("migration_id"), "record_id": item["id"],
    }


def _load_skill_registry(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "skills/registry.json"
    if path.is_symlink() or not path.is_file():
        raise ConsumerUpgradeError("UPGRADE_SKILL_REGISTRY_REQUIRED")
    value = _json_object(path, "UPGRADE_SKILL_REGISTRY_INVALID")
    skills = value.get("skills")
    if not isinstance(skills, list):
        raise ConsumerUpgradeError("UPGRADE_SKILL_REGISTRY_SKILLS_INVALID")
    result: dict[str, dict[str, Any]] = {}
    for item in skills:
        if not isinstance(item, dict) or not item.get("id") or not SHA256.fullmatch(str(item.get("package_sha256") or "")):
            raise ConsumerUpgradeError("UPGRADE_SKILL_REGISTRY_ENTRY_INVALID")
        skill_id = str(item["id"])
        if skill_id in result:
            raise ConsumerUpgradeError("UPGRADE_SKILL_REGISTRY_DUPLICATE")
        result[skill_id] = item
    return result


def _skill_compatibility(
    source_root: Path, target_root: Path, migrations: dict[str, Any], bindings: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if bindings is None:
        return [], []
    if not isinstance(bindings, dict) or bindings.get("schema_version") != SKILL_BINDINGS_SCHEMA_VERSION:
        raise ConsumerUpgradeError("UPGRADE_SKILL_BINDINGS_INVALID")
    entries = bindings.get("bindings")
    if not isinstance(entries, list):
        raise ConsumerUpgradeError("UPGRADE_SKILL_BINDINGS_INVALID")
    source_registry = _load_skill_registry(source_root)
    target_registry = _load_skill_registry(target_root)
    output: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for binding in sorted(entries, key=lambda item: str(item.get("id") if isinstance(item, dict) else "")):
        if not isinstance(binding, dict):
            raise ConsumerUpgradeError("UPGRADE_SKILL_BINDING_INVALID")
        skill_id = str(binding.get("id") or "")
        digest = str(binding.get("package_sha256") or "")
        if not skill_id or skill_id in seen or not SHA256.fullmatch(digest):
            raise ConsumerUpgradeError("UPGRADE_SKILL_BINDING_INVALID")
        seen.add(skill_id)
        source = source_registry.get(skill_id)
        target = target_registry.get(skill_id)
        if source is None or source.get("package_sha256") != digest:
            raise ConsumerUpgradeError("UPGRADE_SKILL_SOURCE_BINDING_STALE:" + skill_id)
        if target is None:
            transition = {
                "contract": "SKILL", "source_identity": f"skill:{skill_id}:{digest}",
                "target_identity": f"skill:{skill_id}:ABSENT", "status": "HUMAN_REQUIRED",
                "migration_id": None, "record_id": None,
            }
            target_digest = None
        else:
            target_digest = str(target["package_sha256"])
            transition = _contract_transition(
                migrations, "SKILL", f"skill:{skill_id}:{digest}", f"skill:{skill_id}:{target_digest}"
            )
        item = {
            "id": skill_id, "source_package_sha256": digest, "target_package_sha256": target_digest,
            "status": transition["status"], "migration_id": transition["migration_id"],
            "record_id": transition["record_id"],
        }
        output.append(item)
        if item["status"] != "PASS":
            findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_SKILL_BINDING_INCOMPATIBLE", "subject": skill_id})
    return output, findings


def _severity_status(findings: list[dict[str, str]]) -> str:
    if any(item["severity"] == "BLOCK" for item in findings):
        return "BLOCK"
    if any(item["severity"] == "HUMAN_REQUIRED" for item in findings):
        return "HUMAN_REQUIRED"
    return "PASS"


def _package_diff(
    source_inventory: dict[str, Any], target_inventory: dict[str, Any], consumer_root: Path,
    snapshot: dict[str, Any], target_instruction_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    source_files = set(source_inventory["files"])
    target_files = set(target_inventory["files"])
    snapshot_by_path = {str(item["path"]): item for item in snapshot["entries"]}
    entries: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    restore: list[dict[str, str]] = []
    remove_on_rollback: list[dict[str, str]] = []

    for rel in sorted(source_files | target_files):
        _safe_rel(rel)
        source_sha = source_inventory["sums"].get(rel)
        target_sha = target_inventory["sums"].get(rel)
        source_ownership = (
            "SHARED_GUARDED" if rel in source_inventory["shared"] else "FRAMEWORK_PRIVATE"
        ) if rel in source_files else "CONSUMER_OWNED"
        target_ownership = (
            "SHARED_GUARDED" if rel in target_inventory["shared"] else "FRAMEWORK_PRIVATE"
        ) if rel in target_files else "CONSUMER_OWNED"

        if source_ownership == "SHARED_GUARDED" or target_ownership == "SHARED_GUARDED":
            classification = "SHARED_GUARDED"
        elif rel not in source_files:
            classification = "ADD_FRAMEWORK_PRIVATE"
        elif rel not in target_files:
            classification = "REMOVE_FRAMEWORK_PRIVATE"
        elif source_sha == target_sha:
            classification = "UNCHANGED_FRAMEWORK_PRIVATE"
        else:
            classification = "MODIFY_FRAMEWORK_PRIVATE"

        state, current_sha = _path_state(consumer_root, rel)
        if state in {"SYMLINK", "PARENT_NON_DIRECTORY", "NON_FILE"}:
            action = "BLOCK"
            findings.append({"severity": "BLOCK", "code": "UPGRADE_PATH_TYPE_AMBIGUITY", "subject": rel})
        elif rel in source_files:
            snap = snapshot_by_path.get(rel)
            if snap is None:
                action = "BLOCK"
                findings.append({"severity": "BLOCK", "code": "UPGRADE_SNAPSHOT_PATH_MISSING", "subject": rel})
            elif snap["ownership"] != source_ownership or snap["installed_sha256"] != source_sha:
                action = "BLOCK"
                findings.append({"severity": "BLOCK", "code": "UPGRADE_SNAPSHOT_BINDING_MISMATCH", "subject": rel})
            elif state != "FILE" or current_sha != (
                snap.get("preserved_sha256") if snap["managed_by_adwf"] is not True and snap.get("preserved_sha256") is not None
                else snap["installed_sha256"]
            ):
                action = "BLOCK"
                findings.append({"severity": "BLOCK", "code": "UPGRADE_CONSUMER_DRIFT", "subject": rel})
            elif snap["managed_by_adwf"] is not True:
                action = "PRESERVE_PREEXISTING"
                changed = source_sha != target_sha or rel not in target_files
                router_transition = legacy_preexisting_router_transition_allowed(
                    target_instruction_policy, path=rel, source_ownership=source_ownership,
                    target_ownership=target_ownership, target_present=rel in target_files,
                )
                if changed and not router_transition:
                    findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_PREEXISTING_PATH_CHANGE", "subject": rel})
            elif source_ownership == "SHARED_GUARDED" or target_ownership == "SHARED_GUARDED":
                action = "PRESERVE_SHARED"
                if source_sha != target_sha or source_ownership != target_ownership:
                    findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_SHARED_PATH_CHANGE", "subject": rel})
            elif target_ownership == "CONSUMER_OWNED":
                action = "REMOVE_PLANNED"
                restore.append({"path": rel, "sha256": str(source_sha)})
            elif source_sha == target_sha:
                action = "KEEP_EXACT"
            else:
                action = "REPLACE_PLANNED"
                restore.append({"path": rel, "sha256": str(source_sha)})
        else:
            if target_ownership == "SHARED_GUARDED":
                action = "PRESERVE_SHARED_ABSENT" if state == "ABSENT" else "PRESERVE_SHARED"
                findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_SHARED_ADDITION_REQUIRES_AUTHORITY", "subject": rel})
            elif state == "ABSENT":
                action = "CREATE_PLANNED"
                remove_on_rollback.append({"path": rel, "sha256": str(target_sha)})
            else:
                action = "PRESERVE_CONSUMER_COLLISION"
                findings.append({"severity": "BLOCK", "code": "UPGRADE_CONSUMER_OWNED_COLLISION", "subject": rel})

        if source_ownership != target_ownership and rel in source_files and rel in target_files:
            findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_OWNERSHIP_TRANSITION", "subject": rel})
        entries.append({
            "path": rel, "classification": classification,
            "source_ownership": source_ownership, "target_ownership": target_ownership,
            "source_sha256": source_sha, "target_sha256": target_sha,
            "consumer_state": state, "consumer_sha256": current_sha, "action": action,
        })
    rollback = {
        "source_revision_required": True,
        "source_manifest_required": True,
        "restore": restore,
        "remove_on_rollback": remove_on_rollback,
    }
    return entries, findings, rollback


def build_upgrade_compatibility(
    source_framework_root: str | Path,
    target_framework_root: str | Path,
    consumer_root: str | Path,
    *,
    source_revision: str,
    target_revision: str,
    snapshot: dict[str, Any],
    skill_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sealed compatibility result without writing to any supplied root."""
    source_root = Path(source_framework_root).resolve()
    target_root = Path(target_framework_root).resolve()
    consumer = _consumer_root(consumer_root)
    _verify_revision(source_root, source_revision)
    _verify_revision(target_root, target_revision)
    if source_revision == target_revision:
        raise ConsumerUpgradeError("UPGRADE_REVISIONS_MUST_DIFFER")

    source_inventory = load_source_inventory(source_root)
    target_inventory = load_source_inventory(target_root)
    try:
        target_instruction_policy = load_consumer_instruction_policy(target_root, target_inventory)
        validate_consumer_instruction_state(consumer, target_instruction_policy)
    except ConsumerInstructionError as exc:
        raise ConsumerUpgradeError("UPGRADE_TARGET_INSTRUCTION_POLICY_INVALID:" + str(exc)) from exc
    _validate_snapshot(snapshot, source_root)
    if snapshot.get("source_revision") != source_revision:
        raise ConsumerUpgradeError("UPGRADE_SNAPSHOT_SOURCE_REVISION_STALE")
    if snapshot.get("consumer_root_sha256") is not None and snapshot.get("consumer_root_sha256") != _root_sha(consumer):
        raise ConsumerUpgradeError("UPGRADE_SNAPSHOT_CONSUMER_ROOT_MISMATCH")

    profile_path = consumer / PROFILE_REL
    if profile_path.is_symlink() or not profile_path.is_file():
        raise ConsumerUpgradeError("UPGRADE_CONSUMER_PROFILE_REQUIRED")
    try:
        current_profile = load_consumer_profile(consumer, source_root, required=True)
    except ConsumerProfileError as exc:
        raise ConsumerUpgradeError("UPGRADE_SOURCE_PROFILE_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if current_profile is None:
        raise ConsumerUpgradeError("UPGRADE_CONSUMER_PROFILE_REQUIRED")
    try:
        target_profile = build_consumer_profile(
            consumer, target_root,
            product_name=str(current_profile["project"]["name"]),
            default_branch=str(current_profile["project"]["default_branch"]),
            repository_visibility=str(current_profile["project"]["repository_visibility"]),
        )
    except ConsumerProfileError as exc:
        raise ConsumerUpgradeError("UPGRADE_TARGET_PROFILE_INCOMPATIBLE:" + str(exc).split(":", 1)[0]) from exc
    migrations = _load_migrations(target_root)
    findings: list[dict[str, str]] = []

    config_source_identity, config_source_version = _config_identity(source_root)
    config_target_identity, config_target_version = _config_identity(target_root)
    profile_source_identity, profile_source_version = _profile_identity(source_root)
    profile_target_identity, profile_target_version = _profile_identity(target_root)
    snapshot_source_identity, snapshot_source_version = _snapshot_identity(source_root)
    snapshot_target_identity, snapshot_target_version = _snapshot_identity(target_root)
    contracts = [
        _contract_transition(migrations, "FRAMEWORK_CONFIG_SCHEMA", config_source_identity, config_target_identity),
        _contract_transition(migrations, "CONSUMER_PROFILE_SCHEMA", profile_source_identity, profile_target_identity),
        _contract_transition(migrations, "MANAGED_SURFACE_SNAPSHOT_SCHEMA", snapshot_source_identity, snapshot_target_identity),
    ]
    for item in contracts:
        if item["status"] != "PASS":
            findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_CONTRACT_MIGRATION_UNKNOWN", "subject": item["contract"]})

    source_pack_id = str(current_profile["project_packs"]["selected"])
    source_pack_digest = str(current_profile["project_pack_digest"])
    target_pack_id = str(target_profile["project_packs"]["selected"])
    target_pack_digest = str(target_profile["project_pack_digest"])
    pack_transition = _contract_transition(
        migrations, "PROJECT_PACK",
        f"pack:{source_pack_id}:{source_pack_digest}", f"pack:{target_pack_id}:{target_pack_digest}",
    )
    if source_pack_id != target_pack_id:
        pack_transition["status"] = "HUMAN_REQUIRED"
        pack_transition["migration_id"] = None
        pack_transition["record_id"] = None
    if pack_transition["status"] != "PASS":
        findings.append({"severity": "HUMAN_REQUIRED", "code": "UPGRADE_PROJECT_PACK_INCOMPATIBLE", "subject": source_pack_id})

    skills, skill_findings = _skill_compatibility(source_root, target_root, migrations, skill_bindings)
    findings.extend(skill_findings)
    entries, path_findings, rollback = _package_diff(
        source_inventory, target_inventory, consumer, snapshot, target_instruction_policy
    )
    findings.extend(path_findings)

    compatibility = {
        "$schema": COMPAT_SCHEMA,
        "schema_version": 1,
        "role": "CONSUMER_UPGRADE_COMPATIBILITY",
        "status": _severity_status(findings),
        "source_revision": source_revision,
        "target_revision": target_revision,
        "consumer_root_sha256": _root_sha(consumer),
        "source_manifest_sha256": source_inventory["manifest_sha256"],
        "target_manifest_sha256": target_inventory["manifest_sha256"],
        "profile": {
            "source_sha256": str(current_profile["profile_sha256"]),
            "target_sha256": str(target_profile["profile_sha256"]),
            "source_file_sha256": _file_sha(profile_path),
            "source_schema_version": profile_source_version,
            "target_schema_version": profile_target_version,
        },
        "framework_config": {"source_schema_version": config_source_version, "target_schema_version": config_target_version},
        "managed_surface": {"source_schema_version": snapshot_source_version, "target_schema_version": snapshot_target_version},
        "project_pack": {
            "source_id": source_pack_id, "source_digest": source_pack_digest,
            "target_id": target_pack_id, "target_digest": target_pack_digest,
            "status": pack_transition["status"], "migration_id": pack_transition["migration_id"],
            "record_id": pack_transition["record_id"],
        },
        "contracts": contracts,
        "skills": skills,
        "path_entries": entries,
        "rollback_prerequisites": rollback,
        "findings": findings,
        "write_performed": False,
    }
    sealed = _seal(compatibility, "compatibility_sha256")
    _validate_schema(sealed, target_root, COMPAT_SCHEMA, "UPGRADE_COMPATIBILITY")
    _verify_seal(sealed, "compatibility_sha256", "UPGRADE_COMPATIBILITY_DIGEST_MISMATCH")
    return sealed


def validate_upgrade_compatibility(value: dict[str, Any], target_framework_root: str | Path) -> None:
    root = Path(target_framework_root).resolve()
    _validate_schema(value, root, COMPAT_SCHEMA, "UPGRADE_COMPATIBILITY")
    _verify_seal(value, "compatibility_sha256", "UPGRADE_COMPATIBILITY_DIGEST_MISMATCH")
    expected = _severity_status(list(value.get("findings") or []))
    if value.get("status") != expected:
        raise ConsumerUpgradeError("UPGRADE_COMPATIBILITY_STATUS_FORGED")
    if value.get("write_performed") is not False:
        raise ConsumerUpgradeError("UPGRADE_V1_WRITE_FORBIDDEN")


def plan_consumer_upgrade(
    source_framework_root: str | Path,
    target_framework_root: str | Path,
    consumer_root: str | Path,
    *,
    source_revision: str,
    target_revision: str,
    snapshot: dict[str, Any],
    skill_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a sealed deterministic dry-run plan; no write/apply mode exists."""
    target_root = Path(target_framework_root).resolve()
    compatibility = build_upgrade_compatibility(
        source_framework_root, target_root, consumer_root,
        source_revision=source_revision, target_revision=target_revision,
        snapshot=snapshot, skill_bindings=skill_bindings,
    )
    validate_upgrade_compatibility(compatibility, target_root)
    status = {
        "PASS": "READY",
        "HUMAN_REQUIRED": "HUMAN_REQUIRED",
        "BLOCK": "BLOCK",
    }[compatibility["status"]]
    plan = {
        "$schema": PLAN_SCHEMA,
        "schema_version": 1,
        "role": "CONSUMER_FRAMEWORK_UPGRADE_PLAN",
        "status": status,
        "source_revision": source_revision,
        "target_revision": target_revision,
        "consumer_root_sha256": compatibility["consumer_root_sha256"],
        "source_manifest_sha256": compatibility["source_manifest_sha256"],
        "target_manifest_sha256": compatibility["target_manifest_sha256"],
        "compatibility_sha256": compatibility["compatibility_sha256"],
        "source_profile_sha256": compatibility["profile"]["source_sha256"],
        "target_profile_sha256": compatibility["profile"]["target_sha256"],
        "entries": copy.deepcopy(compatibility["path_entries"]),
        "rollback_prerequisites": copy.deepcopy(compatibility["rollback_prerequisites"]),
        "findings": copy.deepcopy(compatibility["findings"]),
        "write_performed": False,
    }
    sealed = _seal(plan, "plan_sha256")
    _validate_schema(sealed, target_root, PLAN_SCHEMA, "UPGRADE_PLAN")
    validate_upgrade_plan(sealed, compatibility, target_root)
    return sealed


def validate_upgrade_plan(
    value: dict[str, Any], compatibility: dict[str, Any], target_framework_root: str | Path,
) -> None:
    root = Path(target_framework_root).resolve()
    validate_upgrade_compatibility(compatibility, root)
    _validate_schema(value, root, PLAN_SCHEMA, "UPGRADE_PLAN")
    _verify_seal(value, "plan_sha256", "UPGRADE_PLAN_DIGEST_MISMATCH")
    expected_status = {"PASS": "READY", "HUMAN_REQUIRED": "HUMAN_REQUIRED", "BLOCK": "BLOCK"}[compatibility["status"]]
    if value.get("status") != expected_status:
        raise ConsumerUpgradeError("UPGRADE_PLAN_STATUS_FORGED")
    exact = {
        "source_revision": compatibility["source_revision"],
        "target_revision": compatibility["target_revision"],
        "consumer_root_sha256": compatibility["consumer_root_sha256"],
        "source_manifest_sha256": compatibility["source_manifest_sha256"],
        "target_manifest_sha256": compatibility["target_manifest_sha256"],
        "compatibility_sha256": compatibility["compatibility_sha256"],
        "source_profile_sha256": compatibility["profile"]["source_sha256"],
        "target_profile_sha256": compatibility["profile"]["target_sha256"],
        "entries": compatibility["path_entries"],
        "rollback_prerequisites": compatibility["rollback_prerequisites"],
        "findings": compatibility["findings"],
    }
    for key, expected in exact.items():
        if value.get(key) != expected:
            raise ConsumerUpgradeError("UPGRADE_PLAN_COMPATIBILITY_BINDING_MISMATCH:" + key)
    if value.get("write_performed") is not False:
        raise ConsumerUpgradeError("UPGRADE_V1_WRITE_FORBIDDEN")
