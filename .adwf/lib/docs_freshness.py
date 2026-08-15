"""Связь human-facing документов с точными source digests."""
from __future__ import annotations
from .strict_json import loads as strict_loads

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

from .contracts import validate
from .evidence import parse_time

EXCLUDED = {".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt", ".adwf/effective-policy.json"}
EXCLUDED_PARTS = {".git", ".adwf-runtime", "__pycache__"}


def _safe_pattern(pattern: Any) -> str:
    if not isinstance(pattern, str) or not pattern or "\\" in pattern:
        raise ValueError("invalid watched pattern")
    candidate = Path(pattern)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("watched pattern must stay inside repository")
    return pattern


def _included(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        relative = path.relative_to(root).as_posix()
    except (OSError, ValueError):
        raise ValueError("watched path escapes repository")
    return (
        path.is_file()
        and relative not in EXCLUDED
        and not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
        and path.suffix != ".pyc"
    )


def files_for_pattern(root: str | Path, pattern: str) -> list[Path]:
    """Expand one watch pattern; a terminal ``/**`` means all nested files."""
    base = Path(root).resolve()
    normalized = _safe_pattern(pattern)
    if normalized == "**":
        candidates = base.rglob("*")
    elif normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        directory = base / prefix
        candidates = directory.rglob("*") if directory.is_dir() else iter(())
    else:
        candidates = base.glob(normalized)
    result: set[Path] = set()
    for path in candidates:
        if _included(base, path):
            result.add(path)
    return sorted(result, key=lambda item: item.relative_to(base).as_posix())


def _files(root: Path, patterns: list[str]) -> list[Path]:
    result: set[Path] = set()
    for pattern in patterns:
        result.update(files_for_pattern(root, pattern))
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def source_digest(root: str | Path, patterns: list[str]) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for path in _files(base, patterns):
        relative = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        raw = path.read_bytes()
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def check_docs(root: str | Path, *, now: datetime | None = None) -> list[str]:
    base = Path(root).resolve()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        registry = strict_loads((base / ".adwf/docs-registry.json").read_text(encoding="utf-8"))
        schema = strict_loads((base / ".adwf/schemas/docs-registry.schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"DOCS_REGISTRY_UNREADABLE:{type(exc).__name__}"]
    errors = [f"DOCS_SCHEMA:{item.path}:{item.code}" for item in validate(registry, schema)]
    for item in registry.get("documents", []):
        path = str(item.get("path", ""))
        if not (base / path).is_file():
            errors.append(f"DOCUMENT_MISSING:{path}")
            continue
        watched = item.get("watched", [])
        try:
            for pattern in watched:
                if not files_for_pattern(base, pattern):
                    errors.append(f"DOCUMENT_WATCH_EMPTY:{path}:{pattern}")
            if item.get("source_digest") != source_digest(base, watched):
                errors.append(f"DOCUMENT_STALE:{path}")
        except (TypeError, ValueError):
            errors.append(f"DOCUMENT_WATCH_INVALID:{path}")
        try:
            reviewed, valid_until = parse_time(item["reviewed_at"]), parse_time(item["valid_until"])
            if reviewed > now or valid_until <= now or reviewed >= valid_until:
                errors.append(f"DOCUMENT_REVIEW_EXPIRED:{path}")
        except (KeyError, ValueError):
            errors.append(f"DOCUMENT_REVIEW_TIME_INVALID:{path}")
    return errors


def updated_registry(root: str | Path, reviewed_at: str, valid_until: str) -> dict[str, Any]:
    base = Path(root).resolve()
    reviewed, expires = parse_time(reviewed_at), parse_time(valid_until)
    if reviewed >= expires:
        raise ValueError("documentation review interval is invalid")
    registry = strict_loads((base / ".adwf/docs-registry.json").read_text(encoding="utf-8"))
    for item in registry.get("documents", []):
        watched = item.get("watched", [])
        if not watched or any(not files_for_pattern(base, pattern) for pattern in watched):
            raise ValueError(f"empty watched source for {item.get('path', '')}")
        item["source_digest"] = source_digest(base, watched)
        item["reviewed_at"] = reviewed_at
        item["valid_until"] = valid_until
    return registry
