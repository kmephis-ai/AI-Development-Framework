"""Transactional consumer adoption for Managed Surface Contract v1.

The read-only LIFECYCLE-001 plan remains authoritative for *what* may be
created.  This module adds an explicit apply/recovery transaction that can
create only paths proven ABSENT by that plan. Existing consumer files are never
overwritten. Destructive detach remains deliberately out of scope.
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
        if stored["source_sha256"] != item["source_sha256"] or stored["planned_action"] != item["action"]:
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
        state, _ = _target_state(consumer_root / entry["path"], entry["source_sha256"])
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
                if entry["planned_action"] == "KEEP_EXACT":
                    state, _ = _target_state(target, expected)
                    if state != "EXACT":
                        raise ManagedSurfaceError("PREEXISTING_TARGET_CHANGED:" + rel)
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
                state, _ = _target_state(target_root / entry["path"], entry["source_sha256"])
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
