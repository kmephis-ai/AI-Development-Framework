"""Consumer-owned operational source binding for adopted ADWF repositories.

ADWF self-host keeps canonical `.adwf/roadmap.json` / `.adwf/project-state.json`.
An installed consumer must instead carry an explicit, proof-only binding to its
native Roadmap/work-item sources.  The binding never grants mutation authority
and an installed consumer is never allowed to silently fall back to framework
self-host operational state.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import copy
import hashlib
import json
import os
import re
import tempfile

from .contracts import validate
from .consumer_installation import RECORD_REL, ConsumerInstallationError, load_record
from .consumer_profile import PROFILE_REL, ConsumerProfileError, load_consumer_profile
from .strict_json import loads as strict_loads

BINDING_REL = ".adwf-consumer/operations.json"
SCHEMA_REL = ".adwf/schemas/consumer-operational-binding.schema.json"
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FORBIDDEN_PREFIXES = (".adwf/", ".adwf-runtime/", ".adwf-consumer/")


class ConsumerOperationalError(ValueError):
    """Deterministic fail-closed operational-binding error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_rel(raw: str) -> str:
    value = str(raw or "")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_PATH_INVALID:" + value)
    rel = path.as_posix()
    if rel == ".adwf" or rel == ".adwf-runtime" or rel == ".adwf-consumer" or rel.startswith(FORBIDDEN_PREFIXES):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_FRAMEWORK_PATH_FORBIDDEN:" + rel)
    return rel


def _schema(framework_root: Path) -> dict[str, Any]:
    try:
        value = strict_loads((framework_root / SCHEMA_REL).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SCHEMA_INVALID:" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SCHEMA_OBJECT_REQUIRED")
    return value


