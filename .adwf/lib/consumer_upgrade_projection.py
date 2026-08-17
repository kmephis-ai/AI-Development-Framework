"""Provider-durable proof projection for connected consumer framework upgrades.

Core UPGRADE-002 owns managed-file/profile mutation and recovery.  This module
adds no independent product authority: it only re-seals the three proof-only
consumer sidecars after core COMMITTED, preserving native Roadmap/gate
semantics.  Exact A sidecar preimages are captured in ignored runtime state
before core mutation so projection failure can fail closed and roll back.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os
import tempfile

from .consumer_gates import (
    GATES_REL, ConsumerGateError, build_binding as build_gate_binding,
    load_binding as load_gate_binding,
)
from .consumer_installation import (
    RECORD_REL, ConsumerInstallationError, build_record,
    load_record as load_installation_record, validate_fresh_session,
)
from .consumer_operational import (
    BINDING_REL, ConsumerOperationalError, build_binding as build_operational_binding,
    load_binding as load_operational_binding,
)
from .consumer_upgrade import (
    ConsumerUpgradeError, _file_sha, _root_sha,
    validate_upgrade_compatibility, validate_upgrade_plan,
)
from .github_auth import detect_repository
from .consumer_upgrade_transaction import (
    RUNTIME_REL, UpgradeTransactionStore, _fsync_directory, _transaction_identity,
    apply_upgrade, recover_upgrade, rollback_upgrade,
)
from .strict_json import loads as strict_loads

PROJECTION_ROLE = "CONSUMER_FRAMEWORK_UPGRADE_PROJECTION"
PROJECTION_DIR = RUNTIME_REL / "projections"
PROJECTION_PREIMAGE_DIR = RUNTIME_REL / "projection-preimages"
SIDECARS = (RECORD_REL, BINDING_REL, GATES_REL)


class ConsumerUpgradeProjectionError(ConsumerUpgradeError):
    """Deterministic fail-closed connected-upgrade projection error."""


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    out["journal_sha256"] = _sha_bytes(_canonical({k: v for k, v in out.items() if k != "journal_sha256"}))
    return out


def _runtime_dir(consumer: Path, rel: Path) -> Path:
    current = consumer
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_RUNTIME_SYMLINK_FORBIDDEN:" + current.relative_to(consumer).as_posix())
        if current.exists() and not current.is_dir():
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_RUNTIME_NON_DIRECTORY:" + current.relative_to(consumer).as_posix())
        if not current.exists():
            current.mkdir()
            _fsync_directory(current.parent)
    return current


def _projection_paths(consumer: Path, txid: str) -> tuple[Path, Path]:
    journal_dir = _runtime_dir(consumer, PROJECTION_DIR)
    preimage_root = _runtime_dir(consumer, PROJECTION_PREIMAGE_DIR) / txid
    if preimage_root.is_symlink():
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_PREIMAGE_SYMLINK_FORBIDDEN")
    if not preimage_root.exists():
        preimage_root.mkdir()
        _fsync_directory(preimage_root.parent)
    elif not preimage_root.is_dir():
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_PREIMAGE_NON_DIRECTORY")
    return journal_dir / f"{txid}.json", preimage_root


def _read_file(path: Path, code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ConsumerUpgradeProjectionError(code)
    return path.read_bytes()


def _json_payload(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def _atomic_write(path: Path, payload: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_PARENT_UNSAFE:" + path.as_posix())
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_journal(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    sealed = _seal(value)
    _atomic_write(path, _json_payload(sealed))
    return sealed


def _load_journal(path: Path, txid: str, consumer: Path) -> dict[str, Any]:
    try:
        value = strict_loads(_read_file(path, "UPGRADE_PROJECTION_JOURNAL_REQUIRED").decode("utf-8"))
    except Exception as exc:
        if isinstance(exc, ConsumerUpgradeProjectionError):
            raise
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_JOURNAL_INVALID:" + type(exc).__name__) from exc
    if not isinstance(value, dict) or value.get("role") != PROJECTION_ROLE or value.get("transaction_id") != txid:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_JOURNAL_IDENTITY_INVALID")
    if value.get("consumer_root_sha256") != _root_sha(consumer):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_CONSUMER_ROOT_MISMATCH")
    if value.get("journal_sha256") != _sha_bytes(_canonical({k: v for k, v in value.items() if k != "journal_sha256"})):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_JOURNAL_DIGEST_MISMATCH")
    if set(value.get("sidecars") or {}) != set(SIDECARS):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_SIDECAR_SET_INVALID")
    return value


def _preimage_path(root: Path, rel: str) -> Path:
    return root / (rel.replace("/", "__") + ".preimage")


def _validate_source_bindings(consumer: Path, source_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        install = load_installation_record(consumer, source_root)
        operations = load_operational_binding(consumer, source_root)
        gates = load_gate_binding(consumer, source_root)
    except (ConsumerInstallationError, ConsumerOperationalError, ConsumerGateError) as exc:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_SOURCE_PROOF_INVALID:" + str(exc).split(":", 1)[0]) from exc
    return install, operations, gates


def prepare_projection(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], source_snapshot: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    txid = _transaction_identity(plan, compatibility, source_snapshot, consumer)
    journal_path, preimage_root = _projection_paths(consumer, txid)
    if journal_path.exists():
        return txid, _load_journal(journal_path, txid, consumer)

    install, operations, gates = _validate_source_bindings(consumer, source_root)
    values = {RECORD_REL: install, BINDING_REL: operations, GATES_REL: gates}
    sidecars: dict[str, Any] = {}
    for rel in SIDECARS:
        path = consumer / rel
        payload = _read_file(path, "UPGRADE_PROJECTION_SOURCE_SIDECAR_REQUIRED:" + rel)
        digest = _sha_bytes(payload)
        preimage = _preimage_path(preimage_root, rel)
        _atomic_write(preimage, payload)
        if _sha_bytes(_read_file(preimage, "UPGRADE_PROJECTION_PREIMAGE_WRITE_FAILED:" + rel)) != digest:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_PREIMAGE_DIGEST_MISMATCH:" + rel)
        sidecars[rel] = {"source_sha256": digest, "target_sha256": None, "state": "SOURCE", "preimage": preimage.relative_to(consumer).as_posix()}

    journal = {
        "schema_version": 1,
        "role": PROJECTION_ROLE,
        "transaction_id": txid,
        "status": "PREPARED",
        "source_revision": plan["source_revision"],
        "target_revision": plan["target_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "target_manifest_sha256": plan["target_manifest_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "consumer_root_sha256": _root_sha(consumer),
        "source_semantics": {
            "consumer_repository": install["consumer"]["repository"],
            "consumer_base_sha": install["consumer"].get("base_sha"),
            "consumer_base_tree": install["consumer"].get("base_tree"),
            "roadmap_path": operations["roadmap"]["path"],
            "gate_phases": gates["phases"],
            "gate_required_phases": gates["required_phases"],
        },
        "sidecars": sidecars,
        "last_error": None,
        "journal_sha256": "",
    }
    return txid, _save_journal(journal_path, journal)


def _target_snapshot_result(target_root: Path, consumer: Path, txid: str) -> dict[str, Any]:
    store = UpgradeTransactionStore(target_root, consumer, txid, create=False)
    journal = store.load()
    if journal is None or journal.get("status") != "COMMITTED":
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_CORE_NOT_COMMITTED")
    if not store.snapshot.is_file() or store.snapshot.is_symlink():
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_CORE_SNAPSHOT_REQUIRED")
    snapshot = strict_loads(store.snapshot.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_CORE_SNAPSHOT_INVALID")
    return {
        "status": "COMMITTED",
        "transaction_id": txid,
        "snapshot": snapshot,
        "snapshot_path": store.snapshot.relative_to(consumer).as_posix(),
        "snapshot_sha256": journal.get("snapshot_sha256"),
        "write_performed": False,
    }


def _cas_project(consumer: Path, journal_path: Path, journal: dict[str, Any], rel: str, payload: bytes) -> dict[str, Any]:
    item = journal["sidecars"][rel]
    target_sha = _sha_bytes(payload)
    if item.get("target_sha256") not in {None, target_sha}:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_TARGET_DIGEST_CHANGED:" + rel)
    item["target_sha256"] = target_sha
    journal = _save_journal(journal_path, journal)
    current = _read_file(consumer / rel, "UPGRADE_PROJECTION_SIDECAR_REQUIRED:" + rel)
    current_sha = _sha_bytes(current)
    if current_sha == target_sha:
        item["state"] = "TARGET"
        return _save_journal(journal_path, journal)
    if current_sha != item["source_sha256"]:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_FOREIGN_OR_DRIFTED:" + rel)
    _atomic_write(consumer / rel, payload)
    if _file_sha(consumer / rel) != target_sha:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_POSTCONDITION_FAILED:" + rel)
    item["state"] = "TARGET"
    return _save_journal(journal_path, journal)


def _verify_target(consumer: Path, target_root: Path) -> None:
    try:
        validate_fresh_session(consumer, target_root)
        load_operational_binding(consumer, target_root)
        load_gate_binding(consumer, target_root)
    except (ConsumerInstallationError, ConsumerOperationalError, ConsumerGateError) as exc:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_TARGET_PROOF_INVALID:" + str(exc).split(":", 1)[0]) from exc


def project_committed_upgrade(target_root: Path, consumer: Path, txid: str) -> dict[str, Any]:
    journal_path, _ = _projection_paths(consumer, txid)
    journal = _load_journal(journal_path, txid, consumer)
    if journal.get("status") == "COMMITTED":
        for rel in SIDECARS:
            target_sha = journal["sidecars"][rel].get("target_sha256")
            if not target_sha or _file_sha(consumer / rel) != target_sha:
                raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_COMMITTED_DRIFT:" + rel)
        _verify_target(consumer, target_root)
        return {"status": "COMMITTED", "transaction_id": txid, "write_performed": False, "projection_status": "COMMITTED"}

    core = _target_snapshot_result(target_root, consumer, txid)
    sem = journal["source_semantics"]
    try:
        target_install = build_record(
            target_root, consumer, core,
            consumer_repository=sem["consumer_repository"],
            consumer_base_sha=sem.get("consumer_base_sha"),
            consumer_base_tree=sem.get("consumer_base_tree"),
        )
        journal = _cas_project(consumer, journal_path, journal, RECORD_REL, _json_payload(target_install))
        target_ops = build_operational_binding(
            consumer, target_root,
            consumer_repository=sem["consumer_repository"],
            roadmap_path=sem["roadmap_path"],
        )
        journal = _cas_project(consumer, journal_path, journal, BINDING_REL, _json_payload(target_ops))
        target_gates = build_gate_binding(
            consumer, target_root,
            phases=sem["gate_phases"],
            required_phases=sem["gate_required_phases"],
        )
        journal = _cas_project(consumer, journal_path, journal, GATES_REL, _json_payload(target_gates))
        _verify_target(consumer, target_root)
        journal["status"] = "COMMITTED"; journal["last_error"] = None
        _save_journal(journal_path, journal)
        return {"status": "COMMITTED", "transaction_id": txid, "write_performed": True, "projection_status": "COMMITTED", "snapshot": core["snapshot"]}
    except Exception as exc:
        journal = _load_journal(journal_path, txid, consumer)
        journal["status"] = "RECOVERY_REQUIRED"; journal["last_error"] = f"{type(exc).__name__}:{exc}"
        _save_journal(journal_path, journal)
        raise


def _preflight_restore(consumer: Path, journal: dict[str, Any]) -> None:
    # No restoration starts until every sidecar is proven to be exact A or the
    # exact B payload previously recorded by this transaction.
    for rel in SIDECARS:
        item = journal["sidecars"][rel]
        current = _read_file(consumer / rel, "UPGRADE_PROJECTION_RECOVERY_SIDECAR_REQUIRED:" + rel)
        digest = _sha_bytes(current)
        allowed = {item["source_sha256"]}
        if item.get("target_sha256"):
            allowed.add(item["target_sha256"])
        if digest not in allowed:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_RECOVERY_FOREIGN_SIDECAR:" + rel)


def _restore_source_sidecars(consumer: Path, journal_path: Path, journal: dict[str, Any]) -> dict[str, Any]:
    _, preimage_root = _projection_paths(consumer, journal["transaction_id"])
    _preflight_restore(consumer, journal)
    for rel in reversed(SIDECARS):
        item = journal["sidecars"][rel]
        current_sha = _file_sha(consumer / rel)
        if current_sha == item["source_sha256"]:
            item["state"] = "SOURCE"; continue
        payload = _read_file(_preimage_path(preimage_root, rel), "UPGRADE_PROJECTION_PREIMAGE_REQUIRED:" + rel)
        if _sha_bytes(payload) != item["source_sha256"]:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_PREIMAGE_DRIFT:" + rel)
        _atomic_write(consumer / rel, payload)
        if _file_sha(consumer / rel) != item["source_sha256"]:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_SOURCE_RESTORE_FAILED:" + rel)
        item["state"] = "SOURCE"
        journal = _save_journal(journal_path, journal)
    return journal


def rollback_connected_upgrade(source_root: Path, target_root: Path, consumer: Path, txid: str) -> dict[str, Any]:
    source_root = Path(source_root).resolve(); target_root = Path(target_root).resolve(); consumer = Path(consumer).resolve()
    journal_path, _ = _projection_paths(consumer, txid)
    # Backward compatibility: transactions created before the connected proof
    # layer have no projection journal and retain canonical core recovery.
    if not journal_path.exists():
        return rollback_upgrade(source_root, target_root, consumer, txid)
    journal = _load_journal(journal_path, txid, consumer)
    try:
        journal = _restore_source_sidecars(consumer, journal_path, journal)
    except ConsumerUpgradeProjectionError as exc:
        journal["status"] = "RECOVERY_BLOCKED"; journal["last_error"] = str(exc)
        _save_journal(journal_path, journal)
        return {"status": "RECOVERY_BLOCKED", "transaction_id": txid, "blockers": [str(exc)], "write_performed": False}
    core = rollback_upgrade(source_root, target_root, consumer, txid)
    if core.get("status") != "ROLLED_BACK":
        journal["status"] = "RECOVERY_BLOCKED"; journal["last_error"] = "CORE:" + str(core.get("status"))
        _save_journal(journal_path, journal)
        return {"status": "RECOVERY_BLOCKED", "transaction_id": txid, "blockers": list(core.get("blockers") or []), "write_performed": True}
    try:
        _validate_source_bindings(consumer, source_root)
    except ConsumerUpgradeProjectionError as exc:
        journal["status"] = "RECOVERY_BLOCKED"; journal["last_error"] = str(exc); _save_journal(journal_path, journal)
        return {"status": "RECOVERY_BLOCKED", "transaction_id": txid, "blockers": [str(exc)], "write_performed": True}
    journal["status"] = "ROLLED_BACK"; journal["last_error"] = None
    _save_journal(journal_path, journal)
    return {"status": "ROLLED_BACK", "transaction_id": txid, "blockers": [], "write_performed": True}



def probe_connected_upgrade_committed(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a no-write success when the exact requested A→B plan is already durably at B.

    This does not derive mutation authority from the Installation Record.  It only
    recognizes an already-completed target state after the sealed upgrade plan,
    target installation, operational binding and native gate binding all validate.
    If the consumer is still exact A, return None so normal source-authority
    reconstruction and the mutating executor can proceed.
    """
    source_root = Path(source_root).resolve(); target_root = Path(target_root).resolve(); consumer = Path(consumer).resolve()
    validate_upgrade_compatibility(compatibility, target_root)
    validate_upgrade_plan(plan, compatibility, target_root)
    if compatibility.get("status") != "PASS" or plan.get("status") != "READY" or plan.get("findings") != []:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_READY_PLAN_REQUIRED")
    repository = detect_repository(consumer)
    if repository is None:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_REPOSITORY_NOT_VERIFIABLE")

    # Read the proof using the target schema first.  A source-bound record is a
    # normal pre-upgrade state; verify it under A and let the mutating path run.
    try:
        current = load_installation_record(consumer, target_root)
    except ConsumerInstallationError:
        try:
            validate_fresh_session(consumer, source_root, expected_repository=repository)
        except ConsumerInstallationError as exc:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_PROOF_INVALID:" + str(exc).split(":", 1)[0]) from exc
        return None

    current_revision = current.get("framework", {}).get("source_sha")
    if current_revision == plan.get("source_revision"):
        try:
            validate_fresh_session(consumer, source_root, expected_repository=repository)
        except ConsumerInstallationError as exc:
            raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_SOURCE_INVALID:" + str(exc).split(":", 1)[0]) from exc
        return None
    if current_revision != plan.get("target_revision"):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_UNEXPECTED_REVISION")

    try:
        verified = validate_fresh_session(consumer, target_root, expected_repository=repository)
        operations = load_operational_binding(consumer, target_root)
        gates = load_gate_binding(consumer, target_root)
    except (ConsumerInstallationError, ConsumerOperationalError, ConsumerGateError) as exc:
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_TARGET_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if current.get("framework", {}).get("manifest_sha256") != plan.get("target_manifest_sha256"):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_TARGET_MANIFEST_MISMATCH")
    if current.get("adoption", {}).get("plan_sha256") != plan.get("plan_sha256"):
        raise ConsumerUpgradeProjectionError("UPGRADE_PROJECTION_IDEMPOTENT_PLAN_MISMATCH")
    return {
        "status": "ALREADY_COMMITTED",
        "transaction_id": current.get("adoption", {}).get("transaction_id"),
        "projection_status": "COMMITTED",
        "write_performed": False,
        "framework_source_sha": verified.get("framework_source_sha"),
        "roadmap_path": operations.get("roadmap", {}).get("path"),
        "required_phases": gates.get("required_phases"),
    }

