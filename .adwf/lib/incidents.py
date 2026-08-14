"""Детерминированный Incident Knowledge: sanitization, fingerprint и append-only store.

Модуль намеренно не использует LLM/API. Исходное сообщение сохраняется только
после редактирования секретов/PII. Нормализованные гипотезы отделены от фактов,
а каждая запись журнала связана с предыдущей SHA-256 цепочкой.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import copy
import hashlib
import json
import os
import re


INCIDENT_STATUSES = {
    "DETECTED", "SANITIZED", "NORMALIZED", "CLASSIFIED", "RETRYING",
    "RECOVERED", "RECIPE_SELECTED", "SANDBOX_APPLIED", "VERIFIED",
    "PR_PROPOSED", "OBSERVED", "INVESTIGATING", "CANDIDATE_FIX",
    "SHADOW_EVALUATION", "HUMAN_REQUIRED", "ESCALATED", "CLOSED",
    "ROLLED_BACK",
}
SEVERITIES = {"SEV0", "SEV1", "SEV2", "SEV3", "SEV4"}
ZERO_HASH = "0" * 64

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|session|private[_-]?key|credential)",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN = re.compile(
    r"\b(?:github_pat_[A-Za-z0-9_]{12,}|gh[opusr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})\b"
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret)"
    r"(\s*[:=]\s*)([^\s,;]{6,})"
)
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
_SHA = re.compile(r"\b[0-9a-fA-F]{7,64}\b")
_TMP_PATH = re.compile(r"(?:[A-Za-z]:)?[/\\](?:tmp|temp|private/tmp)[/\\][^\s:]+", re.IGNORECASE)
_LINE_COLUMN = re.compile(r"(?<=:)(?:line\s*)?\d+(?::\d+)?\b", re.IGNORECASE)


def _iso(now: datetime | None = None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_text(value: str) -> tuple[str, list[str]]:
    """Редактировать известные секреты и прямой персональный идентификатор email."""
    text = str(value)
    findings: list[str] = []
    substitutions = (
        ("PRIVATE_KEY", _PRIVATE_KEY, "[REDACTED_PRIVATE_KEY]"),
        ("BEARER_TOKEN", _BEARER, "Bearer [REDACTED]"),
        ("KNOWN_TOKEN", _KNOWN_TOKEN, "[REDACTED_TOKEN]"),
        ("EMAIL", _EMAIL, "[REDACTED_EMAIL]"),
    )
    for code, pattern, replacement in substitutions:
        text, count = pattern.subn(replacement, text)
        findings.extend([code] * count)

    def redact_assignment(match: re.Match[str]) -> str:
        findings.append("ASSIGNED_SECRET")
        return f"{match.group(1)}{match.group(2)}[REDACTED]"

    text = _ASSIGNMENT_SECRET.sub(redact_assignment, text)
    return text, findings


def sanitize_value(value: Any, *, path: str = "$") -> tuple[Any, list[dict[str, str]]]:
    """Рекурсивно sanitise JSON-compatible значение, не изменяя входной объект."""
    findings: list[dict[str, str]] = []
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _SENSITIVE_KEY.search(key_text):
                output[key_text] = "[REDACTED]"
                findings.append({"path": child_path, "code": "SENSITIVE_FIELD"})
                continue
            cleaned, nested = sanitize_value(item, path=child_path)
            output[key_text] = cleaned
            findings.extend(nested)
        return output, findings
    if isinstance(value, list):
        output_list = []
        for index, item in enumerate(value):
            cleaned, nested = sanitize_value(item, path=f"{path}[{index}]")
            output_list.append(cleaned)
            findings.extend(nested)
        return output_list, findings
    if isinstance(value, str):
        cleaned, codes = sanitize_text(value)
        findings.extend({"path": path, "code": code} for code in codes)
        return cleaned, findings
    if value is None or isinstance(value, (bool, int, float)):
        return value, findings
    cleaned, codes = sanitize_text(str(value))
    findings.extend({"path": path, "code": code} for code in codes)
    return cleaned, findings


def _stable_text(value: Any) -> str:
    text, _ = sanitize_text(str(value or ""))
    text = _ANSI.sub("", text)
    text = _TIMESTAMP.sub("<timestamp>", text)
    text = _UUID.sub("<uuid>", text)
    text = _SHA.sub("<revision>", text)
    text = _TMP_PATH.sub("<temp-path>", text)
    text = _LINE_COLUMN.sub("<line>", text)
    return " ".join(text.casefold().split())[:1000]


def stable_fingerprint(failure: dict[str, Any]) -> dict[str, Any]:
    """Получить стабильный fingerprint, исключив volatile timestamps/SHA/temp paths."""
    frames = failure.get("stable_frames") or failure.get("frames") or []
    if not isinstance(frames, list):
        frames = [frames]
    components = {
        "failure_class": _stable_text(failure.get("failure_class") or failure.get("class") or "UNKNOWN"),
        "stage": _stable_text(failure.get("stage") or "UNKNOWN"),
        "tool": _stable_text(failure.get("tool") or "UNKNOWN"),
        "exit_code": failure.get("exit_code") if isinstance(failure.get("exit_code"), int) else None,
        "stable_frames": [_stable_text(item) for item in frames[:20] if _stable_text(item)],
        "symptom": _stable_text(failure.get("symptom") or failure.get("message") or "UNKNOWN"),
    }
    return {"algorithm": "sha256-v1", "hash": digest(components), "components": components}


def normalize_incident(raw: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Преобразовать внешнее сообщение в строгий sanitized incident record."""
    if not isinstance(raw, dict):
        raise ValueError("INCIDENT_NOT_OBJECT")
    cleaned, redactions = sanitize_value(copy.deepcopy(raw))
    severity = str(cleaned.get("severity", "SEV3")).upper()
    if severity not in SEVERITIES:
        raise ValueError("INCIDENT_SEVERITY_INVALID")
    status = str(cleaned.get("status", "NORMALIZED")).upper()
    if status not in INCIDENT_STATUSES:
        raise ValueError("INCIDENT_STATUS_INVALID")
    owner_summary = str(cleaned.get("owner_summary_ru") or "Обнаружена проблема; влияние уточняется.").strip()
    symptom = str(cleaned.get("symptom_original") or cleaned.get("symptom") or "Неизвестный симптом").strip()
    if len(owner_summary) < 10 or len(symptom) < 3:
        raise ValueError("INCIDENT_HUMAN_CONTEXT_MISSING")
    failure = cleaned.get("failure") if isinstance(cleaned.get("failure"), dict) else {}
    failure = dict(failure)
    failure.setdefault("symptom", symptom)
    fingerprint = stable_fingerprint(failure)
    supplied_classification = cleaned.get("classification")
    if isinstance(supplied_classification, dict):
        classification = {
            "type": str(supplied_classification.get("type") or "UNKNOWN").strip().upper(),
            "confidence": str(supplied_classification.get("confidence") or "NOT_VERIFIED").strip().upper(),
            "evidence": supplied_classification.get("evidence")
            if isinstance(supplied_classification.get("evidence"), list) else [],
        }
    else:
        classification = {"type": "UNKNOWN", "confidence": "NOT_VERIFIED", "evidence": []}
    observed_at = _iso(now)
    incident_id = str(cleaned.get("incident_id") or f"INC-{observed_at[:10].replace('-', '')}-{fingerprint['hash'][:12]}")
    if re.fullmatch(r"INC-[0-9]{8}-[0-9a-f]{12}(?:-[0-9]+)?", incident_id) is None:
        raise ValueError("INCIDENT_ID_INVALID")
    record = {
        "schema_version": 1,
        "incident_id": incident_id,
        "status": status,
        "severity": severity,
        "occurred_at": cleaned.get("occurred_at") or observed_at,
        "detected_at": cleaned.get("detected_at") or observed_at,
        "resolved_at": cleaned.get("resolved_at"),
        "source": cleaned.get("source") if isinstance(cleaned.get("source"), dict) else {},
        "subject": cleaned.get("subject") if isinstance(cleaned.get("subject"), dict) else {},
        "owner_summary_ru": owner_summary,
        "symptom_original": symptom,
        "impact": cleaned.get("impact") if isinstance(cleaned.get("impact"), dict) else {},
        "environment": cleaned.get("environment") if isinstance(cleaned.get("environment"), dict) else {},
        "fingerprint": fingerprint,
        "classification": classification,
        "redaction": {
            "status": "SANITIZED",
            "findings_count": len(redactions),
            "finding_codes": sorted({item["code"] for item in redactions}),
        },
        "attempts": cleaned.get("attempts") if isinstance(cleaned.get("attempts"), list) else [],
        "provenance": cleaned.get("provenance") if isinstance(cleaned.get("provenance"), dict) else {},
        "cost_usage": {"monetary_cost": 0, "ai_api_used": False},
    }
    serialized = canonical_json(record)
    if _PRIVATE_KEY.search(serialized) or _KNOWN_TOKEN.search(serialized) or _BEARER.search(serialized):
        raise ValueError("INCIDENT_SANITIZATION_FAILED")
    return record


