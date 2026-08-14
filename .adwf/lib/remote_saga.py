"""Durable observable saga для неатомарных remote state transitions.

GitHub не даёт одной транзакции для Issue body и labels. Модуль не называет
последовательность атомарной: он хранит idempotency journal, использует ETag/CAS,
умеет безопасно продолжить частично выполненный переход и предоставляет явную
compensation. Любое неизвестное remote-состояние переводит saga в
RECOVERY_REQUIRED без перезаписи данных человека.
"""
from __future__ import annotations
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import hashlib
import json
import os
import tempfile


MACHINE_LABELS = {
    "roadmap:ready", "roadmap:in-progress", "roadmap:review",
    "roadmap:verification", "roadmap:blocked", "roadmap:hold",
    "roadmap:needs-spec", "roadmap:needs-split", "roadmap:stale",
    "recovery:active",
}
STEP_NAMES = ("ADD_TARGET_LABEL", "PATCH_MARKER_BODY", "REMOVE_OLD_LABEL", "VERIFY_POSTCONDITION")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class RemoteResponse:
    data: dict[str, Any]
    etag: str | None = None
    status: int = 200


class RemoteTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        etag: str | None = None,
    ) -> RemoteResponse: ...


class SagaError(RuntimeError):
    pass


def transition_key(
    repo: str,
    issue_number: int,
    lease_id: str,
    from_state: str,
    to_state: str,
    policy_hash: str,
) -> str:
    source = {
        "repo": repo,
        "issue_number": issue_number,
        "lease_id": lease_id,
        "from_state": from_state,
        "to_state": to_state,
        "policy_hash": policy_hash,
    }
    return hashlib.sha256(_canonical(source)).hexdigest()


def make_transition_plan(
    *,
    repo: str,
    issue: dict[str, Any],
    lease_id: str,
    from_label: str,
    target_label: str,
    from_state: str,
    to_state: str,
    desired_body: str,
    policy_hash: str,
) -> dict[str, Any]:
    if not repo or "/" not in repo:
        raise ValueError("REPOSITORY_INVALID")
    if not isinstance(issue.get("number"), int):
        raise ValueError("ISSUE_NUMBER_INVALID")
    if from_label not in MACHINE_LABELS or target_label not in MACHINE_LABELS or from_label == target_label:
        raise ValueError("STATE_LABEL_INVALID")
    if not issue.get("updated_at"):
        raise ValueError("ISSUE_PROVIDER_REVISION_MISSING")
    current_labels = {item.get("name") for item in issue.get("labels", [])}
    if from_label not in current_labels or target_label in current_labels:
        raise ValueError("ISSUE_EXPECTED_LABEL_MISMATCH")
    original_body = str(issue.get("body") or "")
    if not desired_body or original_body == desired_body:
        raise ValueError("DESIRED_BODY_INVALID")
    if not isinstance(policy_hash, str) or len(policy_hash) != 64:
        raise ValueError("POLICY_HASH_INVALID")
    key = transition_key(repo, issue["number"], lease_id, from_state, to_state, policy_hash)
    return {
        "$schema": ".adwf/schemas/transition-saga.schema.json",
        "schema_version": 1,
        "saga_id": key,
        "idempotency_key": key,
        "provider": "github",
        "repo": repo,
        "issue_number": issue["number"],
        "lease_id": lease_id,
        "from_label": from_label,
        "target_label": target_label,
        "from_state": from_state,
        "to_state": to_state,
        "original_body": original_body,
        "original_body_sha256": _digest_text(original_body),
        "desired_body": desired_body,
        "desired_body_sha256": _digest_text(desired_body),
        "expected_updated_at": issue["updated_at"],
        "policy_hash": policy_hash,
        "status": "PLANNED",
        "steps": {name: "PENDING" for name in STEP_NAMES},
        "attempts": 0,
        "last_error": None,
        "created_at": _now(),
        "updated_at": _now(),
    }


