"""Проверка происхождения, SHA и срока действия evidence."""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import math
import os
import re
import tempfile

from .contracts import validate

EVENT_RESERVED_FIELDS = {"schema_version", "sequence", "previous_event_sha256", "event_sha256"}
PRODUCT_EVIDENCE_KINDS = {"SMOKE", "GOLDEN_PATHS", "E2E", "REALITY"}
TRUSTED_PRODUCT_DOMAINS = {"adwf-runtime-verifier", "adwf-trusted-runtime"}
DEFAULT_PRODUCT_TTL_HOURS = {"SMOKE": 24, "GOLDEN_PATHS": 24, "E2E": 24, "REALITY": 168}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_evidence(
    record: dict[str, Any],
    schema: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    expected_sha: str | None = None,
    expected_runtime_revision: str | None = None,
    root: str | Path | None = None,
    enforce_freshness: bool = True,
    require_pass: bool = True,
    require_artifact: bool = False,
    require_provenance: bool = False,
    allowed_source_types: set[str] | None = None,
    allowed_trust_domains: set[str] | None = None,
    max_ttl_hours: int | float | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []
    if schema:
        errors.extend(f"SCHEMA:{item.path}:{item.code}" for item in validate(record, schema))
    if require_pass and record.get("status") != "PASS":
        errors.append("STATUS_NOT_PASS")
    try:
        created = parse_time(str(record.get("created_at", "")))
        expires = parse_time(str(record.get("expires_at", "")))
        if created > now:
            errors.append("CREATED_IN_FUTURE")
        if enforce_freshness and expires <= now:
            errors.append("STALE")
        if expires <= created:
            errors.append("INVALID_TTL")
        if max_ttl_hours is not None:
            if (
                isinstance(max_ttl_hours, bool)
                or not isinstance(max_ttl_hours, (int, float))
                or not math.isfinite(float(max_ttl_hours))
                or float(max_ttl_hours) <= 0
            ):
                errors.append("TTL_POLICY_INVALID")
            elif (expires - created).total_seconds() > float(max_ttl_hours) * 3600:
                errors.append("TTL_EXCEEDS_POLICY")
    except ValueError:
        errors.append("INVALID_TIME")
    sha = record.get("sha")
    if expected_sha is not None and sha != expected_sha:
        errors.append("SHA_MISMATCH")
    if expected_runtime_revision is not None and record.get("runtime_revision") != expected_runtime_revision:
        errors.append("RUNTIME_REVISION_MISMATCH")
    if allowed_source_types is not None and record.get("source_type") not in allowed_source_types:
        errors.append("SOURCE_TYPE_NOT_TRUSTED")
    provenance = record.get("provenance")
    if require_provenance:
        required = {"provider", "source_identity", "trust_domain", "repository", "workflow", "invocation_id"}
        if not isinstance(provenance, dict) or any(not str(provenance.get(name, "")).strip() for name in required):
            errors.append("PROVENANCE_NOT_VERIFIED")
        elif allowed_trust_domains is not None and provenance.get("trust_domain") not in allowed_trust_domains:
            errors.append("TRUST_DOMAIN_NOT_APPROVED")
    content_hash = str(record.get("content_sha256", ""))
    if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
        errors.append("CONTENT_HASH_MISSING")
    artifact = record.get("artifact")
    if require_artifact and not artifact:
        errors.append("ARTIFACT_REQUIRED")
    if require_artifact and root is None:
        errors.append("EVIDENCE_ROOT_REQUIRED")
    if artifact and root:
        unresolved = Path(root) / str(artifact)
        path = unresolved.resolve()
        base = Path(root).resolve()
        if path != base and base not in path.parents:
            errors.append("ARTIFACT_OUTSIDE_ROOT")
        elif unresolved.is_symlink():
            errors.append("ARTIFACT_SYMLINK_FORBIDDEN")
        elif not path.is_file():
            errors.append("ARTIFACT_MISSING")
        elif content_hash and sha256_file(path) != content_hash:
            errors.append("CONTENT_HASH_MISMATCH")
    return {
        "status": "VERIFIED" if not errors else ("STALE" if errors == ["STALE"] else "BLOCKED"),
        "valid": not errors,
        "errors": errors,
        "subject": record.get("subject"),
        "sha": sha,
        "provenance": provenance,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_event_sha256(event: dict[str, Any]) -> str:
    payload = {name: value for name, value in event.items() if name != "event_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def evidence_graph_paths(root: str | Path) -> tuple[Path, Path, Path]:
    directory = Path(root).resolve() / ".adwf-runtime/evidence"
    return directory / "events.jsonl", directory / "index.json", directory / "events.lock"


def _load_schema(root: Path, name: str) -> dict[str, Any]:
    return strict_loads((root / ".adwf/schemas" / name).read_text(encoding="utf-8"))


def _event_log_bytes(events: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(event) + b"\n" for event in events)


def _event_key(event: dict[str, Any]) -> str:
    return "|".join(
        str(event.get(name) or "-") for name in ("kind", "sha", "runtime_revision", "subject")
    )


def project_evidence_index(events: list[dict[str, Any]], log_bytes: bytes | None = None) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        latest[_event_key(event)] = {
            "key": _event_key(event),
            "event_id": event["id"],
            "sequence": event["sequence"],
            "kind": event["kind"],
            "status": event["status"],
            "sha": event.get("sha"),
            "runtime_revision": event.get("runtime_revision"),
            "created_at": event["created_at"],
            "expires_at": event["expires_at"],
            "event_sha256": event["event_sha256"],
        }
    raw = _event_log_bytes(events) if log_bytes is None else log_bytes
    return {
        "schema_version": 1,
        "event_count": len(events),
        "head_event_sha256": events[-1]["event_sha256"] if events else None,
        "log_sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": events[-1]["created_at"] if events else None,
        "records": [latest[key] for key in sorted(latest)],
    }


def _read_event_log(root: Path, *, now: datetime) -> tuple[list[dict[str, Any]], bytes, list[str]]:
    log_path, _, _ = evidence_graph_paths(root)
    if not log_path.is_file():
        return [], b"", ["EVIDENCE_LOG_MISSING"]
    raw = log_path.read_bytes()
    errors: list[str] = []
    if raw and not raw.endswith(b"\n"):
        errors.append("EVIDENCE_LOG_TRUNCATED")
    try:
        schema = _load_schema(root, "evidence-event.schema.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], raw, [f"EVIDENCE_EVENT_SCHEMA_UNREADABLE:{type(exc).__name__}"]
    events: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    previous_hash: str | None = None
    previous_created: datetime | None = None
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            event = strict_loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"EVIDENCE_EVENT_JSON_INVALID:{line_number}")
            continue
        if not isinstance(event, dict):
            errors.append(f"EVIDENCE_EVENT_NOT_OBJECT:{line_number}")
            continue
        errors.extend(
            f"EVIDENCE_EVENT_SCHEMA:{line_number}:{item.path}:{item.code}"
            for item in validate(event, schema)
        )
        if event.get("sequence") != line_number:
            errors.append(f"EVIDENCE_SEQUENCE_INVALID:{line_number}")
        if event.get("previous_event_sha256") != previous_hash:
            errors.append(f"EVIDENCE_CHAIN_PREVIOUS_MISMATCH:{line_number}")
        calculated = evidence_event_sha256(event)
        if event.get("event_sha256") != calculated:
            errors.append(f"EVIDENCE_EVENT_DIGEST_MISMATCH:{line_number}")
        if event.get("id") in identifiers:
            errors.append(f"EVIDENCE_EVENT_ID_DUPLICATE:{line_number}")
        identifiers.add(str(event.get("id")))
        verification = verify_evidence(
            event,
            schema,
            now=now,
            enforce_freshness=False,
            require_pass=False,
            require_provenance=True,
        )
        errors.extend(f"EVIDENCE_EVENT_INVALID:{line_number}:{item}" for item in verification["errors"])
        try:
            created = parse_time(str(event["created_at"]))
            if previous_created is not None and created < previous_created:
                errors.append(f"EVIDENCE_EVENT_TIME_REWIND:{line_number}")
            previous_created = created
        except (KeyError, TypeError, ValueError):
            pass
        previous_hash = str(event.get("event_sha256") or "")
        events.append(event)
    return events, raw, list(dict.fromkeys(errors))


def read_evidence_graph(root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    events, raw, errors = _read_event_log(base, now=now)
    _, index_path, _ = evidence_graph_paths(base)
    expected = project_evidence_index(events, raw)
    if not index_path.is_file():
        errors.append("EVIDENCE_INDEX_MISSING")
        stored = None
    else:
        try:
            stored = strict_loads(index_path.read_text(encoding="utf-8"))
            schema = _load_schema(base, "evidence-index.schema.json")
            errors.extend(f"EVIDENCE_INDEX_SCHEMA:{item.path}:{item.code}" for item in validate(stored, schema))
            if stored != expected:
                errors.append("EVIDENCE_INDEX_PROJECTION_MISMATCH")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            stored = None
            errors.append(f"EVIDENCE_INDEX_UNREADABLE:{type(exc).__name__}")
    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "events": events,
        "index": stored,
        "expected_index": expected,
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        # Directory fsync is a POSIX durability hardening step. Windows does
        # not support opening a directory with os.open in this form.
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rebuild_evidence_index(root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    events, raw, errors = _read_event_log(base, now=now)
    if errors:
        raise ValueError("EVIDENCE_LOG_NOT_REBUILDABLE:" + ",".join(errors))
    index = project_evidence_index(events, raw)
    _, index_path, _ = evidence_graph_paths(base)
    _atomic_json(index_path, index)
    return index


def append_evidence_event(
    root: str | Path,
    record: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append-only запись + атомарная index projection под process lock."""
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if EVENT_RESERVED_FIELDS.intersection(record):
        raise ValueError("EVIDENCE_RESERVED_FIELDS_FORBIDDEN")
    log_path, index_path, lock_path = evidence_graph_paths(base)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(lock_path):
        if log_path.is_file():
            graph = read_evidence_graph(base, now=now)
            if not graph["valid"]:
                raise ValueError("EVIDENCE_GRAPH_RECONCILIATION_REQUIRED:" + ",".join(graph["errors"]))
            events = graph["events"]
        elif index_path.exists():
            raise ValueError("EVIDENCE_GRAPH_RECONCILIATION_REQUIRED:EVIDENCE_LOG_MISSING_WITH_INDEX")
        else:
            events = []
        if any(item.get("id") == record.get("id") for item in events):
            raise ValueError("EVIDENCE_EVENT_ID_DUPLICATE")
        event = dict(record)
        event.update(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            }
        )
        event["event_sha256"] = evidence_event_sha256(event)
        try:
            schema = _load_schema(base, "evidence-event.schema.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"EVIDENCE_EVENT_SCHEMA_UNREADABLE:{type(exc).__name__}") from exc
        verification = verify_evidence(
            event,
            schema,
            now=now,
            root=base,
            require_pass=False,
            require_provenance=True,
        )
        if verification["errors"]:
            raise ValueError("EVIDENCE_EVENT_INVALID:" + ",".join(verification["errors"]))
        if events and parse_time(event["created_at"]) < parse_time(events[-1]["created_at"]):
            raise ValueError("EVIDENCE_EVENT_TIME_REWIND")
        line = _canonical(event) + b"\n"
        with log_path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        # POSIX directory fsync hardens rename/append durability. Windows does not
        # support opening a directory with os.open in this form; the file itself
        # has already been flushed and fsynced above.
        if os.name != "nt":
            directory_fd = os.open(log_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        raw = _event_log_bytes([*events, event])
        _atomic_json(index_path, project_evidence_index([*events, event], raw))
        return event


def verify_product_evidence(
    root: str | Path,
    *,
    expected_sha: str,
    required_kinds: set[str] | list[str] | tuple[str, ...],
    now: datetime | None = None,
    max_ttl_hours_by_kind: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    """Product Health выводится только из свежей проверенной evidence chain."""
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    graph = read_evidence_graph(base, now=now)
    if not graph["valid"]:
        absent = set(graph["errors"]).issubset({"EVIDENCE_LOG_MISSING", "EVIDENCE_INDEX_MISSING"})
        return {
            "status": "NOT_VERIFIED" if absent else "BROKEN",
            "valid": False,
            "errors": graph["errors"],
            "evidence_digest": None,
            "events": [],
        }
    try:
        schema = _load_schema(base, "evidence-event.schema.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "BROKEN", "valid": False, "errors": [f"EVIDENCE_EVENT_SCHEMA_UNREADABLE:{type(exc).__name__}"], "evidence_digest": None, "events": []}
    errors: list[str] = []
    accepted: list[dict[str, Any]] = []
    explicit_failure = False
    for kind in sorted(set(required_kinds)):
        if kind not in PRODUCT_EVIDENCE_KINDS:
            errors.append(f"PRODUCT_EVIDENCE_KIND_UNSUPPORTED:{kind}")
            continue
        candidates = [
            event for event in graph["events"]
            if event.get("kind") == kind
            and event.get("sha") == expected_sha
        ]
        if not candidates:
            errors.append(f"PRODUCT_EVIDENCE_MISSING:{kind}")
            continue
        latest = max(candidates, key=lambda item: int(item["sequence"]))
        if latest.get("product_impact") is not True:
            errors.append(f"PRODUCT_EVIDENCE_INVALID:{kind}:PRODUCT_IMPACT_NOT_TRUE")
        verification = verify_evidence(
            latest,
            schema,
            now=now,
            expected_sha=expected_sha,
            expected_runtime_revision=expected_sha,
            root=base,
            require_artifact=True,
            require_provenance=True,
            allowed_source_types={"RUNTIME"},
            allowed_trust_domains=TRUSTED_PRODUCT_DOMAINS,
            max_ttl_hours=(max_ttl_hours_by_kind or DEFAULT_PRODUCT_TTL_HOURS).get(kind),
        )
        if latest.get("status") in {"FAIL", "BLOCKED"}:
            explicit_failure = True
        product_impact_valid = latest.get("product_impact") is True
        if not verification["valid"]:
            errors.extend(f"PRODUCT_EVIDENCE_INVALID:{kind}:{item}" for item in verification["errors"])
        elif product_impact_valid:
            accepted.append(latest)
    return {
        "status": "VERIFIED" if not errors else ("BROKEN" if explicit_failure else "NOT_VERIFIED"),
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "evidence_digest": graph["index"]["log_sha256"],
        "events": accepted,
    }