def apply_connected_upgrade(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], source_snapshot: dict[str, Any],
    *, fault_at: str | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve(); target_root = Path(target_root).resolve(); consumer = Path(consumer).resolve()
    txid, _ = prepare_projection(source_root, target_root, consumer, compatibility, plan, source_snapshot)
    core = apply_upgrade(source_root, target_root, consumer, compatibility, plan, source_snapshot, fault_at=fault_at)
    if core.get("status") not in {"COMMITTED", "ALREADY_COMMITTED"}:
        return core
    try:
        projected = project_committed_upgrade(target_root, consumer, txid)
    except Exception:
        rollback_connected_upgrade(source_root, target_root, consumer, txid)
        raise
    return {
        "status": "ALREADY_COMMITTED" if core.get("status") == "ALREADY_COMMITTED" and projected.get("write_performed") is False else "COMMITTED",
        "transaction_id": txid,
        "snapshot": projected.get("snapshot") or core.get("snapshot"),
        "write_performed": bool(core.get("write_performed")) or bool(projected.get("write_performed")),
        "projection_status": "COMMITTED",
    }


def recover_connected_upgrade(source_root: Path, target_root: Path, consumer: Path, txid: str) -> dict[str, Any]:
    source_root = Path(source_root).resolve(); target_root = Path(target_root).resolve(); consumer = Path(consumer).resolve()
    journal_path, _ = _projection_paths(consumer, txid)
    if not journal_path.exists():
        return recover_upgrade(source_root, target_root, consumer, txid)
    journal = _load_journal(journal_path, txid, consumer)
    core_store = UpgradeTransactionStore(target_root, consumer, txid, create=False)
    core_journal = core_store.load() if core_store.transactions.is_dir() else None
    if core_journal is not None and core_journal.get("status") == "COMMITTED":
        try:
            projected = project_committed_upgrade(target_root, consumer, txid)
            return {"status": "COMMITTED", "transaction_id": txid, "projection_status": projected["projection_status"], "write_performed": projected["write_performed"]}
        except Exception:
            return rollback_connected_upgrade(source_root, target_root, consumer, txid)
    core = recover_upgrade(source_root, target_root, consumer, txid)
    if core.get("status") == "ROLLED_BACK":
        journal = _restore_source_sidecars(consumer, journal_path, journal)
        journal["status"] = "ROLLED_BACK"; journal["last_error"] = None; _save_journal(journal_path, journal)
        return {"status": "ROLLED_BACK", "transaction_id": txid, "blockers": [], "write_performed": True}
    return core
