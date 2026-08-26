"""Canonical ADWF execution-plane boundary.

Reproducible engineering evidence belongs to GitHub-hosted CI.
Private/owner execution nodes are runtime-only and never gain CI or merge authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .contracts import validate
from .strict_json import loads as strict_loads

POLICY_REL = ".adwf/execution-plane.json"
SCHEMA_REL = ".adwf/schemas/execution-plane.schema.json"

EXPECTED_REPRODUCIBLE = {
    "STATIC_ANALYSIS",
    "UNIT_TEST",
    "REPRODUCIBLE_INTEGRATION",
    "SCHEMA_VALIDATION",
    "DOCS_VALIDATION",
    "BUILD",
    "PACKAGE_INTEGRITY",
    "SECURITY_SCAN",
    "PLATFORM_SMOKE",
    "GOVERNANCE_GATE",
    "TRUSTED_GATE",
}
EXPECTED_RUNTIME_ONLY = {
    "PRIVATE_NETWORK",
    "LOCAL_CREDENTIAL_STORE",
    "OS_INTEGRATION",
    "GUI_RUNTIME",
    "PHYSICAL_DEVICE",
    "PRIVATE_SERVICE_ENDPOINT",
}


class ExecutionPlaneError(ValueError):
    """Fail-closed execution-plane contract error."""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ExecutionPlaneError("EXECUTION_PLANE_FILE_REQUIRED:" + path.as_posix())
    value = strict_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionPlaneError("EXECUTION_PLANE_OBJECT_REQUIRED:" + path.as_posix())
    return value


def load_policy(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    policy = _load_json(base / POLICY_REL)
    schema = _load_json(base / SCHEMA_REL)
    findings = validate(policy, schema)
    if findings:
        first = findings[0]
        raise ExecutionPlaneError(f"EXECUTION_PLANE_SCHEMA_MISMATCH:{first.path}:{first.code}")
    if set(policy["reproducible_evidence"]["classes"]) != EXPECTED_REPRODUCIBLE:
        raise ExecutionPlaneError("EXECUTION_PLANE_REPRODUCIBLE_CLASSES_DRIFT")
    if set(policy["private_execution_node"]["allowed_evidence_classes"]) != EXPECTED_RUNTIME_ONLY:
        raise ExecutionPlaneError("EXECUTION_PLANE_RUNTIME_CLASSES_DRIFT")
    if EXPECTED_REPRODUCIBLE & EXPECTED_RUNTIME_ONLY:
        raise ExecutionPlaneError("EXECUTION_PLANE_CLASS_OVERLAP")
    return policy


def _matrix_values(text: str) -> set[str] | None:
    match = re.search(r"^\s*os:\s*\[([^\]]+)\]\s*$", text, re.MULTILINE)
    if not match:
        return None
    return {item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()}


def validate_workflow(path: Path, policy: dict[str, Any]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    lowered = text.lower()
    if "self-hosted" in lowered:
        errors.append(f"{path.name}:SELF_HOSTED_CI_FORBIDDEN")
    allowed = set(policy["ci"]["approved_runners"])
    declarations = re.findall(r"^\s*runs-on:\s*([^\n#]+)", text, re.MULTILINE)
    if not declarations:
        errors.append(f"{path.name}:RUNNER_DECLARATION_REQUIRED")
        return errors
    for raw in declarations:
        value = raw.strip()
        if value in allowed:
            continue
        if value == "${{ matrix.os }}":
            matrix = _matrix_values(text)
            if matrix == allowed:
                continue
            errors.append(f"{path.name}:HOSTED_MATRIX_NOT_EXACT:{','.join(sorted(matrix or set()))}")
            continue
        errors.append(f"{path.name}:NON_GITHUB_HOSTED_RUNNER:{value}")
    return errors


def validate_execution_plane(root: str | Path) -> list[str]:
    base = Path(root).resolve()
    errors: list[str] = []
    try:
        policy = load_policy(base)
    except (ExecutionPlaneError, OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    if policy["ci"]["authority"] != "GITHUB_HOSTED_ONLY":
        errors.append("CI_AUTHORITY_NOT_GITHUB_HOSTED_ONLY")
    if policy["ci"]["owner_workstation_ci_authority"] != "NONE":
        errors.append("OWNER_WORKSTATION_CI_AUTHORITY_FORBIDDEN")
    private = policy["private_execution_node"]
    if private["role"] != "RUNTIME_EXECUTION_NODE_ONLY" or private["ci_authority"] != "NONE":
        errors.append("PRIVATE_NODE_RUNTIME_ONLY_REQUIRED")
    if private["required_for_ci"] is not False:
        errors.append("PRIVATE_NODE_MUST_NOT_BE_REQUIRED_FOR_CI")
    if private["registration_with_public_repository"] != "FORBIDDEN":
        errors.append("PRIVATE_NODE_PUBLIC_RUNNER_REGISTRATION_FORBIDDEN")
    workflows = sorted((base / ".github/workflows").glob("adwf-*.yml"))
    if not workflows:
        errors.append("CANONICAL_ADWF_WORKFLOWS_REQUIRED")
    for path in workflows:
        errors.extend(validate_workflow(path, policy))
    return errors


def evidence_plane(root: str | Path, evidence_class: str) -> dict[str, str]:
    policy = load_policy(root)
    if evidence_class in policy["reproducible_evidence"]["classes"]:
        return {"evidence_class": evidence_class, "execution_plane": "GITHUB_HOSTED", "ci_authority": "GITHUB_ACTIONS"}
    if evidence_class in policy["private_execution_node"]["allowed_evidence_classes"]:
        return {"evidence_class": evidence_class, "execution_plane": "PRIVATE_RUNTIME_NODE", "ci_authority": "NONE"}
    return {"evidence_class": evidence_class, "execution_plane": "UNKNOWN", "ci_authority": "NONE"}
