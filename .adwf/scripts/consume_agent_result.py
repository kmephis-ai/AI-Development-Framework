#!/usr/bin/env python3
"""Consume a bounded GitHub Agent Inbox result, or a strict provider CLAIM request.

The GitHub comment is LOW_TRUST delivery data. Trusted code re-validates the
bound request/result before any durable transition. No comment grants merge,
policy, ruleset, Capability Truth, or secret authority.
"""
from __future__ import annotations
from pathlib import Path
import json,os,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.work_memory import WorkMemoryStore
from lib.durable_orchestrator import OrchestrationJournal
from lib.runtime_supervisor import RuntimeSupervisor
from lib.github_agent_inbox import GitHubAgentInbox
from lib.github_auth import detect_repository,discover_token
from lib.github_provider import GitHubClient
from lib.ai_work_contracts import validate_work_result
from lib.provider_claim_gateway import has_claim_marker,process_issue_comment_claim
from lib.strict_json import loads as strict_loads


def _event():
    raw=os.environ.get('GITHUB_EVENT_PATH','').strip()
    if not raw:return None
    path=Path(raw)
    if not path.is_file():return None
    try:value=strict_loads(path.read_text(encoding='utf-8'))
    except Exception:return None
    return value if isinstance(value,dict) else None


def main()->int:
    event=_event();comment=((event or {}).get('comment') or {}) if isinstance(event,dict) else {}
    if has_claim_marker(comment.get('body')):
        repo=detect_repository(ROOT);token,_=discover_token()
        if not repo or not token:
            print('GITHUB_CONNECTION_REQUIRED');return 1
        result=process_issue_comment_claim(ROOT,event or {},GitHubClient(repo,token))
        if result is None:
            print('CLAIM_REQUEST_ROUTING_FAILED');return 1
        print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':')))
        return 1 if result.get('status')=='NOT_VERIFIED' else 0

    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:print('NO_SINGLE_ACTIVE_RUN');return 0
    state=active[0];phase=state.get('phase')
    if phase not in {'EXECUTE','RECOVERY'}:print('IGNORED_PHASE',phase);return 0
    repo=detect_repository(ROOT);token,_=discover_token()
    if not repo or not token:print('GITHUB_CONNECTION_REQUIRED');return 1
    sup=RuntimeSupervisor(ROOT);key=sup.idempotency_key(state)
    req_path=ROOT/'.adwf-runtime/supervisor/requests'/f'{key}.json'
    if not req_path.is_file():print('REQUEST_NOT_MATERIALIZED');return 0
    request=strict_loads(req_path.read_text(encoding='utf-8'));package=request.get('work_package')
    if not isinstance(package,dict):print('AI_WORK_PACKAGE_MISSING');return 1
    inbox=GitHubAgentInbox(GitHubClient(repo,token));found=inbox.find_matching_result(
        run_id=state['run_id'],phase=phase,idempotency_key=key,work_package_digest=str(request.get('work_package_digest') or package.get('package_digest') or ''))
    if found is None:print('WAITING_AGENT_RESULT');return 0
    result=found['result'];errors=validate_work_result(result,expected_package=package)
    if errors:print('AGENT_RESULT_INVALID',','.join(errors));return 1
    envelope={'phase':phase,'idempotency_key':key,'work_package':package,'work_package_digest':package['package_digest'],'work_result':result,
              'source':{'kind':'GITHUB_ISSUE_COMMENT','issue_number':found['issue_number'],'comment_id':found['comment_id'],'html_url':found['html_url']}}
    result_path=ROOT/'.adwf-runtime/supervisor/results'/f'{key}.json';result_path.parent.mkdir(parents=True,exist_ok=True);result_path.write_text(json.dumps(envelope,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    try:
        memory=WorkMemoryStore(ROOT).load();rev=memory['revision'];memory['result_digest']=result['result_digest'];memory['current_summary_ru']='Получен LOW_TRUST Agent Inbox result; trusted verification продолжается.';memory['next_action_ru']='Продолжить trusted Runtime Supervisor с provider/test evidence.'
        WorkMemoryStore(ROOT).save(memory,expected_revision=rev)
    except ValueError:pass
    updated=sup.tick(safe_context={})
    print('CONSUMED_AGENT_RESULT',found['issue_number'],found['comment_id'],'PHASE',updated.get('phase'));return 0

if __name__=='__main__':raise SystemExit(main())
