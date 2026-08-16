"""Transactional consumer adoption for Managed Surface Contract v1.

The read-only LIFECYCLE-001 plan remains authoritative for *what* may be
created.  This module adds an explicit apply/recovery transaction that can
create only paths proven ABSENT by that plan. Existing consumer files are never
overwritten. LIFECYCLE-003 adds guarded transactional detach with provenance-bound
quarantine/recovery without broadening authority over consumer/shared paths.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import hashlib
import json
import os
import stat
import subprocess
import tempfile

from .contracts import validate
from .file_lock import exclusive_file_lock
from .managed_surface import (
    SHA256,
    ManagedSurfaceError,
    _safe_rel,
    _target_state,
    _validate_plan,
    _validate_snapshot,
    load_source_inventory,
    ownership_for,
    snapshot_from_adoption_plan,
    validate_canonical_contract,
)
from .strict_json import load as strict_load


TRANSACTION_SCHEMA = ".adwf/schemas/managed-surface-transaction.schema.json"
RUNTIME_REL = PurePosixPath(".adwf-runtime/managed-surface")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _root_digest(root: Path) -> str:
    return _sha256_bytes(str(root).encode("utf-8"))


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability on POSIX; Windows has no portable dir fsync."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise ManagedSurfaceError("DIRECTORY_FSYNC_OPEN_FAILED:" + type(exc).__name__) from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise ManagedSurfaceError("DIRECTORY_FSYNC_FAILED:" + type(exc).__name__) from exc
    finally:
        os.close(fd)


def _consumer_root(value: str | Path) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise ManagedSurfaceError("CONSUMER_ROOT_SYMLINK_FORBIDDEN")
    root = raw.resolve()
    if not root.is_dir():
        raise ManagedSurfaceError("CONSUMER_ROOT_DIRECTORY_REQUIRED")
    return root


def _verify_source_revision(root: Path, expected: str) -> None:
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedSurfaceError("SOURCE_REVISION_NOT_VERIFIABLE:" + type(exc).__name__) from exc
    actual = process.stdout.strip() if process.returncode == 0 else ""
    if actual != expected:
        raise ManagedSurfaceError("SOURCE_REVISION_MISMATCH")
    clean = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root, capture_output=True, text=True, timeout=5, check=False,
    )
    if clean.returncode != 0:
        raise ManagedSurfaceError("SOURCE_WORKTREE_NOT_VERIFIABLE")
    if clean.stdout.strip():
        raise ManagedSurfaceError("SOURCE_WORKTREE_NOT_CLEAN")


def _validate_adoption_plan(plan: dict[str, Any], source_root: Path) -> dict[str, Any]:
    _validate_plan(plan, source_root)
    if plan.get("kind") != "ADOPTION" or plan.get("status") != "READY" or plan.get("blockers") != []:
        raise ManagedSurfaceError("APPLY_REQUIRES_READY_ADOPTION_PLAN")
    validate_canonical_contract(source_root)
    inventory = load_source_inventory(source_root)
    if plan.get("source_manifest_sha256") != inventory["manifest_sha256"]:
        raise ManagedSurfaceError("PLAN_SOURCE_MANIFEST_MISMATCH")
    by_path = {str(item.get("path") or ""): item for item in plan.get("entries") or []}
    if set(by_path) != set(inventory["files"]):
        raise ManagedSurfaceError("PLAN_INVENTORY_SET_MISMATCH")
    for rel in inventory["files"]:
        item = by_path[rel]
        if item.get("ownership") != ownership_for(rel, inventory):
            raise ManagedSurfaceError("PLAN_OWNERSHIP_MISMATCH:" + rel)
        if item.get("source_sha256") != inventory["sums"][rel]:
            raise ManagedSurfaceError("PLAN_SOURCE_DIGEST_MISMATCH:" + rel)
        state = item.get("target_state")
        action = item.get("action")
        current = item.get("target_sha256")
        if state == "ABSENT":
            if action != "CREATE_PLANNED" or current is not None:
                raise ManagedSurfaceError("PLAN_ABSENT_ACTION_INVALID:" + rel)
        elif state == "EXACT":
            if action != "KEEP_EXACT" or current != inventory["sums"][rel]:
                raise ManagedSurfaceError("PLAN_EXACT_ACTION_INVALID:" + rel)
        elif state == "COLLISION":
            if (
                item.get("ownership") != "SHARED_GUARDED"
                or action != "PRESERVE_SHARED"
                or not isinstance(current, str)
                or not SHA256.fullmatch(current)
                or current == inventory["sums"][rel]
            ):
                raise ManagedSurfaceError("PLAN_SHARED_PRESERVE_INVALID:" + rel)
        else:
            raise ManagedSurfaceError("READY_PLAN_CONTAINS_BLOCKED_STATE:" + rel)
    _verify_source_revision(source_root, str(plan["source_revision"]))
    return inventory


def _transaction_id(plan: dict[str, Any], consumer_root: Path) -> tuple[str, str]:
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    identity = {
        "kind": "ADOPTION",
        "source_revision": plan["source_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(consumer_root),
    }
    return _sha256_bytes(_canonical_bytes(identity)), plan_sha


def _transaction_journal_digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "journal_sha256"}
    return _sha256_bytes(_canonical_bytes(payload))


def _seal_transaction(value: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(value))
    payload["journal_sha256"] = _transaction_journal_digest(payload)
    return payload


def _validate_transaction(value: dict[str, Any], source_root: Path) -> None:
    schema_path = source_root / TRANSACTION_SCHEMA
    try:
        schema = strict_load(schema_path)
    except Exception as exc:
        raise ManagedSurfaceError("TRANSACTION_SCHEMA_INVALID:" + type(exc).__name__) from exc
    findings = validate(value, schema)
    if findings:
        raise ManagedSurfaceError(
            "TRANSACTION_SCHEMA_MISMATCH:" + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    if value.get("journal_sha256") != _transaction_journal_digest(value):
        raise ManagedSurfaceError("TRANSACTION_JOURNAL_DIGEST_MISMATCH")
    paths = [str(item.get("path") or "") for item in value.get("entries") or []]
    if len(paths) != len(set(paths)):
        raise ManagedSurfaceError("TRANSACTION_PATH_DUPLICATE")
    for rel in paths:
        _safe_rel(rel)
    for rel in value.get("created_dirs") or []:
        _safe_rel(str(rel))
    if value.get("snapshot_path") is not None:
        _safe_rel(str(value["snapshot_path"]))


def _runtime_base(consumer_root: Path, *, create: bool) -> Path:
    current = consumer_root
    for part in RUNTIME_REL.parts:
        nxt = current / part
        if nxt.is_symlink():
            raise ManagedSurfaceError("RUNTIME_SYMLINK_FORBIDDEN:" + nxt.relative_to(consumer_root).as_posix())
        if nxt.exists():
            if not nxt.is_dir():
                raise ManagedSurfaceError("RUNTIME_NON_DIRECTORY:" + nxt.relative_to(consumer_root).as_posix())
        elif create:
            try:
                nxt.mkdir()
            except FileExistsError:
                if nxt.is_symlink() or not nxt.is_dir():
                    raise ManagedSurfaceError("RUNTIME_DIRECTORY_RACE:" + nxt.relative_to(consumer_root).as_posix())
        else:
            return consumer_root / RUNTIME_REL
        current = nxt
    return current


class TransactionStore:
    def __init__(self, source_root: Path, consumer_root: Path, transaction_id: str, *, create: bool) -> None:
        self.source_root = source_root
        self.consumer_root = consumer_root
        self.base = _runtime_base(consumer_root, create=create)
        self.transactions = self.base / "transactions"
        self.snapshots = self.base / "snapshots"
        if create:
            for directory in (self.transactions, self.snapshots):
                if directory.is_symlink():
                    raise ManagedSurfaceError("RUNTIME_SYMLINK_FORBIDDEN:" + directory.name)
                if directory.exists() and not directory.is_dir():
                    raise ManagedSurfaceError("RUNTIME_NON_DIRECTORY:" + directory.name)
                directory.mkdir(exist_ok=True)
        self.path = self.transactions / f"{transaction_id}.json"
        self.lock = self.transactions / f"{transaction_id}.txn.lock"
        self.snapshot = self.snapshots / f"{transaction_id}.snapshot.json"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise ManagedSurfaceError("TRANSACTION_JOURNAL_OBJECT_INVALID")
        try:
            value = strict_load(self.path)
        except Exception as exc:
            raise ManagedSurfaceError("TRANSACTION_JOURNAL_INVALID:" + type(exc).__name__) from exc
        if not isinstance(value, dict):
            raise ManagedSurfaceError("TRANSACTION_JOURNAL_OBJECT_REQUIRED")
        _validate_transaction(value, self.source_root)
        return value

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        value["journal_sha256"] = _transaction_journal_digest(value)
        _validate_transaction(value, self.source_root)
        self.transactions.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.transactions)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.transactions)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return value


def _new_transaction(plan: dict[str, Any], consumer_root: Path, transaction_id: str, plan_sha: str) -> dict[str, Any]:
    return {
        "$schema": TRANSACTION_SCHEMA,
        "schema_version": 1,
        "role": "MANAGED_SURFACE_ADOPTION_TRANSACTION",
        "transaction_id": transaction_id,
        "status": "PLANNED",
        "source_revision": plan["source_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(consumer_root),
        "attempts": 0,
        "entries": [
            {
                "path": item["path"],
                "source_sha256": item["source_sha256"],
                "planned_action": item["action"],
                "preserved_sha256": item.get("target_sha256") if item["action"] == "PRESERVE_SHARED" else None,
                "state": "PENDING",
                "staging_path": None,
            }
            for item in plan["entries"]
        ],
        "created_dirs": [],
        "snapshot_path": None,
        "snapshot_sha256": None,
        "last_error": None,
        "journal_sha256": "0" * 64,
    }


def _assert_journal_identity(journal: dict[str, Any], plan: dict[str, Any], consumer_root: Path, txid: str, plan_sha: str) -> None:
    expected = {
        "transaction_id": txid,
        "source_revision": plan["source_revision"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(consumer_root),
    }
    for key, value in expected.items():
        if journal.get(key) != value:
            raise ManagedSurfaceError("TRANSACTION_IDENTITY_MISMATCH:" + key)
    by_path = {item["path"]: item for item in journal["entries"]}
    if set(by_path) != {item["path"] for item in plan["entries"]}:
        raise ManagedSurfaceError("TRANSACTION_ENTRY_SET_MISMATCH")
    for item in plan["entries"]:
        stored = by_path[item["path"]]
        expected_preserved = item.get("target_sha256") if item["action"] == "PRESERVE_SHARED" else None
        if (
            stored["source_sha256"] != item["source_sha256"]
            or stored["planned_action"] != item["action"]
            or stored.get("preserved_sha256") != expected_preserved
        ):
            raise ManagedSurfaceError("TRANSACTION_ENTRY_IMMUTABLE_MISMATCH:" + item["path"])


def _safe_parent_dirs(consumer_root: Path, rel: str, journal: dict[str, Any], store: TransactionStore) -> Path:
    current = consumer_root
    parts = PurePosixPath(_safe_rel(rel)).parts[:-1]
    prefix: list[str] = []
    for part in parts:
        prefix.append(part)
        rel_dir = PurePosixPath(*prefix).as_posix()
        nxt = current / part
        if nxt.is_symlink():
            raise ManagedSurfaceError("TARGET_PARENT_SYMLINK_FORBIDDEN:" + rel_dir)
        if nxt.exists():
            if not nxt.is_dir():
                raise ManagedSurfaceError("TARGET_PARENT_NON_DIRECTORY:" + rel_dir)
        else:
            try:
                nxt.mkdir()
                _fsync_directory(nxt.parent)
                if rel_dir not in journal["created_dirs"]:
                    journal["created_dirs"].append(rel_dir)
                    store.save(journal)
            except FileExistsError:
                if nxt.is_symlink() or not nxt.is_dir():
                    raise ManagedSurfaceError("TARGET_PARENT_RACE:" + rel_dir)
        current = nxt
    return current


def _stage_rel(rel: str, transaction_id: str) -> str:
    pure = PurePosixPath(rel)
    name = f".{pure.name}.adwf-{transaction_id[:16]}.stage"
    return (pure.parent / name).as_posix() if pure.parent.as_posix() != "." else name


def _source_mode(path: Path) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return 0o644


def _prepare_stage(source: Path, stage: Path, expected_sha: str) -> None:
    if stage.is_symlink():
        raise ManagedSurfaceError("STAGING_SYMLINK_FORBIDDEN")
    if stage.exists():
        if not stage.is_file() or _digest(stage) != expected_sha:
            raise ManagedSurfaceError("STAGING_COLLISION")
        return
    data = source.read_bytes()
    if _sha256_bytes(data) != expected_sha:
        raise ManagedSurfaceError("SOURCE_FILE_DIGEST_CHANGED_DURING_APPLY")
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _source_mode(source) or 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(stage, _source_mode(source))
    except BaseException:
        try:
            stage.unlink()
        except OSError:
            pass
        raise


def _link_stage_no_replace(stage: Path, target: Path, expected_sha: str) -> None:
    state, _ = _target_state(target, expected_sha)
    if state == "ABSENT":
        try:
            os.link(stage, target)
            _fsync_directory(target.parent)
        except FileExistsError as exc:
            raise ManagedSurfaceError("TARGET_CHANGED_BEFORE_CREATE") from exc
        except OSError as exc:
            raise ManagedSurfaceError("ATOMIC_NO_REPLACE_CREATE_FAILED:" + type(exc).__name__) from exc
        return
    if state == "EXACT":
        try:
            if os.path.samefile(stage, target):
                return
        except OSError:
            pass
        raise ManagedSurfaceError("TARGET_EXACT_WITHOUT_TRANSACTION_PROVENANCE")
    if state == "SYMLINK":
        raise ManagedSurfaceError("TARGET_SYMLINK_FORBIDDEN")
    if state == "NON_FILE":
        raise ManagedSurfaceError("TARGET_NON_FILE_COLLISION")
    raise ManagedSurfaceError("TARGET_CONTENT_COLLISION")


def _verify_committed(store: TransactionStore, journal: dict[str, Any], consumer_root: Path) -> dict[str, Any]:
    if journal.get("status") != "COMMITTED":
        raise ManagedSurfaceError("TRANSACTION_NOT_COMMITTED")
    if not store.snapshot.is_file() or store.snapshot.is_symlink():
        raise ManagedSurfaceError("COMMITTED_SNAPSHOT_MISSING")
    if _digest(store.snapshot) != journal.get("snapshot_sha256"):
        raise ManagedSurfaceError("COMMITTED_SNAPSHOT_DIGEST_MISMATCH")
    try:
        snapshot = strict_load(store.snapshot)
    except Exception as exc:
        raise ManagedSurfaceError("COMMITTED_SNAPSHOT_INVALID:" + type(exc).__name__) from exc
    if not isinstance(snapshot, dict):
        raise ManagedSurfaceError("COMMITTED_SNAPSHOT_OBJECT_REQUIRED")
    _validate_snapshot(snapshot, store.source_root)
    if snapshot.get("transaction_id") != journal["transaction_id"]:
        raise ManagedSurfaceError("COMMITTED_SNAPSHOT_TRANSACTION_MISMATCH")
    for entry in journal["entries"]:
        expected = entry.get("preserved_sha256") if entry["planned_action"] == "PRESERVE_SHARED" else entry["source_sha256"]
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise ManagedSurfaceError("COMMITTED_TARGET_EXPECTATION_INVALID:" + entry["path"])
        state, _ = _target_state(consumer_root / entry["path"], expected)
        if state != "EXACT":
            raise ManagedSurfaceError("COMMITTED_TARGET_DRIFT:" + entry["path"])
    return snapshot


def _write_snapshot(store: TransactionStore, snapshot: dict[str, Any]) -> str:
    store.snapshots.mkdir(exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=store.snapshot.name + ".", dir=store.snapshots)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, store.snapshot)
        _fsync_directory(store.snapshots)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return _sha256_bytes(payload)


def _remove_created_dirs(consumer_root: Path, journal: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for rel in sorted(journal.get("created_dirs") or [], key=lambda x: (len(PurePosixPath(x).parts), x), reverse=True):
        path = consumer_root / rel
        if path.is_symlink():
            blockers.append("RECOVERY_CREATED_DIR_SYMLINK:" + rel)
            continue
        if not path.exists():
            continue
        if not path.is_dir():
            blockers.append("RECOVERY_CREATED_DIR_TYPE_DRIFT:" + rel)
            continue
        try:
            path.rmdir()
        except OSError:
            # Non-empty directories may now contain consumer files. Preserve them.
            pass
    return blockers


def recover_adoption(
    framework_root: str | Path,
    consumer_root: str | Path,
    transaction_id: str,
) -> dict[str, Any]:
    source_root = Path(framework_root).resolve()
    target_root = _consumer_root(consumer_root)
    store = TransactionStore(source_root, target_root, transaction_id, create=False)
    if not store.transactions.is_dir():
        raise ManagedSurfaceError("TRANSACTION_NOT_FOUND")
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None:
            raise ManagedSurfaceError("TRANSACTION_NOT_FOUND")
        if journal.get("consumer_root_sha256") != _root_digest(target_root):
            raise ManagedSurfaceError("TRANSACTION_CONSUMER_ROOT_MISMATCH")
        if journal.get("status") == "COMMITTED":
            return {"status": "COMMITTED", "transaction": journal, "snapshot": _verify_committed(store, journal, target_root)}
        blockers: list[str] = []
        for entry in reversed(journal["entries"]):
            entry_blockers_before = len(blockers)
            if entry["planned_action"] != "CREATE_PLANNED":
                if entry["planned_action"] == "PRESERVE_SHARED":
                    expected = entry.get("preserved_sha256")
                    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
                        blockers.append("RECOVERY_PRESERVED_SHARED_EXPECTATION_INVALID:" + entry["path"])
                    else:
                        state, _ = _target_state(target_root / entry["path"], expected)
                        if state != "EXACT":
                            blockers.append("RECOVERY_PRESERVED_SHARED_DRIFT:" + entry["path"])
                if len(blockers) == entry_blockers_before:
                    entry["state"] = "PRESERVED"
                    entry["staging_path"] = None
                continue
            rel = entry["path"]
            target = target_root / rel
            stage = target_root / entry["staging_path"] if entry.get("staging_path") else None
            provenance_link = False
            if stage is not None and stage.exists() and not stage.is_symlink() and target.exists() and not target.is_symlink():
                try:
                    provenance_link = os.path.samefile(stage, target)
                except OSError:
                    provenance_link = False
            should_own_target = entry["state"] in {"CREATED", "VERIFIED"} or provenance_link
            if should_own_target:
                state, _ = _target_state(target, entry["source_sha256"])
                if state == "EXACT":
                    try:
                        target.unlink()
                        _fsync_directory(target.parent)
                    except OSError as exc:
                        blockers.append("RECOVERY_REMOVE_FAILED:" + rel + ":" + type(exc).__name__)
                elif state != "ABSENT":
                    blockers.append("RECOVERY_TARGET_DRIFT:" + rel)
            elif entry["state"] == "STAGING":
                state, _ = _target_state(target, entry["source_sha256"])
                # Foreign concurrent targets were never owned by this transaction.
                # The only ambiguous crash window is exact target + lost stage.
                if state == "EXACT" and not provenance_link and (stage is None or not stage.exists()):
                    blockers.append("RECOVERY_UNPROVEN_EXACT_TARGET:" + rel)
            if stage is not None and stage.exists():
                if stage.is_symlink() or not stage.is_file() or _digest(stage) != entry["source_sha256"]:
                    blockers.append("RECOVERY_STAGING_DRIFT:" + rel)
                else:
                    try:
                        stage.unlink()
                        _fsync_directory(stage.parent)
                    except OSError as exc:
                        blockers.append("RECOVERY_STAGING_REMOVE_FAILED:" + rel + ":" + type(exc).__name__)
            if len(blockers) == entry_blockers_before:
                entry["staging_path"] = None
                entry["state"] = "ROLLED_BACK"
        blockers.extend(_remove_created_dirs(target_root, journal))
        if blockers:
            journal["status"] = "RECOVERY_BLOCKED"
            journal["last_error"] = ";".join(blockers)
        else:
            journal["status"] = "ROLLED_BACK"
            journal["last_error"] = None
            journal["created_dirs"] = []
        store.save(journal)
        return {"status": journal["status"], "transaction": journal, "blockers": blockers, "write_performed": True}


def apply_adoption(
    framework_root: str | Path,
    consumer_root: str | Path,
    plan: dict[str, Any],
    *,
    fault_after_writes: int | None = None,
) -> dict[str, Any]:
    """Explicitly apply a READY adoption plan without overwriting any existing file.

    `fault_after_writes` is a deterministic fault-injection hook used only by
    tests/recovery certification. Production callers should leave it as None.
    """
    source_root = Path(framework_root).resolve()
    target_root = _consumer_root(consumer_root)
    if fault_after_writes is not None and (not isinstance(fault_after_writes, int) or fault_after_writes < 1):
        raise ManagedSurfaceError("FAULT_INJECTION_VALUE_INVALID")
    inventory = _validate_adoption_plan(plan, source_root)
    txid, plan_sha = _transaction_id(plan, target_root)
    store = TransactionStore(source_root, target_root, txid, create=True)
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None:
            journal = _new_transaction(plan, target_root, txid, plan_sha)
            store.save(journal)
        else:
            _assert_journal_identity(journal, plan, target_root, txid, plan_sha)
            if journal["status"] == "COMMITTED":
                snapshot = _verify_committed(store, journal, target_root)
                return {
                    "status": "ALREADY_COMMITTED",
                    "transaction_id": txid,
                    "snapshot": snapshot,
                    "created_files": sum(1 for x in journal["entries"] if x["planned_action"] == "CREATE_PLANNED"),
                    "write_performed": False,
                }
            if journal["status"] == "RECOVERY_BLOCKED":
                raise ManagedSurfaceError("TRANSACTION_RECOVERY_BLOCKED")
            if journal["status"] == "ROLLED_BACK":
                for entry in journal["entries"]:
                    entry["state"] = "PENDING"
                    entry["staging_path"] = None
                journal["created_dirs"] = []
                journal["snapshot_path"] = None
                journal["snapshot_sha256"] = None
                journal["last_error"] = None
                journal["status"] = "PLANNED"
                store.save(journal)
        journal["status"] = "APPLYING"
        journal["attempts"] = int(journal["attempts"]) + 1
        journal["last_error"] = None
        store.save(journal)
        writes = 0
        try:
            by_path = {entry["path"]: entry for entry in journal["entries"]}
            for planned in plan["entries"]:
                rel = planned["path"]
                entry = by_path[rel]
                expected = entry["source_sha256"]
                target = target_root / rel
                source = source_root / rel
                if entry["planned_action"] in {"KEEP_EXACT", "PRESERVE_SHARED"}:
                    preserve_expected = expected if entry["planned_action"] == "KEEP_EXACT" else entry.get("preserved_sha256")
                    if not isinstance(preserve_expected, str) or not SHA256.fullmatch(preserve_expected):
                        raise ManagedSurfaceError("PREEXISTING_TARGET_EXPECTATION_INVALID:" + rel)
                    state, _ = _target_state(target, preserve_expected)
                    if state != "EXACT":
                        code = "PREEXISTING_SHARED_CHANGED:" if entry["planned_action"] == "PRESERVE_SHARED" else "PREEXISTING_TARGET_CHANGED:"
                        raise ManagedSurfaceError(code + rel)
                    entry["state"] = "PRESERVED"
                    entry["staging_path"] = None
                    store.save(journal)
                    continue
                if entry["state"] == "VERIFIED":
                    state, _ = _target_state(target, expected)
                    if state == "EXACT":
                        continue
                    raise ManagedSurfaceError("RESUME_TARGET_DRIFT:" + rel)
                _safe_parent_dirs(target_root, rel, journal, store)
                stage_rel = entry.get("staging_path") or _stage_rel(rel, txid)
                entry["state"] = "STAGING"
                entry["staging_path"] = stage_rel
                store.save(journal)
                stage = target_root / stage_rel
                _prepare_stage(source, stage, expected)
                _link_stage_no_replace(stage, target, expected)
                entry["state"] = "CREATED"
                store.save(journal)
                if stage.exists():
                    stage.unlink()
                    _fsync_directory(stage.parent)
                entry["staging_path"] = None
                state, _ = _target_state(target, expected)
                if state != "EXACT":
                    raise ManagedSurfaceError("POST_CREATE_VERIFY_FAILED:" + rel)
                entry["state"] = "VERIFIED"
                store.save(journal)
                writes += 1
                if fault_after_writes is not None and writes >= fault_after_writes:
                    raise ManagedSurfaceError("INJECTED_ADOPTION_FAILURE")

            for entry in journal["entries"]:
                expected_post = entry.get("preserved_sha256") if entry["planned_action"] == "PRESERVE_SHARED" else entry["source_sha256"]
                if not isinstance(expected_post, str) or not SHA256.fullmatch(expected_post):
                    raise ManagedSurfaceError("ADOPTION_POSTCONDITION_EXPECTATION_INVALID:" + entry["path"])
                state, _ = _target_state(target_root / entry["path"], expected_post)
                if state != "EXACT":
                    raise ManagedSurfaceError("ADOPTION_POSTCONDITION_FAILED:" + entry["path"])
            snapshot = snapshot_from_adoption_plan(
                plan,
                source_root,
                transaction_id=txid,
                plan_sha256=plan_sha,
                consumer_root_sha256=_root_digest(target_root),
            )
            _validate_snapshot(snapshot, source_root)
            snapshot_sha = _write_snapshot(store, snapshot)
            journal["snapshot_path"] = store.snapshot.relative_to(target_root).as_posix()
            journal["snapshot_sha256"] = snapshot_sha
            journal["status"] = "COMMITTED"
            journal["last_error"] = None
            store.save(journal)
            verified = _verify_committed(store, journal, target_root)
            return {
                "status": "COMMITTED",
                "transaction_id": txid,
                "snapshot_path": journal["snapshot_path"],
                "snapshot_sha256": snapshot_sha,
                "snapshot": verified,
                "created_files": writes,
                "write_performed": writes > 0,
                "source_revision": plan["source_revision"],
                "source_manifest_sha256": inventory["manifest_sha256"],
            }
        except Exception as exc:
            journal["status"] = "RECOVERY_REQUIRED"
            journal["last_error"] = f"{type(exc).__name__}:{exc}"
            store.save(journal)
            # Avoid re-entering the same transaction lock. Recovery semantics are
            # executed inline under the lock using the same conservative rules.
            error = journal["last_error"]
            blockers: list[str] = []
            for entry in reversed(journal["entries"]):
                entry_blockers_before = len(blockers)
                if entry["planned_action"] != "CREATE_PLANNED":
                    if entry["planned_action"] == "PRESERVE_SHARED":
                        preserved = entry.get("preserved_sha256")
                        if not isinstance(preserved, str) or not SHA256.fullmatch(preserved):
                            blockers.append("RECOVERY_PRESERVED_SHARED_EXPECTATION_INVALID:" + entry["path"])
                        else:
                            state, _ = _target_state(target_root / entry["path"], preserved)
                            if state != "EXACT":
                                blockers.append("RECOVERY_PRESERVED_SHARED_DRIFT:" + entry["path"])
                    if len(blockers) == entry_blockers_before:
                        entry["state"] = "PRESERVED"
                        entry["staging_path"] = None
                    continue
                rel = entry["path"]
                target = target_root / rel
                stage = target_root / entry["staging_path"] if entry.get("staging_path") else None
                provenance_link = False
                if stage is not None and stage.exists() and not stage.is_symlink() and target.exists() and not target.is_symlink():
                    try:
                        provenance_link = os.path.samefile(stage, target)
                    except OSError:
                        provenance_link = False
                should_own_target = entry["state"] in {"CREATED", "VERIFIED"} or provenance_link
                if should_own_target:
                    state, _ = _target_state(target, entry["source_sha256"])
                    if state == "EXACT":
                        try:
                            target.unlink()
                            _fsync_directory(target.parent)
                        except OSError as remove_exc:
                            blockers.append("RECOVERY_REMOVE_FAILED:" + rel + ":" + type(remove_exc).__name__)
                    elif state != "ABSENT":
                        blockers.append("RECOVERY_TARGET_DRIFT:" + rel)
                elif entry["state"] == "STAGING":
                    state, _ = _target_state(target, entry["source_sha256"])
                    # Foreign concurrent targets were never owned by this transaction.
                    # The only ambiguous crash window is exact target + lost stage.
                    if state == "EXACT" and not provenance_link and (stage is None or not stage.exists()):
                        blockers.append("RECOVERY_UNPROVEN_EXACT_TARGET:" + rel)
                if stage is not None and stage.exists():
                    if stage.is_symlink() or not stage.is_file() or _digest(stage) != entry["source_sha256"]:
                        blockers.append("RECOVERY_STAGING_DRIFT:" + rel)
                    else:
                        try:
                            stage.unlink()
                            _fsync_directory(stage.parent)
                        except OSError as remove_exc:
                            blockers.append("RECOVERY_STAGING_REMOVE_FAILED:" + rel + ":" + type(remove_exc).__name__)
                if len(blockers) == entry_blockers_before:
                    entry["staging_path"] = None
                    entry["state"] = "ROLLED_BACK"
            blockers.extend(_remove_created_dirs(target_root, journal))
            journal["status"] = "RECOVERY_BLOCKED" if blockers else "ROLLED_BACK"
            journal["last_error"] = error if not blockers else error + ";" + ";".join(blockers)
            if not blockers:
                journal["created_dirs"] = []
            store.save(journal)
            return {
                "status": journal["status"],
                "transaction_id": txid,
                "error": error,
                "blockers": blockers,
                "write_performed": writes > 0,
            }


# LIFECYCLE-003: guarded destructive detach.  Adoption and detach deliberately
# share the same runtime root, source/snapshot validation helpers, journal sealing
# model and exact-source recovery primitives, while using a separate schema so
# existing LIFECYCLE-002 adoption journals remain backward compatible.
DETACH_TRANSACTION_SCHEMA = ".adwf/schemas/managed-surface-detach-transaction.schema.json"


def _snapshot_digest(value: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _validate_detach_transaction(value: dict[str, Any], source_root: Path) -> None:
    try:
        schema = strict_load(source_root / DETACH_TRANSACTION_SCHEMA)
    except Exception as exc:
        raise ManagedSurfaceError("DETACH_TRANSACTION_SCHEMA_INVALID:" + type(exc).__name__) from exc
    findings = validate(value, schema)
    if findings:
        raise ManagedSurfaceError(
            "DETACH_TRANSACTION_SCHEMA_MISMATCH:"
            + ",".join(f"{item.path}:{item.code}" for item in findings)
        )
    if value.get("journal_sha256") != _transaction_journal_digest(value):
        raise ManagedSurfaceError("DETACH_TRANSACTION_JOURNAL_DIGEST_MISMATCH")
    paths = [str(item.get("path") or "") for item in value.get("entries") or []]
    if len(paths) != len(set(paths)):
        raise ManagedSurfaceError("DETACH_TRANSACTION_PATH_DUPLICATE")
    for rel in paths:
        _safe_rel(rel)
    for rel in value.get("provenance_dirs") or []:
        _safe_rel(str(rel))
    for rel in value.get("removed_dirs") or []:
        _safe_rel(str(rel))
    for entry in value.get("entries") or []:
        quarantine = entry.get("quarantine_path")
        if quarantine is not None:
            _safe_rel(str(quarantine))


class DetachTransactionStore:
    """Durable store for guarded detach, isolated from adoption journal schema."""

    def __init__(self, source_root: Path, consumer_root: Path, transaction_id: str, *, create: bool) -> None:
        self.source_root = source_root
        self.consumer_root = consumer_root
        self.base = _runtime_base(consumer_root, create=create)
        self.transactions = self.base / "detach-transactions"
        self.quarantines = self.base / "detach-quarantine"
        self.quarantine = self.quarantines / transaction_id
        if create:
            for directory in (self.transactions, self.quarantines):
                if directory.is_symlink():
                    raise ManagedSurfaceError("RUNTIME_SYMLINK_FORBIDDEN:" + directory.name)
                if directory.exists() and not directory.is_dir():
                    raise ManagedSurfaceError("RUNTIME_NON_DIRECTORY:" + directory.name)
                directory.mkdir(exist_ok=True)
            if self.quarantine.is_symlink():
                raise ManagedSurfaceError("DETACH_QUARANTINE_SYMLINK_FORBIDDEN")
            if self.quarantine.exists() and not self.quarantine.is_dir():
                raise ManagedSurfaceError("DETACH_QUARANTINE_NON_DIRECTORY")
            self.quarantine.mkdir(exist_ok=True)
        self.path = self.transactions / f"{transaction_id}.json"
        self.lock = self.transactions / f"{transaction_id}.txn.lock"

    def quarantine_for(self, rel: str) -> Path:
        token = _sha256_bytes(_safe_rel(rel).encode("utf-8"))
        return self.quarantine / f"{token}.quarantine"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise ManagedSurfaceError("DETACH_TRANSACTION_JOURNAL_OBJECT_INVALID")
        try:
            value = strict_load(self.path)
        except Exception as exc:
            raise ManagedSurfaceError("DETACH_TRANSACTION_JOURNAL_INVALID:" + type(exc).__name__) from exc
        if not isinstance(value, dict):
            raise ManagedSurfaceError("DETACH_TRANSACTION_JOURNAL_OBJECT_REQUIRED")
        _validate_detach_transaction(value, self.source_root)
        return value

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        value["journal_sha256"] = _transaction_journal_digest(value)
        _validate_detach_transaction(value, self.source_root)
        self.transactions.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.transactions)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.transactions)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return value


def _load_trusted_adoption_snapshot(
    source_root: Path,
    target_root: Path,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind detach authority to the exact durable LIFECYCLE-002 provenance chain."""
    _validate_snapshot(snapshot, source_root)
    required_binding = ("transaction_id", "plan_sha256", "consumer_root_sha256")
    if any(snapshot.get(key) is None for key in required_binding):
        raise ManagedSurfaceError("DETACH_REQUIRES_TRANSACTION_BOUND_SNAPSHOT")
    if snapshot.get("consumer_root_sha256") != _root_digest(target_root):
        raise ManagedSurfaceError("DETACH_SNAPSHOT_CONSUMER_ROOT_MISMATCH")
    _verify_source_revision(source_root, str(snapshot["source_revision"]))

    adoption_id = str(snapshot["transaction_id"])
    store = TransactionStore(source_root, target_root, adoption_id, create=False)
    if not store.transactions.is_dir():
        raise ManagedSurfaceError("DETACH_ADOPTION_TRANSACTION_NOT_FOUND")
    adoption = store.load()
    if adoption is None:
        raise ManagedSurfaceError("DETACH_ADOPTION_TRANSACTION_NOT_FOUND")
    expected_identity = {
        "transaction_id": adoption_id,
        "status": "COMMITTED",
        "source_revision": snapshot["source_revision"],
        "source_manifest_sha256": snapshot["source_manifest_sha256"],
        "plan_sha256": snapshot["plan_sha256"],
        "consumer_root_sha256": snapshot["consumer_root_sha256"],
    }
    for key, expected in expected_identity.items():
        if adoption.get(key) != expected:
            raise ManagedSurfaceError("DETACH_ADOPTION_PROVENANCE_MISMATCH:" + key)
    snapshot_rel = adoption.get("snapshot_path")
    if not isinstance(snapshot_rel, str):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_PATH_MISSING")
    _safe_rel(snapshot_rel)
    stored_path = target_root / snapshot_rel
    if stored_path.is_symlink() or not stored_path.is_file():
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_MISSING")
    if _digest(stored_path) != adoption.get("snapshot_sha256"):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_FILE_DIGEST_MISMATCH")
    try:
        stored_snapshot = strict_load(stored_path)
    except Exception as exc:
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_INVALID:" + type(exc).__name__) from exc
    if not isinstance(stored_snapshot, dict):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_OBJECT_REQUIRED")
    _validate_snapshot(stored_snapshot, source_root)
    if stored_snapshot != snapshot:
        raise ManagedSurfaceError("DETACH_SNAPSHOT_DOES_NOT_MATCH_DURABLE_PROVENANCE")
    return adoption, stored_snapshot


