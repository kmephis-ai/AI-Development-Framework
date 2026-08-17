"""Provider-durable proof of an exact ADWF consumer installation.

The ignored `.adwf-runtime` journal remains the transaction/recovery SSOT while a
transaction is executing.  After COMMITTED adoption and a valid Consumer Profile,
this module can publish a *proof-only* consumer-owned record.  A fresh checkout can
validate installed managed bytes and the original snapshot binding without runtime
state.  The record never grants write/delete authority.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile

from .contracts import validate
from .consumer_profile import PROFILE_REL, ConsumerProfileError, load_consumer_profile
from .managed_surface import SHA256, _validate_snapshot, load_source_inventory
from .strict_json import loads as strict_loads

RECORD_REL = ".adwf-consumer/installation.json"
SCHEMA_REL = ".adwf/schemas/consumer-installation-record.schema.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

class ConsumerInstallationError(ValueError):
    """Deterministic fail-closed installation-record error."""


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
        raise ConsumerInstallationError("INSTALLATION_PATH_INVALID:" + value)
    return path.as_posix()


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConsumerInstallationError("INSTALLATION_GIT_NOT_VERIFIABLE:" + type(exc).__name__) from exc
    if proc.returncode != 0:
        raise ConsumerInstallationError("INSTALLATION_GIT_NOT_VERIFIABLE")
    return proc.stdout.strip()


def _schema(framework_root: Path) -> dict[str, Any]:
    try:
        value = strict_loads((framework_root / SCHEMA_REL).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConsumerInstallationError("INSTALLATION_SCHEMA_INVALID:" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerInstallationError("INSTALLATION_SCHEMA_OBJECT_REQUIRED")
    return value


def seal_record(record: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(record)
    value["record_sha256"] = _sha({k: v for k, v in value.items() if k != "record_sha256"})
    return value


def _snapshot_from_record(record: dict[str, Any]) -> dict[str, Any]:
    ms = record["managed_surface"]
    adoption = record["adoption"]
    return {
        "$schema": ".adwf/schemas/managed-surface-snapshot.schema.json",
        "schema_version": 1,
        "role": "MANAGED_SURFACE_SNAPSHOT",
        "source_revision": ms["source_revision"],
        "source_manifest_sha256": ms["source_manifest_sha256"],
        "entries": copy.deepcopy(ms["entries"]),
        "transaction_id": adoption["transaction_id"],
        "plan_sha256": adoption["plan_sha256"],
        "consumer_root_sha256": ms["consumer_root_sha256"],
    }


def validate_record_schema(record: dict[str, Any], framework_root: str | Path) -> None:
    root = Path(framework_root).resolve()
    findings = validate(record, _schema(root))
    if findings:
        raise ConsumerInstallationError("INSTALLATION_SCHEMA_MISMATCH")
    expected = _sha({k: v for k, v in record.items() if k != "record_sha256"})
    if record.get("record_sha256") != expected:
        raise ConsumerInstallationError("INSTALLATION_RECORD_DIGEST_MISMATCH")
    entries = record.get("managed_surface", {}).get("entries") or []
    paths = [_safe_rel(str(item.get("path") or "")) for item in entries]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        raise ConsumerInstallationError("INSTALLATION_MANAGED_ENTRIES_INVALID")
    for item in entries:
        if item.get("managed_by_adwf") is True and item.get("preserved_sha256") is not None:
            raise ConsumerInstallationError("INSTALLATION_MANAGED_PRESERVED_FORBIDDEN:" + item["path"])
        if item.get("managed_by_adwf") is False and not SHA256.fullmatch(str(item.get("preserved_sha256") or "")):
            raise ConsumerInstallationError("INSTALLATION_PRESERVED_DIGEST_REQUIRED:" + item["path"])
    if record.get("mutation_authority") != "NONE_RECORD_IS_PROOF_ONLY":
        raise ConsumerInstallationError("INSTALLATION_MUTATION_AUTHORITY_FORBIDDEN")


def build_record(
    framework_root: str | Path,
    consumer_root: str | Path,
    adoption_result: dict[str, Any],
    *,
    consumer_repository: str,
    consumer_base_sha: str | None,
    consumer_base_tree: str | None,
) -> dict[str, Any]:
    framework = Path(framework_root).resolve()
    consumer = Path(consumer_root).resolve()
    if not REPOSITORY.fullmatch(str(consumer_repository or "")):
        raise ConsumerInstallationError("INSTALLATION_CONSUMER_REPOSITORY_INVALID")
    for name, value in (("base_sha", consumer_base_sha), ("base_tree", consumer_base_tree)):
        if value is not None and not SHA40.fullmatch(str(value)):
            raise ConsumerInstallationError("INSTALLATION_CONSUMER_" + name.upper() + "_INVALID")
    if adoption_result.get("status") not in {"COMMITTED", "ALREADY_COMMITTED"}:
        raise ConsumerInstallationError("INSTALLATION_REQUIRES_COMMITTED_ADOPTION")
    snapshot = adoption_result.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ConsumerInstallationError("INSTALLATION_SNAPSHOT_REQUIRED")
    _validate_snapshot(snapshot, framework)
    txid = str(adoption_result.get("transaction_id") or snapshot.get("transaction_id") or "")
    if not SHA256.fullmatch(txid) or snapshot.get("transaction_id") != txid:
        raise ConsumerInstallationError("INSTALLATION_TRANSACTION_BINDING_INVALID")
    inventory = load_source_inventory(framework)
    source_sha = _git(framework, "rev-parse", "HEAD")
    source_tree = _git(framework, "rev-parse", "HEAD^{tree}")
    if snapshot.get("source_revision") != source_sha:
        raise ConsumerInstallationError("INSTALLATION_SOURCE_REVISION_MISMATCH")
    if snapshot.get("source_manifest_sha256") != inventory["manifest_sha256"]:
        raise ConsumerInstallationError("INSTALLATION_SOURCE_MANIFEST_MISMATCH")
    profile = load_consumer_profile(consumer, consumer, required=True)
    assert profile is not None
    profile_path = consumer / PROFILE_REL
    snapshot_sha = adoption_result.get("snapshot_sha256")
    if not isinstance(snapshot_sha, str) or not SHA256.fullmatch(snapshot_sha):
        snapshot_sha = _file_sha(consumer / str(adoption_result.get("snapshot_path") or "")) if adoption_result.get("snapshot_path") else None
    if not isinstance(snapshot_sha, str) or not SHA256.fullmatch(snapshot_sha):
        raise ConsumerInstallationError("INSTALLATION_SNAPSHOT_DIGEST_REQUIRED")
    # The stored runtime snapshot is canonical pretty JSON; the embedded semantic
    # snapshot must still bind to that exact file digest at publication time.
    record = {
        "$schema": SCHEMA_REL,
        "schema_version": 1,
        "role": "CONSUMER_INSTALLATION_RECORD",
        "framework": {
            "source_sha": source_sha,
            "source_tree": source_tree,
            "manifest_sha256": inventory["manifest_sha256"],
        },
        "consumer": {
            "repository": consumer_repository,
            "base_sha": consumer_base_sha,
            "base_tree": consumer_base_tree,
        },
        "adoption": {
            "transaction_id": txid,
            "status": "COMMITTED",
            "plan_sha256": snapshot["plan_sha256"],
            "snapshot_sha256": snapshot_sha,
        },
        "managed_surface": {
            "source_revision": snapshot["source_revision"],
            "source_manifest_sha256": snapshot["source_manifest_sha256"],
            "consumer_root_sha256": snapshot["consumer_root_sha256"],
            "entries": copy.deepcopy(snapshot["entries"]),
        },
        "consumer_profile": {
            "required": True,
            "profile_sha256": _file_sha(profile_path),
            "project_pack_id": profile["project_packs"]["selected"],
            "project_pack_digest": profile["project_packs"]["selected_digest"],
        },
        "safety": {"monetary_budget_usd": 0, "secrets": "FORBIDDEN"},
        "mutation_authority": "NONE_RECORD_IS_PROOF_ONLY",
    }
    sealed = seal_record(record)
    validate_record_schema(sealed, framework)
    return sealed


def write_record(record: dict[str, Any], consumer_root: str | Path, framework_root: str | Path) -> Path:
    consumer = Path(consumer_root).resolve()
    framework = Path(framework_root).resolve()
    validate_record_schema(record, framework)
    target = consumer / RECORD_REL
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ConsumerInstallationError("INSTALLATION_RECORD_TARGET_INVALID")
    if target.exists():
        current = strict_loads(target.read_text(encoding="utf-8"))
        if current == record:
            return target
        raise ConsumerInstallationError("INSTALLATION_RECORD_FOREIGN_OR_DRIFTED")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try: os.unlink(temp)
        except FileNotFoundError: pass
    return target


def load_record(consumer_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    consumer = Path(consumer_root).resolve(); framework = Path(framework_root).resolve()
    path = consumer / RECORD_REL
    if path.is_symlink() or not path.is_file():
        raise ConsumerInstallationError("INSTALLATION_RECORD_REQUIRED")
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConsumerInstallationError("INSTALLATION_RECORD_INVALID:" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerInstallationError("INSTALLATION_RECORD_OBJECT_REQUIRED")
    validate_record_schema(value, framework)
    return value


def validate_fresh_session(
    consumer_root: str | Path,
    framework_root: str | Path,
    *, expected_repository: str | None = None,
) -> dict[str, Any]:
    consumer = Path(consumer_root).resolve(); framework = Path(framework_root).resolve()
    record = load_record(consumer, framework)
    if expected_repository is not None and record["consumer"]["repository"] != expected_repository:
        raise ConsumerInstallationError("INSTALLATION_CONSUMER_REPOSITORY_MISMATCH")
    inventory = load_source_inventory(framework)
    if _git(framework, "rev-parse", "HEAD") != record["framework"]["source_sha"]:
        raise ConsumerInstallationError("INSTALLATION_FRAMEWORK_SHA_MISMATCH")
    if _git(framework, "rev-parse", "HEAD^{tree}") != record["framework"]["source_tree"]:
        raise ConsumerInstallationError("INSTALLATION_FRAMEWORK_TREE_MISMATCH")
    if inventory["manifest_sha256"] != record["framework"]["manifest_sha256"]:
        raise ConsumerInstallationError("INSTALLATION_FRAMEWORK_MANIFEST_MISMATCH")
    snapshot = _snapshot_from_record(record)
    _validate_snapshot(snapshot, framework)
    snapshot_payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if hashlib.sha256(snapshot_payload).hexdigest() != record["adoption"]["snapshot_sha256"]:
        raise ConsumerInstallationError("INSTALLATION_SNAPSHOT_DIGEST_MISMATCH")
    try:
        profile = load_consumer_profile(consumer, consumer, required=True)
    except ConsumerProfileError as exc:
        raise ConsumerInstallationError("INSTALLATION_PROFILE_INVALID:" + str(exc)) from exc
    assert profile is not None
    cp = record["consumer_profile"]
    if _file_sha(consumer / PROFILE_REL) != cp["profile_sha256"]:
        raise ConsumerInstallationError("INSTALLATION_PROFILE_DIGEST_MISMATCH")
    if profile["project_packs"]["selected"] != cp["project_pack_id"] or profile["project_packs"]["selected_digest"] != cp["project_pack_digest"]:
        raise ConsumerInstallationError("INSTALLATION_PROFILE_PACK_MISMATCH")
    verified = 0
    for entry in snapshot["entries"]:
        rel = _safe_rel(entry["path"])
        path = consumer / rel
        expected = entry.get("preserved_sha256") if entry.get("managed_by_adwf") is False else entry["installed_sha256"]
        if path.is_symlink() or not path.is_file() or _file_sha(path) != expected:
            raise ConsumerInstallationError("INSTALLATION_MANAGED_BYTES_MISMATCH:" + rel)
        verified += 1
    return {
        "status": "VERIFIED",
        "framework_source_sha": record["framework"]["source_sha"],
        "framework_source_tree": record["framework"]["source_tree"],
        "consumer_repository": record["consumer"]["repository"],
        "managed_entries_verified": verified,
        "mutation_authority": "NONE_RECORD_IS_PROOF_ONLY",
        "monetary_budget_usd": 0,
        "secrets": "FORBIDDEN",
    }
