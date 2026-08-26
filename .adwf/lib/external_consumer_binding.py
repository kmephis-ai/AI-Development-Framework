"""External thin consumer binding: exact provenance without managed-surface adoption.

The binding is proof/configuration only. It grants no workflow, provider, product,
runtime, secret, deployment, or filesystem mutation authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import re
import subprocess

from .contracts import validate
from .project_packs import detect_pack, load_packs
from .strict_json import loads as strict_loads

BINDING_REL = ".adwf-consumer/external-binding.json"
SCHEMA_REL = ".adwf/schemas/external-consumer-binding.schema.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RESERVED_CHECKS = {
    "fast-feedback",
    "canonical-verification",
    "adwf/governance-gate",
    "adwf/trusted-gate",
}


class ExternalConsumerBindingError(ValueError):
    """Deterministic fail-closed external consumer binding error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def seal_binding(value: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(value)
    out["binding_sha256"] = _sha({key: item for key, item in out.items() if key != "binding_sha256"})
    return out


def _schema(framework_root: Path) -> dict[str, Any]:
    path = framework_root / SCHEMA_REL
    if not path.is_file() or path.is_symlink():
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_SCHEMA_REQUIRED")
    value = strict_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_SCHEMA_OBJECT_REQUIRED")
    return value


def _git(root: Path, *args: str) -> str:
    try:
        process = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_GIT_UNAVAILABLE") from exc
    if process.returncode:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_GIT_IDENTITY_UNAVAILABLE")
    return process.stdout.strip()


def _repository_from_remote(root: Path) -> str:
    value = _git(root, "config", "--get", "remote.origin.url")
    patterns = (
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value)
        if match:
            repository = match.group(1).removesuffix(".git")
            if REPOSITORY.fullmatch(repository):
                return repository
    raise ExternalConsumerBindingError("EXTERNAL_BINDING_REPOSITORY_UNRESOLVED")


def _head_sha(root: Path) -> str:
    value = _git(root, "rev-parse", "HEAD")
    if SHA40.fullmatch(value) is None:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_SOURCE_SHA_UNRESOLVED")
    return value


def _validate_default_branch(value: str) -> None:
    if (
        not value
        or value.startswith(("/", "."))
        or value.endswith(("/", "."))
        or ".." in value
        or "@{" in value
        or "\\" in value
        or any(ch.isspace() for ch in value)
    ):
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_DEFAULT_BRANCH_INVALID")


def _validate_gate_declarations(binding: dict[str, Any]) -> None:
    phases = binding.get("native_gates") or {}
    for phase in ("pr", "main"):
        declarations = phases.get(phase) or []
        seen: set[str] = set()
        for item in declarations:
            name = str(item.get("check_name") or "")
            lowered = name.lower()
            if lowered in RESERVED_CHECKS or lowered.startswith("adwf/"):
                raise ExternalConsumerBindingError("EXTERNAL_BINDING_RESERVED_GATE:" + name)
            if name in seen:
                raise ExternalConsumerBindingError("EXTERNAL_BINDING_DUPLICATE_GATE:" + name)
            seen.add(name)


def _validate_pack_marker(project: Path, framework: Path, pack_id: str) -> None:
    packs = load_packs(framework)
    loaded = packs.get(pack_id)
    if loaded is None:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_PACK_UNKNOWN:" + pack_id)
    detect = loaded["definition"].get("detect") or {}
    for rel in detect.get("files") or []:
        marker = project / str(rel)
        if marker.is_symlink():
            raise ExternalConsumerBindingError("EXTERNAL_BINDING_PACK_MARKER_SYMLINK:" + str(rel))
        if not marker.is_file():
            raise ExternalConsumerBindingError("EXTERNAL_BINDING_PACK_MARKER_REQUIRED:" + str(rel))


def validate_binding(
    binding: dict[str, Any],
    consumer_root: str | Path,
    framework_root: str | Path,
) -> dict[str, Any]:
    consumer = Path(consumer_root).resolve()
    framework = Path(framework_root).resolve()
    if validate(binding, _schema(framework)):
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_SCHEMA_MISMATCH")
    expected_digest = _sha({key: item for key, item in binding.items() if key != "binding_sha256"})
    if binding.get("binding_sha256") != expected_digest:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_DIGEST_MISMATCH")
    if binding.get("mutation_authority") != "NONE_BINDING_IS_PROOF_ONLY":
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_AUTHORITY_INVALID")
    if binding.get("safety") != {"monetary_budget_usd": 0, "secrets": "FORBIDDEN"}:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_SAFETY_INVALID")
    if (consumer / ".adwf").exists() or (consumer / ".adwf").is_symlink():
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_MANAGED_SURFACE_FORBIDDEN")

    framework_identity = binding["framework"]
    if _repository_from_remote(framework) != framework_identity["repository"]:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_FRAMEWORK_REPOSITORY_MISMATCH")
    if _head_sha(framework) != framework_identity["source_sha"]:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_FRAMEWORK_SOURCE_SHA_MISMATCH")

    consumer_identity = binding["consumer"]
    if _repository_from_remote(consumer) != consumer_identity["repository"]:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_CONSUMER_REPOSITORY_MISMATCH")
    _validate_default_branch(str(consumer_identity["default_branch"]))

    pack_id = str(binding["project_pack"]["id"])
    _validate_pack_marker(consumer, framework, pack_id)
    detected = detect_pack(consumer, framework)
    if detected.get("pack") != pack_id:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_PACK_DETECTION_MISMATCH")
    if detected.get("pack_digest") != binding["project_pack"]["digest"]:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_PACK_DIGEST_MISMATCH")

    _validate_gate_declarations(binding)
    return {
        "status": "VERIFIED_CONTRACT",
        "role": "EXTERNAL_CONSUMER_BINDING",
        "framework_repository": framework_identity["repository"],
        "framework_source_sha": framework_identity["source_sha"],
        "consumer_repository": consumer_identity["repository"],
        "consumer_default_branch": consumer_identity["default_branch"],
        "project_pack": pack_id,
        "project_pack_digest": binding["project_pack"]["digest"],
        "native_gate_evidence": "NOT_VERIFIED",
        "runtime_evidence": "NOT_VERIFIED",
        "mutation_authority": "NONE_BINDING_IS_PROOF_ONLY",
        "managed_surface_adoption": False,
    }


def build_binding(
    consumer_root: str | Path,
    framework_root: str | Path,
    *,
    default_branch: str,
    native_gates: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    consumer = Path(consumer_root).resolve()
    framework = Path(framework_root).resolve()
    if (consumer / ".adwf").exists() or (consumer / ".adwf").is_symlink():
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_MANAGED_SURFACE_FORBIDDEN")
    _validate_default_branch(default_branch)
    detected = detect_pack(consumer, framework)
    pack_id = detected.get("pack")
    pack_digest = detected.get("pack_digest")
    if not pack_id or not pack_digest:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_PROJECT_PACK_REQUIRED")
    raw = {
        "$schema": SCHEMA_REL,
        "schema_version": 1,
        "role": "EXTERNAL_CONSUMER_BINDING",
        "framework": {
            "repository": _repository_from_remote(framework),
            "source_sha": _head_sha(framework),
        },
        "consumer": {
            "repository": _repository_from_remote(consumer),
            "default_branch": default_branch,
        },
        "project_pack": {
            "id": pack_id,
            "digest": pack_digest,
        },
        "native_gates": {
            "pr": copy.deepcopy(native_gates.get("pr") or []),
            "main": copy.deepcopy(native_gates.get("main") or []),
        },
        "safety": {
            "monetary_budget_usd": 0,
            "secrets": "FORBIDDEN",
        },
        "mutation_authority": "NONE_BINDING_IS_PROOF_ONLY",
    }
    binding = seal_binding(raw)
    validate_binding(binding, consumer, framework)
    return binding


def load_binding(
    consumer_root: str | Path,
    framework_root: str | Path,
    *,
    binding_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    consumer = Path(consumer_root).resolve()
    path = Path(binding_path).resolve() if binding_path is not None else consumer / BINDING_REL
    if not path.is_file() or path.is_symlink():
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_FILE_REQUIRED")
    try:
        path.relative_to(consumer)
    except ValueError as exc:
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_PATH_OUTSIDE_CONSUMER") from exc
    value = strict_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExternalConsumerBindingError("EXTERNAL_BINDING_OBJECT_REQUIRED")
    return value, validate_binding(value, consumer, framework_root)