def _validate_detach_recovery_provenance(
    source_root: Path,
    target_root: Path,
    store: DetachTransactionStore,
    journal: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild immutable detach authority from durable adoption provenance.

    A self-sealed detach journal is useful for torn-write/tamper detection, but
    its mutable recovery fields must never be sufficient to invent deletion or
    restore authority.  Recovery therefore re-binds the journal to the exact
    committed adoption transaction and its durable snapshot before touching the
    consumer tree.
    """
    expected_identity = {
        "kind": "DETACH",
        "adoption_transaction_id": journal["adoption_transaction_id"],
        "source_revision": journal["source_revision"],
        "source_manifest_sha256": journal["source_manifest_sha256"],
        "snapshot_sha256": journal["snapshot_sha256"],
        "plan_sha256": journal["plan_sha256"],
        "consumer_root_sha256": journal["consumer_root_sha256"],
    }
    if journal.get("transaction_id") != _sha256_bytes(_canonical_bytes(expected_identity)):
        raise ManagedSurfaceError("DETACH_TRANSACTION_IDENTITY_DIGEST_MISMATCH")

    adoption_id = str(journal["adoption_transaction_id"])
    adoption_store = TransactionStore(source_root, target_root, adoption_id, create=False)
    if not adoption_store.transactions.is_dir():
        raise ManagedSurfaceError("DETACH_ADOPTION_TRANSACTION_NOT_FOUND")
    adoption = adoption_store.load()
    if adoption is None or adoption.get("status") != "COMMITTED":
        raise ManagedSurfaceError("DETACH_ADOPTION_TRANSACTION_NOT_COMMITTED")
    for key in ("source_revision", "source_manifest_sha256", "consumer_root_sha256"):
        if adoption.get(key) != journal.get(key):
            raise ManagedSurfaceError("DETACH_ADOPTION_PROVENANCE_MISMATCH:" + key)

    snapshot_rel = adoption.get("snapshot_path")
    if not isinstance(snapshot_rel, str):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_PATH_MISSING")
    _safe_rel(snapshot_rel)
    snapshot_path = target_root / snapshot_rel
    if snapshot_path.is_symlink() or not snapshot_path.is_file():
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_MISSING")
    if _digest(snapshot_path) != adoption.get("snapshot_sha256"):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_FILE_DIGEST_MISMATCH")
    try:
        snapshot = strict_load(snapshot_path)
    except Exception as exc:
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_INVALID:" + type(exc).__name__) from exc
    if not isinstance(snapshot, dict):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_OBJECT_REQUIRED")
    _validate_snapshot(snapshot, source_root)
    if snapshot.get("transaction_id") != adoption_id:
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_TRANSACTION_MISMATCH")
    if snapshot.get("plan_sha256") != adoption.get("plan_sha256"):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_PLAN_MISMATCH")
    if snapshot.get("consumer_root_sha256") != journal.get("consumer_root_sha256"):
        raise ManagedSurfaceError("DETACH_ADOPTION_SNAPSHOT_ROOT_MISMATCH")
    if _snapshot_digest(snapshot) != journal.get("snapshot_sha256"):
        raise ManagedSurfaceError("DETACH_TRANSACTION_SNAPSHOT_DIGEST_MISMATCH")

    snapshot_by_path = {item["path"]: item for item in snapshot["entries"]}
    journal_by_path = {item["path"]: item for item in journal["entries"]}
    if set(snapshot_by_path) != set(journal_by_path):
        raise ManagedSurfaceError("DETACH_TRANSACTION_ENTRY_SET_MISMATCH")
    for rel, snap in snapshot_by_path.items():
        item = journal_by_path[rel]
        immutable = {
            "installed_sha256": snap["installed_sha256"],
            "preserved_sha256": snap.get("preserved_sha256"),
            "ownership": snap["ownership"],
            "managed_by_adwf": snap["managed_by_adwf"],
        }
        for key, expected in immutable.items():
            if item.get(key) != expected:
                raise ManagedSurfaceError("DETACH_TRANSACTION_ENTRY_PROVENANCE_MISMATCH:" + rel + ":" + key)
        if snap["managed_by_adwf"] is not True:
            allowed_actions = {"PRESERVE_PREEXISTING"}
        elif snap["ownership"] == "SHARED_GUARDED":
            allowed_actions = {"PRESERVE_SHARED"}
        elif snap["ownership"] == "FRAMEWORK_PRIVATE":
            allowed_actions = {"REMOVE_ELIGIBLE", "ALREADY_ABSENT"}
        else:
            allowed_actions = {"PRESERVE_CONSUMER"}
        if item.get("planned_action") not in allowed_actions:
            raise ManagedSurfaceError("DETACH_TRANSACTION_ACTION_PROVENANCE_MISMATCH:" + rel)
        quarantine_rel = item.get("quarantine_path")
        if quarantine_rel is not None:
            expected_quarantine = store.quarantine_for(rel).relative_to(target_root).as_posix()
            if quarantine_rel != expected_quarantine:
                raise ManagedSurfaceError("DETACH_TRANSACTION_QUARANTINE_PATH_MISMATCH:" + rel)

    provenance_dirs = list(adoption.get("created_dirs") or [])
    if journal.get("provenance_dirs") != provenance_dirs:
        raise ManagedSurfaceError("DETACH_TRANSACTION_DIRECTORY_PROVENANCE_MISMATCH")
    if not set(journal.get("removed_dirs") or []).issubset(set(provenance_dirs)):
        raise ManagedSurfaceError("DETACH_TRANSACTION_REMOVED_DIRECTORY_AUTHORITY_INVALID")
    return adoption, snapshot


def _validate_detach_inputs(
    source_root: Path,
    target_root: Path,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _validate_plan(plan, source_root)
    if plan.get("kind") != "DETACH" or plan.get("status") != "READY" or plan.get("blockers") != []:
        raise ManagedSurfaceError("DETACH_APPLY_REQUIRES_READY_PLAN")
    adoption, stored_snapshot = _load_trusted_adoption_snapshot(source_root, target_root, snapshot)
    for entry in plan["entries"]:
        action = entry.get("action")
        if action in {"BLOCK", "PRESERVE_BLOCK"}:
            raise ManagedSurfaceError("DETACH_READY_PLAN_CONTAINS_BLOCKED_ACTION:" + entry["path"])
        if action == "REMOVE_ELIGIBLE":
            if not (
                entry.get("ownership") == "FRAMEWORK_PRIVATE"
                and entry.get("managed_by_adwf") is True
                and entry.get("target_state") == "EXACT"
                and entry.get("target_sha256") == entry.get("installed_sha256")
            ):
                raise ManagedSurfaceError("DETACH_REMOVE_AUTHORITY_INVALID:" + entry["path"])
        elif action == "ALREADY_ABSENT":
            if not (entry.get("managed_by_adwf") is True and entry.get("target_state") == "ABSENT"):
                raise ManagedSurfaceError("DETACH_ABSENT_AUTHORITY_INVALID:" + entry["path"])
        elif action not in {"PRESERVE_PREEXISTING", "PRESERVE_SHARED", "PRESERVE_CONSUMER"}:
            raise ManagedSurfaceError("DETACH_ACTION_INVALID:" + entry["path"])
    inventory = load_source_inventory(source_root)
    return inventory, adoption, stored_snapshot


def _assert_detach_plan_matches_current(
    source_root: Path, target_root: Path, snapshot: dict[str, Any], plan: dict[str, Any]
) -> None:
    from .managed_surface import plan_detach

    expected_plan = plan_detach(target_root, snapshot, framework_root=source_root)
    if _canonical_bytes(expected_plan) != _canonical_bytes(plan):
        raise ManagedSurfaceError("DETACH_PLAN_TARGET_STATE_CHANGED")


def _detach_transaction_identity(
    snapshot: dict[str, Any], plan: dict[str, Any], target_root: Path
) -> tuple[str, str, str]:
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    snapshot_sha = _snapshot_digest(snapshot)
    identity = {
        "kind": "DETACH",
        "adoption_transaction_id": snapshot["transaction_id"],
        "source_revision": snapshot["source_revision"],
        "source_manifest_sha256": snapshot["source_manifest_sha256"],
        "snapshot_sha256": snapshot_sha,
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(target_root),
    }
    return _sha256_bytes(_canonical_bytes(identity)), plan_sha, snapshot_sha


def _new_detach_transaction(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    adoption: dict[str, Any],
    target_root: Path,
    transaction_id: str,
    plan_sha: str,
    snapshot_sha: str,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in plan["entries"]:
        action = item["action"]
        if action == "ALREADY_ABSENT":
            state = "ALREADY_ABSENT"
        elif action in {"PRESERVE_PREEXISTING", "PRESERVE_SHARED", "PRESERVE_CONSUMER"}:
            state = "PRESERVED"
        else:
            state = "PENDING"
        entries.append(
            {
                "path": item["path"],
                "installed_sha256": item["installed_sha256"],
                "preserved_sha256": item.get("preserved_sha256"),
                "ownership": item["ownership"],
                "managed_by_adwf": item["managed_by_adwf"],
                "planned_action": action,
                "state": state,
                "quarantine_path": None,
            }
        )
    return {
        "$schema": DETACH_TRANSACTION_SCHEMA,
        "schema_version": 1,
        "role": "MANAGED_SURFACE_DETACH_TRANSACTION",
        "transaction_id": transaction_id,
        "status": "PLANNED",
        "adoption_transaction_id": snapshot["transaction_id"],
        "source_revision": snapshot["source_revision"],
        "source_manifest_sha256": snapshot["source_manifest_sha256"],
        "snapshot_sha256": snapshot_sha,
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(target_root),
        "attempts": 0,
        "entries": entries,
        "provenance_dirs": list(adoption.get("created_dirs") or []),
        "removed_dirs": [],
        "last_error": None,
        "journal_sha256": "0" * 64,
    }


def _assert_detach_journal_identity(
    journal: dict[str, Any],
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    target_root: Path,
    transaction_id: str,
    plan_sha: str,
    snapshot_sha: str,
) -> None:
    expected = {
        "transaction_id": transaction_id,
        "adoption_transaction_id": snapshot["transaction_id"],
        "source_revision": snapshot["source_revision"],
        "source_manifest_sha256": snapshot["source_manifest_sha256"],
        "snapshot_sha256": snapshot_sha,
        "plan_sha256": plan_sha,
        "consumer_root_sha256": _root_digest(target_root),
    }
    for key, value in expected.items():
        if journal.get(key) != value:
            raise ManagedSurfaceError("DETACH_TRANSACTION_IDENTITY_MISMATCH:" + key)
    by_path = {item["path"]: item for item in journal["entries"]}
    if set(by_path) != {item["path"] for item in plan["entries"]}:
        raise ManagedSurfaceError("DETACH_TRANSACTION_ENTRY_SET_MISMATCH")
    for item in plan["entries"]:
        stored = by_path[item["path"]]
        immutable = {
            "installed_sha256": item["installed_sha256"],
            "preserved_sha256": item.get("preserved_sha256"),
            "ownership": item["ownership"],
            "managed_by_adwf": item["managed_by_adwf"],
            "planned_action": item["action"],
        }
        for key, expected_value in immutable.items():
            if stored.get(key) != expected_value:
                raise ManagedSurfaceError("DETACH_TRANSACTION_ENTRY_IMMUTABLE_MISMATCH:" + item["path"])


def _assert_detach_parent_chain(target_root: Path, rel: str) -> Path:
    current = target_root
    parts = PurePosixPath(_safe_rel(rel)).parts[:-1]
    prefix: list[str] = []
    for part in parts:
        prefix.append(part)
        rel_dir = PurePosixPath(*prefix).as_posix()
        nxt = current / part
        if nxt.is_symlink():
            raise ManagedSurfaceError("DETACH_PARENT_SYMLINK_FORBIDDEN:" + rel_dir)
        if not nxt.exists():
            raise ManagedSurfaceError("DETACH_PARENT_MISSING:" + rel_dir)
        if not nxt.is_dir():
            raise ManagedSurfaceError("DETACH_PARENT_NON_DIRECTORY:" + rel_dir)
        current = nxt
    return current


def _move_to_detach_quarantine(
    target_root: Path,
    rel: str,
    expected_sha: str,
    quarantine: Path,
) -> None:
    _assert_detach_parent_chain(target_root, rel)
    target = target_root / rel
    state, current = _target_state(target, expected_sha)
    if state == "SYMLINK":
        raise ManagedSurfaceError("DETACH_TARGET_SYMLINK_FORBIDDEN:" + rel)
    if state == "NON_FILE":
        raise ManagedSurfaceError("DETACH_TARGET_NON_FILE_DRIFT:" + rel)
    if state == "ABSENT":
        raise ManagedSurfaceError("DETACH_TARGET_CONCURRENTLY_ABSENT:" + rel)
    if state != "EXACT" or current != expected_sha:
        raise ManagedSurfaceError("DETACH_TARGET_CONTENT_DRIFT:" + rel)
    if quarantine.is_symlink() or quarantine.exists():
        raise ManagedSurfaceError("DETACH_QUARANTINE_COLLISION:" + rel)
    try:
        os.rename(target, quarantine)
        _fsync_directory(target.parent)
        _fsync_directory(quarantine.parent)
    except OSError as exc:
        raise ManagedSurfaceError("DETACH_ATOMIC_QUARANTINE_MOVE_FAILED:" + rel + ":" + type(exc).__name__) from exc
    if quarantine.is_symlink() or not quarantine.is_file():
        raise ManagedSurfaceError("DETACH_QUARANTINE_OBJECT_INVALID:" + rel)
    actual = _digest(quarantine)
    if actual != expected_sha:
        # A concurrent replacement may have won the race between precheck and
        # rename. The moved bytes remain in quarantine and recovery restores
        # them rather than deleting unknown consumer content.
        raise ManagedSurfaceError("DETACH_QUARANTINE_CONTENT_DRIFT:" + rel)
    post, _ = _target_state(target, expected_sha)
    if post != "ABSENT":
        raise ManagedSurfaceError("DETACH_POST_MOVE_TARGET_NOT_ABSENT:" + rel)


def _restore_exact_source_no_replace(
    source_root: Path,
    target_root: Path,
    rel: str,
    expected_sha: str,
    transaction_id: str,
) -> None:
    _assert_detach_parent_chain(target_root, rel)
    target = target_root / rel
    state, _ = _target_state(target, expected_sha)
    if state == "EXACT":
        return
    if state != "ABSENT":
        raise ManagedSurfaceError("DETACH_RECOVERY_TARGET_OCCUPIED:" + rel)
    source = source_root / rel
    if source.is_symlink() or not source.is_file() or _digest(source) != expected_sha:
        raise ManagedSurfaceError("DETACH_RECOVERY_SOURCE_UNTRUSTED:" + rel)
    stage = target.parent / f".{target.name}.adwf-detach-restore-{transaction_id[:16]}.stage"
    _prepare_stage(source, stage, expected_sha)
    try:
        _link_stage_no_replace(stage, target, expected_sha)
    finally:
        if stage.exists() and not stage.is_symlink():
            try:
                stage.unlink()
                _fsync_directory(stage.parent)
            except OSError:
                pass
    state, _ = _target_state(target, expected_sha)
    if state != "EXACT":
        raise ManagedSurfaceError("DETACH_RECOVERY_SOURCE_RESTORE_FAILED:" + rel)


def _recover_detach_locked(
    source_root: Path,
    target_root: Path,
    store: DetachTransactionStore,
    journal: dict[str, Any],
) -> tuple[list[str], bool]:
    blockers: list[str] = []
    restored = False
    for entry in reversed(journal["entries"]):
        if entry["planned_action"] != "REMOVE_ELIGIBLE":
            continue
        rel = entry["path"]
        expected = entry["installed_sha256"]
        target = target_root / rel
        quarantine = target_root / entry["quarantine_path"] if entry.get("quarantine_path") else None
        q_exists = quarantine is not None and quarantine.exists()
        if quarantine is not None and quarantine.is_symlink():
            blockers.append("DETACH_RECOVERY_QUARANTINE_SYMLINK:" + rel)
            entry["state"] = "RECOVERY_BLOCKED"
            continue
        if q_exists and quarantine is not None:
            if not quarantine.is_file():
                blockers.append("DETACH_RECOVERY_QUARANTINE_NON_FILE:" + rel)
                entry["state"] = "RECOVERY_BLOCKED"
                continue
            quarantine_sha = _digest(quarantine)
            state, _ = _target_state(target, expected)
            if state == "ABSENT":
                try:
                    _assert_detach_parent_chain(target_root, rel)
                    os.rename(quarantine, target)
                    _fsync_directory(target.parent)
                    _fsync_directory(quarantine.parent)
                    restored = True
                    entry["state"] = "RESTORED" if quarantine_sha == expected else "RECOVERY_BLOCKED"
                    entry["quarantine_path"] = None
                    if quarantine_sha != expected:
                        blockers.append("DETACH_RECOVERY_RESTORED_CONCURRENT_BYTES:" + rel)
                except (OSError, ManagedSurfaceError) as exc:
                    blockers.append("DETACH_RECOVERY_QUARANTINE_RESTORE_FAILED:" + rel + ":" + type(exc).__name__)
                    entry["state"] = "RECOVERY_BLOCKED"
            elif state == "EXACT":
                if quarantine_sha == expected:
                    try:
                        quarantine.unlink()
                        _fsync_directory(quarantine.parent)
                        entry["quarantine_path"] = None
                        entry["state"] = "RESTORED"
                    except OSError as exc:
                        blockers.append("DETACH_RECOVERY_QUARANTINE_CLEANUP_FAILED:" + rel + ":" + type(exc).__name__)
                        entry["state"] = "RECOVERY_BLOCKED"
                else:
                    blockers.append("DETACH_RECOVERY_FOREIGN_QUARANTINE_PRESERVED:" + rel)
                    entry["state"] = "RECOVERY_BLOCKED"
            else:
                blockers.append("DETACH_RECOVERY_TARGET_DRIFT:" + rel)
                entry["state"] = "RECOVERY_BLOCKED"
            continue

        state, _ = _target_state(target, expected)
        if state == "EXACT":
            entry["state"] = "RESTORED"
            entry["quarantine_path"] = None
            continue
        if state == "ABSENT" and entry["state"] in {
            "QUARANTINE_PLANNED", "QUARANTINED", "VERIFIED_ABSENT", "PURGED"
        }:
            try:
                _restore_exact_source_no_replace(source_root, target_root, rel, expected, journal["transaction_id"])
                restored = True
                entry["state"] = "RESTORED"
                entry["quarantine_path"] = None
            except ManagedSurfaceError as exc:
                blockers.append(str(exc))
                entry["state"] = "RECOVERY_BLOCKED"
        elif state == "ABSENT" and entry["state"] == "PENDING":
            # This entry was never touched by the transaction.
            entry["state"] = "RESTORED"
        else:
            blockers.append("DETACH_RECOVERY_TARGET_DRIFT:" + rel)
            entry["state"] = "RECOVERY_BLOCKED"
    return blockers, restored


def _cleanup_detach_provenance_dirs(target_root: Path, journal: dict[str, Any]) -> tuple[list[str], list[str]]:
    removed: list[str] = list(journal.get("removed_dirs") or [])
    preserved: list[str] = []
    already = set(removed)
    for rel in sorted(
        journal.get("provenance_dirs") or [],
        key=lambda value: (len(PurePosixPath(value).parts), value),
        reverse=True,
    ):
        if rel in already:
            continue
        path = target_root / rel
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            preserved.append(rel)
            continue
        if not path.exists():
            continue
        try:
            path.rmdir()
            _fsync_directory(path.parent)
            removed.append(rel)
            already.add(rel)
        except OSError:
            # Non-empty or concurrently changed directories are outside
            # destructive authority and are deliberately preserved.
            preserved.append(rel)
    journal["removed_dirs"] = removed
    return removed, preserved


def _verify_detach_committed(target_root: Path, journal: dict[str, Any]) -> None:
    if journal.get("status") != "COMMITTED":
        raise ManagedSurfaceError("DETACH_TRANSACTION_NOT_COMMITTED")
    for entry in journal["entries"]:
        action = entry["planned_action"]
        if action in {"REMOVE_ELIGIBLE", "ALREADY_ABSENT"}:
            state, _ = _target_state(target_root / entry["path"], entry["installed_sha256"])
            if state != "ABSENT":
                raise ManagedSurfaceError("DETACH_COMMITTED_TARGET_REAPPEARED:" + entry["path"])
        elif action == "PRESERVE_PREEXISTING":
            expected = entry.get("preserved_sha256") or entry["installed_sha256"]
            state, _ = _target_state(target_root / entry["path"], expected)
            if state != "EXACT":
                raise ManagedSurfaceError("DETACH_COMMITTED_PREEXISTING_DRIFT:" + entry["path"])
        quarantine = target_root / entry["quarantine_path"] if entry.get("quarantine_path") else None
        if quarantine is not None and quarantine.exists():
            raise ManagedSurfaceError("DETACH_COMMITTED_QUARANTINE_REMAINS:" + entry["path"])


def recover_detach(
    framework_root: str | Path,
    consumer_root: str | Path,
    transaction_id: str,
) -> dict[str, Any]:
    """Restore an incomplete detach using quarantine or exact verified source bytes."""
    source_root = Path(framework_root).resolve()
    target_root = _consumer_root(consumer_root)
    store = DetachTransactionStore(source_root, target_root, transaction_id, create=False)
    if not store.transactions.is_dir():
        raise ManagedSurfaceError("DETACH_TRANSACTION_NOT_FOUND")
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None:
            raise ManagedSurfaceError("DETACH_TRANSACTION_NOT_FOUND")
        if journal.get("consumer_root_sha256") != _root_digest(target_root):
            raise ManagedSurfaceError("DETACH_TRANSACTION_CONSUMER_ROOT_MISMATCH")
        _verify_source_revision(source_root, str(journal["source_revision"]))
        _validate_detach_recovery_provenance(source_root, target_root, store, journal)
        if journal["status"] == "COMMITTED":
            _verify_detach_committed(target_root, journal)
            return {
                "status": "COMMITTED",
                "transaction_id": transaction_id,
                "blockers": [],
                "human_required": False,
                "write_performed": False,
            }
        if journal["status"] == "ROLLED_BACK":
            return {
                "status": "ROLLED_BACK",
                "transaction_id": transaction_id,
                "blockers": [],
                "human_required": False,
                "write_performed": False,
            }
        blockers, restored = _recover_detach_locked(source_root, target_root, store, journal)
        if blockers:
            journal["status"] = "RECOVERY_BLOCKED"
            journal["last_error"] = ";".join(blockers)
        else:
            journal["status"] = "ROLLED_BACK"
            journal["last_error"] = None
        store.save(journal)
        return {
            "status": journal["status"],
            "transaction_id": transaction_id,
            "blockers": blockers,
            "human_required": bool(blockers),
            "write_performed": restored,
        }


def apply_detach(
    framework_root: str | Path,
    consumer_root: str | Path,
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    *,
    fault_after_deletes: int | None = None,
) -> dict[str, Any]:
    """Explicitly detach only exact managed FRAMEWORK_PRIVATE paths.

    Every removable file is first moved atomically to a transaction-private
    quarantine. Unknown/concurrent bytes are never purged. Any failure before
    COMMITTED rolls the transaction back from quarantine or the exact verified
    source revision; a drifted/occupied target is preserved and surfaced as a
    human-required blocker.
    """
    source_root = Path(framework_root).resolve()
    target_root = _consumer_root(consumer_root)
    if fault_after_deletes is not None and (
        not isinstance(fault_after_deletes, int) or isinstance(fault_after_deletes, bool) or fault_after_deletes < 1
    ):
        raise ManagedSurfaceError("DETACH_FAULT_INJECTION_VALUE_INVALID")
    inventory, adoption, stored_snapshot = _validate_detach_inputs(
        source_root, target_root, snapshot, plan
    )
    txid, plan_sha, snapshot_sha = _detach_transaction_identity(stored_snapshot, plan, target_root)
    store = DetachTransactionStore(source_root, target_root, txid, create=True)
    with exclusive_file_lock(store.lock):
        journal = store.load()
        if journal is None:
            _assert_detach_plan_matches_current(source_root, target_root, stored_snapshot, plan)
            journal = _new_detach_transaction(
                stored_snapshot, plan, adoption, target_root, txid, plan_sha, snapshot_sha
            )
            store.save(journal)
        else:
            _validate_detach_recovery_provenance(source_root, target_root, store, journal)
            _assert_detach_journal_identity(
                journal, stored_snapshot, plan, target_root, txid, plan_sha, snapshot_sha
            )
            if journal["status"] == "COMMITTED":
                _verify_detach_committed(target_root, journal)
                removed_dirs, preserved_dirs = _cleanup_detach_provenance_dirs(target_root, journal)
                store.save(journal)
                return {
                    "status": "ALREADY_COMMITTED",
                    "transaction_id": txid,
                    "removed_files": sum(1 for item in journal["entries"] if item["planned_action"] == "REMOVE_ELIGIBLE"),
                    "removed_dirs": removed_dirs,
                    "preserved_dirs": preserved_dirs,
                    "write_performed": False,
                    "human_required": False,
                }
            if journal["status"] == "RECOVERY_BLOCKED":
                raise ManagedSurfaceError("DETACH_TRANSACTION_RECOVERY_BLOCKED")
            if journal["status"] == "RECOVERY_REQUIRED":
                blockers, _ = _recover_detach_locked(source_root, target_root, store, journal)
                if blockers:
                    journal["status"] = "RECOVERY_BLOCKED"
                    journal["last_error"] = ";".join(blockers)
                    store.save(journal)
                    raise ManagedSurfaceError("DETACH_TRANSACTION_RECOVERY_BLOCKED")
                journal["status"] = "ROLLED_BACK"
                journal["last_error"] = None
                store.save(journal)
            if journal["status"] == "ROLLED_BACK":
                _assert_detach_plan_matches_current(source_root, target_root, stored_snapshot, plan)
                for entry in journal["entries"]:
                    action = entry["planned_action"]
                    if action == "REMOVE_ELIGIBLE":
                        entry["state"] = "PENDING"
                    elif action == "ALREADY_ABSENT":
                        entry["state"] = "ALREADY_ABSENT"
                    else:
                        entry["state"] = "PRESERVED"
                    entry["quarantine_path"] = None
                journal["removed_dirs"] = []
                journal["last_error"] = None
                journal["status"] = "PLANNED"
                store.save(journal)
            elif journal["status"] == "PLANNED":
                _assert_detach_plan_matches_current(source_root, target_root, stored_snapshot, plan)

        journal["status"] = "DETACHING"
        journal["attempts"] = int(journal["attempts"]) + 1
        journal["last_error"] = None
        store.save(journal)
        deletes = 0
        try:
            by_path = {entry["path"]: entry for entry in journal["entries"]}
            for planned in plan["entries"]:
                rel = planned["path"]
                entry = by_path[rel]
                action = entry["planned_action"]
                expected = entry["installed_sha256"]
                target = target_root / rel
                if action in {"PRESERVE_PREEXISTING", "PRESERVE_SHARED", "PRESERVE_CONSUMER"}:
                    entry["state"] = "PRESERVED"
                    entry["quarantine_path"] = None
                    store.save(journal)
                    continue
                if action == "ALREADY_ABSENT":
                    state, _ = _target_state(target, expected)
                    if state != "ABSENT":
                        raise ManagedSurfaceError("DETACH_ABSENT_TARGET_REAPPEARED:" + rel)
                    entry["state"] = "ALREADY_ABSENT"
                    entry["quarantine_path"] = None
                    store.save(journal)
                    continue
                if action != "REMOVE_ELIGIBLE":
                    raise ManagedSurfaceError("DETACH_RUNTIME_ACTION_INVALID:" + rel)

                if entry["state"] == "PURGED":
                    state, _ = _target_state(target, expected)
                    if state != "ABSENT":
                        raise ManagedSurfaceError("DETACH_RESUME_TARGET_REAPPEARED:" + rel)
                    continue

                quarantine = store.quarantine_for(rel)
                quarantine_rel = quarantine.relative_to(target_root).as_posix()
                if entry["quarantine_path"] is not None and entry["quarantine_path"] != quarantine_rel:
                    raise ManagedSurfaceError("DETACH_QUARANTINE_IDENTITY_MISMATCH:" + rel)
                entry["quarantine_path"] = quarantine_rel

                if entry["state"] == "PENDING":
                    entry["state"] = "QUARANTINE_PLANNED"
                    store.save(journal)

                if entry["state"] == "QUARANTINE_PLANNED":
                    if quarantine.exists():
                        state, _ = _target_state(target, expected)
                        if state != "ABSENT":
                            raise ManagedSurfaceError("DETACH_AMBIGUOUS_QUARANTINE_STATE:" + rel)
                    else:
                        _move_to_detach_quarantine(target_root, rel, expected, quarantine)
                    entry["state"] = "QUARANTINED"
                    store.save(journal)

                if entry["state"] == "QUARANTINED":
                    if quarantine.is_symlink() or not quarantine.is_file():
                        raise ManagedSurfaceError("DETACH_QUARANTINE_MISSING_OR_INVALID:" + rel)
                    if _digest(quarantine) != expected:
                        raise ManagedSurfaceError("DETACH_QUARANTINE_CONTENT_DRIFT:" + rel)
                    state, _ = _target_state(target, expected)
                    if state != "ABSENT":
                        raise ManagedSurfaceError("DETACH_POST_MOVE_TARGET_NOT_ABSENT:" + rel)
                    entry["state"] = "VERIFIED_ABSENT"
                    store.save(journal)

                if entry["state"] == "VERIFIED_ABSENT":
                    if quarantine.is_symlink() or not quarantine.is_file() or _digest(quarantine) != expected:
                        raise ManagedSurfaceError("DETACH_QUARANTINE_PURGE_PRECONDITION_FAILED:" + rel)
                    try:
                        quarantine.unlink()
                        _fsync_directory(quarantine.parent)
                    except OSError as exc:
                        raise ManagedSurfaceError("DETACH_QUARANTINE_PURGE_FAILED:" + rel + ":" + type(exc).__name__) from exc
                    entry["quarantine_path"] = None
                    entry["state"] = "PURGED"
                    store.save(journal)
                    deletes += 1
                    if fault_after_deletes is not None and deletes >= fault_after_deletes:
                        raise ManagedSurfaceError("INJECTED_DETACH_FAILURE")

            for entry in journal["entries"]:
                if entry["planned_action"] in {"REMOVE_ELIGIBLE", "ALREADY_ABSENT"}:
                    state, _ = _target_state(target_root / entry["path"], entry["installed_sha256"])
                    if state != "ABSENT":
                        raise ManagedSurfaceError("DETACH_POSTCONDITION_FAILED:" + entry["path"])
                if entry.get("quarantine_path") is not None:
                    quarantine = target_root / entry["quarantine_path"]
                    if quarantine.exists():
                        raise ManagedSurfaceError("DETACH_POSTCONDITION_QUARANTINE_REMAINS:" + entry["path"])
                    entry["quarantine_path"] = None

            journal["status"] = "COMMITTED"
            journal["last_error"] = None
            store.save(journal)
            _verify_detach_committed(target_root, journal)
            removed_dirs, preserved_dirs = _cleanup_detach_provenance_dirs(target_root, journal)
            store.save(journal)
            return {
                "status": "COMMITTED",
                "transaction_id": txid,
                "removed_files": deletes,
                "removed_dirs": removed_dirs,
                "preserved_dirs": preserved_dirs,
                "write_performed": deletes > 0 or bool(removed_dirs),
                "human_required": False,
                "source_revision": stored_snapshot["source_revision"],
                "source_manifest_sha256": inventory["manifest_sha256"],
                "adoption_transaction_id": stored_snapshot["transaction_id"],
            }
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            journal["status"] = "RECOVERY_REQUIRED"
            journal["last_error"] = error
            store.save(journal)
            blockers, restored = _recover_detach_locked(source_root, target_root, store, journal)
            if blockers:
                journal["status"] = "RECOVERY_BLOCKED"
                journal["last_error"] = error + ";" + ";".join(blockers)
            else:
                journal["status"] = "ROLLED_BACK"
                journal["last_error"] = error
            store.save(journal)
            return {
                "status": journal["status"],
                "transaction_id": txid,
                "error": error,
                "blockers": blockers,
                "write_performed": deletes > 0 or restored,
                "human_required": bool(blockers),
            }
