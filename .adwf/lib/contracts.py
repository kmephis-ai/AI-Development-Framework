"""Детерминированная проверка JSON-контрактов ADWF без сетевых зависимостей.

Это намеренно небольшой, fail-closed subset JSON Schema Draft 2020-12. Он
поддерживает только ключевые слова, используемые схемами ADWF. Неизвестное
ключевое слово в ``RECOGNIZED_KEYWORDS`` не добавляется молча: сначала должны
появиться реализация и negative test.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import math
import re


@dataclass(frozen=True)
class ValidationFinding:
    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


SUPPORTED_KEYWORDS = {
    "$schema", "$id", "title", "description", "default", "examples",
    "type", "required", "properties", "additionalProperties", "enum",
    "const", "items", "minItems", "maxItems", "uniqueItems", "minLength",
    "maxLength", "minimum", "maximum", "pattern", "format", "anyOf",
    "oneOf", "allOf",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": _is_number(value),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _valid_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "T" in value
    except (TypeError, ValueError):
        return False


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[ValidationFinding]:
    """Проверить значение по используемому ADWF subset JSON Schema."""
    findings: list[ValidationFinding] = []
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    for keyword in unknown:
        findings.append(ValidationFinding(path, "unsupported_schema_keyword", keyword))

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not types or not all(isinstance(item, str) for item in types):
            findings.append(ValidationFinding(path, "invalid_schema_type", repr(expected)))
            return findings
        if not any(_matches_type(instance, item) for item in types):
            findings.append(ValidationFinding(path, "type", f"ожидалось {types}"))
            return findings

    if "const" in schema and instance != schema["const"]:
        findings.append(ValidationFinding(path, "const", f"ожидалось {schema['const']!r}"))
    if "enum" in schema and instance not in schema["enum"]:
        findings.append(ValidationFinding(path, "enum", f"значение {instance!r} не разрешено"))

    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        results = [validate(instance, branch, path) for branch in branches]
        passed = sum(not result for result in results)
        if keyword == "allOf":
            for result in results:
                findings.extend(result)
        elif keyword == "anyOf" and passed == 0:
            findings.append(ValidationFinding(path, "anyOf", "ни один вариант не прошёл"))
        elif keyword == "oneOf" and passed != 1:
            findings.append(ValidationFinding(path, "oneOf", f"прошло вариантов: {passed}, требуется 1"))

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                findings.append(ValidationFinding(f"{path}.{key}", "required", "обязательное поле отсутствует"))
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                findings.extend(validate(value, properties[key], child_path))
            elif schema.get("additionalProperties") is False:
                findings.append(ValidationFinding(child_path, "additionalProperties", "неизвестное поле запрещено"))
            elif isinstance(schema.get("additionalProperties"), dict):
                findings.extend(validate(value, schema["additionalProperties"], child_path))

    if isinstance(instance, list):
        if len(instance) < int(schema.get("minItems", 0)):
            findings.append(ValidationFinding(path, "minItems", "слишком мало элементов"))
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            findings.append(ValidationFinding(path, "maxItems", "слишком много элементов"))
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(canonical) != len(set(canonical)):
                findings.append(ValidationFinding(path, "uniqueItems", "найдены дубликаты"))
        if isinstance(schema.get("items"), dict):
            for index, value in enumerate(instance):
                findings.extend(validate(value, schema["items"], f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            findings.append(ValidationFinding(path, "minLength", "строка слишком короткая"))
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            findings.append(ValidationFinding(path, "maxLength", "строка слишком длинная"))
        if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
            findings.append(ValidationFinding(path, "pattern", "строка не соответствует шаблону"))
        if "format" in schema and schema.get("format") not in {"date-time", "sha256"}:
            findings.append(ValidationFinding(path, "unsupported_format", str(schema.get("format"))))
        if schema.get("format") == "date-time" and not _valid_datetime(instance):
            findings.append(ValidationFinding(path, "format", "требуется RFC 3339 date-time"))
        if schema.get("format") == "sha256" and re.fullmatch(r"[0-9a-f]{64}", instance) is None:
            findings.append(ValidationFinding(path, "format", "требуется SHA-256 в lower-case hex"))

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            findings.append(ValidationFinding(path, "minimum", f"минимум {schema['minimum']}"))
        if "maximum" in schema and instance > schema["maximum"]:
            findings.append(ValidationFinding(path, "maximum", f"максимум {schema['maximum']}"))
    return findings


def validate_files(data_path: str | Path, schema_path: str | Path) -> list[ValidationFinding]:
    data = strict_loads(Path(data_path).read_text(encoding="utf-8"))
    schema = strict_loads(Path(schema_path).read_text(encoding="utf-8"))
    return validate(data, schema)
