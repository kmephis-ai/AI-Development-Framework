"""Atomic Owner Intent service bound to the Durable Orchestrator SSOT."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import hashlib,json,os,tempfile
from .durable_orchestrator import OrchestrationJournal,new_run
from .owner_experience import create_product_brief
from .work_memory import WorkMemoryStore,new_work_memory
from .controller_wakeup import wake_controller
from .github_auth import detect_repository, discover_token
from .github_provider import GitHubClient
from .github_runtime_store import GitHubRuntimeStore
from .github_rulesets import verify_rulesets,verify_runtime_anchor_ruleset,discover_check_source
from .strict_json import loads as strict_loads
from .file_lock import exclusive_file_lock


def create_intent(task:str)->dict[str,Any]:
    task=str(task).strip()
    if len(task)<5:raise ValueError('OWNER_INTENT_TOO_SHORT')
    return create_product_brief({'goal_ru':task,'value_ru':'Получить полезный результат, описанный владельцем.','outcome_ru':task,
      'acceptance_criteria_ru':['Результат соответствует исходному намерению владельца.'],
      'visual_expectation_ru':'Если изменение визуальное — показать before/after desktop/mobile; иначе показать проверяемое резюме результата.',
      'constraints_ru':['FREE_ONLY mandatory path; fail-closed; no trust-boundary self-approval.'],'owner_request_original':task})


def _atomic(path:Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _queue_brief(root:Path,brief:dict[str,Any])->dict[str,Any]:
    path=root/'.adwf-runtime/backlog/briefs.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
    existing=[]
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():existing.append(strict_loads(line))
    if any(x.get('brief_id')==brief.get('brief_id') for x in existing):return {'status':'ALREADY_QUEUED','brief':brief}
    with path.open('a',encoding='utf-8') as h:h.write(json.dumps({'status':'QUEUED',**brief},ensure_ascii=False,sort_keys=True)+'\n')
    return {'status':'QUEUED_NEW_TASK','brief':brief,'queue_position':len(existing)+1}


def start_or_queue(root:str|Path,task:str,*,queue_if_busy:bool=True,wake:bool=True)->dict[str,Any]:
    base=Path(root).resolve();lock=base/'.adwf-runtime/owner-intent.lock'
    # One critical section owns active-run discovery + brief/run creation. This
    # prevents two simultaneous Portal/CLI requests from creating split-brain runs.
    with exclusive_file_lock(lock):
        return _start_or_queue_locked(base,task,queue_if_busy=queue_if_busy,wake=wake)


def _start_or_queue_locked(base:Path,task:str,*,queue_if_busy:bool,wake:bool)->dict[str,Any]:
    journal=OrchestrationJournal(base);active=journal.list_active()
    # Critical v1.6 ordering invariant: never mutate active Brief/Work Memory before
    # deciding what to do with an already-active run.
    if active:
        if len(active)!=1:raise ValueError('MULTIPLE_OR_BROKEN_ACTIVE_RUNS')
        if not queue_if_busy:return {'status':'ACTIVE_TASK_EXISTS','run_id':active[0].get('run_id'),'phase':active[0].get('phase'),'brief_id':active[0].get('roadmap_id')}
        return _queue_brief(base,create_intent(task))
    brief=create_intent(task);digest=hashlib.sha256(str(brief.get('owner_request_original') or task).encode()).hexdigest()
    issue_id='PENDING'; repo=detect_repository(base);token,credential_source=discover_token()
    client=None
    if repo and token:
        client=GitHubClient(repo,token);rules=client.rulesets();source_proof=discover_check_source(client)
        integration_id=source_proof.get('integration_id') if source_proof.get('status')=='VERIFIED' else None
        branch_rules=verify_rulesets(rules,expected_integration_id=int(integration_id) if integration_id is not None else None);anchor_rules=verify_runtime_anchor_ruleset(rules)
        if source_proof.get('status')!='VERIFIED' or not branch_rules.get('readback_verified') or not anchor_rules.get('readback_verified'):
            return {'status':'HUMAN_REQUIRED','reason':'GITHUB_BOOTSTRAP_NOT_VERIFIED','repository':repo,'check_source':source_proof,'ruleset':branch_rules,'runtime_anchor_ruleset':anchor_rules,'credential_source':credential_source}
        issue=client.create_issue('[ADWF] '+str(task).strip()[:90],f"ADWF Owner Intent\n\nBrief: `{brief['brief_id']}`\n\n{str(task).strip()}\n")
        issue_id=str(issue.get('number') or 'PENDING')
    run=new_run(base,roadmap_id=brief['brief_id'],issue_id=issue_id,risk='R1',work_type='feature',product_impact=True,owner_request_digest=digest,max_elapsed_minutes=10080)
    memory=new_work_memory(brief_id=brief['brief_id'],task_ru=task,run_id=run['run_id']);memory['product_brief']=brief;memory['constraints']=list(brief.get('constraints_ru') or []);memory['status']='ACTIVE';memory['next_action_ru']='ADWF сверяет GitHub и готовит безопасный рабочий цикл.'
    WorkMemoryStore(base).save(memory)
    _atomic(base/'.adwf-runtime/owner-intent.json',brief)
    # project-state is a projection only; mark the source run/revision explicitly.
    state_path=base/'.adwf-runtime/project-state.json'
    state=strict_loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {}
    state.setdefault('owner_experience',{})['product_brief']=brief
    state['durable_projection']={'run_id':run['run_id'],'run_revision':run['revision'],'brief_id':brief['brief_id'],'authoritative_source':'DURABLE_ORCHESTRATOR'}
    _atomic(state_path,state)
    output={'status':'AUTOPILOT_STARTED','brief':brief,'run_id':run['run_id'],'phase':run['phase'],'issue_id':issue_id}
    if client is not None:
        output['runtime_ledger']=GitHubRuntimeStore(client).append(run,memory)
    if wake:output['wakeup']=wake_controller(base,run_id=run['run_id'],reason='OWNER_INTENT',request_id=brief['brief_id'])
    return output
