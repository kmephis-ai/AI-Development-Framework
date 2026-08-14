"""Durable, provider-neutral work memory for long-running AI development.

Stores decisions and hand-off facts, never private chain-of-thought. The file is
safe to pass between agent sessions and survives chat/process restarts.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy, hashlib, json, os, tempfile

from .file_lock import exclusive_file_lock
from .strict_json import loads as strict_loads

ALLOWED_STATUS = {"NEW","ACTIVE","WAITING_CI","WAITING_OWNER","RECOVERY","BLOCKED","DONE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def new_work_memory(*, brief_id: str, task_ru: str, run_id: str | None = None) -> dict[str, Any]:
    task = str(task_ru).strip()
    if len(task) < 5:
        raise ValueError("WORK_MEMORY_TASK_TOO_SHORT")
    value = {
        "schema_version": 1,
        "brief_id": str(brief_id),
        "run_id": run_id,
        "status": "NEW",
        "task_ru": task,
        "product_brief": {},
        "decisions": [],
        "constraints": [],
        "completed": [],
        "verification": [],
        "blockers": [],
        "open_questions": [],
        "next_action_ru": "Подготовить первый безопасный шаг.",
        "references": {"issues": [], "pull_requests": [], "commits": [], "previews": []},
        "session_handoffs": [],
        "created_at": _now(),
        "updated_at": _now(),
        "revision": 0,
    }
    value["memory_digest"] = _digest({k:v for k,v in value.items() if k != "memory_digest"})
    return value


def validate_work_memory(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != 1: errors.append("WORK_MEMORY_SCHEMA")
    if value.get("status") not in ALLOWED_STATUS: errors.append("WORK_MEMORY_STATUS")
    if not str(value.get("brief_id") or "").strip(): errors.append("WORK_MEMORY_BRIEF")
    if len(str(value.get("task_ru") or "").strip()) < 5: errors.append("WORK_MEMORY_TASK")
    for field in ("decisions","constraints","completed","verification","blockers","open_questions","session_handoffs"):
        if not isinstance(value.get(field), list): errors.append(f"WORK_MEMORY_{field.upper()}")
    body = {k:v for k,v in value.items() if k != "memory_digest"}
    if value.get("memory_digest") != _digest(body): errors.append("WORK_MEMORY_DIGEST")
    return errors


class WorkMemoryStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / ".adwf-runtime" / "work-context.json"
        self.lock_path = self.path.with_suffix(".lock")

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file(): return None
        value = strict_loads(self.path.read_text(encoding="utf-8"))
        errors = validate_work_memory(value)
        if errors: raise ValueError("WORK_MEMORY_INVALID:" + ",".join(errors))
        return value

    def save(self, value: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            current = self.load() if self.path.is_file() else None
            if expected_revision is not None and int((current or {}).get("revision", -1)) != int(expected_revision):
                raise ValueError("WORK_MEMORY_REVISION_CONFLICT")
            payload = copy.deepcopy(value)
            payload["revision"] = int((current or {}).get("revision", -1)) + 1
            payload["updated_at"] = _now()
            payload["memory_digest"] = _digest({k:v for k,v in payload.items() if k != "memory_digest"})
            fd, temp = tempfile.mkstemp(prefix=self.path.name+".", dir=self.path.parent)
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as h:
                    json.dump(payload,h,ensure_ascii=False,indent=2); h.write("\n"); h.flush(); os.fsync(h.fileno())
                os.replace(temp,self.path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
            return payload

    def update(self, **changes: Any) -> dict[str, Any]:
        current = self.load()
        if current is None: raise ValueError("WORK_MEMORY_NOT_FOUND")
        revision = current["revision"]
        for key, value in changes.items():
            if key not in current or key in {"revision","memory_digest","created_at","updated_at"}:
                raise ValueError(f"WORK_MEMORY_FIELD_FORBIDDEN:{key}")
            current[key] = value
        return self.save(current, expected_revision=revision)

    def handoff(self, *, summary_ru: str, next_action_ru: str, session_id: str | None = None) -> dict[str, Any]:
        current = self.load()
        if current is None: raise ValueError("WORK_MEMORY_NOT_FOUND")
        revision = current["revision"]
        summary = str(summary_ru).strip(); nxt = str(next_action_ru).strip()
        if len(summary) < 5 or len(nxt) < 3: raise ValueError("WORK_MEMORY_HANDOFF_INCOMPLETE")
        current["session_handoffs"].append({"session_id":session_id,"summary_ru":summary,"next_action_ru":nxt,"at":_now()})
        current["next_action_ru"] = nxt
        return self.save(current, expected_revision=revision)
