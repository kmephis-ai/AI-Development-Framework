#!/usr/bin/env python3
"""ADWF v1.6 CLI — engineering fallback for the Executive Autopilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from lib.adwf_core import evaluate_adversarial_case, issue_quality, load_json, roadmap_audit
from lib.contracts import validate_files
from lib.cost_guard import evaluate_provider
from lib.dashboard import render_dashboard, render_executive_html
from lib.evidence import verify_evidence
from lib.health import active_state_path, doctor
from lib.leases import atomic_update, claim, reconcile
from lib.metrics import summarize_ci
from lib.orchestration import authorize_next_action  # legacy claim compatibility only
from lib.policy import DecisionContext
from lib.policy_compiler import check_compiled_policy, compile_policy
from lib.policy_runtime import evaluate_with_effective_policy, load_effective_policy
from lib.durable_orchestrator import OrchestrationJournal, advance_run, new_run
from lib.setup_assistant import build_setup_plan
from lib.incidents import incident_store_summary, normalize_incident, read_incident_events, record_incident
from lib.healing import evaluate_healing, load_healing_config, load_recipe_registry, verify_h4
from lib.owner_portal import bootstrap_plan, serve as serve_owner_portal
from lib.owner_intent_service import start_or_queue
from lib.owner_authority import accept_and_continue
from lib.github_bootstrap import bootstrap_repository
from lib.owner_experience import (
    create_preview,
    create_product_brief,
    record_owner_acceptance,
    render_human_changelog,
)
from lib.state_engine import apply_transition, evaluate_transition
from lib.workspaces import OCCUPYING, cleanup_workspace, complete_workspace, create_workspace, heartbeat_workspace, plan_workspace, read_registry, reconcile_workspaces, schedule_retry
from lib.runtime_supervisor import RuntimeSupervisor
from lib.work_memory import WorkMemoryStore
from lib.preview_engine import capture_preview
from lib.roadmap_view import build_roadmap_view
from lib.project_packs import commands_for_pack
from lib.pack_materializer import materialize_project_pack
from lib.portfolio import portfolio_view, register_project
from lib.semantic_release import release_plan

ROOT = Path(__file__).resolve().parents[1]


def framework_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_doctor(args) -> int:
    result = doctor(ROOT, scope=args.scope)
    if args.json:
        emit(result)
    else:
        print(f"ADWF v{framework_version()} — проверка контуров")
        for name, category in result["categories"].items():
            print(f"{name:28} {category['status']}")
            for finding in category["findings"]:
                print(f"  - {finding}")
        print(f"\nИТОГ ({args.scope}): {result['overall']}")
    return 0 if result["overall"] == "VERIFIED" else 2


def cmd_status(args) -> int:
    state = load_json(args.input or active_state_path(ROOT))
    health = doctor(ROOT)
    p = state.get("progress", {})
    q = state.get("queue", {})
    o = state.get("orchestration", {})
    print(f"Продукт: {health['categories']['product_health']['status']}")
    print(f"Control Plane: {health['categories']['control_plane_health']['status']}")
    print(f"Package / Config: {health['categories']['package_integrity']['status']} / {health['categories']['config_health']['status']}")
    print(f"Готовность продукта: {float(p.get('product_readiness', 0))*100:.0f}%")
    print(f"Реализация / Verification / Gap: {float(p.get('implementation', 0))*100:.0f}% / {float(p.get('verification', 0))*100:.0f}% / {float(p.get('verification_gap', 0))*100:.0f}%")
    print(f"Очередь READY / IN_PROGRESS / REVIEW / BLOCKED: {q.get('ready', 0)} / {q.get('in_progress', 0)} / {q.get('review', 0)} / {q.get('blocked', 0)}")
    print(f"Writer / conflicts: {o.get('writers_active', 'NOT_VERIFIED')} / {o.get('conflicts', 'NOT_VERIFIED')}")
    print(f"Следующая задача: {state.get('active', {}).get('roadmap_id') or 'не выбрана'}")
    return 0


def cmd_validate(args) -> int:
    findings = validate_files(args.input, args.schema)
    emit({"status": "PASS" if not findings else "FAIL", "findings": [item.to_dict() for item in findings]})
    return 0 if not findings else 3


def cmd_issue(args) -> int:
    policy = load_json(ROOT / ".adwf/policies/roadmap-quality.json")
    issue = load_json(args.input)
    schema_findings = validate_files(args.input, ROOT / ".adwf/schemas/issue.schema.json")
    result = issue_quality(issue, policy)
    if schema_findings:
        result["status"] = "FAIL"
        result["findings"] = [f"schema:{item.path}:{item.code}" for item in schema_findings] + result["findings"]
    emit(result)
    return 0 if result["status"] == "PASS" else 3


def cmd_roadmap(args) -> int:
    policy = load_json(ROOT / ".adwf/policies/roadmap-quality.json")
    result = roadmap_audit(load_json(args.input), policy)
    emit(result)
    return 0 if result["health"] != "CRITICAL" else 4


def _default_policy(action: str = "claim", risk: str = "R0", work_type: str = "feature") -> dict:
    cfg = load_json(ROOT / ".adwf/config.json")
    state = load_json(active_state_path(ROOT))
    health = doctor(ROOT)
    registry = load_json(ROOT / ".adwf/providers.json")
    capability = cfg.get("cost", {}).get("default_ci_capability", "")
    provider = evaluate_provider(
        registry,
        {"provider": capability, "mandatory_ci": action in {"test", "merge", "deploy_dev", "deploy_prod"}, "automated": True, "projected_cost": 0},
        canonical_provider=cfg.get("provider", {}).get("mode"),
    )
    return {
        "action": action,
        "autonomy": state.get("autonomy_level", cfg["policy"]["active_autonomy"]),
        "risk": risk,
        "max_autonomous_risk": state.get("risk_ceiling", cfg["policy"]["max_autonomous_risk"]),
        "work_type": work_type,
        "health": {name: value["status"] for name, value in health["categories"].items()},
        "provider_allowed": provider["result"] == "ALLOW",
        "provider_potentially_paid": provider.get("effective_classification", provider.get("classification")) in {"METERED", "PAID", "UNKNOWN", "STALE"},
        "projected_cost": 0,
    }


def cmd_permission(args) -> int:
    payload = load_json(args.input)
    try:
        decision = evaluate_with_effective_policy(ROOT, DecisionContext.from_dict(payload))
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)], "message_ru": "Decision context не прошёл строгий контракт."})
        return 5
    emit(decision.to_dict())
    return 0 if decision.result == "ALLOW" else (6 if decision.result == "HUMAN_REQUIRED" else 5)


def cmd_continue(args) -> int:
    """Executive Continue: operate only on the single Durable Orchestrator run."""
    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:
        emit({"status":"BLOCK","reason":"SINGLE_ACTIVE_RUN_REQUIRED","active":len(active)});return 5
    run=active[0]
    try:
        if run.get("phase")=="OWNER_ACCEPTANCE":
            state=load_json(active_state_path(ROOT));exp=state.get("owner_experience") or {};preview=exp.get("current_preview") or {}
            result=accept_and_continue(ROOT,brief_id=str(run.get("roadmap_id")),head_sha=str(run.get("subject_sha") or preview.get("head_sha") or ""),preview_digest=str(run.get("preview_digest") or preview.get("preview_digest") or ""));emit(result);return 0 if result.get("status")=="CONTINUED" else 6
        result=RuntimeSupervisor(ROOT).tick(run["run_id"]);emit(result);return 0 if result.get("status") not in {"BLOCKED","BLOCK"} else 5
    except ValueError as exc:
        emit({"status":"BLOCK","reason":str(exc)});return 5

def cmd_claim(args) -> int:
    queue_path = Path(args.queue)
    queue = load_json(queue_path)
    issue = next((item for item in queue.get("issues", []) if str(item.get("id")) == str(args.issue)), None)
    if not issue:
        emit({"result": "BLOCK", "reason": "ISSUE_NOT_FOUND"})
        return 5
    context = _default_policy("claim", issue.get("risk", "UNKNOWN"), issue.get("type", "feature"))
    try:
        policy_ir = load_effective_policy(ROOT)
    except ValueError as exc:
        emit({"result": "BLOCK", "action": "BLOCKED", "reason_codes": [str(exc)]})
        return 5
    authorization = authorize_next_action(queue, context, policy_ir=policy_ir)
    selected = authorization.get("issue") or {}
    if authorization.get("action") != "CLAIM_ONE_READY" or str(selected.get("id")) != str(args.issue):
        emit(authorization)
        return 6 if authorization.get("result") == "HUMAN_REQUIRED" else 5

    def transform(current):
        updated, lease = claim(current, args.issue, args.worker, args.base_sha, ttl_minutes=args.ttl, permission_allowed=True)
        return updated, lease

    try:
        if args.apply:
            lease = atomic_update(queue_path, transform)
            try:
                workspace = create_workspace(ROOT, lease, _workspace_config(), apply=True)
            except ValueError as exc:
                def recover(current):
                    for current_lease in current.get("leases", []):
                        if current_lease.get("lease_id") == lease["lease_id"]:
                            current_lease["status"] = "RELEASED"
                    for current_issue in current.get("issues", []):
                        if str(current_issue.get("id")) == str(lease["issue_id"]):
                            current_issue["state"] = "RECOVERY"
                    return current, str(exc)
                reason = atomic_update(queue_path, recover)
                emit({"result": "RECOVERY", "reason": f"WORKSPACE_CREATE_FAILED:{reason}", "lease": lease})
                return 5
            emit({"result": "APPLIED", "lease": lease, "workspace": workspace})
        else:
            _, lease = transform(queue)
            emit({"result": "DRY_RUN", "lease": lease, "workspace": plan_workspace(ROOT, lease, _workspace_config())})
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_reconcile(args) -> int:
    queue_path = Path(args.queue)
    queue = load_json(queue_path)
    updated, expired = reconcile(queue)
    if args.apply and expired:
        def transform(current):
            result, ids = reconcile(current)
            return result, ids
        expired = atomic_update(queue_path, transform)
    emit({"result": "APPLIED" if args.apply else "DRY_RUN", "expired_lease_ids": expired})
    return 0


def cmd_transition(args) -> int:
    item_path = Path(args.input)
    item = load_json(item_path)
    predicates = load_json(args.predicates)
    machine = load_json(ROOT / ".adwf/state-machine.json")
    decision = evaluate_transition(item, args.to, machine, predicates, expected_state=args.expected)
    if decision.result != "ALLOW":
        emit(decision.to_dict())
        return 5
    if args.apply:
        def transform(current):
            updated, result = apply_transition(current, args.to, machine, predicates, expected_state=args.expected)
            return updated, result.to_dict()
        applied = atomic_update(item_path, transform)
        emit({"result": "APPLIED", "transition": applied})
    else:
        emit({"result": "DRY_RUN", "transition": decision.to_dict()})
    return 0


def cmd_evidence(args) -> int:
    record = load_json(args.input)
    schema = load_json(ROOT / ".adwf/schemas/evidence.schema.json")
    result = verify_evidence(record, schema, expected_sha=args.sha, expected_runtime_revision=args.runtime_revision, root=ROOT)
    emit(result)
    return 0 if result["valid"] else 5


def cmd_provider(args) -> int:
    registry = load_json(ROOT / ".adwf/providers.json")
    config = load_json(ROOT / ".adwf/config.json")
    request = load_json(args.input)
    result = evaluate_provider(registry, request, canonical_provider=config.get("provider", {}).get("mode"))
    emit(result)
    return 0 if result["result"] == "ALLOW" else 5


def run_tests(pattern: str) -> int:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / ".adwf/tests"), "-p", pattern, "-v"],
        check=False,
        env=env,
    )
    return process.returncode


def cmd_dashboard(args) -> int:
    state = load_json(args.state or active_state_path(ROOT))
    runtime = [item for item in read_registry(ROOT).get("workspaces", []) if item.get("status") in OCCUPYING]
    if len(runtime) == 1:
        item = runtime[0]
        state = json.loads(json.dumps(state))
        state["workspace"] = {"status": item.get("status"), "workspace_id": item.get("workspace_id"),
                              "heartbeat_at": item.get("heartbeat_at"), "expires_at": item.get("expires_at"),
                              "retry_count": item.get("retry_count", 0),
                              "next_retry_at": item.get("next_retry_at")}
    health = doctor(ROOT)
    providers = load_json(ROOT / ".adwf/providers.json")
    config = load_json(ROOT / ".adwf/config.json")
    capability = args.provider or config.get("cost", {}).get("default_ci_capability")
    cost = evaluate_provider(providers, {"provider": capability, "mandatory_ci": False, "projected_cost": 0}, canonical_provider=config.get("provider", {}).get("mode"))
    try:
        policy_ir = load_effective_policy(ROOT)
        decision = continue_decision(
            load_json(args.queue) if args.queue else _runtime_queue(),
            _default_policy(),
            policy_ir=policy_ir,
        )
    except ValueError as exc:
        decision = {"result": "BLOCK", "action": "BLOCKED", "reason_codes": [str(exc)], "issue": None}
    suffix = "CONTROL_CENTER.html" if args.format == "html" else "CONTROL_CENTER.md"
    output = Path(args.output or ROOT / suffix)
    rendered = render_executive_html(state, health, cost, decision) if args.format == "html" else render_dashboard(state, health, cost, decision)
    output.write_text(rendered, encoding="utf-8")
    print(f"Контрольная панель обновлена: {output}")
    return 0


def cmd_policy_compile(args) -> int:
    compiled, errors = compile_policy(ROOT)
    if args.check:
        errors = check_compiled_policy(ROOT)
    emit({"status": "PASS" if not errors else "BLOCKED", "policy": compiled, "errors": errors})
    return 0 if not errors else 5


def cmd_orchestration_start(args) -> int:
    request = load_json(args.input)
    required = {"roadmap_id", "issue_id", "risk", "work_type", "product_impact", "owner_request_digest"}
    missing = sorted(required - set(request))
    if missing:
        emit({"result": "BLOCK", "reason_codes": ["REQUEST_FIELDS_MISSING:" + ",".join(missing)]})
        return 5
    if not args.apply:
        try:
            policy = load_effective_policy(ROOT)
        except ValueError as exc:
            emit({"result": "BLOCK", "reason_codes": [str(exc)]})
            return 5
        emit({
            "result": "DRY_RUN",
            "initial_phase": "RECONCILE",
            "policy_hash": policy["policy_hash"],
            "monetary_budget_usd": 0,
            "request": request,
        })
        return 0
    try:
        state = new_run(
            ROOT,
            roadmap_id=str(request["roadmap_id"]),
            issue_id=str(request["issue_id"]),
            risk=str(request["risk"]),
            work_type=str(request["work_type"]),
            product_impact=request["product_impact"] is True,
            owner_request_digest=str(request["owner_request_digest"]),
            run_id=request.get("run_id"),
            max_attempts=int(request.get("max_attempts", 3)),
            max_cycles=int(request.get("max_cycles", 100)),
            max_elapsed_minutes=int(request.get("max_elapsed_minutes", 1440)),
        )
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5
    emit({"result": "APPLIED", "run": state})
    return 0


def cmd_orchestration_step(args) -> int:
    try:
        current = OrchestrationJournal(ROOT).load(args.run_id)
    except ValueError as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5
    result = load_json(args.result)
    context = load_json(args.control_context)
    if not args.apply:
        emit({
            "result": "DRY_RUN",
            "current_phase": current["phase"],
            "current_revision": current["revision"],
            "submitted_phase": result.get("phase"),
            "policy_hash": current["policy_hash"],
        })
        return 0
    try:
        state = advance_run(ROOT, args.run_id, result, context)
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5
    emit({"result": state["status"], "run": state})
    return 0 if state["status"] in {"RUNNING", "RETRY_WAIT", "RECOVERY", "COMPLETE"} else 5


def cmd_orchestration_status(args) -> int:
    try:
        emit(OrchestrationJournal(ROOT).load(args.run_id))
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_ci_setup(args) -> int:
    registry = load_json(ROOT / ".adwf/providers.json")
    config = load_json(ROOT / ".adwf/config.json")
    emit(build_setup_plan(
        args.project_root or ROOT,
        registry,
        capability=args.capability or config.get("cost", {}).get("default_ci_capability", ""),
        canonical_provider=config.get("provider", {}).get("mode", "local"),
    ))
    return 0


def cmd_incident_normalize(args) -> int:
    try:
        emit(normalize_incident(load_json(args.input)))
        return 0
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_incident_record(args) -> int:
    try:
        raw = load_json(args.input)
        if not args.apply:
            emit({"result": "DRY_RUN", "incident": normalize_incident(raw)})
        else:
            store = Path(args.store or ROOT / ".adwf-runtime/incidents/events.jsonl")
            emit(record_incident(store, raw))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_incident_summary(args) -> int:
    try:
        store = Path(args.store or ROOT / ".adwf-runtime/incidents/events.jsonl")
        emit(incident_store_summary(read_incident_events(store)))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_healing_evaluate(args) -> int:
    try:
        incident = load_json(args.incident)
        registry = load_recipe_registry(args.recipes or ROOT / ".adwf/knowledge/recipes/registry.json")
        config = load_healing_config(ROOT / ".adwf/healing-config.json")
        context = load_json(args.context)
        circuit = load_json(args.circuit) if args.circuit else {}
        decision = evaluate_healing(incident, registry, config, context, circuit)
        emit(decision)
        return 0 if decision["result"] == "ALLOW" else (6 if decision["result"] == "HUMAN_REQUIRED" else 5)
    except (OSError, TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_healing_verify(args) -> int:
    try:
        result = verify_h4(load_json(args.context), load_healing_config(ROOT / ".adwf/healing-config.json"))
        emit(result)
        return 0 if result["result"] == "VERIFIED" else 5
    except (OSError, TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_owner_brief(args) -> int:
    try:
        emit(create_product_brief(load_json(args.input)))
        return 0
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_owner_preview(args) -> int:
    try:
        value = load_json(args.input)
        emit(create_preview(
            head_sha=value.get("head_sha"), preview_digest=value.get("preview_digest"),
            created_at=value.get("created_at"), valid_until=value.get("valid_until"),
            url=value.get("url"), screenshots=value.get("screenshots"),
        ))
        return 0
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_owner_acceptance(args) -> int:
    try:
        value = load_json(args.input)
        emit(record_owner_acceptance(
            brief_id=value.get("brief_id"), decision=value.get("decision"),
            head_sha=value.get("head_sha"), preview_digest=value.get("preview_digest"),
            actor=value.get("actor"), decided_by=value.get("decided_by"),
            authority=value.get("authority", "OWNER"), nonce=value.get("nonce"),
            source=value.get("source", "LOCAL_AUTHENTICATED"),
            provider_readback=value.get("provider_readback", False),
            policy_hash=value.get("policy_hash"), note_ru=value.get("note_ru", ""),
        ))
        return 0
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_owner_changelog(args) -> int:
    try:
        value = load_json(args.input)
        print(render_human_changelog(
            value.get("changes", []), limitations_ru=value.get("limitations_ru"),
            owner_action_ru=value.get("owner_action_ru"), preview_url=value.get("preview_url"),
        ), end="")
        return 0
    except (TypeError, ValueError) as exc:
        emit({"result": "BLOCK", "reason_codes": [str(exc)]})
        return 5


def cmd_owner_init(args) -> int:
    product = args.product
    outcome = args.outcome
    if (not product or not outcome) and sys.stdin.isatty():
        product = product or input("Какой цифровой продукт вы создаёте? ").strip()
        outcome = outcome or input("Какой результат вы хотите получить? ").strip()
    if not product or not outcome:
        emit({"status": "HUMAN_REQUIRED", "reason": "PRODUCT_AND_OUTCOME_REQUIRED", "questions_max": 3})
        return 6
    try:
        plan = bootstrap_plan(product, outcome, public_confirmed=args.public_confirmed, license_acknowledged=args.license_acknowledged)
        if plan.get("status") == "READY":
            live=bootstrap_repository(ROOT,apply=True);plan["github"]=live
            plan["status"]="READY" if live.get("status")=="VERIFIED" else str(live.get("status") or "NOT_VERIFIED")
        emit(plan)
        return 0 if plan.get("status") in {"READY","WAITING_SEED_CHECKS","WAITING_OWNER_GOVERNANCE_APPROVAL"} else 6
    except ValueError as exc:
        emit({"status":"BLOCK","reason":str(exc)}); return 5


def cmd_owner_start(args) -> int:
    try:
        started=start_or_queue(ROOT,args.task,queue_if_busy=not getattr(args,"reject_if_busy",False),wake=True)
        if started.get("status")=="AUTOPILOT_STARTED":
            started["supervisor"]=RuntimeSupervisor(ROOT).status(started["run_id"]).to_dict()
        started["mandatory_ai_api_calls"]=0
        emit(started);return 0 if started.get("status") in {"AUTOPILOT_STARTED","QUEUED_NEW_TASK","ALREADY_QUEUED"} else 6
    except ValueError as exc:
        emit({"status":"BLOCK","reason":str(exc)});return 5

def cmd_dashboard_serve(args) -> int:
    try:
        serve_owner_portal(ROOT, bind=args.bind, port=args.port)
        return 0
    except (OSError, ValueError) as exc:
        emit({"status":"BLOCK","reason":str(exc)}); return 5



def cmd_preview(args) -> int:
    try:
        manifest=capture_preview(ROOT,url=args.url,baseline_url=args.baseline_url,head_sha=args.head_sha,output_dir=args.output,install=args.install)
        emit(manifest); return 0
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        emit({"status":"NOT_VERIFIED","reason":str(exc)}); return 5

def cmd_roadmap_view(args) -> int:
    state=load_json(args.state or active_state_path(ROOT)); emit(build_roadmap_view(ROOT,state)); return 0

def cmd_runtime_tick(args) -> int:
    try:
        result=RuntimeSupervisor(ROOT).tick(args.run_id); emit(result); return 0 if result.get("status") not in {"BLOCKED"} else 5
    except ValueError as exc:
        emit({"status":"BLOCK","reason":str(exc)}); return 5

def cmd_runtime_status(args) -> int:
    try: emit(RuntimeSupervisor(ROOT).status(args.run_id).to_dict()); return 0
    except ValueError as exc: emit({"status":"BLOCK","reason":str(exc)}); return 5

def cmd_project_pack(args) -> int:
    project=args.project_root or ROOT
    if args.apply:
        result=materialize_project_pack(project,ROOT,apply=True); emit(result); return 0 if result.get("status")=="APPLIED" else 6
    emit(materialize_project_pack(project,ROOT,apply=False) if args.plan else commands_for_pack(project,ROOT)); return 0

def cmd_portfolio_view(args) -> int:
    if args.register: register_project(args.register)
    emit(portfolio_view()); return 0

def cmd_release_plan(args) -> int:
    try:
        changes=load_json(args.input); items=changes.get("changes") if isinstance(changes,dict) else None
        emit(release_plan(framework_version(),items or [])); return 0
    except (TypeError,ValueError) as exc: emit({"status":"BLOCK","reason":str(exc)}); return 5


def cmd_release(args) -> int:
    command=[sys.executable,str(ROOT/".adwf/scripts/release.py")]
    if args.auto: command.append("--auto")
    if args.input: command += ["--changes",args.input]
    if args.prepare: command.append("--prepare")
    if args.confirm: command.append("--confirm")
    if args.external: command.append("--external")
    if args.publish_github: command.append("--publish-github")
    if args.output: command += ["--output",args.output]
    return subprocess.run(command,cwd=ROOT,check=False).returncode

def _workspace_config() -> dict:
    return load_json(ROOT / ".adwf/config.json")["workspace"]


def _runtime_queue() -> dict:
    state = load_json(active_state_path(ROOT))
    issues = json.loads(json.dumps(state.get("work_items", [])))
    leases = []
    registry = read_registry(ROOT)
    for item in registry.get("workspaces", []):
        if item.get("status") in OCCUPYING:
            roadmap_id = item.get("roadmap_id")
            for issue in issues:
                if str(issue.get("id")) == str(roadmap_id):
                    issue.update({
                        "workspace_id": item.get("workspace_id"),
                        "heartbeat_at": item.get("heartbeat_at"),
                        "expires_at": item.get("expires_at"),
                        "worker_id": item.get("worker_id"),
                    })
            leases.append({"lease_id": item.get("lease_id"), "issue_id": roadmap_id,
                           "status": "ACTIVE", "expires_at": item.get("expires_at"),
                           "heartbeat_at": item.get("heartbeat_at"),
                           "workspace_id": item.get("workspace_id"),
                           "worker_id": item.get("worker_id"),
                           "conflict_domains": [], "workspace_status": item.get("status")})
    if not leases:
        projected_workspace = state.get("workspace", {})
        for item in issues:
            if item.get("lease_id") and item.get("state") in {"IN_PROGRESS", "REVIEW"}:
                if str(state.get("active", {}).get("roadmap_id")) == str(item.get("id")):
                    item.setdefault("workspace_id", projected_workspace.get("workspace_id"))
                    item.setdefault("heartbeat_at", projected_workspace.get("heartbeat_at"))
                    item.setdefault("expires_at", projected_workspace.get("expires_at"))
                leases.append({"lease_id": item.get("lease_id"), "issue_id": item.get("id"), "status": "ACTIVE",
                               "expires_at": item.get("expires_at"), "heartbeat_at": item.get("heartbeat_at"),
                               "workspace_id": item.get("workspace_id"), "worker_id": item.get("writer_id"),
                               "conflict_domains": item.get("conflict_domains", []),
                               "workspace_status": "ACTIVE"})
    return {"leases": leases, "issues": issues}


def cmd_workspace_plan(args) -> int:
    try:
        emit({"result": "DRY_RUN", **plan_workspace(ROOT, load_json(args.lease), _workspace_config())})
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_create(args) -> int:
    try:
        emit(create_workspace(ROOT, load_json(args.lease), _workspace_config(), apply=args.apply))
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_heartbeat(args) -> int:
    try:
        emit({"result": "APPLIED", "workspace": heartbeat_workspace(ROOT, args.workspace_id, args.worker)})
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_reconcile(args) -> int:
    try:
        emit(reconcile_workspaces(ROOT, _workspace_config(), apply=args.apply))
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_retry(args) -> int:
    try:
        emit({"result": "SCHEDULED", "workspace": schedule_retry(ROOT, args.workspace_id, args.error, _workspace_config())})
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_complete(args) -> int:
    try:
        emit({"result": "COMPLETED", "workspace": complete_workspace(ROOT, args.workspace_id)})
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_workspace_cleanup(args) -> int:
    try:
        emit(cleanup_workspace(ROOT, args.workspace_id, _workspace_config(), apply=args.apply))
        return 0
    except ValueError as exc:
        emit({"result": "BLOCK", "reason": str(exc)})
        return 5


def cmd_metrics(args) -> int:
    result = summarize_ci(load_json(args.input))
    emit(result)
    return 0 if result["status"] == "VERIFIED" else 5


def cmd_render_readme(args) -> int:
    state = load_json(args.state or active_state_path(ROOT))
    health = doctor(ROOT)
    readme = Path(args.readme or ROOT / "README.md")
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- ADWF:STATUS:START -->", "<!-- ADWF:STATUS:END -->"
    if start not in text or end not in text:
        print("README markers missing", file=sys.stderr)
        return 6
    categories = health["categories"]
    block = (
        f"{start}\n## Текущее состояние framework\n\n"
        f"- Framework: `{framework_version()}`\n"
        f"- Package Integrity: `{categories['package_integrity']['status']}`\n"
        f"- Configuration: `{categories['config_health']['status']}`\n"
        f"- Control Plane: `{categories['control_plane_health']['status']}`\n"
        f"- Product Health: `{categories['product_health']['status']}`\n"
        f"- Autonomy: `{state.get('autonomy_level', 'A1')}`\n\n"
        f"> Отсутствующее или устаревшее evidence остаётся `NOT_VERIFIED/STALE`.\n{end}"
    )
    readme.write_text(text.split(start, 1)[0] + block + text.split(end, 1)[1], encoding="utf-8")
    print("README status block updated")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="adwf", description="ADWF v1.6 Executive Autopilot — integrated public-first fail-closed control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.add_argument("--product"); p.add_argument("--outcome"); p.add_argument("--public-confirmed", action="store_true"); p.add_argument("--license-acknowledged", action="store_true")
    p = sub.add_parser("start"); p.add_argument("task"); p.add_argument("--reject-if-busy", action="store_true")
    p = sub.add_parser("dashboard"); dsub = p.add_subparsers(dest="dashboard_command", required=True); ds = dsub.add_parser("serve"); ds.add_argument("--bind", default="127.0.0.1"); ds.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("preview"); p.add_argument("--url", required=True); p.add_argument("--baseline-url"); p.add_argument("--head-sha", required=True); p.add_argument("--output"); p.add_argument("--install", action="store_true")
    p = sub.add_parser("roadmap-view"); p.add_argument("--state")
    p = sub.add_parser("runtime-tick"); p.add_argument("--run-id", required=True)
    p = sub.add_parser("runtime-status"); p.add_argument("--run-id", required=True)
    p = sub.add_parser("project-pack"); p.add_argument("--project-root"); p.add_argument("--plan",action="store_true"); p.add_argument("--apply",action="store_true")
    p = sub.add_parser("portfolio-view"); p.add_argument("--register")
    p = sub.add_parser("release-plan"); p.add_argument("--input", required=True)
    p = sub.add_parser("release"); p.add_argument("--auto",action="store_true"); p.add_argument("--input"); p.add_argument("--prepare",action="store_true"); p.add_argument("--confirm",action="store_true"); p.add_argument("--external",action="store_true"); p.add_argument("--publish-github",action="store_true"); p.add_argument("--output",default="dist")
    p = sub.add_parser("doctor"); p.add_argument("--scope", choices=["all", "package_integrity", "config_health", "control_plane_health", "product_health"], default="all"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("status"); p.add_argument("--input")
    p = sub.add_parser("validate"); p.add_argument("--input", required=True); p.add_argument("--schema", required=True)
    p = sub.add_parser("issue-audit"); p.add_argument("--input", required=True)
    p = sub.add_parser("roadmap-audit"); p.add_argument("--input", required=True)
    p = sub.add_parser("permission"); p.add_argument("--input", required=True)
    sub.add_parser("continue")
    p = sub.add_parser("claim"); p.add_argument("--queue", required=True); p.add_argument("--issue", required=True); p.add_argument("--worker", required=True); p.add_argument("--base-sha", required=True); p.add_argument("--ttl", type=int, default=120); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("reconcile"); p.add_argument("--queue", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("transition"); p.add_argument("--input", required=True); p.add_argument("--to", required=True); p.add_argument("--predicates", required=True); p.add_argument("--expected"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("evidence-verify"); p.add_argument("--input", required=True); p.add_argument("--sha"); p.add_argument("--runtime-revision")
    p = sub.add_parser("provider-check"); p.add_argument("--input", required=True)
    sub.add_parser("self-test"); sub.add_parser("adversarial-test")
    p = sub.add_parser("render-control-center"); p.add_argument("--state"); p.add_argument("--queue"); p.add_argument("--output"); p.add_argument("--provider"); p.add_argument("--format", choices=["md", "html"], default="md")
    p = sub.add_parser("render-readme"); p.add_argument("--state"); p.add_argument("--readme")
    p = sub.add_parser("policy-compile"); p.add_argument("--check", action="store_true")
    p = sub.add_parser("workspace-plan"); p.add_argument("--lease", required=True)
    p = sub.add_parser("workspace-create"); p.add_argument("--lease", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("workspace-heartbeat"); p.add_argument("--workspace-id", required=True); p.add_argument("--worker", required=True)
    p = sub.add_parser("workspace-reconcile"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("workspace-retry"); p.add_argument("--workspace-id", required=True); p.add_argument("--error", required=True)
    p = sub.add_parser("workspace-complete"); p.add_argument("--workspace-id", required=True)
    p = sub.add_parser("workspace-cleanup"); p.add_argument("--workspace-id", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("metrics-summary"); p.add_argument("--input", required=True)
    p = sub.add_parser("orchestration-start"); p.add_argument("--input", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("orchestration-step"); p.add_argument("--run-id", required=True); p.add_argument("--result", required=True); p.add_argument("--control-context", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("orchestration-status"); p.add_argument("--run-id", required=True)
    p = sub.add_parser("ci-setup-plan"); p.add_argument("--project-root"); p.add_argument("--capability")
    p = sub.add_parser("incident-normalize"); p.add_argument("--input", required=True)
    p = sub.add_parser("incident-record"); p.add_argument("--input", required=True); p.add_argument("--store"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("incident-summary"); p.add_argument("--store")
    p = sub.add_parser("healing-evaluate"); p.add_argument("--incident", required=True); p.add_argument("--context", required=True); p.add_argument("--recipes"); p.add_argument("--circuit")
    p = sub.add_parser("healing-verify"); p.add_argument("--context", required=True)
    p = sub.add_parser("owner-brief"); p.add_argument("--input", required=True)
    p = sub.add_parser("owner-preview"); p.add_argument("--input", required=True)
    p = sub.add_parser("owner-acceptance"); p.add_argument("--input", required=True)
    p = sub.add_parser("owner-changelog"); p.add_argument("--input", required=True)
    args = parser.parse_args()
    routes = {
        "init": cmd_owner_init, "start": cmd_owner_start, "dashboard": cmd_dashboard_serve,
        "preview": cmd_preview, "roadmap-view": cmd_roadmap_view, "runtime-tick": cmd_runtime_tick, "runtime-status": cmd_runtime_status,
        "project-pack": cmd_project_pack, "portfolio-view": cmd_portfolio_view, "release-plan": cmd_release_plan, "release": cmd_release,
        "doctor": cmd_doctor, "status": cmd_status, "validate": cmd_validate,
        "issue-audit": cmd_issue, "roadmap-audit": cmd_roadmap, "permission": cmd_permission,
        "continue": cmd_continue, "claim": cmd_claim, "reconcile": cmd_reconcile,
        "transition": cmd_transition, "evidence-verify": cmd_evidence,
        "provider-check": cmd_provider, "self-test": lambda _: run_tests("test_*.py"),
        "adversarial-test": lambda _: run_tests("test_adversarial.py"),
        "render-control-center": cmd_dashboard, "render-readme": cmd_render_readme,
        "policy-compile": cmd_policy_compile,
        "workspace-plan": cmd_workspace_plan, "workspace-create": cmd_workspace_create,
        "workspace-heartbeat": cmd_workspace_heartbeat, "workspace-reconcile": cmd_workspace_reconcile,
        "workspace-retry": cmd_workspace_retry, "workspace-complete": cmd_workspace_complete,
        "workspace-cleanup": cmd_workspace_cleanup,
        "metrics-summary": cmd_metrics,
        "orchestration-start": cmd_orchestration_start,
        "orchestration-step": cmd_orchestration_step,
        "orchestration-status": cmd_orchestration_status,
        "ci-setup-plan": cmd_ci_setup,
        "incident-normalize": cmd_incident_normalize,
        "incident-record": cmd_incident_record,
        "incident-summary": cmd_incident_summary,
        "healing-evaluate": cmd_healing_evaluate,
        "healing-verify": cmd_healing_verify,
        "owner-brief": cmd_owner_brief,
        "owner-preview": cmd_owner_preview,
        "owner-acceptance": cmd_owner_acceptance,
        "owner-changelog": cmd_owner_changelog,
    }
    return routes[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
