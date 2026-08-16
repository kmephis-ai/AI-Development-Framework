#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json
import re
import shutil
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply.py <candidate-root>")
root = Path(sys.argv[1]).resolve()
support = Path(__file__).resolve().parent / "payload"
if not ((root / ".git").is_file() or (root / ".git").is_dir()):
    raise SystemExit("candidate is not a git worktree")

copies = {
    "creative_agent_qualification.py": ".adwf/lib/creative_agent_qualification.py",
    "reference_agent_adapter.py": ".adwf/scripts/reference_agent_adapter.py",
    "qualify_creative_agent.py": ".adwf/scripts/qualify_creative_agent.py",
    "test_creative_agent_qualification.py": ".adwf/tests/test_creative_agent_qualification.py",
    "creative-agent-adapters.schema.json": ".adwf/schemas/creative-agent-adapters.schema.json",
    "creative-agent-qualification-report.schema.json": ".adwf/schemas/creative-agent-qualification-report.schema.json",
    "CREATIVE_AGENT_QUALIFICATION.md": "docs/governance/CREATIVE_AGENT_QUALIFICATION.md",
}
for source_name, target_name in copies.items():
    source = support / source_name
    target = root / target_name
    if not source.is_file():
        raise SystemExit("SUPPORT_PAYLOAD_MISSING:" + source_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)

# Replace raw env-command authority with qualified adapter authority.
path = root / ".adwf/lib/action_executors.py"
text = path.read_text(encoding="utf-8")
old_import = "import json,os,re,shlex,subprocess,sys"
if text.count(old_import) != 1:
    raise SystemExit("ACTION_EXECUTOR_IMPORT_BASE_MISMATCH")
text = text.replace(old_import, "import json,os,re,subprocess,sys", 1)
old_ai = "from .ai_work_contracts import canonicalize_low_trust_claim\n"
new_ai = old_ai + "from .creative_agent_qualification import command_argv,load_qualified_command_adapter,sanitized_agent_environment,verify_local_command_result\n"
if text.count(old_ai) != 1:
    raise SystemExit("ACTION_EXECUTOR_AI_IMPORT_BASE_MISMATCH")
text = text.replace(old_ai, new_ai, 1)
replacement = '''def _run_agent_command(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|None:
    raw_command=os.environ.get('ADWF_AGENT_COMMAND','').strip()
    adapter_id=os.environ.get('ADWF_AGENT_ADAPTER_ID','').strip()
    if not raw_command and not adapter_id:return None
    if raw_command and not adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_UNQUALIFIED'])
    if raw_command and adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_OVERRIDE_FORBIDDEN'])
    try:
        adapter=load_qualified_command_adapter(root,adapter_id,state['phase'])
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_ADAPTER_UNQUALIFIED'],metadata={'contract_error':str(exc)[:300]})
    if adapter.get('kind')=='REFERENCE_DETERMINISTIC' and os.environ.get('ADWF_ALLOW_REFERENCE_AGENT')!='1':
        return _result(state,key,'FAIL',reason_codes=['REFERENCE_AGENT_RUNTIME_FORBIDDEN'])
    package=envelope.get('work_package')
    if not isinstance(package,dict):return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_MISSING'])
    if envelope.get('work_package_digest') not in {None,package.get('package_digest')}:
        return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_DIGEST_MISMATCH'])
    request=root/'.adwf-runtime/supervisor/requests'/f'{key}.json';result=root/'.adwf-runtime/supervisor/results'/f'{key}.json'
    try:
        argv=command_argv(root,adapter)
        env=sanitized_agent_environment(os.environ,request=request,result=result,state=state,adapter=adapter)
        proc=subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,check=False,timeout=int(adapter['timeout_seconds']))
    except subprocess.TimeoutExpired:
        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_TIMEOUT'])
    except (OSError,ValueError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_START_FAILED'],metadata={'contract_error':str(exc)[:300]})
    if proc.returncode:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_FAILED'],metadata={'exit_code':proc.returncode,'stderr_tail':proc.stderr[-500:]})
    if not result.is_file():return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_MISSING'])
    try:value=strict_loads(result.read_text(encoding='utf-8'))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'],metadata={'contract_error':str(exc)[:300]})
    if not isinstance(value,dict):return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'])
    try:work_result=canonicalize_low_trust_claim(value,package=package)
    except ValueError as exc:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_CONTRACT_INVALID'],metadata={'contract_error':str(exc)[:300]})
    local_errors=verify_local_command_result(root,package,work_result)
    if local_errors:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_LOCAL_BINDING_INVALID'],metadata={'binding_errors':local_errors})
    return {'phase':state['phase'],'outcome':work_result['outcome'],'idempotency_key':key,'subject_sha':work_result.get('head_sha'),
            'preview_digest':state.get('preview_digest'),'evidence_refs':[],'reason_codes':work_result['reason_codes'],
            'transient':work_result['outcome']=='RETRY','cost_usd':0,'metadata':{'source':'LOW_TRUST_AGENT_COMMAND','adapter_id':adapter['id'],'adapter_version':adapter['version'],'ai_work_result':work_result}}
'''
pattern = re.compile(r"def _run_agent_command\(.*?\n\ndef creative_executor", re.S)
matches = list(pattern.finditer(text))
if len(matches) != 1:
    raise SystemExit("ACTION_EXECUTOR_FUNCTION_BASE_MISMATCH")
