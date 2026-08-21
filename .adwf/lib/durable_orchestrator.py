"""Durable provider-neutral orchestration loop ADWF v1.6.

Модуль не исполняет произвольный код и не притворяется живым GitHub daemon.
Он является детерминированным ядром: каждый внешний adapter сообщает результат
одного шага, ядро заново авторизует действие через Effective Policy, проверяет
exact-SHA/evidence/cost, пишет tamper-evident journal и выбирает ровно следующий
шаг. После restart состояние полностью восстанавливается из journal.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid

from .policy import DecisionContext
from .policy_runtime import evaluate_with_effective_policy, load_effective_policy


PHASES = (
    "RECONCILE", "AUTHORIZE", "CLAIM", "WORKSPACE", "EXECUTE", "OPEN_PR",
    "CI", "REVIEW", "PREVIEW", "OWNER_ACCEPTANCE", "MERGE", "PROMOTE",
    "OBSERVE", "DONE", "CLEANUP", "NEXT", "RECOVERY",
)
ACTION_BY_PHASE = {
    "RECONCILE": "reconcile",
    "AUTHORIZE": "plan",
    "CLAIM": "claim",
    "WORKSPACE": "edit",
    "EXECUTE": "edit",
    "OPEN_PR": "open_pr",
    "CI": "test",
    "REVIEW": "review",
    "PREVIEW": "verify",
    "OWNER_ACCEPTANCE": "owner_accept",
    "MERGE": "merge",
    "PROMOTE": "promote",
    "OBSERVE": "observe",
    "DONE": "close_issue",
    "CLEANUP": "cleanup",
    "NEXT": "reconcile",
    "RECOVERY": "repair",
}
NEXT_PHASE = {
    "RECONCILE": "AUTHORIZE",
    "AUTHORIZE": "CLAIM",
    "CLAIM": "WORKSPACE",
    "WORKSPACE": "EXECUTE",
    "EXECUTE": "OPEN_PR",
    "OPEN_PR": "CI",
    "CI": "REVIEW",
    "REVIEW": "PREVIEW",
    "PREVIEW": "OWNER_ACCEPTANCE",
    "OWNER_ACCEPTANCE": "MERGE",
    "MERGE": "PROMOTE",
    "PROMOTE": "OBSERVE",
    "OBSERVE": "DONE",
    "DONE": "CLEANUP",
    "CLEANUP": "NEXT",
}
EVIDENCE_PHASES = {"CI", "REVIEW", "PREVIEW", "MERGE", "PROMOTE", "OBSERVE", "DONE"}
SHA_PHASES = {"OPEN_PR", "CI", "REVIEW", "PREVIEW", "OWNER_ACCEPTANCE", "MERGE", "PROMOTE", "OBSERVE", "DONE"}
OUTCOMES = {"PASS", "FAIL", "BLOCK", "HUMAN_REQUIRED", "RETRY", "CHANGES_REQUESTED", "ROADMAP_COMPLETE", "NEXT_READY"}
ACTIVE_STATUSES = {"RUNNING", "RETRY_WAIT", "RECOVERY", "HUMAN_REQUIRED"}
DELIVERY_RESULT_PHASES = {"PROMOTE", "OBSERVE", "DONE"}
DELIVERY_STATE_PHASES = {"PROMOTE", "OBSERVE", "DONE", "CLEANUP", "NEXT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _run_id(value: str | None = None) -> str:
    result = value or str(uuid.uuid4())
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{7,96}", result) is None:
        raise ValueError("RUN_ID_INVALID")
    return result


def _event_hash(event: dict[str, Any]) -> str:
    unsigned = dict(event)
    unsigned.pop("event_hash", None)
    return _digest(unsigned)


class OrchestrationJournal:
    def __init__(self, root: str | Path):
        self.directory = Path(root).resolve() / ".adwf-runtime" / "orchestration"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{_run_id(run_id)}.json"

    def load(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.is_file():
            raise ValueError("ORCHESTRATION_RUN_NOT_FOUND")
        value = strict_loads(path.read_text(encoding="utf-8"))
        findings = validate_journal(value)
        if findings:
            raise ValueError("ORCHESTRATION_JOURNAL_INVALID:" + ",".join(findings))
        return value

    def list_active(self) -> list[dict[str, Any]]:
        active = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                value = strict_loads(path.read_text(encoding="utf-8"))
                if not validate_journal(value) and value.get("status") in ACTIVE_STATUSES:
                    active.append(value)
            except (OSError, ValueError, json.JSONDecodeError):
                # Повреждённый journal не игнорируется при создании нового Writer.
                active.append({"run_id": path.stem, "status": "BROKEN"})
        return active

    def save(self, value: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        path = self._path(str(value.get("run_id", "")))
        lock_path = path.with_suffix(".lock")
        with exclusive_file_lock(lock_path):
            current = strict_loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if expected_revision is not None:
                actual = int((current or {}).get("revision", -1))
                if actual != expected_revision:
                    raise ValueError("ORCHESTRATION_REVISION_CONFLICT")
            payload = copy.deepcopy(value)
            payload["revision"] = int((current or {}).get("revision", -1)) + 1
            payload["updated_at"] = _now()
            fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return payload


def validate_journal(value: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if value.get("schema_version") != 1:
        findings.append("SCHEMA_VERSION")
    if value.get("phase") not in PHASES and value.get("status") != "COMPLETE":
        findings.append("PHASE")
    if float(value.get("monetary_budget_usd", -1)) != 0:
        findings.append("MONETARY_BUDGET")
    if not isinstance(value.get("events"), list):
        return findings + ["EVENTS"]
    previous = None
    for index, event in enumerate(value["events"]):
        if event.get("sequence") != index + 1:
            findings.append(f"EVENT_SEQUENCE:{index}")
        if event.get("previous_event_hash") != previous:
            findings.append(f"EVENT_CHAIN:{index}")
        if event.get("event_hash") != _event_hash(event):
            findings.append(f"EVENT_HASH:{index}")
        previous = event.get("event_hash")
    if value.get("event_head") != previous:
        findings.append("EVENT_HEAD")
    delivery_sha = value.get("delivery_sha")
    if delivery_sha is not None and (not isinstance(delivery_sha, str) or re.fullmatch(r"[0-9a-f]{40}", delivery_sha) is None):
        findings.append("DELIVERY_SHA")
    if value.get("phase") in DELIVERY_STATE_PHASES:
        if not isinstance(delivery_sha, str) or re.fullmatch(r"[0-9a-f]{40}", delivery_sha) is None:
            findings.append("DELIVERY_SHA_REQUIRED")
        elif value.get("subject_sha") != delivery_sha:
            findings.append("DELIVERY_SUBJECT_MISMATCH")
    return findings


def new_run(
    root: str | Path,
    *,
    roadmap_id: str,
    issue_id: str,
    risk: str,
    work_type: str,
    product_impact: bool,
    owner_request_digest: str,
    run_id: str | None = None,
    max_attempts: int = 3,
    max_cycles: int = 100,
    max_elapsed_minutes: int = 1440,
) -> dict[str, Any]:
    journal = OrchestrationJournal(root)
    active = journal.list_active()
    if active:
        raise ValueError("ACTIVE_OR_BROKEN_ORCHESTRATION_EXISTS")
    policy = load_effective_policy(root)
    created = datetime.now(timezone.utc)
    value = {
        "$schema": ".adwf/schemas/orchestration-run.schema.json",
        "schema_version": 1,
        "run_id": _run_id(run_id),
        "roadmap_id": roadmap_id,
        "issue_id": str(issue_id),
        "risk": risk,
        "work_type": work_type,
        "product_impact": bool(product_impact),
        "owner_request_digest": owner_request_digest,
        "phase": "RECONCILE",
        "status": "RUNNING",
        "cycle": 0,
        "subject_sha": None,
        "preview_digest": None,
        "owner_acceptance_sha": None,
        "delivery_sha": None,
        "pull_request_number": None,
        "preview_attestation_id": None,
        "work_branch": None,
        "policy_hash": policy["policy_hash"],
        "attempts": {},
        "max_attempts": max(0, min(int(max_attempts), 10)),
        "max_cycles": max(1, min(int(max_cycles), 1000)),
        "deadline_at": (created + timedelta(minutes=max(1, min(int(max_elapsed_minutes), 10080)))).isoformat().replace("+00:00", "Z"),
        "last_failed_phase": None,
        "blockers": [],
        "monetary_budget_usd": 0,
        "events": [],
        "event_head": None,
        "revision": 0,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": created.isoformat().replace("+00:00", "Z"),
    }
    return journal.save(value)


def _normalize_result(result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "phase", "outcome", "idempotency_key", "subject_sha", "preview_digest",
        "evidence_refs", "reason_codes", "transient", "cost_usd", "metadata",
    }
    unknown = set(result) - allowed
    if unknown:
        raise ValueError("UNKNOWN_STEP_RESULT_FIELDS:" + ",".join(sorted(unknown)))
    normalized = {
        "phase": result.get("phase"),
        "outcome": result.get("outcome"),
        "idempotency_key": result.get("idempotency_key"),
        "subject_sha": result.get("subject_sha"),
        "preview_digest": result.get("preview_digest"),
        "evidence_refs": list(result.get("evidence_refs") or []),
        "reason_codes": list(result.get("reason_codes") or []),
        "transient": result.get("transient") is True,
        "cost_usd": float(result.get("cost_usd", 0)),
        "metadata": dict(result.get("metadata") or {}),
    }
    if normalized["phase"] != state["phase"] or normalized["phase"] not in PHASES:
        raise ValueError("STEP_PHASE_MISMATCH")
    if normalized["outcome"] not in OUTCOMES:
        raise ValueError("STEP_OUTCOME_INVALID")
    if not isinstance(normalized["idempotency_key"], str) or len(normalized["idempotency_key"]) < 8:
        raise ValueError("STEP_IDEMPOTENCY_KEY_INVALID")
    if normalized["cost_usd"] != 0:
        raise ValueError("NON_ZERO_COST")
    if normalized["phase"] in SHA_PHASES:
        sha = normalized["subject_sha"] or state.get("subject_sha")
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError("EXACT_SHA_REQUIRED")
        normalized["subject_sha"] = sha
    if normalized["phase"] == "MERGE" and normalized["outcome"] == "PASS":
        merge_sha = normalized["metadata"].get("merge_sha")
        if merge_sha is None:
            raise ValueError("MERGE_SHA_REQUIRED")
        if not isinstance(merge_sha, str) or re.fullmatch(r"[0-9a-f]{40}", merge_sha) is None:
            raise ValueError("MERGE_SHA_INVALID")
    if normalized["phase"] in DELIVERY_RESULT_PHASES:
        delivery_sha = state.get("delivery_sha")
        if not isinstance(delivery_sha, str) or re.fullmatch(r"[0-9a-f]{40}", delivery_sha) is None:
            raise ValueError("DELIVERY_SHA_REQUIRED")
        if normalized["subject_sha"] != delivery_sha:
            raise ValueError("DELIVERY_SUBJECT_SHA_MISMATCH")
    if normalized["outcome"] == "PASS" and normalized["phase"] in EVIDENCE_PHASES:
        if not normalized["evidence_refs"] and not normalized["metadata"].get("not_applicable_reason"):
            raise ValueError("EVIDENCE_REQUIRED")
    if normalized["outcome"] == "RETRY" and not normalized["transient"]:
        raise ValueError("RETRY_REQUIRES_TRANSIENT_CLASSIFICATION")
    if normalized["outcome"] in {"ROADMAP_COMPLETE", "NEXT_READY"} and normalized["phase"] != "NEXT":
        raise ValueError("NEXT_OUTCOME_WRONG_PHASE")
    if normalized["outcome"] == "CHANGES_REQUESTED" and normalized["phase"] != "OWNER_ACCEPTANCE":
        raise ValueError("CHANGES_REQUESTED_WRONG_PHASE")
    if normalized["phase"] == "PREVIEW" and normalized["outcome"] == "PASS" and state.get("product_impact"):
        if not isinstance(normalized["preview_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", normalized["preview_digest"]) is None:
            raise ValueError("PREVIEW_DIGEST_REQUIRED")
    if normalized["phase"] == "OWNER_ACCEPTANCE" and normalized["outcome"] == "PASS":
        if normalized["metadata"].get("owner_acceptance_exact") is not True:
            raise ValueError("OWNER_ACCEPTANCE_NOT_EXACT")
        if normalized["metadata"].get("accepted_preview_digest") != state.get("preview_digest"):
            raise ValueError("OWNER_ACCEPTANCE_PREVIEW_MISMATCH")
    return normalized


def _control_context(state: dict[str, Any], result: dict[str, Any], supplied: dict[str, Any]) -> DecisionContext:
    value = dict(supplied)
    value.update({
        "action": ACTION_BY_PHASE[state["phase"]],
        "risk": state["risk"],
        "work_type": state["work_type"],
        "expected_policy_hash": state["policy_hash"],
        "projected_cost": result["cost_usd"],
    })
    if state["phase"] == "OWNER_ACCEPTANCE":
        value["human_approved"] = result["outcome"] == "PASS"
        value["owner_acceptance_exact"] = result["metadata"].get("owner_acceptance_exact") is True
    return DecisionContext.from_dict(value)


def _append_event(state: dict[str, Any], result: dict[str, Any], decision: dict[str, Any]) -> None:
    event = {
        "sequence": len(state["events"]) + 1,
        "event_id": str(uuid.uuid4()),
        "idempotency_key": result["idempotency_key"],
        "phase": state["phase"],
        "outcome": result["outcome"],
        "subject_sha": result["subject_sha"],
        "evidence_refs": result["evidence_refs"],
        "reason_codes": result["reason_codes"],
        "decision": decision,
        "cost_usd": result["cost_usd"],
        "metadata": copy.deepcopy(result.get("metadata") or {}),
        "occurred_at": _now(),
        "previous_event_hash": state.get("event_head"),
    }
    event["event_hash"] = _event_hash(event)
    state["events"].append(event)
    state["event_head"] = event["event_hash"]


def advance_run(
    root: str | Path,
    run_id: str,
    step_result: dict[str, Any],
    control_context: dict[str, Any] | None = None,
    *,
    trusted_context: DecisionContext | None = None,
) -> dict[str, Any]:
    journal = OrchestrationJournal(root)
    state = journal.load(run_id)
    if state["status"] in {"COMPLETE", "BLOCKED"}:
        return state
    try:
        if datetime.now(timezone.utc) > _parse_time(state["deadline_at"]):
            state["status"] = "BLOCKED"
            state["blockers"] = ["ORCHESTRATION_TIME_BUDGET_EXHAUSTED"]
            return journal.save(state, expected_revision=state["revision"])
    except (KeyError, TypeError, ValueError):
        state["status"] = "BLOCKED"
        state["blockers"] = ["ORCHESTRATION_DEADLINE_INVALID"]
        return journal.save(state, expected_revision=state["revision"])
    raw_key = step_result.get("idempotency_key")
    duplicate = next((event for event in state["events"] if event["idempotency_key"] == raw_key), None)
    if duplicate:
        if duplicate["phase"] != step_result.get("phase") or duplicate["outcome"] != step_result.get("outcome"):
            raise ValueError("STEP_IDEMPOTENCY_CONFLICT")
        return state
    result = _normalize_result(step_result, state)

    if trusted_context is not None:
        expected_action = ACTION_BY_PHASE[state["phase"]]
        operational_work_type = "recovery" if state["phase"] == "RECOVERY" else ("verification" if state["phase"] in {"CI","REVIEW","PREVIEW","OBSERVE"} else state["work_type"])
        if trusted_context.action != expected_action or trusted_context.risk != state["risk"] or trusted_context.work_type != operational_work_type:
            raise ValueError("TRUSTED_CONTEXT_RUN_BINDING_MISMATCH")
        if trusted_context.expected_policy_hash != state["policy_hash"]:
            raise ValueError("TRUSTED_CONTEXT_POLICY_MISMATCH")
        if float(trusted_context.projected_cost) != float(result["cost_usd"]):
            raise ValueError("TRUSTED_CONTEXT_COST_MISMATCH")
        context = trusted_context
    else:
        if control_context is None:
            raise ValueError("TRUSTED_OR_LEGACY_CONTEXT_REQUIRED")
        context = _control_context(state, result, control_context)
    decision = evaluate_with_effective_policy(root, context)
    decision_value = decision.to_dict()
    if decision.policy_hash != state["policy_hash"] and decision.result == "ALLOW":
        raise ValueError("ORCHESTRATION_POLICY_DRIFT")
    _append_event(state, result, decision_value)
    if decision.result != "ALLOW":
        state["status"] = decision.result
        state["blockers"] = list(decision.reason_codes)
        return journal.save(state, expected_revision=state["revision"])

    phase = state["phase"]
    outcome = result["outcome"]
    if result.get("subject_sha"):
        previous_sha = state.get("subject_sha")
        state["subject_sha"] = result["subject_sha"]
        if previous_sha and previous_sha != result["subject_sha"]:
            state["preview_digest"] = None
            state["owner_acceptance_sha"] = None
            state["preview_attestation_id"] = None
    metadata = result.get("metadata") or {}
    if phase == "RECONCILE" and metadata.get("issue_id") is not None:
        state["issue_id"] = str(metadata["issue_id"])
    if phase in {"CLAIM", "EXECUTE", "RECOVERY"} and metadata.get("branch"):
        branch=str(metadata["branch"])
        if not branch.startswith("adwf/") or len(branch)>180: raise ValueError("WORK_BRANCH_INVALID")
        state["work_branch"] = branch
    if phase == "OPEN_PR" and metadata.get("pull_request_number") is not None:
        state["pull_request_number"] = int(metadata["pull_request_number"])
    if phase == "PREVIEW" and metadata.get("attestation_id"):
        state["preview_attestation_id"] = str(metadata["attestation_id"])
    if phase == "MERGE" and outcome == "PASS":
        merge_sha = str(metadata["merge_sha"])
        state["delivery_sha"] = merge_sha
        # The MERGE event remains bound to the candidate HEAD, while all durable
        # downstream delivery phases switch to the provider-confirmed merge revision.
        state["subject_sha"] = merge_sha
    if phase == "PREVIEW" and outcome == "PASS":
        state["preview_digest"] = result["preview_digest"]
    if phase == "OWNER_ACCEPTANCE" and outcome == "PASS":
        state["owner_acceptance_sha"] = state["subject_sha"]

    if outcome == "PASS":
        if phase == "REVIEW" and not state["product_impact"]:
            state["phase"] = "MERGE"
        elif phase == "RECOVERY":
            state["phase"] = state.get("last_failed_phase") or "RECONCILE"
            state["last_failed_phase"] = None
        else:
            state["phase"] = NEXT_PHASE.get(phase, phase)
        state["status"] = "RUNNING"
        state["blockers"] = []
    elif outcome == "FAIL":
        if phase == "RECOVERY":
            state["status"] = "BLOCKED"
            state["blockers"] = result["reason_codes"] or ["RECOVERY_FAILED"]
        else:
            state["last_failed_phase"] = phase
            state["phase"] = "RECOVERY"
            state["status"] = "RECOVERY"
    elif outcome == "RETRY":
        used = int(state["attempts"].get(phase, 0)) + 1
        state["attempts"][phase] = used
        if used > state["max_attempts"]:
            state["last_failed_phase"] = phase
            state["phase"] = "RECOVERY"
            state["status"] = "RECOVERY"
            state["blockers"] = ["RETRY_BUDGET_EXHAUSTED"]
        else:
            state["status"] = "RETRY_WAIT"
    elif outcome == "CHANGES_REQUESTED":
        state["phase"] = "EXECUTE"
        state["status"] = "RUNNING"
        state["preview_digest"] = None
        state["owner_acceptance_sha"] = None
    elif outcome == "HUMAN_REQUIRED":
        state["status"] = "HUMAN_REQUIRED"
        state["blockers"] = result["reason_codes"] or ["HUMAN_DECISION_REQUIRED"]
    elif outcome == "BLOCK":
        state["status"] = "BLOCKED"
        state["blockers"] = result["reason_codes"] or ["STEP_BLOCKED"]
    elif outcome == "ROADMAP_COMPLETE":
        state["phase"] = "NEXT"
        state["status"] = "COMPLETE"
    elif outcome == "NEXT_READY":
        state["cycle"] = int(state["cycle"]) + 1
        if state["cycle"] >= state["max_cycles"]:
            state["status"] = "BLOCKED"
            state["blockers"] = ["MAX_CYCLES_REACHED"]
        else:
            state["phase"] = "RECONCILE"
            state["status"] = "RUNNING"
            state["subject_sha"] = None
            state["preview_digest"] = None
            state["owner_acceptance_sha"] = None
            state["delivery_sha"] = None
            state["pull_request_number"] = None
            state["preview_attestation_id"] = None
            state["work_branch"] = None
            state["attempts"] = {}
    return journal.save(state, expected_revision=state["revision"])


def advance_run_trusted(root: str | Path, run_id: str, step_result: dict[str, Any], context: DecisionContext) -> dict[str, Any]:
    """Production path: advance only from a compiler-produced DecisionContext."""
    return advance_run(root, run_id, step_result, None, trusted_context=context)
