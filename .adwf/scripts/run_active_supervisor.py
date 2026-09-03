#!/usr/bin/env python3
"""Run one bounded provider-reconciled active-supervisor cycle."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.durable_orchestrator import OrchestrationJournal
from lib.execution_node import run_execution_node_cycle
from lib.orch_provider_context import ProviderContextError, compile_provider_context
from lib.orch_next import bootstrap_next_run
from lib.github_provider import GitHubClient
from lib.github_lease_store import GitHubLeaseStore
from lib.policy_runtime import load_effective_policy
from lib.strict_json import loads as strict_loads


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
        "resume_decision": None if status in {"NO_SINGLE_ACTIVE_RUN", "NO_ROADMAP_WORK", "WRITER_BUSY", "NO_PROVIDER_CONTEXT"} else "BLOCK",
        "node_outcome": "IDLE" if active == 0 else status,
        "supervisor_status": None,
        "provider_write_authorized": False,
        "ci_authority": False,
        "merge_authority": False,
    }


def _bootstrap_idle_from_provider(root: str | Path) -> dict[str, Any]:
    """Compile fresh GitHub facts and attempt one provider-neutral idle bootstrap."""
    base = Path(root).resolve()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        return {"status": "NO_PROVIDER_CONTEXT", "durable_run_created": False}
    reconcile = subprocess.run(
        [sys.executable, str(base / ".adwf/scripts/github_reconcile.py"), "--apply"],
        cwd=base, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if reconcile.returncode != 0:
        return {"status": "BLOCK", "reason": "FRESH_PROVIDER_RECONCILIATION_FAILED", "durable_run_created": False}
    try:
        state = strict_loads((base / ".adwf-runtime/project-state.json").read_text(encoding="utf-8"))
        readback = strict_loads((base / ".adwf-runtime/provider-readback.json").read_text(encoding="utf-8"))
        assurance = strict_loads((base / ".adwf-runtime/assurance/current.json").read_text(encoding="utf-8"))
        main_sha = str((state.get("main") or {}).get("head") or "")
        policy = load_effective_policy(base)
        registry, _ = GitHubLeaseStore(GitHubClient(repo, token)).read(
            expected_main_sha=main_sha,
            policy_max_parallel_writers=int(policy.get("max_parallel_writers", 1)),
        )
    except Exception as exc:
        return {"status": "BLOCK", "reason": f"IDLE_BOOTSTRAP_INPUTS_INVALID:{type(exc).__name__}", "durable_run_created": False}
    return bootstrap_next_run(
        base, project_state=state, lease_registry=registry, assurance_snapshot=assurance, provider_readback=readback
    )


def run_active_supervisor(
    root: str | Path,
    *,
    max_steps: int = 12,
    journal: Any = None,
    context_compiler: Callable[..., dict[str, Any]] = compile_provider_context,
    node_runner: Callable[..., dict[str, Any]] = run_execution_node_cycle,
    compiler_kwargs: dict[str, Any] | None = None,
    node_kwargs: dict[str, Any] | None = None,
    idle_bootstrapper: Callable[[str | Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Route one healthy durable run through fresh reconciliation and Execution Node."""
    durable_journal = journal if journal is not None else OrchestrationJournal(root)
    active = durable_journal.list_active()
    if not active:
        bootstrapper = idle_bootstrapper if idle_bootstrapper is not None else (_bootstrap_idle_from_provider if journal is None else None)
        if bootstrapper is None:
            return _bounded("NO_SINGLE_ACTIVE_RUN", active=0)
        bootstrap = bootstrapper(root)
        if bootstrap.get("status") == "RUN_CREATED":
            active = durable_journal.list_active()
            if len(active) != 1:
                return _bounded("IDLE_BOOTSTRAP_BLOCK", active=len(active), reason="BOOTSTRAP_RUN_READBACK_FAILED")
        elif bootstrap.get("status") in {"NO_ROADMAP_WORK", "WRITER_BUSY", "NO_PROVIDER_CONTEXT"}:
            return _bounded(str(bootstrap.get("status")), active=0, reason=str(bootstrap.get("reason") or "") or None)
        elif bootstrap.get("status") == "ACTIVE_RUN_EXISTS":
            active = durable_journal.list_active()
            if len(active) != 1:
                return _bounded("IDLE_BOOTSTRAP_BLOCK", active=len(active), reason="ACTIVE_RUN_READBACK_FAILED")
        else:
            result = _bounded("IDLE_BOOTSTRAP_BLOCK", active=0, reason=str(bootstrap.get("reason") or "IDLE_BOOTSTRAP_FAILED"))
            result["resume_decision"] = "BLOCK"
            return result
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