text = text[:matches[0].start()] + replacement + "\n\ndef creative_executor" + text[matches[0].end():]
path.write_text(text, encoding="utf-8", newline="\n")

# Human-facing AI Work Contract truth.
ai_doc = root / "docs/governance/AI_WORK_CONTRACTS.md"
ai_text = ai_doc.read_text(encoding="utf-8")
marker = "## Qualified Creative Agent invocation boundary"
if marker in ai_text:
    raise SystemExit("AI_WORK_DOC_ALREADY_HAS_AGENTQUAL")
ai_text = ai_text.rstrip() + '''

## Qualified Creative Agent invocation boundary

`AGENTQUAL-001` не повышает trust creative output. Command executor принимается только через versioned Creative Agent qualification registry/report; raw `ADWF_AGENT_COMMAND` без qualified adapter блокируется. Qualified command получает secret-filtered environment и exact `AIWorkPackage`, а возвращаемый `AIWorkResult` остаётся `LOW_TRUST` до downstream trusted/provider verification. `reference-local` является deterministic offline qualification fixture, а не внешним AI/provider evidence.
'''
ai_doc.write_text(ai_text, encoding="utf-8", newline="\n")

sys.path.insert(0, str(root / ".adwf"))
from lib.creative_agent_qualification import PROFILE_ID, PROFILE_VERSION, qualification_profile_digest, reference_qualification_report, seal_registry

profile_digest = qualification_profile_digest()
registry = seal_registry({
    "$schema": ".adwf/schemas/creative-agent-adapters.schema.json",
    "schema_version": 1,
    "qualification_profile": {"id": PROFILE_ID, "version": PROFILE_VERSION, "digest": profile_digest},
    "adapters": [{
        "id": "reference-local",
        "version": "1.0.0",
        "kind": "REFERENCE_DETERMINISTIC",
        "invocation_mode": "COMMAND",
        "supported_phases": ["EXECUTE", "RECOVERY"],
        "command": {"runner": "PYTHON", "path": ".adwf/scripts/reference_agent_adapter.py"},
        "authority": {"network": "NONE", "secrets": "FORBIDDEN", "filesystem": "PACKAGE_SCOPED"},
        "monetary_budget_usd": 0,
        "timeout_seconds": 60,
        "result_channel": "ADWF_ACTION_RESULT_JSON",
        "package_schema": ".adwf/schemas/ai-work-package.schema.json",
        "result_schema": ".adwf/schemas/ai-work-result.schema.json",
        "qualification_report": ".adwf/creative-agent-qualification.json",
        "qualification_profile_id": PROFILE_ID,
        "qualification_profile_version": PROFILE_VERSION,
        "qualification_profile_digest": profile_digest,
    }],
})
(root / ".adwf/creative-agent-adapters.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
report = reference_qualification_report(registry["adapters"][0])
(root / ".adwf/creative-agent-qualification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Rolling-wave Roadmap tail.
roadmap_path = root / ".adwf/roadmap.json"
roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))
tasks = next(goal["tasks"] for goal in roadmap["goals"] if goal["id"] == "FOUNDATION-ENGINEERING-OS")
if tasks[-1].get("roadmap_id") != "EDGEREF-001" or any(item.get("roadmap_id") == "AGENTQUAL-001" for item in tasks):
    raise SystemExit("ROADMAP_TAIL_DRIFT")
tasks.append({
    "roadmap_id": "AGENTQUAL-001",
    "title_ru": "Replaceable Creative Agent Qualification Contract + Reference Adapter v1",
    "dependencies": ["EDGEREF-001"],
    "product_impact": False,
})
roadmap_path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Conservative capability truth.
cap_path = root / ".adwf/capability-traceability.json"
cap = json.loads(cap_path.read_text(encoding="utf-8"))
if any(item.get("id") == "CREATIVE_AGENT_QUALIFICATION" for item in cap["capabilities"]):
    raise SystemExit("CAPABILITY_ALREADY_HAS_AGENTQUAL")