def _event_hash(event_without_hash: dict[str, Any]) -> str:
    return digest(event_without_hash)


def _decode_events(lines: Iterable[str], *, verify_chain: bool = True) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    expected_sequence = 0
    for line in lines:
        if not line.strip():
            continue
        expected_sequence += 1
        try:
            event = strict_loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("INCIDENT_STORE_INVALID_JSON") from exc
        if not isinstance(event, dict):
            raise ValueError("INCIDENT_STORE_EVENT_NOT_OBJECT")
        supplied_hash = event.get("event_hash")
        unsigned = {key: value for key, value in event.items() if key != "event_hash"}
        if event.get("sequence") != expected_sequence:
            raise ValueError("INCIDENT_STORE_SEQUENCE_BROKEN")
        if verify_chain and event.get("previous_hash") != previous:
            raise ValueError("INCIDENT_STORE_CHAIN_BROKEN")
        if verify_chain and supplied_hash != _event_hash(unsigned):
            raise ValueError("INCIDENT_STORE_HASH_INVALID")
        previous = str(supplied_hash)
        events.append(event)
    return events


def read_incident_events(
    path: str | Path, *, verify_chain: bool = True,
    expected_tail_hash: str | None = None, expected_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """Read and verify the chain, optionally against an external SSOT anchor."""
    store = Path(path)
    if not store.exists():
        if expected_tail_hash not in {None, ZERO_HASH} or expected_sequence not in {None, 0}:
            raise ValueError("INCIDENT_STORE_ANCHOR_MISMATCH")
        return []
    events = _decode_events(store.read_text(encoding="utf-8").splitlines(), verify_chain=verify_chain)
    actual_tail = events[-1]["event_hash"] if events else ZERO_HASH
    if expected_tail_hash is not None and actual_tail != expected_tail_hash:
        raise ValueError("INCIDENT_STORE_TAIL_MISMATCH")
    if expected_sequence is not None and len(events) != expected_sequence:
        raise ValueError("INCIDENT_STORE_LENGTH_MISMATCH")
    return events


def _find_original(events: list[dict[str, Any]], fingerprint_hash: str) -> dict[str, Any] | None:
    for event in events:
        record = event.get("incident")
        if event.get("event_type") == "INCIDENT_RECORDED" and isinstance(record, dict):
            if record.get("fingerprint", {}).get("hash") == fingerprint_hash:
                return record
    return None


def _append_locked(path: Path, build_event) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("INCIDENT_STORE_SYMLINK_FORBIDDEN")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        events = _decode_events(path.read_text(encoding="utf-8").splitlines()) if path.exists() else []
        event = build_event(events)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


def record_incident(path: str | Path, raw: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Append normalized incident or a dedup occurrence; never rewrite existing lines."""
    record = normalize_incident(raw, now=now)
    observed_at = _iso(now)

    def build(events: list[dict[str, Any]]) -> dict[str, Any]:
        previous_hash = events[-1]["event_hash"] if events else ZERO_HASH
        original = _find_original(events, record["fingerprint"]["hash"])
        if not original and any(item.get("incident_id") == record["incident_id"] for item in events):
            raise ValueError("INCIDENT_ID_COLLISION")
        unsigned: dict[str, Any] = {
            "schema_version": 1,
            "sequence": len(events) + 1,
            "event_id": f"IEV-{len(events) + 1:08d}-{record['fingerprint']['hash'][:12]}",
            "event_type": "DUPLICATE_OBSERVED" if original else "INCIDENT_RECORDED",
            "observed_at": observed_at,
            "previous_hash": previous_hash,
            "incident_id": original["incident_id"] if original else record["incident_id"],
            "fingerprint_hash": record["fingerprint"]["hash"],
        }
        if original:
            unsigned["duplicate_observation"] = {
                "source": record["source"], "subject": record["subject"],
                "severity": record["severity"], "owner_summary_ru": record["owner_summary_ru"],
            }
        else:
            unsigned["incident"] = record
        return {**unsigned, "event_hash": _event_hash(unsigned)}

    event = _append_locked(Path(path), build)
    return {
        "result": "DEDUPLICATED" if event["event_type"] == "DUPLICATE_OBSERVED" else "RECORDED",
        "incident_id": event["incident_id"],
        "fingerprint_hash": event["fingerprint_hash"],
        "sequence": event["sequence"],
        "event_hash": event["event_hash"],
    }


def incident_store_summary(events_or_path: list[dict[str, Any]] | str | Path) -> dict[str, Any]:
    events = read_incident_events(events_or_path) if isinstance(events_or_path, (str, Path)) else events_or_path
    originals = [item for item in events if item.get("event_type") == "INCIDENT_RECORDED"]
    duplicates = [item for item in events if item.get("event_type") == "DUPLICATE_OBSERVED"]
    open_records = [
        item["incident"] for item in originals
        if item.get("incident", {}).get("status") not in {"RECOVERED", "CLOSED", "ROLLED_BACK"}
    ]
    return {
        "status": "VERIFIED",
        "incident_count": len(originals),
        "open_count": len(open_records),
        "repeated_count": len(duplicates),
        "latest_incident_id": originals[-1]["incident_id"] if originals else None,
        "store_digest": events[-1]["event_hash"] if events else ZERO_HASH,
    }
