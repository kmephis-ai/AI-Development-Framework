"""Transactional apply/rollback for a sealed UPGRADE-001 plan.

UPGRADE-002 adds mutation authority only for framework-private paths proven by a
READY upgrade plan. Shared and pre-existing paths are verification-only. Every
destructive operation is preceded by durable exact-byte quarantine provenance;
recovery never deletes foreign/drifted content.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import copy
import hashlib
import json
import os
import stat
import tempfile

from .consumer_profile import PROFILE_REL, ConsumerProfileError, build_consumer_profile, load_consumer_profile
from .consumer_instructions import (
    ConsumerInstructionError, legacy_preexisting_router_transition_allowed,
    load_consumer_instruction_policy, validate_consumer_instruction_state,
)
from .consumer_installation import (
    REPOSITORY, ConsumerInstallationError, _snapshot_from_record, load_record as load_installation_record,
    validate_fresh_session as validate_installation_fresh_session,
)
from .github_auth import detect_repository
from .consumer_upgrade import (
    ConsumerUpgradeError, _canonical, _file_sha, _path_state, _root_sha, _safe_rel, _verify_revision,
    validate_upgrade_compatibility, validate_upgrade_plan,
)
from .contracts import validate
from .file_lock import exclusive_file_lock
from .managed_surface import SHA256, ManagedSurfaceError, _validate_snapshot, load_source_inventory
from .managed_surface_transaction import TransactionStore as AdoptionTransactionStore, _fsync_directory
from .strict_json import load as strict_load

TRANSACTION_SCHEMA = ".adwf/schemas/consumer-upgrade-transaction.schema.json"
RUNTIME_REL = PurePosixPath(".adwf-runtime/consumer-upgrade")
WRITE_ACTIONS = {"CREATE_PLANNED", "REPLACE_PLANNED", "REMOVE_PLANNED"}
VERIFY_ACTIONS = {"KEEP_EXACT", "PRESERVE_SHARED", "PRESERVE_PREEXISTING"}
ALLOWED_ACTIONS = WRITE_ACTIONS | VERIFY_ACTIONS
BUILTIN_MIGRATION_HANDLERS: frozenset[str] = frozenset()


class SimulatedUpgradeCrash(BaseException):
    """Test-only crash injection that deliberately bypasses normal exception recovery."""


def _bytes_sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = strict_load(path)
    except Exception as exc:
        raise ConsumerUpgradeError(code + ":" + type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise ConsumerUpgradeError(code + ":OBJECT_REQUIRED")
    return value


def _seal_digest(value: dict[str, Any]) -> str:
    return _bytes_sha(_canonical({key: item for key, item in value.items() if key != "journal_sha256"}))


def _snapshot_digest(value: dict[str, Any]) -> str:
    return _bytes_sha(_canonical(value))


def _profile_payload(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _safe_consumer(value: str | Path) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_APPLY_CONSUMER_ROOT_SYMLINK_FORBIDDEN")
    root = raw.resolve()
    if not root.is_dir():
        raise ConsumerUpgradeError("UPGRADE_APPLY_CONSUMER_ROOT_DIRECTORY_REQUIRED")
    return root


def _state(path: Path) -> tuple[str, str | None]:
    if path.is_symlink():
        return "SYMLINK", None
    if not path.exists():
        return "ABSENT", None
    if not path.is_file():
        return "NON_FILE", None
    return "FILE", _file_sha(path)


def _validate_journal(value: dict[str, Any], target_root: Path) -> None:
    schema = _object(target_root / TRANSACTION_SCHEMA, "UPGRADE_TRANSACTION_SCHEMA_INVALID")
    findings = validate(value, schema)
    if findings:
        raise ConsumerUpgradeError(
            "UPGRADE_TRANSACTION_SCHEMA_MISMATCH:"
            + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    if value.get("journal_sha256") != _seal_digest(value):
        raise ConsumerUpgradeError("UPGRADE_TRANSACTION_JOURNAL_DIGEST_MISMATCH")
    paths = [str(item.get("path") or "") for item in value.get("entries") or []]
    if len(paths) != len(set(paths)):
        raise ConsumerUpgradeError("UPGRADE_TRANSACTION_PATH_DUPLICATE")
    for rel in paths:
        _safe_rel(rel)
    _safe_rel(str(value["profile"]["path"]))
    for item in value.get("entries") or []:
        for key in ("staging_path", "quarantine_path"):
            if item.get(key) is not None:
                _safe_rel(str(item[key]))
    for key in ("staging_path", "quarantine_path"):
        if value["profile"].get(key) is not None:
            _safe_rel(str(value["profile"][key]))
    if value.get("snapshot_path") is not None:
        _safe_rel(str(value["snapshot_path"]))
    for rel in value.get("created_dirs") or []:
        _safe_rel(str(rel))


def _runtime_base(consumer: Path, *, create: bool) -> Path:
    current = consumer
    for part in RUNTIME_REL.parts:
        nxt = current / part
        rel = nxt.relative_to(consumer).as_posix()
        if nxt.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_RUNTIME_SYMLINK_FORBIDDEN:" + rel)
        if nxt.exists():
            if not nxt.is_dir():
                raise ConsumerUpgradeError("UPGRADE_RUNTIME_NON_DIRECTORY:" + rel)
        elif create:
            try:
                nxt.mkdir()
                _fsync_directory(nxt.parent)
            except FileExistsError:
                if nxt.is_symlink() or not nxt.is_dir():
                    raise ConsumerUpgradeError("UPGRADE_RUNTIME_DIRECTORY_RACE:" + rel)
        else:
            return consumer / RUNTIME_REL
        current = nxt
    return current


class UpgradeTransactionStore:
    def __init__(self, target_root: Path, consumer: Path, txid: str, *, create: bool) -> None:
        self.target_root = target_root
        self.consumer = consumer
        self.base = _runtime_base(consumer, create=create)
        self.transactions = self.base / "transactions"
        self.quarantines = self.base / "quarantine"
        self.snapshots = self.base / "snapshots"
        for directory in (self.transactions, self.quarantines, self.snapshots):
            if directory.is_symlink():
                raise ConsumerUpgradeError("UPGRADE_RUNTIME_SYMLINK_FORBIDDEN:" + directory.name)
            if directory.exists() and not directory.is_dir():
                raise ConsumerUpgradeError("UPGRADE_RUNTIME_NON_DIRECTORY:" + directory.name)
            if create and not directory.exists():
                directory.mkdir()
                _fsync_directory(directory.parent)
        self.path = self.transactions / f"{txid}.json"
        self.lock = self.transactions / f"{txid}.txn.lock"
        self.quarantine = self.quarantines / txid
        self.snapshot = self.snapshots / f"{txid}.snapshot.json"

    def quarantine_for(self, rel: str) -> Path:
        return self.quarantine / "files" / PurePosixPath(_safe_rel(rel))

    @property
    def profile_quarantine(self) -> Path:
        return self.quarantine / "profile" / "profile.json"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise ConsumerUpgradeError("UPGRADE_TRANSACTION_JOURNAL_OBJECT_INVALID")
        value = _object(self.path, "UPGRADE_TRANSACTION_JOURNAL_INVALID")
        _validate_journal(value, self.target_root)
        return value

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        value["journal_sha256"] = _seal_digest(value)
        _validate_journal(value, self.target_root)
        if self.transactions.is_symlink() or not self.transactions.is_dir():
            raise ConsumerUpgradeError("UPGRADE_RUNTIME_TRANSACTIONS_UNSAFE")
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.transactions)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.path); _fsync_directory(self.transactions)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return value


def _assert_no_unhandled_migrations(compatibility: dict[str, Any]) -> None:
    ids: list[str] = []
    for item in list(compatibility.get("contracts") or []) + [compatibility.get("project_pack") or {}] + list(compatibility.get("skills") or []):
        migration_id = item.get("migration_id")
        if migration_id is not None:
            ids.append(str(migration_id))
    unsupported = sorted(set(ids) - BUILTIN_MIGRATION_HANDLERS)
    if unsupported:
        raise ConsumerUpgradeError("UPGRADE_APPLY_UNSUPPORTED_MIGRATION:" + ",".join(unsupported))


def _trusted_source_snapshot(
    source_root: Path,
    target_root: Path,
    consumer: Path,
    snapshot: dict[str, Any],
    *,
    consumer_repository: str | None = None,
) -> None:
    try:
        _validate_snapshot(snapshot, source_root)
    except ManagedSurfaceError as exc:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_SNAPSHOT_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if snapshot.get("source_revision") is None or snapshot.get("source_manifest_sha256") is None:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_SNAPSHOT_IDENTITY_MISSING")
    if snapshot.get("consumer_root_sha256") != _root_sha(consumer):
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_SNAPSHOT_ROOT_MISMATCH")
    txid = snapshot.get("transaction_id")
    if not isinstance(txid, str) or len(txid) != 64:
        raise ConsumerUpgradeError("UPGRADE_APPLY_TRUSTED_SOURCE_SNAPSHOT_REQUIRED")

    # Prior UPGRADE-002 provenance wins if the transaction exists.
    upgrade_store = UpgradeTransactionStore(source_root, consumer, txid, create=False)
    if upgrade_store.transactions.is_dir() and upgrade_store.path.is_file():
        journal = upgrade_store.load()
        if journal is None or journal.get("status") != "COMMITTED":
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_UPGRADE_NOT_COMMITTED")
        if journal.get("target_revision") != snapshot.get("source_revision"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_UPGRADE_REVISION_MISMATCH")
        if journal.get("target_manifest_sha256") != snapshot.get("source_manifest_sha256"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_UPGRADE_MANIFEST_MISMATCH")
        if not upgrade_store.snapshot.is_file() or upgrade_store.snapshot.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_UPGRADE_SNAPSHOT_MISSING")
        stored = _object(upgrade_store.snapshot, "UPGRADE_APPLY_SOURCE_UPGRADE_SNAPSHOT_INVALID")
        if stored != snapshot or _bytes_sha(upgrade_store.snapshot.read_bytes()) != journal.get("snapshot_sha256"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_UPGRADE_SNAPSHOT_PROVENANCE_MISMATCH")
        return

    # Initial adoption provenance remains authoritative whenever its exact journal exists.
    # A committed provider-durable installation record is only a fresh-session fallback
    # when that ignored runtime journal is absent; it never overrides invalid runtime state.
    try:
        adoption_store = AdoptionTransactionStore(source_root, consumer, txid, create=False)
        adoption = adoption_store.load()
    except ManagedSurfaceError as exc:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_PROVENANCE_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if adoption is not None:
        if adoption.get("status") != "COMMITTED":
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_NOT_COMMITTED")
        if adoption.get("source_revision") != snapshot.get("source_revision"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_REVISION_MISMATCH")
        if adoption.get("source_manifest_sha256") != snapshot.get("source_manifest_sha256"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_MANIFEST_MISMATCH")
        if not adoption_store.snapshot.is_file() or adoption_store.snapshot.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_SNAPSHOT_MISSING")
        try:
            stored = strict_load(adoption_store.snapshot)
        except Exception as exc:
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_SNAPSHOT_INVALID:" + type(exc).__name__) from exc
        if stored != snapshot or _bytes_sha(adoption_store.snapshot.read_bytes()) != adoption.get("snapshot_sha256"):
            raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_ADOPTION_SNAPSHOT_PROVENANCE_MISMATCH")
        return

    if consumer_repository is None:
        repository = detect_repository(consumer)
        if repository is None:
            raise ConsumerUpgradeError("UPGRADE_APPLY_INSTALLATION_REPOSITORY_NOT_VERIFIABLE")
    else:
        repository = str(consumer_repository)
        if not REPOSITORY.fullmatch(repository):
            raise ConsumerUpgradeError("UPGRADE_APPLY_INSTALLATION_REPOSITORY_INVALID")
    try:
        validate_installation_fresh_session(
            consumer, source_root, expected_repository=repository,
        )
        installation = load_installation_record(consumer, source_root)
    except ConsumerInstallationError as exc:
        raise ConsumerUpgradeError(
            "UPGRADE_APPLY_SOURCE_INSTALLATION_PROVENANCE_INVALID:" + str(exc).split(":", 1)[0]
        ) from exc
    installed_snapshot = _snapshot_from_record(installation)
    # Durable installation identity carries the historical checkout locator.
    # A fresh session may rebind only that locator after full proof validation.
    installed_snapshot["consumer_root_sha256"] = _root_sha(consumer)
    if installed_snapshot != snapshot:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_INSTALLATION_SNAPSHOT_PROVENANCE_MISMATCH")


def _build_target_profile(consumer: Path, source_root: Path, target_root: Path, compatibility: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    try:
        current = load_consumer_profile(consumer, source_root, required=True)
    except ConsumerProfileError as exc:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_PROFILE_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if current is None or current.get("profile_sha256") != compatibility["profile"]["source_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_PROFILE_BINDING_MISMATCH")
    path = consumer / PROFILE_REL
    if _file_sha(path) != compatibility["profile"]["source_file_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_PROFILE_FILE_MISMATCH")
    try:
        target = build_consumer_profile(
            consumer, target_root,
            product_name=str(current["project"]["name"]),
            default_branch=str(current["project"]["default_branch"]),
            repository_visibility=str(current["project"]["repository_visibility"]),
        )
    except ConsumerProfileError as exc:
        raise ConsumerUpgradeError("UPGRADE_APPLY_TARGET_PROFILE_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if target.get("profile_sha256") != compatibility["profile"]["target_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_TARGET_PROFILE_BINDING_MISMATCH")
    return target, _profile_payload(target)



def _validate_static_apply_bindings(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
    *, consumer_repository: str | None = None,
) -> None:
    """Validate immutable upgrade identities without requiring consumer to still be at A."""
    _verify_revision(source_root, str(plan.get("source_revision") or ""))
    _verify_revision(target_root, str(plan.get("target_revision") or ""))
    validate_upgrade_compatibility(compatibility, target_root)
    validate_upgrade_plan(plan, compatibility, target_root)
    if compatibility.get("status") != "PASS" or plan.get("status") != "READY" or plan.get("findings") != []:
        raise ConsumerUpgradeError("UPGRADE_APPLY_REQUIRES_READY_PLAN")
    _assert_no_unhandled_migrations(compatibility)
    if plan.get("consumer_root_sha256") != _root_sha(consumer):
        raise ConsumerUpgradeError("UPGRADE_APPLY_CONSUMER_ROOT_MISMATCH")
    source_inventory = load_source_inventory(source_root)
    target_inventory = load_source_inventory(target_root)
    if source_inventory["manifest_sha256"] != plan["source_manifest_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_MANIFEST_MISMATCH")
    if target_inventory["manifest_sha256"] != plan["target_manifest_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_TARGET_MANIFEST_MISMATCH")
    _trusted_source_snapshot(
        source_root, target_root, consumer, snapshot,
        consumer_repository=consumer_repository,
    )
    if snapshot.get("source_revision") != plan["source_revision"] or snapshot.get("source_manifest_sha256") != plan["source_manifest_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_SOURCE_SNAPSHOT_BINDING_MISMATCH")

def _assert_static_journal_identity(
    journal: dict[str, Any], compatibility: dict[str, Any], plan: dict[str, Any],
    snapshot: dict[str, Any], consumer: Path, txid: str,
) -> None:
    expected = {
        "transaction_id": txid,
        "source_revision": plan["source_revision"],
        "target_revision": plan["target_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "target_manifest_sha256": plan["target_manifest_sha256"],
        "compatibility_sha256": compatibility["compatibility_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "source_snapshot_sha256": _snapshot_digest(snapshot),
        "consumer_root_sha256": _root_sha(consumer),
    }
    for key, value in expected.items():
        if journal.get(key) != value:
            raise ConsumerUpgradeError("UPGRADE_TRANSACTION_IDENTITY_MISMATCH:" + key)

def _source_snapshot_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["path"]): item for item in snapshot["entries"]}


def _preflight(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
    *, consumer_repository: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    _validate_static_apply_bindings(
        source_root, target_root, consumer, compatibility, plan, snapshot,
        consumer_repository=consumer_repository,
    )
    source_inventory = load_source_inventory(source_root)
    target_inventory = load_source_inventory(target_root)
    try:
        target_instruction_policy = load_consumer_instruction_policy(target_root, target_inventory)
        validate_consumer_instruction_state(consumer, target_instruction_policy)
    except ConsumerInstructionError as exc:
        raise ConsumerUpgradeError("UPGRADE_APPLY_TARGET_INSTRUCTION_POLICY_INVALID:" + str(exc)) from exc

    snap = _source_snapshot_map(snapshot)
    by_path = {str(item["path"]): item for item in plan["entries"]}
    if len(by_path) != len(plan["entries"]):
        raise ConsumerUpgradeError("UPGRADE_APPLY_PLAN_PATH_DUPLICATE")
    if set(by_path) != set(source_inventory["files"]) | set(target_inventory["files"]):
        raise ConsumerUpgradeError("UPGRADE_APPLY_PLAN_INVENTORY_SET_MISMATCH")

    restore_expected: set[tuple[str, str]] = set()
    remove_expected: set[tuple[str, str]] = set()
    for rel in sorted(by_path):
        item = by_path[rel]
        action = item.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ConsumerUpgradeError("UPGRADE_APPLY_ACTION_NOT_AUTHORIZED:" + rel)
        source_sha = item.get("source_sha256")
        target_sha = item.get("target_sha256")
        current_state, current_sha = _path_state(consumer, rel)
        source_snap = snap.get(rel)
        if source_sha is not None:
            if source_snap is None or source_snap.get("installed_sha256") != source_sha or source_snap.get("ownership") != item.get("source_ownership"):
                raise ConsumerUpgradeError("UPGRADE_APPLY_SNAPSHOT_PATH_BINDING_MISMATCH:" + rel)
        if action == "CREATE_PLANNED":
            if not (source_sha is None and target_sha and item.get("target_ownership") == "FRAMEWORK_PRIVATE" and current_state == "ABSENT"):
                raise ConsumerUpgradeError("UPGRADE_APPLY_CREATE_AUTHORITY_INVALID:" + rel)
            remove_expected.add((rel, str(target_sha)))
        elif action == "REPLACE_PLANNED":
            if not (source_sha and target_sha and source_sha != target_sha and source_snap and source_snap.get("managed_by_adwf") is True and item.get("source_ownership") == "FRAMEWORK_PRIVATE" and item.get("target_ownership") == "FRAMEWORK_PRIVATE" and current_state == "FILE" and current_sha == source_sha):
                raise ConsumerUpgradeError("UPGRADE_APPLY_REPLACE_AUTHORITY_INVALID:" + rel)
            restore_expected.add((rel, str(source_sha)))
        elif action == "REMOVE_PLANNED":
            if not (source_sha and target_sha is None and source_snap and source_snap.get("managed_by_adwf") is True and item.get("source_ownership") == "FRAMEWORK_PRIVATE" and current_state == "FILE" and current_sha == source_sha):
                raise ConsumerUpgradeError("UPGRADE_APPLY_REMOVE_AUTHORITY_INVALID:" + rel)
            restore_expected.add((rel, str(source_sha)))
        elif action == "KEEP_EXACT":
            if not (source_sha and target_sha == source_sha and current_state == "FILE" and current_sha == source_sha):
                raise ConsumerUpgradeError("UPGRADE_APPLY_KEEP_AUTHORITY_INVALID:" + rel)
        elif action == "PRESERVE_SHARED":
            if not (source_sha and target_sha == source_sha and item.get("source_ownership") == "SHARED_GUARDED" and item.get("target_ownership") == "SHARED_GUARDED" and current_state == "FILE" and current_sha == source_sha):
                raise ConsumerUpgradeError("UPGRADE_APPLY_SHARED_PRESERVE_INVALID:" + rel)
        elif action == "PRESERVE_PREEXISTING":
            preserved = (source_snap or {}).get("preserved_sha256") or source_sha
            exact_unchanged = target_sha == source_sha
            router_transition = legacy_preexisting_router_transition_allowed(
                target_instruction_policy, path=rel, source_ownership=str(item.get("source_ownership")),
                target_ownership=str(item.get("target_ownership")), target_present=target_sha is not None,
            )
            if not (
                source_sha and target_sha and source_snap and source_snap.get("managed_by_adwf") is False
                and current_state == "FILE" and current_sha == preserved
                and (exact_unchanged or router_transition)
            ):
                raise ConsumerUpgradeError("UPGRADE_APPLY_PREEXISTING_PRESERVE_INVALID:" + rel)

    rollback = plan["rollback_prerequisites"]
    restore_actual = {(str(x["path"]), str(x["sha256"])) for x in rollback.get("restore") or []}
    remove_actual = {(str(x["path"]), str(x["sha256"])) for x in rollback.get("remove_on_rollback") or []}
    if restore_actual != restore_expected or remove_actual != remove_expected:
        raise ConsumerUpgradeError("UPGRADE_APPLY_ROLLBACK_PREREQUISITES_MISMATCH")
    if rollback.get("source_revision_required") is not True or rollback.get("source_manifest_required") is not True:
        raise ConsumerUpgradeError("UPGRADE_APPLY_ROLLBACK_PREREQUISITES_WEAK")

    profile, profile_payload = _build_target_profile(consumer, source_root, target_root, compatibility)
    if plan.get("source_profile_sha256") != compatibility["profile"]["source_sha256"] or plan.get("target_profile_sha256") != profile["profile_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_APPLY_PROFILE_PLAN_BINDING_MISMATCH")
    return source_inventory, target_inventory, profile, profile_payload


def _target_snapshot(
    target_root: Path, consumer: Path, snapshot: dict[str, Any], plan: dict[str, Any], txid: str,
) -> dict[str, Any]:
    snap = _source_snapshot_map(snapshot)
    entries: list[dict[str, Any]] = []
    for item in plan["entries"]:
        if item.get("target_sha256") is None:
            continue
        rel = str(item["path"])
        action = item["action"]
        if action == "CREATE_PLANNED":
            managed = True
        else:
            managed = bool((snap.get(rel) or {}).get("managed_by_adwf"))
        entry = {
            "path": rel,
            "ownership": item["target_ownership"],
            "installed_sha256": item["target_sha256"],
            "managed_by_adwf": managed,
        }
        if not managed:
            source_snap = snap.get(rel) or {}
            preserved = source_snap.get("preserved_sha256") or source_snap.get("installed_sha256")
            if not isinstance(preserved, str) or not SHA256.fullmatch(preserved):
                raise ConsumerUpgradeError("UPGRADE_TARGET_SNAPSHOT_PRESERVED_DIGEST_REQUIRED:" + rel)
            entry["preserved_sha256"] = preserved
        entries.append(entry)
    value = {
        "$schema": ".adwf/schemas/managed-surface-snapshot.schema.json",
        "schema_version": 1,
        "role": "MANAGED_SURFACE_SNAPSHOT",
        "source_revision": plan["target_revision"],
        "source_manifest_sha256": plan["target_manifest_sha256"],
        "entries": entries,
        "transaction_id": txid,
        "plan_sha256": plan["plan_sha256"],
        "consumer_root_sha256": _root_sha(consumer),
    }
    try:
        _validate_snapshot(value, target_root)
    except ManagedSurfaceError as exc:
        raise ConsumerUpgradeError("UPGRADE_TARGET_SNAPSHOT_INVALID:" + str(exc).split(":", 1)[0]) from exc
    return value


def _transaction_identity(plan: dict[str, Any], compatibility: dict[str, Any], snapshot: dict[str, Any], consumer: Path) -> str:
    return _bytes_sha(_canonical({
        "kind": "CONSUMER_FRAMEWORK_UPGRADE",
        "source_revision": plan["source_revision"], "target_revision": plan["target_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"], "target_manifest_sha256": plan["target_manifest_sha256"],
        "compatibility_sha256": compatibility["compatibility_sha256"], "plan_sha256": plan["plan_sha256"],
        "source_snapshot_sha256": _snapshot_digest(snapshot), "consumer_root_sha256": _root_sha(consumer),
    }))


def _quarantine_rel(txid: str, rel: str) -> str:
    return (RUNTIME_REL / "quarantine" / txid / "files" / PurePosixPath(_safe_rel(rel))).as_posix()


def _profile_quarantine_rel(txid: str) -> str:
    return (RUNTIME_REL / "quarantine" / txid / "profile" / "profile.json").as_posix()


def _stage_rel(rel: str, txid: str) -> str:
    pure = PurePosixPath(_safe_rel(rel))
    name = f".{pure.name}.adwf-upgrade-{txid[:16]}.stage"
    return (pure.parent / name).as_posix() if pure.parent.as_posix() != "." else name


def _new_journal(
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any], consumer: Path,
    txid: str, profile_payload: bytes,
) -> dict[str, Any]:
    profile_changed = compatibility["profile"]["source_sha256"] != compatibility["profile"]["target_sha256"]
    source_snapshot = _source_snapshot_map(snapshot)
    value = {
        "$schema": TRANSACTION_SCHEMA, "schema_version": 1, "role": "CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION",
        "transaction_id": txid, "status": "PLANNED",
        "source_revision": plan["source_revision"], "target_revision": plan["target_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"], "target_manifest_sha256": plan["target_manifest_sha256"],
        "compatibility_sha256": compatibility["compatibility_sha256"], "plan_sha256": plan["plan_sha256"],
        "source_snapshot_sha256": _snapshot_digest(snapshot), "consumer_root_sha256": _root_sha(consumer),
        "attempts": 0,
        "entries": [{
            "path": item["path"], "planned_action": item["action"],
            "source_sha256": item["source_sha256"], "target_sha256": item["target_sha256"],
            "preserved_sha256": (
                (source_snapshot.get(item["path"]) or {}).get("preserved_sha256")
                or (source_snapshot.get(item["path"]) or {}).get("installed_sha256")
            ) if item["action"] == "PRESERVE_PREEXISTING" else None,
            "state": "PENDING", "staging_path": None,
            "quarantine_path": _quarantine_rel(txid, item["path"]) if item["action"] in {"REPLACE_PLANNED", "REMOVE_PLANNED"} else None,
        } for item in plan["entries"]],
        "profile": {
            "path": PROFILE_REL,
            "source_profile_sha256": compatibility["profile"]["source_sha256"],
            "target_profile_sha256": compatibility["profile"]["target_sha256"],
            "source_file_sha256": compatibility["profile"]["source_file_sha256"],
            "target_file_sha256": _bytes_sha(profile_payload),
            "state": "PENDING" if profile_changed else "UNCHANGED",
            "staging_path": None,
            "quarantine_path": _profile_quarantine_rel(txid) if profile_changed else None,
        },
        "created_dirs": [], "snapshot_path": None, "snapshot_sha256": None,
        "last_error": None, "journal_sha256": "0" * 64,
    }
    value["journal_sha256"] = _seal_digest(value)
    return value


def _assert_journal_identity(journal: dict[str, Any], compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any], consumer: Path, txid: str, profile_payload: bytes) -> None:
    expected = _new_journal(compatibility, plan, snapshot, consumer, txid, profile_payload)
    for key in (
        "transaction_id", "source_revision", "target_revision", "source_manifest_sha256", "target_manifest_sha256",
        "compatibility_sha256", "plan_sha256", "source_snapshot_sha256", "consumer_root_sha256",
    ):
        if journal.get(key) != expected[key]:
            raise ConsumerUpgradeError("UPGRADE_TRANSACTION_IDENTITY_MISMATCH:" + key)
    expected_by_path = {x["path"]: x for x in expected["entries"]}; actual_by_path = {x["path"]: x for x in journal["entries"]}
    if set(expected_by_path) != set(actual_by_path):
        raise ConsumerUpgradeError("UPGRADE_TRANSACTION_ENTRY_SET_MISMATCH")
    for rel, expected_item in expected_by_path.items():
        actual = actual_by_path[rel]
        for key in ("planned_action", "source_sha256", "target_sha256", "preserved_sha256", "quarantine_path"):
            if actual.get(key) != expected_item.get(key):
                raise ConsumerUpgradeError("UPGRADE_TRANSACTION_ENTRY_IMMUTABLE_MISMATCH:" + rel + ":" + key)
    for key in ("path", "source_profile_sha256", "target_profile_sha256", "source_file_sha256", "target_file_sha256", "quarantine_path"):
        if journal["profile"].get(key) != expected["profile"].get(key):
            raise ConsumerUpgradeError("UPGRADE_TRANSACTION_PROFILE_IMMUTABLE_MISMATCH:" + key)


def _assert_existing_parent_chain(consumer: Path, rel: str) -> None:
    current = consumer
    prefix: list[str] = []
    for part in PurePosixPath(_safe_rel(rel)).parts[:-1]:
        prefix.append(part); rel_dir = PurePosixPath(*prefix).as_posix(); current = current / part
        if current.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_TARGET_PARENT_SYMLINK_FORBIDDEN:" + rel_dir)
        if not current.exists() or not current.is_dir():
            raise ConsumerUpgradeError("UPGRADE_TARGET_PARENT_NON_DIRECTORY:" + rel_dir)


def _ensure_parent(consumer: Path, rel: str, journal: dict[str, Any], store: UpgradeTransactionStore) -> None:
    current = consumer
    prefix: list[str] = []
    for part in PurePosixPath(_safe_rel(rel)).parts[:-1]:
        prefix.append(part); rel_dir = PurePosixPath(*prefix).as_posix(); nxt = current / part
        if nxt.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_TARGET_PARENT_SYMLINK_FORBIDDEN:" + rel_dir)
        if nxt.exists():
            if not nxt.is_dir():
                raise ConsumerUpgradeError("UPGRADE_TARGET_PARENT_NON_DIRECTORY:" + rel_dir)
        else:
            try:
                nxt.mkdir(); _fsync_directory(nxt.parent)
                if rel_dir not in journal["created_dirs"]:
                    journal["created_dirs"].append(rel_dir); store.save(journal)
            except FileExistsError:
                if nxt.is_symlink() or not nxt.is_dir():
                    raise ConsumerUpgradeError("UPGRADE_TARGET_PARENT_RACE:" + rel_dir)
        current = nxt


def _write_exact_stage(source: Path, stage: Path, expected: str) -> None:
    if stage.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_STAGING_SYMLINK_FORBIDDEN")
    if stage.exists():
        if not stage.is_file() or _file_sha(stage) != expected:
            raise ConsumerUpgradeError("UPGRADE_STAGING_COLLISION")
        return
    payload = source.read_bytes()
    if _bytes_sha(payload) != expected:
        raise ConsumerUpgradeError("UPGRADE_SOURCE_FILE_DIGEST_CHANGED")
    mode = stat.S_IMODE(source.stat().st_mode) or 0o600
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.chmod(stage, mode); _fsync_directory(stage.parent)
    except BaseException:
        try: stage.unlink()
        except OSError: pass
        raise


def _write_payload_stage(stage: Path, payload: bytes, expected: str) -> None:
    if stage.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_STAGING_SYMLINK_FORBIDDEN")
    if stage.exists():
        if not stage.is_file() or _file_sha(stage) != expected:
            raise ConsumerUpgradeError("UPGRADE_STAGING_COLLISION")
        return
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        _fsync_directory(stage.parent)
    except BaseException:
        try: stage.unlink()
        except OSError: pass
        raise


def _ensure_internal_parent(consumer: Path, rel: str) -> None:
    current = consumer
    prefix: list[str] = []
    for part in PurePosixPath(_safe_rel(rel)).parts[:-1]:
        prefix.append(part); rel_dir = PurePosixPath(*prefix).as_posix(); nxt = current / part
        if nxt.is_symlink():
            raise ConsumerUpgradeError("UPGRADE_INTERNAL_PARENT_SYMLINK_FORBIDDEN:" + rel_dir)
        if nxt.exists():
            if not nxt.is_dir():
                raise ConsumerUpgradeError("UPGRADE_INTERNAL_PARENT_NON_DIRECTORY:" + rel_dir)
        else:
            try:
                nxt.mkdir(); _fsync_directory(nxt.parent)
            except FileExistsError:
                if nxt.is_symlink() or not nxt.is_dir():
                    raise ConsumerUpgradeError("UPGRADE_INTERNAL_PARENT_RACE:" + rel_dir)
        current = nxt


def _copy_quarantine(source: Path, quarantine: Path, expected: str) -> None:
    if quarantine.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_QUARANTINE_SYMLINK_FORBIDDEN")
    if quarantine.exists():
        if not quarantine.is_file() or _file_sha(quarantine) != expected:
            raise ConsumerUpgradeError("UPGRADE_QUARANTINE_COLLISION")
        return
    if not quarantine.parent.is_dir() or quarantine.parent.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_QUARANTINE_PARENT_UNSAFE")
    payload = source.read_bytes()
    if _bytes_sha(payload) != expected:
        raise ConsumerUpgradeError("UPGRADE_QUARANTINE_SOURCE_DRIFT")
    fd = os.open(quarantine, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(source.stat().st_mode) or 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        _fsync_directory(quarantine.parent)
    except BaseException:
        try: quarantine.unlink()
        except OSError: pass
        raise


def _link_stage(stage: Path, target: Path, expected: str) -> None:
    state, digest = _state(target)
    if state != "ABSENT":
        raise ConsumerUpgradeError("UPGRADE_TARGET_CHANGED_BEFORE_INSTALL")
    try:
        os.link(stage, target); _fsync_directory(target.parent)
    except FileExistsError as exc:
        raise ConsumerUpgradeError("UPGRADE_TARGET_CHANGED_BEFORE_INSTALL") from exc
    except OSError as exc:
        raise ConsumerUpgradeError("UPGRADE_ATOMIC_NO_REPLACE_INSTALL_FAILED:" + type(exc).__name__) from exc
    state, digest = _state(target)
    if state != "FILE" or digest != expected:
        raise ConsumerUpgradeError("UPGRADE_POST_INSTALL_VERIFY_FAILED")
    try:
        if not os.path.samefile(stage, target):
            raise ConsumerUpgradeError("UPGRADE_INSTALL_PROVENANCE_LINK_MISMATCH")
    except OSError as exc:
        raise ConsumerUpgradeError("UPGRADE_INSTALL_PROVENANCE_NOT_VERIFIABLE") from exc


def _fault(fault_at: str | None, checkpoint: str) -> None:
    if fault_at == checkpoint:
        raise SimulatedUpgradeCrash(checkpoint)


def _apply_entry(source_root: Path, target_root: Path, consumer: Path, journal: dict[str, Any], entry: dict[str, Any], store: UpgradeTransactionStore, fault_at: str | None) -> None:
    rel = entry["path"]; action = entry["planned_action"]; target = consumer / rel
    source_sha = entry["source_sha256"]; target_sha = entry["target_sha256"]
    if action in VERIFY_ACTIONS:
        expected = entry.get("preserved_sha256") if action == "PRESERVE_PREEXISTING" else source_sha
        _assert_existing_parent_chain(consumer, rel)
        state, digest = _path_state(consumer, rel)
        if state != "FILE" or digest != expected:
            raise ConsumerUpgradeError("UPGRADE_PRESERVED_PATH_DRIFT:" + rel)
        if action != "PRESERVE_PREEXISTING" and target_sha != source_sha:
            raise ConsumerUpgradeError("UPGRADE_PRESERVED_PATH_DRIFT:" + rel)
        entry["state"] = "PRESERVED"; store.save(journal); return
    if entry["state"] == "VERIFIED":
        expected_state, digest = _path_state(consumer, rel)
        if action == "REMOVE_PLANNED":
            if expected_state == "ABSENT": return
        elif expected_state == "FILE" and digest == target_sha: return
        raise ConsumerUpgradeError("UPGRADE_RESUME_TARGET_DRIFT:" + rel)

    if action in {"CREATE_PLANNED", "REPLACE_PLANNED"}:
        _ensure_parent(consumer, rel, journal, store)
        stage_rel = entry.get("staging_path") or _stage_rel(rel, journal["transaction_id"])
        stage = consumer / stage_rel
        _write_exact_stage(target_root / rel, stage, str(target_sha))
        entry["staging_path"] = stage_rel; entry["state"] = "STAGED"; store.save(journal)
        _fault(fault_at, "after_stage:" + rel)

    if action in {"REPLACE_PLANNED", "REMOVE_PLANNED"}:
        _assert_existing_parent_chain(consumer, rel)
        state, digest = _path_state(consumer, rel)
        if state != "FILE" or digest != source_sha:
            raise ConsumerUpgradeError("UPGRADE_SOURCE_TARGET_DRIFT_BEFORE_BACKUP:" + rel)
        quarantine_rel = str(entry["quarantine_path"]); _ensure_internal_parent(consumer, quarantine_rel)
        quarantine = consumer / quarantine_rel
        _copy_quarantine(target, quarantine, str(source_sha))
        entry["state"] = "BACKED_UP"; store.save(journal)
        _fault(fault_at, "after_backup:" + rel)
        state, digest = _path_state(consumer, rel)
        if state != "FILE" or digest != source_sha:
            raise ConsumerUpgradeError("UPGRADE_SOURCE_TARGET_DRIFT_BEFORE_REMOVE:" + rel)
        target.unlink(); _fsync_directory(target.parent)
        entry["state"] = "SOURCE_REMOVED"; store.save(journal)
        _fault(fault_at, "after_remove:" + rel)

    if action in {"CREATE_PLANNED", "REPLACE_PLANNED"}:
        stage = consumer / str(entry["staging_path"])
        if action == "CREATE_PLANNED":
            state, _ = _path_state(consumer, rel)
            if state != "ABSENT":
                raise ConsumerUpgradeError("UPGRADE_CREATE_COLLISION:" + rel)
        _link_stage(stage, target, str(target_sha))
        entry["state"] = "TARGET_INSTALLED"; store.save(journal)
        _fault(fault_at, "after_install:" + rel)
        if stage.exists():
            if stage.is_symlink() or not stage.is_file() or _file_sha(stage) != target_sha:
                raise ConsumerUpgradeError("UPGRADE_STAGING_DRIFT:" + rel)
            stage.unlink(); _fsync_directory(stage.parent)
        entry["staging_path"] = None
        state, digest = _path_state(consumer, rel)
        if state != "FILE" or digest != target_sha:
            raise ConsumerUpgradeError("UPGRADE_POSTCONDITION_FAILED:" + rel)
    else:
        state, _ = _path_state(consumer, rel)
        if state != "ABSENT":
            raise ConsumerUpgradeError("UPGRADE_REMOVE_POSTCONDITION_FAILED:" + rel)
    entry["state"] = "VERIFIED"; store.save(journal)


def _apply_profile(consumer: Path, journal: dict[str, Any], profile_payload: bytes, store: UpgradeTransactionStore, fault_at: str | None) -> None:
    item = journal["profile"]
    path = consumer / PROFILE_REL
    _assert_existing_parent_chain(consumer, PROFILE_REL)
    if item["state"] == "UNCHANGED":
        state, digest = _path_state(consumer, PROFILE_REL)
        if state != "FILE" or digest != item["source_file_sha256"]:
            raise ConsumerUpgradeError("UPGRADE_PROFILE_DRIFT_UNCHANGED")
        return
    if item["state"] == "VERIFIED":
        state, digest = _path_state(consumer, PROFILE_REL)
        if state == "FILE" and digest == item["target_file_sha256"]:
            return
        raise ConsumerUpgradeError("UPGRADE_PROFILE_RESUME_DRIFT")
    stage_rel = item.get("staging_path") or _stage_rel(PROFILE_REL, journal["transaction_id"])
    stage = consumer / stage_rel
    _write_payload_stage(stage, profile_payload, item["target_file_sha256"])
    item["staging_path"] = stage_rel; item["state"] = "STAGED"; store.save(journal)
    _fault(fault_at, "after_profile_stage")
    state, digest = _path_state(consumer, PROFILE_REL)
    if state != "FILE" or digest != item["source_file_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_PROFILE_DRIFT_BEFORE_BACKUP")
    quarantine_rel = str(item["quarantine_path"]); _ensure_internal_parent(consumer, quarantine_rel)
    quarantine = consumer / quarantine_rel
    _copy_quarantine(path, quarantine, item["source_file_sha256"])
    item["state"] = "BACKED_UP"; store.save(journal)
    _fault(fault_at, "after_profile_backup")
    state, digest = _path_state(consumer, PROFILE_REL)
    if state != "FILE" or digest != item["source_file_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_PROFILE_DRIFT_BEFORE_REMOVE")
    path.unlink(); _fsync_directory(path.parent)
    item["state"] = "SOURCE_REMOVED"; store.save(journal)
    _fault(fault_at, "after_profile_remove")
    _assert_existing_parent_chain(consumer, PROFILE_REL)
    _link_stage(stage, path, item["target_file_sha256"])
    item["state"] = "TARGET_INSTALLED"; store.save(journal)
    _fault(fault_at, "after_profile_install")
    if stage.exists():
        stage.unlink(); _fsync_directory(stage.parent)
    item["staging_path"] = None
    state, digest = _path_state(consumer, PROFILE_REL)
    if state != "FILE" or digest != item["target_file_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_PROFILE_POSTCONDITION_FAILED")
    item["state"] = "VERIFIED"; store.save(journal)

def _write_target_snapshot(store: UpgradeTransactionStore, value: dict[str, Any]) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if store.snapshots.is_symlink() or not store.snapshots.is_dir():
        raise ConsumerUpgradeError("UPGRADE_RUNTIME_SNAPSHOTS_UNSAFE")
    fd, temporary = tempfile.mkstemp(prefix=store.snapshot.name + ".", dir=store.snapshots)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, store.snapshot); _fsync_directory(store.snapshots)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return _bytes_sha(payload)


def _verify_committed(source_root: Path, target_root: Path, consumer: Path, store: UpgradeTransactionStore, journal: dict[str, Any]) -> dict[str, Any]:
    if journal.get("status") != "COMMITTED":
        raise ConsumerUpgradeError("UPGRADE_TRANSACTION_NOT_COMMITTED")
    _verify_revision(source_root, journal["source_revision"]); _verify_revision(target_root, journal["target_revision"])
    for entry in journal["entries"]:
        action = entry["planned_action"]; state, digest = _path_state(consumer, entry["path"])
        expected = entry.get("preserved_sha256") if action == "PRESERVE_PREEXISTING" else entry["target_sha256"]
        if action == "REMOVE_PLANNED":
            if state != "ABSENT": raise ConsumerUpgradeError("UPGRADE_COMMITTED_TARGET_DRIFT:" + entry["path"])
        elif state != "FILE" or digest != expected:
            raise ConsumerUpgradeError("UPGRADE_COMMITTED_TARGET_DRIFT:" + entry["path"])
    if _file_sha(consumer / PROFILE_REL) != journal["profile"]["target_file_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_COMMITTED_PROFILE_DRIFT")
    if not store.snapshot.is_file() or store.snapshot.is_symlink():
        raise ConsumerUpgradeError("UPGRADE_COMMITTED_SNAPSHOT_MISSING")
    if _bytes_sha(store.snapshot.read_bytes()) != journal.get("snapshot_sha256"):
        raise ConsumerUpgradeError("UPGRADE_COMMITTED_SNAPSHOT_DIGEST_MISMATCH")
    value = _object(store.snapshot, "UPGRADE_COMMITTED_SNAPSHOT_INVALID")
    try: _validate_snapshot(value, target_root)
    except ManagedSurfaceError as exc: raise ConsumerUpgradeError("UPGRADE_COMMITTED_SNAPSHOT_INVALID:" + str(exc).split(":", 1)[0]) from exc
    if value.get("transaction_id") != journal["transaction_id"]:
        raise ConsumerUpgradeError("UPGRADE_COMMITTED_SNAPSHOT_TRANSACTION_MISMATCH")
    return value


def _cleanup_created_dirs(consumer: Path, journal: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for rel in sorted(journal.get("created_dirs") or [], key=lambda x: (len(PurePosixPath(x).parts), x), reverse=True):
        path = consumer / rel
        if path.is_symlink(): blockers.append("UPGRADE_RECOVERY_CREATED_DIR_SYMLINK:" + rel); continue
        if not path.exists(): continue
        if not path.is_dir(): blockers.append("UPGRADE_RECOVERY_CREATED_DIR_TYPE_DRIFT:" + rel); continue
        try: path.rmdir()
        except OSError: pass
    return blockers


def _remove_exact(path: Path, expected: str, code: str, blockers: list[str]) -> bool:
    state, digest = _state(path)
    if state == "ABSENT": return True
    if state != "FILE" or digest != expected:
        blockers.append(code); return False
    try: path.unlink(); _fsync_directory(path.parent); return True
    except OSError as exc: blockers.append(code + ":" + type(exc).__name__); return False


def _remove_consumer_exact(consumer: Path, rel: str, expected: str, code: str, blockers: list[str]) -> bool:
    state, digest = _path_state(consumer, _safe_rel(rel))
    if state == "ABSENT": return True
    if state != "FILE" or digest != expected:
        blockers.append(code); return False
    _assert_existing_parent_chain(consumer, rel)
    path = consumer / rel
    try:
        path.unlink(); _fsync_directory(path.parent); return True
    except OSError as exc:
        blockers.append(code + ":" + type(exc).__name__); return False


def _restore_from_quarantine(
    consumer: Path, target_rel: str, quarantine_rel: str, source_sha: str, blockers: list[str], subject: str,
) -> bool:
    target_rel = _safe_rel(target_rel); quarantine_rel = _safe_rel(quarantine_rel)
    tstate, tdigest = _path_state(consumer, target_rel)
    qstate, qdigest = _path_state(consumer, quarantine_rel)
    if tstate == "FILE" and tdigest == source_sha:
        return True
    if tstate != "ABSENT":
        blockers.append("UPGRADE_RECOVERY_FOREIGN_TARGET:" + subject); return False
    if qstate != "FILE" or qdigest != source_sha:
        blockers.append("UPGRADE_RECOVERY_QUARANTINE_INVALID:" + subject); return False
    _assert_existing_parent_chain(consumer, target_rel)
    _assert_existing_parent_chain(consumer, quarantine_rel)
    target = consumer / target_rel; quarantine = consumer / quarantine_rel
    try:
        os.link(quarantine, target); _fsync_directory(target.parent)
    except FileExistsError:
        blockers.append("UPGRADE_RECOVERY_TARGET_RACE:" + subject); return False
    except OSError as exc:
        blockers.append("UPGRADE_RECOVERY_RESTORE_FAILED:" + subject + ":" + type(exc).__name__); return False
    state, digest = _path_state(consumer, target_rel)
    if state != "FILE" or digest != source_sha:
        blockers.append("UPGRADE_RECOVERY_RESTORE_VERIFY_FAILED:" + subject); return False
    return True

def _recover_locked(source_root: Path, target_root: Path, consumer: Path, store: UpgradeTransactionStore, journal: dict[str, Any], *, rollback_committed: bool) -> dict[str, Any]:
    if journal.get("status") == "COMMITTED" and not rollback_committed:
        snapshot = _verify_committed(source_root, target_root, consumer, store, journal)
        return {"status": "COMMITTED", "transaction_id": journal["transaction_id"], "snapshot": snapshot, "write_performed": False}
    _verify_revision(source_root, journal["source_revision"]); _verify_revision(target_root, journal["target_revision"])
    if load_source_inventory(source_root)["manifest_sha256"] != journal["source_manifest_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_RECOVERY_SOURCE_MANIFEST_MISMATCH")
    if load_source_inventory(target_root)["manifest_sha256"] != journal["target_manifest_sha256"]:
        raise ConsumerUpgradeError("UPGRADE_RECOVERY_TARGET_MANIFEST_MISMATCH")

    blockers: list[str] = []
    # Snapshot is transaction-owned if it matches the recorded target digest.
    if store.snapshot.exists():
        expected = journal.get("snapshot_sha256")
        if expected and store.snapshot.is_file() and not store.snapshot.is_symlink() and _bytes_sha(store.snapshot.read_bytes()) == expected:
            try: store.snapshot.unlink(); _fsync_directory(store.snapshots)
            except OSError as exc: blockers.append("UPGRADE_RECOVERY_SNAPSHOT_REMOVE_FAILED:" + type(exc).__name__)
        elif journal.get("status") == "COMMITTED":
            blockers.append("UPGRADE_RECOVERY_SNAPSHOT_DRIFT")

    # Profile first in reverse of commit order.  An unchanged profile has no
    # quarantine by design and is verification-only even during committed
    # rollback.  Never turn an unchanged exact profile into destructive
    # restore authority merely because the surrounding transaction committed.
    profile = journal["profile"]
    if profile["state"] == "UNCHANGED":
        pstate, pdigest = _path_state(consumer, PROFILE_REL)
        if (
            profile.get("source_file_sha256") != profile.get("target_file_sha256")
            or profile.get("quarantine_path") is not None
            or profile.get("staging_path") is not None
        ):
            blockers.append("UPGRADE_RECOVERY_UNCHANGED_PROFILE_PROVENANCE_INVALID")
        elif pstate != "FILE" or pdigest != profile.get("source_file_sha256"):
            blockers.append("UPGRADE_RECOVERY_FOREIGN_UNCHANGED_PROFILE")
        else:
            profile["state"] = "ROLLED_BACK"
    elif profile["state"] not in {"PENDING", "ROLLED_BACK"} or journal.get("status") == "COMMITTED":
        path = consumer / PROFILE_REL; q = consumer / str(profile["quarantine_path"])
        pstate, pdigest = _path_state(consumer, PROFILE_REL)
        if pdigest == profile["target_file_sha256"]:
            _remove_consumer_exact(consumer, PROFILE_REL, profile["target_file_sha256"], "UPGRADE_RECOVERY_FOREIGN_PROFILE", blockers)
        elif pstate not in {"ABSENT"} and pdigest != profile["source_file_sha256"]:
            blockers.append("UPGRADE_RECOVERY_FOREIGN_PROFILE")
        if not blockers or (blockers and blockers[-1] != "UPGRADE_RECOVERY_FOREIGN_PROFILE"):
            _restore_from_quarantine(consumer, PROFILE_REL, str(profile["quarantine_path"]), profile["source_file_sha256"], blockers, PROFILE_REL)
        stage_rel = profile.get("staging_path")
        if stage_rel:
            _remove_consumer_exact(consumer, stage_rel, profile["target_file_sha256"], "UPGRADE_RECOVERY_PROFILE_STAGE_DRIFT", blockers)
        if not any(PROFILE_REL in b or "PROFILE" in b for b in blockers):
            profile["state"] = "ROLLED_BACK"; profile["staging_path"] = None

    for entry in reversed(journal["entries"]):
        rel = entry["path"]; action = entry["planned_action"]; target = consumer / rel
        if action in VERIFY_ACTIONS:
            state, digest = _state(target)
            expected = entry.get("preserved_sha256") if action == "PRESERVE_PREEXISTING" else entry["source_sha256"]
            if state != "FILE" or digest != expected:
                blockers.append("UPGRADE_RECOVERY_PRESERVED_DRIFT:" + rel)
            else: entry["state"] = "ROLLED_BACK"
            continue
        stage_rel = entry.get("staging_path")
        stage = consumer / stage_rel if stage_rel else None
        tstate, tdigest = _path_state(consumer, rel)
        if action == "CREATE_PLANNED":
            if tstate == "FILE" and tdigest == entry["target_sha256"]:
                proven = entry["state"] in {"TARGET_INSTALLED", "VERIFIED"}
                if not proven and stage is not None and stage.exists() and not stage.is_symlink():
                    try: proven = os.path.samefile(stage, target)
                    except OSError: proven = False
                if proven: _remove_consumer_exact(consumer, rel, entry["target_sha256"], "UPGRADE_RECOVERY_CREATE_REMOVE_FAILED:" + rel, blockers)
                else: blockers.append("UPGRADE_RECOVERY_UNPROVEN_CREATED_TARGET:" + rel)
            elif tstate != "ABSENT": blockers.append("UPGRADE_RECOVERY_FOREIGN_TARGET:" + rel)
        else:
            # REPLACE/REMOVE: a committed or provenance-linked B target may be removed; foreign content blocks.
            if tstate == "FILE" and tdigest == entry["target_sha256"] and action == "REPLACE_PLANNED":
                proven = entry["state"] in {"TARGET_INSTALLED", "VERIFIED"}
                if not proven and stage is not None and stage.exists() and not stage.is_symlink():
                    try: proven = os.path.samefile(stage, target)
                    except OSError: proven = False
                if proven: _remove_consumer_exact(consumer, rel, entry["target_sha256"], "UPGRADE_RECOVERY_REPLACE_REMOVE_FAILED:" + rel, blockers)
                else: blockers.append("UPGRADE_RECOVERY_UNPROVEN_REPLACEMENT:" + rel)
            elif tstate == "FILE" and tdigest == entry["source_sha256"]:
                pass
            elif tstate != "ABSENT": blockers.append("UPGRADE_RECOVERY_FOREIGN_TARGET:" + rel)
            if not any(b.endswith(":" + rel) or (":" + rel + ":") in b for b in blockers):
                _restore_from_quarantine(consumer, rel, str(entry["quarantine_path"]), str(entry["source_sha256"]), blockers, rel)
        if stage is not None and stage.exists():
            _remove_consumer_exact(consumer, stage_rel, str(entry["target_sha256"]), "UPGRADE_RECOVERY_STAGE_DRIFT:" + rel, blockers)
        if not any(b.endswith(":" + rel) or (":" + rel + ":") in b for b in blockers):
            entry["state"] = "ROLLED_BACK"; entry["staging_path"] = None

    blockers.extend(_cleanup_created_dirs(consumer, journal))
    if blockers:
        journal["status"] = "RECOVERY_BLOCKED"; journal["last_error"] = ";".join(blockers)
    else:
        journal["status"] = "ROLLED_BACK"; journal["last_error"] = None; journal["snapshot_path"] = None; journal["snapshot_sha256"] = None; journal["created_dirs"] = []
        # Quarantine can be deleted only after exact A restoration is proven.
        if store.quarantine.exists() and not store.quarantine.is_symlink():
            import shutil
            shutil.rmtree(store.quarantine)
            _fsync_directory(store.quarantines)
    store.save(journal)
    return {"status": journal["status"], "transaction_id": journal["transaction_id"], "blockers": blockers, "write_performed": True}


def apply_upgrade(
    source_framework_root: str | Path, target_framework_root: str | Path, consumer_root: str | Path,
    compatibility: dict[str, Any], plan: dict[str, Any], source_snapshot: dict[str, Any], *,
    fault_at: str | None = None, consumer_repository: str | None = None,
) -> dict[str, Any]:
    """Apply a READY upgrade plan; no write happens before full exact-state preflight."""
    source_root = Path(source_framework_root).resolve(); target_root = Path(target_framework_root).resolve(); consumer = _safe_consumer(consumer_root)
    _validate_static_apply_bindings(
        source_root, target_root, consumer, compatibility, plan, source_snapshot,
        consumer_repository=consumer_repository,
    )
    txid = _transaction_identity(plan, compatibility, source_snapshot, consumer)
    probe = UpgradeTransactionStore(target_root, consumer, txid, create=False)
    if probe.transactions.is_dir() and probe.path.is_file():
        with exclusive_file_lock(probe.lock):
            committed = probe.load()
            if committed is not None and committed.get("status") == "COMMITTED":
                _assert_static_journal_identity(committed, compatibility, plan, source_snapshot, consumer, txid)
                snapshot = _verify_committed(source_root, target_root, consumer, probe, committed)
                return {"status": "ALREADY_COMMITTED", "transaction_id": txid, "snapshot": snapshot, "write_performed": False}
    _, _, target_profile, profile_payload = _preflight(
        source_root, target_root, consumer, compatibility, plan, source_snapshot,
        consumer_repository=consumer_repository,
    )
    target_snapshot = _target_snapshot(target_root, consumer, source_snapshot, plan, txid)
    store = UpgradeTransactionStore(target_root, consumer, txid, create=True)
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None:
            journal = _new_journal(compatibility, plan, source_snapshot, consumer, txid, profile_payload); store.save(journal)
        else:
            _assert_journal_identity(journal, compatibility, plan, source_snapshot, consumer, txid, profile_payload)
            if journal["status"] == "COMMITTED":
                snapshot = _verify_committed(source_root, target_root, consumer, store, journal)
                return {"status": "ALREADY_COMMITTED", "transaction_id": txid, "snapshot": snapshot, "write_performed": False}
            if journal["status"] == "RECOVERY_BLOCKED":
                raise ConsumerUpgradeError("UPGRADE_TRANSACTION_RECOVERY_BLOCKED")
            if journal["status"] != "ROLLED_BACK" and journal["status"] != "PLANNED":
                recovered = _recover_locked(source_root, target_root, consumer, store, journal, rollback_committed=False)
                if recovered["status"] != "ROLLED_BACK": return recovered
            if journal["status"] == "ROLLED_BACK":
                # Full preflight above proved exact A again; reset mutable transaction state.
                fresh = _new_journal(compatibility, plan, source_snapshot, consumer, txid, profile_payload)
                fresh["attempts"] = journal["attempts"]
                journal = fresh; store.save(journal)
        journal["status"] = "APPLYING"; journal["attempts"] += 1; journal["last_error"] = None; store.save(journal)
        try:
            by_path = {entry["path"]: entry for entry in journal["entries"]}
            for item in plan["entries"]:
                _apply_entry(source_root, target_root, consumer, journal, by_path[item["path"]], store, fault_at)
            _apply_profile(consumer, journal, profile_payload, store, fault_at)
            # Exact B postcondition before durable B snapshot publication.
            for entry in journal["entries"]:
                state, digest = _state(consumer / entry["path"])
                if entry["planned_action"] == "REMOVE_PLANNED":
                    if state != "ABSENT": raise ConsumerUpgradeError("UPGRADE_FINAL_B_POSTCONDITION:" + entry["path"])
                else:
                    expected = entry.get("preserved_sha256") if entry["planned_action"] == "PRESERVE_PREEXISTING" else entry["target_sha256"]
                    if state != "FILE" or digest != expected:
                        raise ConsumerUpgradeError("UPGRADE_FINAL_B_POSTCONDITION:" + entry["path"])
            try:
                loaded = load_consumer_profile(consumer, target_root, required=True)
            except ConsumerProfileError as exc:
                raise ConsumerUpgradeError("UPGRADE_FINAL_B_PROFILE_INVALID:" + str(exc).split(":", 1)[0]) from exc
            if loaded is None or loaded.get("profile_sha256") != target_profile["profile_sha256"]:
                raise ConsumerUpgradeError("UPGRADE_FINAL_B_PROFILE_MISMATCH")
            snapshot_sha = _write_target_snapshot(store, target_snapshot)
            journal["snapshot_path"] = store.snapshot.relative_to(consumer).as_posix(); journal["snapshot_sha256"] = snapshot_sha; store.save(journal)
            _fault(fault_at, "after_snapshot")
            journal["status"] = "COMMITTED"; journal["last_error"] = None; store.save(journal)
            verified = _verify_committed(source_root, target_root, consumer, store, journal)
            return {"status": "COMMITTED", "transaction_id": txid, "snapshot_path": journal["snapshot_path"], "snapshot_sha256": snapshot_sha, "snapshot": verified, "write_performed": True}
        except Exception as exc:
            journal["status"] = "RECOVERY_REQUIRED"; journal["last_error"] = f"{type(exc).__name__}:{exc}"; store.save(journal)
            return _recover_locked(source_root, target_root, consumer, store, journal, rollback_committed=False)


def recover_upgrade(source_framework_root: str | Path, target_framework_root: str | Path, consumer_root: str | Path, transaction_id: str) -> dict[str, Any]:
    source_root = Path(source_framework_root).resolve(); target_root = Path(target_framework_root).resolve(); consumer = _safe_consumer(consumer_root)
    store = UpgradeTransactionStore(target_root, consumer, transaction_id, create=False)
    if not store.transactions.is_dir(): raise ConsumerUpgradeError("UPGRADE_TRANSACTION_NOT_FOUND")
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None: raise ConsumerUpgradeError("UPGRADE_TRANSACTION_NOT_FOUND")
        if journal.get("consumer_root_sha256") != _root_sha(consumer): raise ConsumerUpgradeError("UPGRADE_TRANSACTION_CONSUMER_ROOT_MISMATCH")
        return _recover_locked(source_root, target_root, consumer, store, journal, rollback_committed=False)


def rollback_upgrade(source_framework_root: str | Path, target_framework_root: str | Path, consumer_root: str | Path, transaction_id: str) -> dict[str, Any]:
    source_root = Path(source_framework_root).resolve(); target_root = Path(target_framework_root).resolve(); consumer = _safe_consumer(consumer_root)
    store = UpgradeTransactionStore(target_root, consumer, transaction_id, create=False)
    if not store.transactions.is_dir(): raise ConsumerUpgradeError("UPGRADE_TRANSACTION_NOT_FOUND")
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None: raise ConsumerUpgradeError("UPGRADE_TRANSACTION_NOT_FOUND")
        if journal.get("consumer_root_sha256") != _root_sha(consumer): raise ConsumerUpgradeError("UPGRADE_TRANSACTION_CONSUMER_ROOT_MISMATCH")
        return _recover_locked(source_root, target_root, consumer, store, journal, rollback_committed=True)
