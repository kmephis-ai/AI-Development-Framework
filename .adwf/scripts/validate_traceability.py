#!/usr/bin/env python3
"""Fail-closed validator for durable Decision/Requirement Traceability Graph v1."""
from __future__ import annotations

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.decision_traceability import validate_repository_traceability  # noqa: E402


def main() -> int:
    try:
        result = validate_repository_traceability(ROOT)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"DECISION/REQUIREMENT TRACEABILITY: FAIL:{type(exc).__name__}")
        return 1
    if not result["valid"]:
        print("DECISION/REQUIREMENT TRACEABILITY: FAIL")
        for error in result["errors"]:
            print("-", error)
        return 1
    projection = result["projection"]
    print("DECISION/REQUIREMENT TRACEABILITY: PASS")
    print("TRACE COVERAGE:", projection["status"])
    for key in ("orphan_requirements", "orphan_decisions", "orphan_work_units", "missing_downstream_evidence", "unverified_evidence_refs"):
        if projection[key]:
            print(f"- {key}:" + ",".join(projection[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
