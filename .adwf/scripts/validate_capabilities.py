#!/usr/bin/env python3
"""Fail-closed Capability Truth Model v2 validator.

`.adwf/capability-traceability.json` is the canonical capability truth source.
`.adwf/capabilities.json` remains a cost/implementation summary and must not
claim live verification.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.contracts import validate  # noqa: E402
from lib.strict_json import load  # noqa: E402

TRUTH_STATUSES = {
    "NOT_DESIGNED", "DESIGNED_ONLY", "PARTIAL", "IMPLEMENTED",
    "LIVE_NOT_VERIFIED", "LIVE_VERIFIED", "DEPRECATED", "BLOCKED",
}
IMPLEMENTED_STATES = {"PARTIAL", "IMPLEMENTED", "LIVE_NOT_VERIFIED", "LIVE_VERIFIED"}
LIVE_REF = re.compile(r"^(?:provider|runtime|github|evidence):[A-Za-z0-9._:/#-]+$")


def _path_exists(value: str) -> bool:
    path = ROOT / value
    return path.is_file() or path.is_dir()


def main() -> int:
    errors: list[str] = []

    summary = load(ROOT / ".adwf/capabilities.json")
    summary_schema = load(ROOT / ".adwf/schemas/capability.schema.json")
    if summary.get("truth_model_version") != 2:
        errors.append("CAPABILITY_SUMMARY_TRUTH_MODEL_VERSION_INVALID")
    if summary.get("canonical_truth") != ".adwf/capability-traceability.json":
        errors.append("CAPABILITY_CANONICAL_TRUTH_POINTER_INVALID")
    if summary.get("catalog_role") != "COST_AND_IMPLEMENTATION_SUMMARY":
        errors.append("CAPABILITY_SUMMARY_ROLE_INVALID")
    for cap in summary.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        errors.extend(f"SUMMARY_SCHEMA:{cid}:{item.path}:{item.code}" for item in validate(cap, summary_schema))
        if "state" in cap:
            errors.append("CAPABILITY_LEGACY_STATE_FORBIDDEN:" + cid)
        if str(cap.get("implementation_status") or "") in {"LIVE_NOT_VERIFIED", "LIVE_VERIFIED"}:
            errors.append("CAPABILITY_SUMMARY_LIVE_STATUS_FORBIDDEN:" + cid)

    trace = load(ROOT / ".adwf/capability-traceability.json")
    schema = load(ROOT / ".adwf/schemas/capability-traceability.schema.json")
    errors.extend(f"SCHEMA:{item.path}:{item.code}" for item in validate(trace, schema))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if trace.get("framework_version") != version:
        errors.append("CAPABILITY_VERSION_DRIFT")
    if trace.get("schema_version") != 2 or trace.get("truth_model_version") != 2:
        errors.append("CAPABILITY_TRUTH_MODEL_VERSION_INVALID")
    if trace.get("role") != "CANONICAL_CAPABILITY_TRUTH":
        errors.append("CAPABILITY_TRUTH_ROLE_INVALID")

    seen: set[str] = set()
    for cap in trace.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if cid in seen:
            errors.append("CAPABILITY_DUPLICATE:" + cid)
        seen.add(cid)
        status = str(cap.get("status") or "")
        if status not in TRUTH_STATUSES:
            errors.append("CAPABILITY_STATUS_INVALID:" + cid)
        for field in ("entrypoints", "production_paths", "verification"):
            values = cap.get(field) or []
            if status in IMPLEMENTED_STATES and not values:
                errors.append(f"CAPABILITY_{field.upper()}_EMPTY:{cid}")
            for rel in values:
                if not _path_exists(str(rel)):
                    errors.append(f"CAPABILITY_PATH_MISSING:{cid}:{rel}")
        live_boundary = str(cap.get("live_boundary") or "").strip()
        live_evidence = cap.get("live_evidence") or []
        if status in {"LIVE_NOT_VERIFIED", "LIVE_VERIFIED"} and not live_boundary:
            errors.append("CAPABILITY_LIVE_BOUNDARY_MISSING:" + cid)
        if status == "LIVE_VERIFIED":
            if not live_evidence:
                errors.append("CAPABILITY_LIVE_EVIDENCE_MISSING:" + cid)
            for ref in live_evidence:
                value = str(ref)
                if not LIVE_REF.fullmatch(value):
                    errors.append("CAPABILITY_LIVE_EVIDENCE_INVALID:" + cid + ":" + value)
                if value.startswith(".adwf/") or "test_" in value:
                    errors.append("CAPABILITY_TEST_EVIDENCE_CANNOT_BE_LIVE:" + cid)
        elif live_evidence:
            errors.append("CAPABILITY_LIVE_EVIDENCE_WITHOUT_LIVE_VERIFIED:" + cid)

    required = {
        "TRUSTED_GATE", "DURABLE_FULL_LOOP", "OWNER_WAKEUP_CONTINUE", "SINGLE_SSOT",
        "ACTIVE_TASK_IDENTITY", "EXACT_SHA_PREVIEW", "TRANSACTIONAL_AUTO_RELEASE",
        "PROJECT_PACKS", "PUBLIC_SAFE_RUNTIME_LEDGER", "RULESET_READBACK",
        "PIPELINE_IR_GENERATION", "PERFORMANCE_PLANE", "AGENT_RETURN_WAKEUP",
        "DELIVERY_OBSERVATION", "WINDOWS_HOSTED_SMOKE",
    }
    if not required.issubset(seen):
        errors.append("CAPABILITY_REQUIRED_MISSING:" + ",".join(sorted(required - seen)))

    control = (ROOT / ".github/workflows/adwf-control.yml").read_text(encoding="utf-8")
    for needle in (
        "publish_trusted_gate.py", "run_active_supervisor.py", "consume_agent_result.py",
        "github_runtime_sync.py", "github_metrics_collector.py",
    ):
        if needle not in control:
            errors.append("CAPABILITY_PRODUCTION_WIRING_MISSING:" + needle)
    if "orchestrate_event.py" in control:
        errors.append("CAPABILITY_LEGACY_ORCHESTRATOR_STILL_WIRED")

    if errors:
        print("CAPABILITY TRUTH: FAIL")
        for error in errors:
            print("-", error)
        return 1
    print("CAPABILITY TRUTH: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
