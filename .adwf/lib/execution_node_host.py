"""Persistent private Execution Node host loop with bounded public-safe evidence.

This layer is runtime-only. It repeatedly invokes the existing active-supervisor
composition and never grants provider, CI, or merge authority.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .file_lock import exclusive_file_lock

_EVIDENCE_KEYS = (
    "status", "active", "run_id", "reason", "resume_decision", "node_outcome",
    "supervisor_status", "cycle_duration_seconds",
)
_MAX_REASON = 160


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_private_token(path: str | Path) -> str:
    token_path = Path(path).expanduser().resolve()
    if not token_path.is_file():
        raise RuntimeError("EXECNODE_HOST_TOKEN_FILE_MISSING")
    if os.name != "nt" and token_path.stat().st_mode & 0o077:
        raise RuntimeError("EXECNODE_HOST_TOKEN_FILE_PERMISSIONS_UNSAFE")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token or "\n" in token or "\r" in token:
        raise RuntimeError("EXECNODE_HOST_TOKEN_FILE_INVALID")
    return token


class LinuxProcessLock(AbstractContextManager["LinuxProcessLock"]):
    """Host singleton wrapper over ADWF's canonical cross-platform file lock."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = None
        self.handle = None

    def __enter__(self) -> "LinuxProcessLock":
        self._lock = exclusive_file_lock(self.path, timeout_seconds=0.1)
        try:
            self.handle = self._lock.__enter__()
        except TimeoutError as exc:
            self._lock = None
            raise RuntimeError("EXECNODE_HOST_ALREADY_RUNNING") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()).encode("ascii"))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        lock = self._lock
        self._lock = None
        self.handle = None
        if lock is not None:
            lock.__exit__(exc_type, exc, tb)


def _git_head(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8",
        errors="strict", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    return value if len(value) == 40 else None


def bounded_cycle_evidence(result: dict[str, Any], *, local_head_sha: str | None, timestamp: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "role": "ADWF_EXECUTION_NODE_HOST_EVIDENCE_V1",
        "timestamp": timestamp,
        "local_head_sha": local_head_sha,
        "provider_write_authorized": False,
        "ci_authority": False,
        "merge_authority": False,
    }
    for key in _EVIDENCE_KEYS:
        value = result.get(key)
        if key == "reason" and value is not None:
            value = str(value)[:_MAX_REASON]
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


def append_evidence(path: str | Path, record: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def run_host_loop(
    root: str | Path,
    *,
    cycle_runner: Callable[[Path], dict[str, Any]],
    evidence_path: str | Path,
    lock_factory: Callable[[], AbstractContextManager[Any]],
    interval_seconds: float = 300.0,
    max_cycles: int | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = _utc_now,
    head_reader: Callable[[Path], str | None] = _git_head,
) -> int:
    base = Path(root).resolve()
    if interval_seconds < 1:
        raise ValueError("EXECNODE_HOST_INTERVAL_TOO_SMALL")
    cycles = 0
    with lock_factory():
        append_evidence(evidence_path, {
            "role": "ADWF_EXECUTION_NODE_HOST_EVENT_V1", "event": "HOST_START",
            "timestamp": now(), "provider_write_authorized": False,
            "ci_authority": False, "merge_authority": False,
        })
        while not should_stop() and (max_cycles is None or cycles < max_cycles):
            started = time.monotonic()
            try:
                result = cycle_runner(base)
                if not isinstance(result, dict):
                    raise RuntimeError("EXECNODE_HOST_CYCLE_RESULT_INVALID")
            except Exception as exc:
                result = {
                    "status": "EXECUTION_NODE_HOST_BLOCK", "active": 0,
                    "reason": f"HOST_CYCLE_FAILED:{type(exc).__name__}",
                    "resume_decision": "BLOCK", "node_outcome": "BLOCK",
                    "supervisor_status": None,
                }
            result = dict(result)
            result["cycle_duration_seconds"] = max(0.0, time.monotonic() - started)
            append_evidence(evidence_path, bounded_cycle_evidence(
                result, local_head_sha=head_reader(base), timestamp=now()
            ))
            cycles += 1
            if should_stop() or (max_cycles is not None and cycles >= max_cycles):
                break
            sleeper(interval_seconds)
        append_evidence(evidence_path, {
            "role": "ADWF_EXECUTION_NODE_HOST_EVENT_V1", "event": "HOST_STOP",
            "timestamp": now(), "cycles": cycles,
            "provider_write_authorized": False, "ci_authority": False,
            "merge_authority": False,
        })
    return cycles
