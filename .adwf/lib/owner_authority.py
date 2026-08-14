"""Single owner authority adapter for Executive Portal and CLI."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json,secrets,hashlib
from .durable_orchestrator import OrchestrationJournal
from .runtime_supervisor import RuntimeSupervisor
from .github_auth import detect_repository,discover_token
from .github_provider import GitHubClient
from .github_owner_decisions import GitHubOwnerDecisionStore
from .owner_experience import record_owner_acceptance
from .policy_runtime import load_effective_policy
from .controller_wakeup import wake_controller


def _decision_key(record:dict[str,Any])->str:
    body={k:record.get(k) for k in ('brief_id','status','head_sha','preview_digest','policy_hash','decided_by','nonce')}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _persist_local(root:Path,record:dict[str,Any])->dict[str,Any]:
    ledger=root/'.adwf-runtime/owner-decisions.jsonl';ledger.parent.mkdir(parents=True,exist_ok=True);key=_decision_key(record)
    if ledger.is_file():
        for line in ledger.read_text(encoding='utf-8').splitlines():
            if line.strip():
                existing=json.loads(line)
                if existing.get('idempotency_key')==key:return existing
    stored={'idempotency_key':key,**record}
    with ledger.open('a',encoding='utf-8') as h:h.write(json.dumps(stored,ensure_ascii=False,sort_keys=True)+'\n')
    return stored


def accept_and_continue(root:str|Path, *, brief_id:str, head_sha:str, preview_digest:str)->dict[str,Any]:
    base=Path(root).resolve();active=OrchestrationJournal(base).list_active()
    if len(active)!=1:return {'status':'BLOCK','reason':'SINGLE_ACTIVE_RUN_REQUIRED'}
    state=active[0]
    if state.get('roadmap_id')!=brief_id:return {'status':'BLOCK','reason':'OWNER_BRIEF_RUN_BINDING_MISMATCH'}
    if state.get('phase')!='OWNER_ACCEPTANCE':return {'status':'BLOCK','reason':'OWNER_ACCEPTANCE_PHASE_REQUIRED','phase':state.get('phase')}
    if state.get('subject_sha')!=head_sha or state.get('preview_digest')!=preview_digest:
        return {'status':'BLOCK','reason':'OWNER_ACCEPTANCE_EXACT_REVISION_MISMATCH'}
    policy=load_effective_policy(base);nonce=secrets.token_hex(16);repo=detect_repository(base);token,source=discover_token()
    if not repo or not token:
        record=record_owner_acceptance(brief_id=brief_id,decision='ACCEPTED',head_sha=head_sha,preview_digest=preview_digest,actor='local-owner',authority='OWNER',nonce=nonce,source='LOCAL_AUTHENTICATED',provider_readback=False,policy_hash=policy.get('policy_hash'),note_ru='Продолжить из Executive Portal')
        _persist_local(base,record)
        return {'status':'HUMAN_REQUIRED','reason':'PROVIDER_ATTESTED_OWNER_IDENTITY_REQUIRED','credential_source':source}
    client=GitHubClient(repo,token)
    provider=GitHubOwnerDecisionStore(client).record(decision='ACCEPTED',head_sha=head_sha,preview_digest=preview_digest,policy_hash=policy.get('policy_hash'),nonce=nonce)
    record=record_owner_acceptance(brief_id=brief_id,decision='ACCEPTED',head_sha=head_sha,preview_digest=preview_digest,actor=provider['actor_login'],authority='OWNER',nonce=nonce,source='GITHUB_AUTHENTICATED',provider_readback=True,policy_hash=policy.get('policy_hash'),note_ru='Продолжить из Executive Portal')
    stored=_persist_local(base,record)
    sup=RuntimeSupervisor(base);key,_=sup.envelopes.ensure_request(state)
    result={'phase':'OWNER_ACCEPTANCE','outcome':'PASS','idempotency_key':key,'subject_sha':head_sha,'preview_digest':preview_digest,'evidence_refs':[],'reason_codes':[],'cost_usd':0,'metadata':{'owner_acceptance_exact':True,'accepted_preview_digest':preview_digest,'provider_decision_id':provider.get('comment_id'),'actor_login':provider.get('actor_login')}}
    sup.envelopes.write_result(key,result)
    advanced=sup.tick(state['run_id'])
    wake=wake_controller(base,run_id=state['run_id'],reason='OWNER_ACCEPTED',request_id=stored['idempotency_key'])
    return {'status':'CONTINUED' if advanced.get('status') not in {'BLOCKED'} else 'BLOCK','decision':stored,'run':advanced.get('run'),'wakeup':wake}