class SagaJournal:
    def __init__(self, root: str | Path):
        self.directory = Path(root).resolve() / ".adwf-runtime" / "transactions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, saga_id: str) -> Path:
        if len(saga_id) != 64 or any(ch not in "0123456789abcdef" for ch in saga_id):
            raise ValueError("SAGA_ID_INVALID")
        return self.directory / f"{saga_id}.json"

    def load(self, saga_id: str) -> dict[str, Any] | None:
        path = self._path(saga_id)
        if not path.is_file():
            return None
        return strict_loads(path.read_text(encoding="utf-8"))

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        path = self._path(str(value.get("saga_id", "")))
        lock_path = path.with_suffix(".lock")
        with exclusive_file_lock(lock_path):
            current = strict_loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if current and current.get("idempotency_key") != value.get("idempotency_key"):
                raise ValueError("SAGA_IDEMPOTENCY_CONFLICT")
            payload = dict(value)
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


def _machine_labels(issue: dict[str, Any]) -> set[str]:
    return {str(item.get("name")) for item in issue.get("labels", []) if item.get("name") in MACHINE_LABELS}


def _get_issue(plan: dict[str, Any], transport: RemoteTransport) -> RemoteResponse:
    return transport.request("GET", f"/repos/{plan['repo']}/issues/{plan['issue_number']}")


def _classify_remote(plan: dict[str, Any], response: RemoteResponse) -> str:
    issue = response.data
    body_sha = _digest_text(str(issue.get("body") or ""))
    labels = _machine_labels(issue)
    allowed_labels = {plan["from_label"], plan["target_label"]}
    if labels - allowed_labels:
        return "CONFLICT"
    if body_sha not in {plan["original_body_sha256"], plan["desired_body_sha256"]}:
        return "CONFLICT"
    if labels == {plan["target_label"]} and body_sha == plan["desired_body_sha256"]:
        return "COMMITTED"
    if labels.issubset(allowed_labels) and labels and body_sha in {
        plan["original_body_sha256"], plan["desired_body_sha256"]
    }:
        return "RESUMABLE"
    return "CONFLICT"


