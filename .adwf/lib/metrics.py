"""Дешёвые CI-метрики из provider events без внешнего SaaS."""
from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Any

from .evidence import parse_time


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, ceil(percentile * len(ordered)) - 1)], 3)


def summarize_ci(payload: dict[str, Any], *, now: datetime | None = None, ttl_hours: int = 24) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []
    try:
        observed_at = parse_time(str(payload["observed_at"]))
        if observed_at > now:
            errors.append("OBSERVED_IN_FUTURE")
        elif (now - observed_at).total_seconds() > ttl_hours * 3600:
            errors.append("METRICS_STALE")
    except (KeyError, ValueError):
        observed_at = None
        errors.append("OBSERVED_AT_INVALID")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        errors.append("RUNS_NOT_VERIFIED")
        runs = []
    durations: list[float] = []
    queues: list[float] = []
    first_failures: list[float] = []
    flaky = 0
    superseded = 0
    superseded_cancelled = 0
    for index, run in enumerate(runs):
        try:
            queued, started, completed = parse_time(run["queued_at"]), parse_time(run["started_at"]), parse_time(run["completed_at"])
            if not queued <= started <= completed:
                raise ValueError("time order")
            durations.append((completed - started).total_seconds())
            queues.append((started - queued).total_seconds())
            if run.get("first_failure_at"):
                failure = parse_time(run["first_failure_at"])
                if not started <= failure <= completed:
                    raise ValueError("failure order")
                first_failures.append((failure - started).total_seconds())
            if run.get("flaky") is True:
                flaky += 1
            if run.get("superseded") is True:
                superseded += 1
                if run.get("conclusion") == "CANCELLED":
                    superseded_cancelled += 1
            if run.get("conclusion") not in {"PASS", "FAIL", "CANCELLED"}:
                raise ValueError("conclusion")
        except (KeyError, TypeError, ValueError):
            errors.append(f"RUN_INVALID:{index}")
    status = "VERIFIED" if not errors else ("STALE" if errors == ["METRICS_STALE"] else "NOT_VERIFIED")
    return {
        "status": status,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z") if observed_at else None,
        "runs": len(durations),
        "p50_duration_seconds": _percentile(durations, 0.50),
        "p95_duration_seconds": _percentile(durations, 0.95),
        "p95_time_to_first_failure_seconds": _percentile(first_failures, 0.95),
        "p95_queue_seconds": _percentile(queues, 0.95),
        "flake_rate": round(flaky / len(durations), 4) if durations else None,
        "superseded_runs": superseded,
        "superseded_cancellation_rate": round(superseded_cancelled / superseded, 4) if superseded else None,
        "errors": list(dict.fromkeys(errors)),
    }
