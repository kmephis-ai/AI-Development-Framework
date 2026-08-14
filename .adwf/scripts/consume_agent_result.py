#!/usr/bin/env python3
"""Consume a bounded low-trust Agent Inbox result and wake Runtime Supervisor."""
from __future__ import annotations
from pathlib import Path
import json,os,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.runtime_supervisor import RuntimeSupervisor
from lib.github_provider import GitHubClient
from lib.github_agent_inbox import GitHubAgentInbox,validate_agent_result
from lib.strict_json import loads as strict_loads
from lib.work_memory import WorkMemoryStore

def main()->int:
    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:print(json.dumps({'status':'NO_SINGLE_ACTIVE_RUN','active':len(active)}));return 0
    state=active[0]
    if state.get('phase') not in {'EXECUTE','RECOVERY'}:print(json.dumps({'status':'IGNORED_PHASE','phase':state.get('phase')}));return 0
    repo,token=os.environ.get('GITHUB_REPOSITORY',''),os.environ.get('GITHUB_TOKEN','')
    if not repo or not token:raise SystemExit('GITHUB_REPOSITORY/GITHUB_TOKEN missing')
    sup=RuntimeSupervisor(ROOT);key,request_path=sup.envelopes.ensure_request(state);request=strict_loads(request_path.read_text(encoding='utf-8'));inbox=GitHubAgentInbox(GitHubClient(repo,token))
    matches=[x for x in inbox.results() if x.get('idempotency_key')==key and x.get('run_id')==state['run_id'] and x.get('phase')==state['phase']]
    if not matches:print(json.dumps({'status':'WAITING_AGENT_RESULT','idempotency_key':key}));return 0
    result=validate_agent_result(matches[-1],request=request);sup.envelopes.write_result(key,result)
    branch=(result.get('metadata') or {}).get('branch');memory=WorkMemoryStore(ROOT).load()
    if memory and branch and result.get('subject_sha'):
        rev=memory['revision'];refs=memory.setdefault('references',{});refs.setdefault('branches',[]).append({'name':branch,'sha':result['subject_sha']});summary=(result.get('metadata') or {}).get('summary_ru')
        if summary:memory.setdefault('completed',[]).append(summary)
        WorkMemoryStore(ROOT).save(memory,expected_revision=rev)
    out=sup.tick(state['run_id']);print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