def _record_error(journal: SagaJournal, plan: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    updated = dict(plan)
    updated["status"] = "RECOVERY_REQUIRED"
    updated["last_error"] = f"{type(exc).__name__}:{exc}"
    return journal.save(updated)


def run_transition(
    root: str | Path,
    plan: dict[str, Any],
    transport: RemoteTransport,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Выполнить или возобновить transition; неизвестное состояние не мутируется."""
    journal = SagaJournal(root)
    stored = journal.load(plan["saga_id"])
    if stored:
        immutable = (
            "idempotency_key", "repo", "issue_number", "lease_id", "from_label",
            "target_label", "from_state", "to_state", "original_body_sha256",
            "desired_body_sha256", "policy_hash",
        )
        if any(stored.get(name) != plan.get(name) for name in immutable):
            raise SagaError("SAGA_PLAN_CONFLICT")
        plan = stored
    else:
        plan = journal.save(plan)
    if plan.get("status") == "COMMITTED":
        return plan

    first = _get_issue(plan, transport)
    state = _classify_remote(plan, first)
    if state == "COMMITTED":
        plan["status"] = "COMMITTED"
        plan["steps"] = {name: "PASS" for name in STEP_NAMES}
        return journal.save(plan)
    if state == "CONFLICT":
        return _record_error(journal, plan, SagaError("REMOTE_STATE_CONFLICT"))
    if not apply:
        plan["status"] = "DRY_RUN"
        return journal.save(plan)

    plan["status"] = "APPLYING"
    plan["attempts"] = int(plan.get("attempts", 0)) + 1
    plan["last_error"] = None
    plan = journal.save(plan)
    try:
        current = first
        labels = _machine_labels(current.data)
        body_sha = _digest_text(str(current.data.get("body") or ""))

        if plan["target_label"] not in labels:
            transport.request(
                "POST",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}/labels",
                {"labels": [plan["target_label"]]},
                etag=current.etag,
            )
            plan["steps"]["ADD_TARGET_LABEL"] = "PASS"
            plan = journal.save(plan)
            current = _get_issue(plan, transport)
            if _classify_remote(plan, current) == "CONFLICT":
                raise SagaError("REMOTE_STATE_CONFLICT_AFTER_ADD")
        else:
            plan["steps"]["ADD_TARGET_LABEL"] = "PASS"

        body_sha = _digest_text(str(current.data.get("body") or ""))
        if body_sha != plan["desired_body_sha256"]:
            if body_sha != plan["original_body_sha256"]:
                raise SagaError("CONCURRENT_BODY_EDIT")
            transport.request(
                "PATCH",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}",
                {"body": plan["desired_body"]},
                etag=current.etag,
            )
            plan["steps"]["PATCH_MARKER_BODY"] = "PASS"
            plan = journal.save(plan)
            current = _get_issue(plan, transport)
            if _classify_remote(plan, current) == "CONFLICT":
                raise SagaError("REMOTE_STATE_CONFLICT_AFTER_PATCH")
        else:
            plan["steps"]["PATCH_MARKER_BODY"] = "PASS"

        labels = _machine_labels(current.data)
        if plan["from_label"] in labels:
            transport.request(
                "DELETE",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}/labels/{plan['from_label']}",
                etag=current.etag,
            )
            plan["steps"]["REMOVE_OLD_LABEL"] = "PASS"
            plan = journal.save(plan)
        else:
            plan["steps"]["REMOVE_OLD_LABEL"] = "PASS"

        verified = _get_issue(plan, transport)
        if _classify_remote(plan, verified) != "COMMITTED":
            raise SagaError("POSTCONDITION_FAILED")
        plan["steps"]["VERIFY_POSTCONDITION"] = "PASS"
        plan["status"] = "COMMITTED"
        plan["last_error"] = None
        return journal.save(plan)
    except BaseException as exc:
        _record_error(journal, plan, exc)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise SagaError(str(exc)) from exc


def compensate_transition(
    root: str | Path,
    saga_id: str,
    transport: RemoteTransport,
) -> dict[str, Any]:
    """Безопасно вернуть исходные body/label, только если нет чужого изменения."""
    journal = SagaJournal(root)
    plan = journal.load(saga_id)
    if not plan:
        raise SagaError("SAGA_NOT_FOUND")
    current = _get_issue(plan, transport)
    state = _classify_remote(plan, current)
    if state == "CONFLICT":
        return _record_error(journal, plan, SagaError("COMPENSATION_REMOTE_CONFLICT"))
    body_sha = _digest_text(str(current.data.get("body") or ""))
    labels = _machine_labels(current.data)
    try:
        if body_sha == plan["desired_body_sha256"]:
            transport.request(
                "PATCH",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}",
                {"body": plan["original_body"]},
                etag=current.etag,
            )
            current = _get_issue(plan, transport)
        if plan["from_label"] not in _machine_labels(current.data):
            transport.request(
                "POST",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}/labels",
                {"labels": [plan["from_label"]]},
                etag=current.etag,
            )
            current = _get_issue(plan, transport)
        if plan["target_label"] in _machine_labels(current.data):
            transport.request(
                "DELETE",
                f"/repos/{plan['repo']}/issues/{plan['issue_number']}/labels/{plan['target_label']}",
                etag=current.etag,
            )
        verified = _get_issue(plan, transport)
        if (
            _machine_labels(verified.data) != {plan["from_label"]}
            or _digest_text(str(verified.data.get("body") or "")) != plan["original_body_sha256"]
        ):
            raise SagaError("COMPENSATION_POSTCONDITION_FAILED")
        plan["status"] = "COMPENSATED"
        plan["last_error"] = None
        return journal.save(plan)
    except BaseException as exc:
        _record_error(journal, plan, exc)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise SagaError(str(exc)) from exc
