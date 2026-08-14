"""Strict JSON loader for trusted ADWF paths.

Duplicate object keys are rejected instead of silently keeping the last value.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json


class DuplicateKeyError(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_pairs)


def load(path: str | Path) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"))
