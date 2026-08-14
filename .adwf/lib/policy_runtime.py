"""Загрузка и проверка исполняемой Effective Policy ADWF v1.6."""
from __future__ import annotations
from .strict_json import loads as strict_loads

from pathlib import Path
from typing import Any
import hashlib
import json

from .policy import Decision, DecisionContext, evaluate_permission


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_policy_ir(policy: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if not isinstance(policy, dict):
        return ["POLICY_IR_NOT_OBJECT"]
    actual = policy.get("policy_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        findings.append("POLICY_HASH_INVALID")
    unsigned = dict(policy)
    unsigned.pop("policy_hash", None)
    expected = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if actual != expected:
        findings.append("POLICY_HASH_MISMATCH")
    if policy.get("schema_version") != 2:
        findings.append("POLICY_SCHEMA_VERSION_INVALID")
    if not isinstance(policy.get("rules"), dict):
        findings.append("POLICY_RULES_MISSING")
    return findings


def load_effective_policy(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve() / ".adwf/effective-policy.json"
    try:
        policy = strict_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"POLICY_IR_UNREADABLE:{type(exc).__name__}") from exc
    findings = verify_policy_ir(policy)
    if findings:
        raise ValueError("POLICY_IR_INVALID:" + ",".join(findings))
    return policy


def evaluate_with_effective_policy(root: str | Path, context: DecisionContext) -> Decision:
    try:
        policy = load_effective_policy(root)
    except ValueError:
        return Decision("BLOCK", ("POLICY_IR_INVALID",), "Исполняемая политика не прошла проверку.", None)
    return evaluate_permission(context, policy)
