#!/usr/bin/env python3
"""Run one bounded provider-reconciled active-supervisor cycle."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.durable_orchestrator import OrchestrationJournal
from lib.execution_node import run_execution_node_cycle
from lib.orch_provider_context import ProviderContextError, compile_provider_context


def _bounded(
    status: str,
    *,
    active: int,
    run_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "active": active,
        "run_id": run_id,
        "reason": reason,
        "resume_decision": None if status == "NO_SINGLE_ACTIVE_RUN" else "BLOCK",
        "node_outcome": "IDLE" if active == 0 else status,
        "supervisor_status": None,
        "provider_write_authorized": False,
        "ci_authority": False,
        "merge_authority": False,
    }


def run_active_supervisor(
    root: str | Path,
    *,
    max_steps: int = 12,
    journal: Any = None,
    context_compiler: Callable[..., dict[str, Any]] = compile_provider_context,
    node_runner: Callable[..., dict[str, Any]] = run_execution_node_cycle,
    compiler_kwargs: dict[str, Any] | None = None,
    node_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route one healthy durable run through fresh reconciliation and Execution Node."""
    durable_journal = journal if journal is not None else OrchestrationJournal(root)
    active = durable_journal.list_active()
    if not active:
        return _bounded("NO_SINGLE_ACTIVE_RUN", active=0)
    if len(active) != 1:
        return _bounded(
            "AMBIGUOUS_ACTIVE_RUNS",
            active=len(active),
            reason="MULTIPLE_ACTIVE_OR_BROKEN_RUNS",
        )

    durable = active[0]
    run_id = str(durable.get("run_id") or "") or None
    if durable.get("status") == "BROKEN":
        return _bounded(
            "BROKEN_ACTIVE_RUN",
            active=1,
            run_id=run_id,
            reason="DURABLE_JOURNAL_BROKEN",
        )

    try:
        context = context_compiler(durable, **(compiler_kwargs or {}))
    except ProviderContextError as exc:
        return _bounded(
            "PROVIDER_CONTEXT_BLOCK",
            active=1,
            run_id=run_id,
            reason=str(exc)[:160],
        )
    except Exception:
        return _bounded(
            "PROVIDER_CONTEXT_BLOCK",
            active=1,
            run_id=run_id,
            reason="PROVIDER_CONTEXT_COMPILATION_FAILED",
        )

    try:
        return node_runner(
            root,
            context,
            max_steps=max_steps,
            journal=durable_journal,
            **(node_kwargs or {}),
        )
    except Exception:
        return _bounded(
            "EXECUTION_NODE_BLOCK",
            active=1,
            run_id=run_id,
            reason="EXECUTION_NODE_FAILED",
        )


def main() -> int:
    result = run_active_supervisor(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    blocked = result.get("resume_decision") == "BLOCK" or result.get("status") in {
        "BROKEN_ACTIVE_RUN",
        "PROVIDER_CONTEXT_BLOCK",
        "EXECUTION_NODE_BLOCK",
    }
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
