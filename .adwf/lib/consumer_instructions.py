"""Fail-closed layered instruction contract for connected consumers."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import json

from .contracts import validate
from .strict_json import load as strict_load

POLICY_REL = ".adwf/consumer-instruction-policy.json"
POLICY_SCHEMA_REL = ".adwf/schemas/consumer-instruction-policy.schema.json"
ROUTER_CONTRACT = "ADWF_CONSUMER_ROUTER_V1"
ROUTER_REQUIRED_MARKERS = (
    "ADWF_CONSUMER_ROUTER_V1",
    "FRAMEWORK_CORE: .adwf/instructions/CORE.md",
    "CONSUMER_INVARIANTS: .adwf-consumer/INVARIANTS.md",
    "LIVE_STATE: provider/runtime",
)
FORBIDDEN_VOLATILE_MARKERS = ("CURRENT_WRITER:", "CURRENT_TASK:", "CURRENT_SHA:")


class ConsumerInstructionError(ValueError):
    """Deterministic instruction-contract validation error."""


def _safe_rel(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_PATH_INVALID")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("./") or any(part in {"", ".", ".."} for part in pure.parts):
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_PATH_INVALID")
    if pure.as_posix() != value:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_PATH_NOT_CANONICAL")
    return value


def _object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_load(path)
    except Exception as exc:
        raise ConsumerInstructionError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerInstructionError(code + ":OBJECT_REQUIRED")
    return value


def load_consumer_instruction_policy(root: str | Path, inventory: dict[str, Any]) -> dict[str, Any]:
    """Load and bind the target instruction policy to exact package ownership."""
    base = Path(root).resolve()
    policy_path = base / POLICY_REL
    schema_path = base / POLICY_SCHEMA_REL
    if policy_path.is_symlink() or not policy_path.is_file():
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_POLICY_REQUIRED")
    if schema_path.is_symlink() or not schema_path.is_file():
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_POLICY_SCHEMA_REQUIRED")
    policy = _object(policy_path, "CONSUMER_INSTRUCTION_POLICY_INVALID")
    schema = _object(schema_path, "CONSUMER_INSTRUCTION_POLICY_SCHEMA_INVALID")
    findings = validate(policy, schema)
    if findings:
        raise ConsumerInstructionError(
            "CONSUMER_INSTRUCTION_POLICY_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )

    files = set(str(item) for item in inventory.get("files") or [])
    shared = set(str(item) for item in inventory.get("shared") or [])
    core = _safe_rel(str(policy["framework_core"]["path"]))
    router = _safe_rel(str(policy["router"]["path"]))
    invariants = _safe_rel(str(policy["consumer_invariants"]["path"]))
    if POLICY_REL not in files or POLICY_SCHEMA_REL not in files:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_POLICY_NOT_IN_PACKAGE")
    core_path = base / core
    router_path = base / router
    if core_path.is_symlink() or not core_path.is_file() or core not in files or core in shared:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_CORE_OWNERSHIP_INVALID")
    if router_path.is_symlink() or not router_path.is_file() or router not in files or router not in shared:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_ROUTER_OWNERSHIP_INVALID")
    if invariants in files or invariants in shared or not invariants.startswith(".adwf-consumer/"):
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_INVARIANTS_OWNERSHIP_INVALID")
    if len({core, router, invariants}) != 3:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_LAYER_PATH_COLLISION")
    return policy


def legacy_preexisting_router_transition_allowed(
    policy: dict[str, Any], *, path: str, source_ownership: str, target_ownership: str, target_present: bool,
) -> bool:
    """Return true only for the exact legacy consumer-preserved root router transition."""
    router = policy.get("router") or {}
    return bool(
        router.get("contract") == ROUTER_CONTRACT
        and router.get("legacy_preexisting_transition") is True
        and router.get("mode") == "CONSUMER_PRESERVED"
        and path == router.get("path")
        and source_ownership == "SHARED_GUARDED"
        and target_ownership == "SHARED_GUARDED"
        and target_present
    )


def validate_consumer_router(consumer_root: str | Path, policy: dict[str, Any]) -> None:
    """Validate a newly materialized/migrated compact router without reading live state."""
    root = Path(consumer_root).resolve()
    rel = _safe_rel(str((policy.get("router") or {}).get("path") or ""))
    path = root / rel
    if path.is_symlink() or not path.is_file():
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_ROUTER_REQUIRED")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConsumerInstructionError("CONSUMER_INSTRUCTION_ROUTER_NOT_READABLE") from exc
    for marker in ROUTER_REQUIRED_MARKERS:
        if marker not in text:
            raise ConsumerInstructionError("CONSUMER_INSTRUCTION_ROUTER_MARKER_MISSING:" + marker.split(":", 1)[0])
    for marker in FORBIDDEN_VOLATILE_MARKERS:
        if marker in text:
            raise ConsumerInstructionError("CONSUMER_INSTRUCTION_ROUTER_VOLATILE_STATE_FORBIDDEN:" + marker[:-1])