def seal_binding(binding: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(binding)
    value["binding_sha256"] = _sha({k: v for k, v in value.items() if k != "binding_sha256"})
    return value


def _validate_source_file(project: Path, rel: str) -> None:
    safe = _safe_rel(rel)
    target = project / safe
    if target.is_symlink():
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SOURCE_SYMLINK_FORBIDDEN:" + safe)
    if not target.is_file():
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SOURCE_MISSING:" + safe)
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SOURCE_NOT_RESOLVABLE:" + safe) from exc
    if project not in resolved.parents:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SOURCE_ESCAPE:" + safe)


def validate_binding(binding: dict[str, Any], project_root: str | Path, framework_root: str | Path) -> None:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    findings = validate(binding, _schema(framework))
    if findings:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_SCHEMA_MISMATCH")
    expected = _sha({k: v for k, v in binding.items() if k != "binding_sha256"})
    if binding.get("binding_sha256") != expected:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_DIGEST_MISMATCH")
    if binding.get("mode") != "CONSUMER_NATIVE":
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_MODE_INVALID")
    if binding.get("mutation_authority") != "NONE_BINDING_IS_REFERENCE_ONLY":
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_MUTATION_AUTHORITY_FORBIDDEN")
    try:
        profile = load_consumer_profile(project, framework, required=True)
        installation = load_record(project, framework)
    except (ConsumerProfileError, ConsumerInstallationError) as exc:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_INSTALLATION_INVALID:" + str(exc)) from exc
    assert profile is not None
    profile_path = project / PROFILE_REL
    installation_path = project / RECORD_REL
    if binding.get("consumer_profile_sha256") != _file_sha(profile_path):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_PROFILE_STALE")
    if binding.get("installation_record_sha256") != _file_sha(installation_path):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_INSTALLATION_STALE")
    repository = str(binding.get("consumer_repository") or "")
    if not REPOSITORY.fullmatch(repository) or installation["consumer"]["repository"] != repository:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_REPOSITORY_MISMATCH")
    work = binding.get("work_items") or {}
    if work.get("provider") != "GITHUB_ISSUES" or work.get("repository") != repository:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_WORK_PROVIDER_MISMATCH")
    roadmap = binding.get("roadmap") or {}
    if roadmap.get("kind") != "MARKDOWN_FILE":
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_ROADMAP_KIND_INVALID")
    _validate_source_file(project, str(roadmap.get("path") or ""))


def build_binding(
    project_root: str | Path,
    framework_root: str | Path,
    *,
    consumer_repository: str,
    roadmap_path: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    if not REPOSITORY.fullmatch(str(consumer_repository or "")):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_REPOSITORY_INVALID")
    try:
        profile = load_consumer_profile(project, framework, required=True)
        installation = load_record(project, framework)
    except (ConsumerProfileError, ConsumerInstallationError) as exc:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_INSTALLATION_INVALID:" + str(exc)) from exc
    assert profile is not None
    if installation["consumer"]["repository"] != consumer_repository:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_REPOSITORY_MISMATCH")
    safe_roadmap = _safe_rel(roadmap_path)
    _validate_source_file(project, safe_roadmap)
    binding = {
        "$schema": SCHEMA_REL,
        "schema_version": 1,
        "role": "CONSUMER_OPERATIONAL_BINDING",
        "mode": "CONSUMER_NATIVE",
        "consumer_repository": consumer_repository,
        "consumer_profile_sha256": _file_sha(project / PROFILE_REL),
        "installation_record_sha256": _file_sha(project / RECORD_REL),
        "roadmap": {"kind": "MARKDOWN_FILE", "path": safe_roadmap},
        "work_items": {"provider": "GITHUB_ISSUES", "repository": consumer_repository},
        "project_state": {"kind": "PROVIDER_NATIVE_NOT_MATERIALIZED"},
        "safety": {"monetary_budget_usd": 0, "secrets": "FORBIDDEN"},
        "mutation_authority": "NONE_BINDING_IS_REFERENCE_ONLY",
    }
    sealed = seal_binding(binding)
    validate_binding(sealed, project, framework)
    return sealed


def write_binding(binding: dict[str, Any], project_root: str | Path, framework_root: str | Path) -> Path:
    project = Path(project_root).resolve()
    framework = Path(framework_root).resolve()
    validate_binding(binding, project, framework)
    target = project / BINDING_REL
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_TARGET_INVALID")
    if target.exists():
        try:
            current = strict_loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConsumerOperationalError("CONSUMER_OPERATIONAL_EXISTING_INVALID") from exc
        if current == binding:
            return target
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_FOREIGN_OR_DRIFTED")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(binding, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try: os.unlink(temp)
        except FileNotFoundError: pass
    return target


def load_binding(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve(); framework = Path(framework_root).resolve()
    path = project / BINDING_REL
    if path.is_symlink() or not path.is_file():
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_BINDING_REQUIRED")
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_BINDING_INVALID:" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_BINDING_OBJECT_REQUIRED")
    validate_binding(value, project, framework)
    return value


def resolve_operational_context(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve(); framework = Path(framework_root).resolve()
    profile_exists = (project / PROFILE_REL).exists()
    installation_exists = (project / RECORD_REL).exists()
    if not profile_exists and not installation_exists:
        return {
            "mode": "SELF_HOST_CANONICAL",
            "roadmap": {"kind": "ADWF_CANONICAL_JSON", "path": ".adwf/roadmap.json"},
            "work_items": {"provider": "ADWF_PROJECT_STATE"},
            "project_state": {"kind": "ADWF_CANONICAL_OR_RUNTIME"},
            "mutation_authority": "UNCHANGED_SELF_HOST_POLICY",
        }
    if profile_exists != installation_exists:
        raise ConsumerOperationalError("CONSUMER_OPERATIONAL_INSTALLATION_INCOMPLETE")
    binding = load_binding(project, framework)
    return {
        "mode": "CONSUMER_NATIVE",
        "consumer_repository": binding["consumer_repository"],
        "roadmap": copy.deepcopy(binding["roadmap"]),
        "work_items": copy.deepcopy(binding["work_items"]),
        "project_state": copy.deepcopy(binding["project_state"]),
        "binding_sha256": binding["binding_sha256"],
        "mutation_authority": binding["mutation_authority"],
    }
