"""Bounded provider-reconciled execution-node cycle kernel.

This module composes durable run discovery, the existing resume decision
boundary, and at most one runtime-supervisor tick.  It grants no provider,
CI, or merge authority.
"""
from __future__ import annotations

from pathlib import Path
from time import monotonic as _monotonic
from typing import Any, Callable

from .durable_orchestrator import OrchestrationJournal
from .orch_resume import decide_resume
from .runtime_supervisor import RuntimeSupervisor

_NON_RESUME_DECISIONS = {"YIELD", "RECONCILE", "BLOCK", "ACQUIRE_NEW"}


def _result(
    *,
    started: float,
    monotonic: Callable[[], float],
    run_id: str | None,
    resume_decision: str | None,
    node_outcome: str,
    supervisor_status: Any = None,
) -> dict[str, Any]:
    duration = max(0.0, float(monotonic()) - float(started))
    return {
        "run_id": run_id,
        "resume_decision": resume_decision,
        "node_outcome": node_outcome,
        "supervisor_status": supervisor_status,
        "cycle_duration_seconds": duration,
        "provider_write_authorized": False,
        "ci_authority": False,
        "merge_authority": False,
    }


def run_execution_node_cycle(
    root: str | Path,
    provider_context: dict[str, Any],
    *,
    max_steps: int = 12,
    monotonic: Callable[[], float] = _monotonic,
    journal: Any = None,
    supervisor: Any = None,
    resume_decider: Callable[..., dict[str, Any]] = decide_resume,
) -> dict[str, Any]:
    """Execute one bounded, provider-reconciled local cycle."""
    started = float(monotonic())
    durable_journal = journal if journal is not None else OrchestrationJournal(root)
    active = durable_journal.list_active()

    if not active:
        return _result(
            started=started,
            monotonic=monotonic,
            run_id=None,
            resume_decision=None,
            node_outcome="IDLE",
        )

    if len(active) != 1:
        return _result(
            started=started,
            monotonic=monotonic,
            run_id=None,
            resume_decision="BLOCK",
            node_outcome="AMBIGUOUS_ACTIVE_RUNS",
        )

    run_id = str(active[0]["run_id"])
    decision_result = resume_decider(**provider_context)
    decision = str(decision_result.get("decision", "BLOCK"))

    if decision == "RESUME_EXISTING":
        runtime_supervisor = supervisor if supervisor is not None else RuntimeSupervisor(root)
        tick_result = runtime_supervisor.tick(run_id, max_steps=max_steps)
        status = tick_result.get("status") if isinstance(tick_result, dict) else tick_result
        return _result(
            started=started,
            monotonic=monotonic,
            run_id=run_id,
            resume_decision=decision,
            node_outcome="RESUME_EXISTING",
            supervisor_status=status,
        )

    if decision not in _NON_RESUME_DECISIONS:
        decision = "BLOCK"
        outcome = "INVALID_RESUME_DECISION"
    else:
        outcome = decision
    return _result(
        started=started,
        monotonic=monotonic,
        run_id=run_id,
        resume_decision=decision,
        node_outcome=outcome,
    )
