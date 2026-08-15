"""First-class bounded AI work contracts for creative executor handoffs.

AIWorkPackage is trusted control data compiled from the durable run/work memory.
AIWorkResult is an immutable canonicalization of a low-trust creative claim; it
never becomes PASS evidence by itself. Downstream trusted provider/evidence
verification remains authoritative.
"""
from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any
import hashlib
import json
import re

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_ID_RE = re.compile(r"^AIWP-[0-9a-f]{24}$")
RESULT_ID_RE = re.compile(r"^AIWR-[0-9a-f]{24}$")
CREATIVE_PHASES = {"EXECUTE", "RECOVERY"}
OUTCOMES = {"PASS", "FAIL", "HUMAN_REQUIRED", "RETRY"}
DEFAULT_ALLOWED_WRITE_SURFACES = ["**"]
DEFAULT_FORBIDDEN_WRITE_SURFACES = [".git/**", ".adwf-runtime/**"]
DEFAULT_REQUIRED_EVIDENCE = ["changed_paths", "verification_claims"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _without(value: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in names}


def _safe_pattern(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    if value.startswith(("/", "\\")) or "\\" in value:
        return False
    return not any(part == ".." for part in value.split("/"))


def _safe_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    if value.startswith(("/", "\\")) or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _package_id(package: dict[str, Any]) -> str:
    identity = {
        "run_id": package.get("run_id"),
        "roadmap_id": package.get("roadmap_id"),
        "issue_id": package.get("issue_id"),
        "revision": package.get("revision"),
        "phase": package.get("phase"),
        "base_sha": package.get("base_sha"),
    }
    return "AIWP-" + _digest(identity)[:24]


def _result_id(result: dict[str, Any]) -> str:
    identity = {
        "package_digest": result.get("package_digest"),
        "outcome": result.get("outcome"),
        "head_sha": result.get("head_sha"),
        "created_at": result.get("created_at"),
    }
    return "AIWR-" + _digest(identity)[:24]


def _goal_and_acceptance(work_memory: dict[str, Any] | None) -> tuple[str, list[str], list[str]]:
    memory = work_memory or {}
    brief = memory.get("product_brief") if isinstance(memory.get("product_brief"), dict) else {}
    goal = str(brief.get("outcome_ru") or brief.get("goal_ru") or memory.get("task_ru") or "Выполнить ограниченный AI work package.").strip()
    if len(goal) < 10:
        goal = "Выполнить ограниченный AI work package: " + goal
    acceptance = _strings(brief.get("acceptance_criteria_ru")) or ["Результат соответствует исходному намерению владельца."]
    verification = _strings(memory.get("verification")) or ["Downstream trusted verification подтверждает exact head и требуемые gates."]
    return goal, acceptance, verification


def compile_work_package(
    state: dict[str, Any],
    work_memory: dict[str, Any] | None,
    *,
    allowed_write_surfaces: list[str] | None = None,
    forbidden_write_surfaces: list[str] | None = None,
    required_evidence: list[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Compile one immutable package from trusted durable runtime facts.

    Broad compatibility is explicit: repository content is allowed by default,
    while Git internals and runtime state are always excluded. Project-specific
    callers may narrow the positive allowlist further. Forbidden always wins.
    """
    base_sha = str(state.get("subject_sha") or "")
    if SHA_RE.fullmatch(base_sha) is None:
        raise ValueError("AI_WORK_PACKAGE_BASE_SHA_REQUIRED")
    phase = str(state.get("phase") or "")
    if phase not in CREATIVE_PHASES:
        raise ValueError("AI_WORK_PACKAGE_PHASE_NOT_CREATIVE")
    goal, acceptance, verification = _goal_and_acceptance(work_memory)
    state_allowed = state.get("allowed_write_surfaces") if isinstance(state.get("allowed_write_surfaces"), list) else None
    state_forbidden = state.get("forbidden_write_surfaces") if isinstance(state.get("forbidden_write_surfaces"), list) else None
    state_evidence = state.get("required_evidence") if isinstance(state.get("required_evidence"), list) else None
    allowed = list(allowed_write_surfaces if allowed_write_surfaces is not None else (state_allowed if state_allowed is not None else DEFAULT_ALLOWED_WRITE_SURFACES))
    forbidden = list(forbidden_write_surfaces if forbidden_write_surfaces is not None else (state_forbidden if state_forbidden is not None else DEFAULT_FORBIDDEN_WRITE_SURFACES))
    evidence = list(required_evidence if required_evidence is not None else (state_evidence if state_evidence is not None else DEFAULT_REQUIRED_EVIDENCE))
    work_type = str(state.get("work_type") or "feature")
    conflicts = _strings(state.get("conflict_domains")) or [f"work:{work_type}"]
    package: dict[str, Any] = {
        "$schema": ".adwf/schemas/ai-work-package.schema.json",
        "schema_version": 1,
        "package_id": "",
        "package_digest": "",
        "run_id": str(state.get("run_id") or ""),
        "roadmap_id": str(state.get("roadmap_id") or ""),
        "issue_id": str(state.get("issue_id") or ""),
        "revision": int(state.get("revision", -1)),
        "phase": phase,
        "work_type": work_type,
        "risk": str(state.get("risk") or ""),
        "base_sha": base_sha,
        "goal": goal,
        "acceptance_criteria": acceptance,
        "verification_plan": verification,
        "conflict_domains": conflicts,
        "allowed_write_surfaces": allowed,
        "forbidden_write_surfaces": forbidden,
        "required_evidence": evidence,
        "monetary_budget_usd": 0,
        "created_at": created_at or _now(),
    }
    package["package_id"] = _package_id(package)
    package["package_digest"] = _digest(_without(package, "package_digest"))
    errors = validate_work_package(package, expected_state=state)
    if errors:
        raise ValueError("AI_WORK_PACKAGE_INVALID:" + ",".join(errors))
    return package


def validate_work_package(package: dict[str, Any], *, expected_state: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    required = {
        "$schema", "schema_version", "package_id", "package_digest", "run_id", "roadmap_id", "issue_id", "revision",
        "phase", "work_type", "risk", "base_sha", "goal", "acceptance_criteria", "verification_plan", "conflict_domains",
        "allowed_write_surfaces", "forbidden_write_surfaces", "required_evidence", "monetary_budget_usd", "created_at",
    }
    if set(package) != required:
        errors.append("PACKAGE_FIELDS_INVALID")
    if package.get("schema_version") != 1:
        errors.append("PACKAGE_SCHEMA_VERSION")
    if PACKAGE_ID_RE.fullmatch(str(package.get("package_id") or "")) is None:
        errors.append("PACKAGE_ID_INVALID")
    if DIGEST_RE.fullmatch(str(package.get("package_digest") or "")) is None:
        errors.append("PACKAGE_DIGEST_INVALID")
    if SHA_RE.fullmatch(str(package.get("base_sha") or "")) is None:
        errors.append("PACKAGE_BASE_SHA_INVALID")
    if package.get("phase") not in CREATIVE_PHASES:
        errors.append("PACKAGE_PHASE_INVALID")
    if package.get("risk") not in {"R0", "R1", "R2", "R3", "R4"}:
        errors.append("PACKAGE_RISK_INVALID")
    if package.get("work_type") not in {"feature", "bug", "recovery", "verification", "governance", "docs", "migration"}:
        errors.append("PACKAGE_WORK_TYPE_INVALID")
    if package.get("$schema") != ".adwf/schemas/ai-work-package.schema.json":
        errors.append("PACKAGE_SCHEMA_REF_INVALID")
    if not _valid_timestamp(package.get("created_at")):
        errors.append("PACKAGE_CREATED_AT_INVALID")
    if not str(package.get("run_id") or "") or not str(package.get("roadmap_id") or "") or not str(package.get("issue_id") or ""):
        errors.append("PACKAGE_IDENTITY_MISSING")
    if not isinstance(package.get("revision"), int) or isinstance(package.get("revision"), bool) or package.get("revision", -1) < 0:
        errors.append("PACKAGE_REVISION_INVALID")
    if len(str(package.get("goal") or "").strip()) < 10:
        errors.append("PACKAGE_GOAL_INVALID")
    for field in ("acceptance_criteria", "verification_plan", "conflict_domains", "required_evidence"):
        values = package.get(field)
        if not isinstance(values, list) or not values or len(values) != len(set(values)) or not all(isinstance(item, str) and item.strip() for item in values):
            errors.append(f"PACKAGE_{field.upper()}_INVALID")
    allowed = package.get("allowed_write_surfaces")
    forbidden = package.get("forbidden_write_surfaces")
    if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)) or not all(_safe_pattern(item) for item in allowed):
        errors.append("PACKAGE_ALLOWED_SURFACES_INVALID")
    if not isinstance(forbidden, list) or len(forbidden) != len(set(forbidden)) or not all(_safe_pattern(item) for item in forbidden):
        errors.append("PACKAGE_FORBIDDEN_SURFACES_INVALID")
    if isinstance(allowed, list) and isinstance(forbidden, list) and set(allowed).intersection(forbidden):
        errors.append("PACKAGE_SURFACE_RULE_CONFLICT")
    if package.get("monetary_budget_usd") != 0:
        errors.append("PACKAGE_COST_NOT_FREE_ONLY")
    if package.get("package_id") != _package_id(package):
        errors.append("PACKAGE_ID_MISMATCH")
    if package.get("package_digest") != _digest(_without(package, "package_digest")):
        errors.append("PACKAGE_DIGEST_MISMATCH")
    if expected_state is not None:
        bindings = {
            "run_id": expected_state.get("run_id"),
            "roadmap_id": expected_state.get("roadmap_id"),
            "issue_id": str(expected_state.get("issue_id") or ""),
            "revision": expected_state.get("revision"),
            "phase": expected_state.get("phase"),
            "work_type": expected_state.get("work_type"),
            "risk": expected_state.get("risk"),
            "base_sha": expected_state.get("subject_sha"),
        }
        for field, expected in bindings.items():
            if package.get(field) != expected:
                errors.append("PACKAGE_STATE_BINDING_MISMATCH:" + field)
    return list(dict.fromkeys(errors))


def path_is_allowed(path: str, package: dict[str, Any]) -> bool:
    if not _safe_path(path):
        return False
    allowed = any(fnmatchcase(path, pattern) for pattern in package.get("allowed_write_surfaces") or [])
    forbidden = any(fnmatchcase(path, pattern) for pattern in package.get("forbidden_write_surfaces") or [])
    return allowed and not forbidden


def build_work_result(
    package: dict[str, Any],
    *,
    outcome: str,
    head_sha: str | None,
    changed_paths: list[str] | None = None,
    verification_claims: list[str] | None = None,
    evidence_claims: list[str] | None = None,
    reason_codes: list[str] | None = None,
    summary_ru: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    package_errors = validate_work_package(package)
    if package_errors:
        raise ValueError("AI_WORK_PACKAGE_INVALID:" + ",".join(package_errors))
    result: dict[str, Any] = {
        "$schema": ".adwf/schemas/ai-work-result.schema.json",
        "schema_version": 1,
        "result_id": "",
        "result_digest": "",
        "package_id": package["package_id"],
        "package_digest": package["package_digest"],
        "run_id": package["run_id"],
        "roadmap_id": package["roadmap_id"],
        "issue_id": package["issue_id"],
        "phase": package["phase"],
        "base_sha": package["base_sha"],
        "outcome": str(outcome),
        "head_sha": head_sha,
        "changed_paths": list(changed_paths or []),
        "verification_claims": list(verification_claims or []),
        "evidence_claims": list(evidence_claims or []),
        "reason_codes": list(reason_codes or []),
        "summary_ru": str(summary_ru or "")[:1000],
        "cost_usd": 0,
        "created_at": created_at or _now(),
    }
    result["result_id"] = _result_id(result)
    result["result_digest"] = _digest(_without(result, "result_digest"))
    errors = validate_work_result(result, package=package)
    if errors:
        raise ValueError("AI_WORK_RESULT_INVALID:" + ",".join(errors))
    return result


def validate_work_result(result: dict[str, Any], *, package: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    package_errors = validate_work_package(package)
    if package_errors:
        return ["RESULT_PACKAGE_INVALID:" + item for item in package_errors]
    required = {
        "$schema", "schema_version", "result_id", "result_digest", "package_id", "package_digest", "run_id", "roadmap_id",
        "issue_id", "phase", "base_sha", "outcome", "head_sha", "changed_paths", "verification_claims", "evidence_claims",
        "reason_codes", "summary_ru", "cost_usd", "created_at",
    }
    if set(result) != required:
        errors.append("RESULT_FIELDS_INVALID")
    if result.get("schema_version") != 1:
        errors.append("RESULT_SCHEMA_VERSION")
    if result.get("$schema") != ".adwf/schemas/ai-work-result.schema.json":
        errors.append("RESULT_SCHEMA_REF_INVALID")
    if not _valid_timestamp(result.get("created_at")):
        errors.append("RESULT_CREATED_AT_INVALID")
    for field in ("package_id", "package_digest", "run_id", "roadmap_id", "issue_id", "phase", "base_sha"):
        if result.get(field) != package.get(field):
            errors.append("RESULT_PACKAGE_BINDING_MISMATCH:" + field)
    if RESULT_ID_RE.fullmatch(str(result.get("result_id") or "")) is None:
        errors.append("RESULT_ID_INVALID")
    if DIGEST_RE.fullmatch(str(result.get("result_digest") or "")) is None:
        errors.append("RESULT_DIGEST_INVALID")
    if result.get("outcome") not in OUTCOMES:
        errors.append("RESULT_OUTCOME_INVALID")
    head_sha = result.get("head_sha")
    if head_sha is not None and SHA_RE.fullmatch(str(head_sha)) is None:
        errors.append("RESULT_HEAD_SHA_INVALID")
    changed = result.get("changed_paths")
    if not isinstance(changed, list) or len(changed) != len(set(changed)) or not all(_safe_path(item) for item in changed):
        errors.append("RESULT_CHANGED_PATHS_INVALID")
        changed = []
    for path in changed:
        if not path_is_allowed(path, package):
            errors.append("RESULT_WRITE_SURFACE_FORBIDDEN:" + path)
    for field in ("verification_claims", "evidence_claims", "reason_codes"):
        values = result.get(field)
        if not isinstance(values, list) or len(values) != len(set(values)) or not all(isinstance(item, str) and item.strip() for item in values):
            errors.append(f"RESULT_{field.upper()}_INVALID")
    if result.get("cost_usd") != 0:
        errors.append("RESULT_COST_NOT_FREE_ONLY")
    if result.get("outcome") == "PASS":
        if SHA_RE.fullmatch(str(head_sha or "")) is None:
            errors.append("RESULT_PASS_HEAD_SHA_REQUIRED")
        if package.get("phase") == "EXECUTE" and not changed:
            errors.append("RESULT_PASS_CHANGED_PATHS_REQUIRED")
        if changed and head_sha == package.get("base_sha"):
            errors.append("RESULT_PASS_HEAD_EQUALS_BASE")
        if not result.get("verification_claims"):
            errors.append("RESULT_PASS_VERIFICATION_CLAIMS_REQUIRED")
        missing = sorted(set(package.get("required_evidence") or []) - set(result.get("evidence_claims") or []))
        if missing:
            errors.append("RESULT_REQUIRED_EVIDENCE_CLAIM_MISSING:" + ",".join(missing))
    if result.get("result_id") != _result_id(result):
        errors.append("RESULT_ID_MISMATCH")
    if result.get("result_digest") != _digest(_without(result, "result_digest")):
        errors.append("RESULT_DIGEST_MISMATCH")
    return list(dict.fromkeys(errors))


def canonicalize_low_trust_claim(claim: dict[str, Any], *, package: dict[str, Any]) -> dict[str, Any]:
    """Turn a bounded agent claim into an immutable AIWorkResult claim.

    This validates scope/binding only. The returned object remains LOW_TRUST and
    must not be used as CI/review/provider PASS evidence.
    """
    for field in ("package_id", "package_digest", "base_sha"):
        if claim.get(field) != package.get(field):
            raise ValueError("AGENT_RESULT_PACKAGE_BINDING_MISMATCH:" + field)
    head = claim.get("head_sha", claim.get("subject_sha"))
    return build_work_result(
        package,
        outcome=str(claim.get("outcome") or ""),
        head_sha=head,
        changed_paths=claim.get("changed_paths") if isinstance(claim.get("changed_paths"), list) else [],
        verification_claims=claim.get("verification_claims") if isinstance(claim.get("verification_claims"), list) else [],
        evidence_claims=claim.get("evidence_claims") if isinstance(claim.get("evidence_claims"), list) else [],
        reason_codes=claim.get("reason_codes") if isinstance(claim.get("reason_codes"), list) else [],
        summary_ru=str(claim.get("summary_ru") or ""),
        created_at=_now(),
    )