cap["capabilities"].append({
    "id": "CREATIVE_AGENT_QUALIFICATION",
    "status": "LIVE_NOT_VERIFIED",
    "execution_mode": "OPTIONAL_ADAPTER",
    "owner_claim_ru": "ADWF принимает заменяемый Creative Agent command adapter только через строгую versioned qualification declaration/report; invocation authority ограничена exact work package, а creative result остаётся low-trust до trusted/provider verification.",
    "entrypoints": [".adwf/scripts/qualify_creative_agent.py"],
    "production_paths": [
        ".adwf/lib/creative_agent_qualification.py",
        ".adwf/creative-agent-adapters.json",
        ".adwf/creative-agent-qualification.json",
        ".adwf/schemas/creative-agent-adapters.schema.json",
        ".adwf/schemas/creative-agent-qualification-report.schema.json",
        ".adwf/lib/action_executors.py"
    ],
    "verification": [".adwf/tests/test_creative_agent_qualification.py", ".adwf/tests/test_ai_work_contracts.py"],
    "live_boundary": "Synthetic reference-local qualification доказывает fail-closed invocation contract, exact package/result binding и zero-cost offline boundary. LIVE_VERIFIED требует реальный внешний creative adapter/agent result, привязанный к exact AIWorkPackage, плюс downstream trusted/provider exact-SHA evidence; deterministic reference adapter не является AI/provider/live proof.",
    "live_evidence": []
})
cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Append-only durable requirement/decision trace.
trace_path = root / ".adwf/decision-requirement-traceability.json"
trace = json.loads(trace_path.read_text(encoding="utf-8"))
if trace.get("revision") != 18 or any(item.get("id") == "REQ-AGENTQUAL-001" for item in trace["records"]):
    raise SystemExit("TRACE_BASE_DRIFT")
trace["revision"] = 19
trace["records"].extend([
    {
        "id": "REQ-AGENTQUAL-001", "kind": "REQUIREMENT", "version": 1, "status": "ACTIVE",
        "title_ru": "Creative Agent invocation должна иметь квалифицированный versioned boundary",
        "statement_ru": "Raw replaceable creative executor не получает authority только из env command: adapter обязан декларировать phase/invocation/cost/network/secrets/filesystem/result-channel semantics и быть exact-package/result bound; synthetic qualification не становится trusted или live creative evidence.",
        "source_path": None, "source_sha256": None, "record_sha256": ""
    },
    {
        "id": "DEC-AGENTQUAL-001", "kind": "DECISION", "version": 1, "status": "ACCEPTED",
        "title_ru": "Квалификация ограничивает invocation authority, но не повышает trust creative output",
        "statement_ru": "Добавить provider-neutral versioned adapter registry + qualification report; mandatory reference adapter остаётся local/offline/zero-cost/secret-filtered/package-scoped. Low-trust AIWorkResult по-прежнему требует существующую trusted/provider verification, а GitHub Agent Inbox остаётся low-trust channel.",
        "source_path": None, "source_sha256": None, "record_sha256": ""
    }
])
trace["capability_refs"].append({"id": "CAPREF-CREATIVE-AGENT-QUALIFICATION", "capability_id": "CREATIVE_AGENT_QUALIFICATION", "ref_sha256": ""})
trace["work_unit_refs"].append({"id": "WORKREF-AGENTQUAL-001", "roadmap_id": "AGENTQUAL-001", "issue_number": 99, "ai_work_package_id": None, "ref_sha256": ""})
trace["edges"].extend([
    {"id": "EDGE-REQ-DEC-AGENTQUAL-001", "type": "REQUIREMENT_TO_DECISION", "from": "REQ-AGENTQUAL-001", "to": "DEC-AGENTQUAL-001", "edge_sha256": ""},
    {"id": "EDGE-DEC-CAP-AGENTQUAL-001", "type": "DECISION_TO_CAPABILITY", "from": "DEC-AGENTQUAL-001", "to": "CAPREF-CREATIVE-AGENT-QUALIFICATION", "edge_sha256": ""},
    {"id": "EDGE-CAP-WORK-AGENTQUAL-001", "type": "CAPABILITY_TO_WORK", "from": "CAPREF-CREATIVE-AGENT-QUALIFICATION", "to": "WORKREF-AGENTQUAL-001", "edge_sha256": ""}
])
from lib.decision_traceability import seal_graph
trace_path.write_text(json.dumps(seal_graph(trace), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Register new governance document; canonical docs generator replaces placeholder digest/times.
docs_path = root / ".adwf/docs-registry.json"
docs = json.loads(docs_path.read_text(encoding="utf-8"))
if any(item.get("path") == "docs/governance/CREATIVE_AGENT_QUALIFICATION.md" for item in docs["documents"]):
    raise SystemExit("DOCS_ALREADY_HAS_AGENTQUAL")
docs["documents"].append({
    "path": "docs/governance/CREATIVE_AGENT_QUALIFICATION.md",
    "watched": [
        ".adwf/lib/creative_agent_qualification.py",
        ".adwf/lib/action_executors.py",
        ".adwf/creative-agent-adapters.json",
        ".adwf/creative-agent-qualification.json",
        ".adwf/schemas/creative-agent-adapters.schema.json",
        ".adwf/schemas/creative-agent-qualification-report.schema.json",
        ".adwf/scripts/reference_agent_adapter.py",
        ".adwf/scripts/qualify_creative_agent.py",
        ".adwf/tests/test_creative_agent_qualification.py",
        ".adwf/roadmap.json",
        ".adwf/capability-traceability.json",
        ".adwf/decision-requirement-traceability.json"
    ],
    "mode": "governance-contract",
    "source_digest": "0" * 64,
    "reviewed_at": "2026-08-16T20:30:00Z",
    "valid_until": "2026-11-16T20:30:00Z"
})
docs_path.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("AGENTQUAL_APPLY_V2: PASS")
