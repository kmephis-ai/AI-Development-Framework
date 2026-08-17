"""Public-safe GitHub checkpoint ledger for hosted multi-day execution.

The public Issue stores only Durable Orchestrator state + a safe Work Memory
projection. Every checkpoint is externally anchored by a unique protected
annotated Git tag whose name contains the event hash. The separate tag ruleset
blocks update/deletion, so deleting/replacing Issue comments leaves orphan
anchors that the verifier detects instead of silently accepting a rewritten
history.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import hashlib,json,os,tempfile,copy,re
from .durable_orchestrator import validate_journal
from .github_provider import GitHubClient
from .github_rulesets import verify_runtime_anchor_ruleset
from .session_continuity import reconcile_checkpoint,validate_checkpoint

TITLE='[ADWF] Runtime Ledger';PREFIX='<!-- ADWF-RUNTIME-EVENT v3 -->\n```json\n';SUFFIX='\n```';ANCHOR_PREFIX='adwf-runtime-anchor-'
def _now()->str:return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def _canonical(v:Any)->bytes:return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def _hash(v:Any)->str:return hashlib.sha256(_canonical(v)).hexdigest()
def _tag_name(event:dict[str,Any])->str:return f"{ANCHOR_PREFIX}{int(event['sequence']):06d}-{str(event['event_hash'])[:16]}"


SAFE_EVENT_METADATA={'issue_id','repository','default_branch','pull_request_number','merge_sha','attestation_id','artifact_digest','adapter','not_applicable_reason','owner_acceptance_exact','provider_decision_id','actor_login','work_branch','promotion','observation','production_verified','cleanup','queue_handled_by_owner_intent_service','authorization_source','lease_model','workspace_mode','run_id'}
SAFE_REASON=re.compile(r'^[A-Z0-9_:.\-]{1,160}$')

def public_state_projection(state:dict[str,Any])->dict[str,Any]:
    """Create a restorable journal without owner text, stderr or arbitrary agent metadata."""
    projected=copy.deepcopy(state);projected['blockers']=[str(x) for x in (state.get('blockers') or []) if SAFE_REASON.fullmatch(str(x))]
    events=[];prev=None
    for source in state.get('events') or []:
        event={k:copy.deepcopy(source.get(k)) for k in ('sequence','event_id','idempotency_key','phase','outcome','subject_sha','evidence_refs','reason_codes','cost_usd','occurred_at')}
        event['reason_codes']=[str(x) for x in (event.get('reason_codes') or []) if SAFE_REASON.fullmatch(str(x))]
        decision=source.get('decision') or {};event['decision']={k:copy.deepcopy(decision.get(k)) for k in ('result','reason_codes','policy_hash','decision_id') if k in decision}
        if 'reason_codes' in event['decision']:event['decision']['reason_codes']=[str(x) for x in (event['decision'].get('reason_codes') or []) if SAFE_REASON.fullmatch(str(x))]
        md=source.get('metadata') or {};event['metadata']={k:copy.deepcopy(v) for k,v in md.items() if k in SAFE_EVENT_METADATA and isinstance(v,(str,int,float,bool,type(None)))}
        event['previous_event_hash']=prev;event['event_hash']=_hash({k:v for k,v in event.items() if k!='event_hash'});prev=event['event_hash'];events.append(event)
    projected['events']=events;projected['event_head']=prev
    return projected

def public_memory_projection(memory:dict[str,Any]|None)->dict[str,Any]|None:
    if not isinstance(memory,dict):return None
    return {k:memory.get(k) for k in ('schema_version','brief_id','run_id','status','revision','memory_digest')}

def public_session_continuity_projection(checkpoint:dict[str,Any]|None)->dict[str,Any]|None:
    if checkpoint is None:return None
    if not isinstance(checkpoint,dict):raise ValueError('REMOTE_RUNTIME_SESSION_CONTINUITY_INVALID:CONTINUITY_CHECKPOINT_NOT_OBJECT')
    errors=validate_checkpoint(checkpoint)
    if errors:raise ValueError('REMOTE_RUNTIME_SESSION_CONTINUITY_INVALID:'+','.join(errors))
    return copy.deepcopy(checkpoint)

def _checkpoint_state_binding_errors(checkpoint:dict[str,Any],state:dict[str,Any])->list[str]:
    errors=[];work=checkpoint.get('work_identity') or {}
    if str(work.get('roadmap_id') or '')!=str(state.get('roadmap_id') or ''):errors.append('REMOTE_SESSION_CONTINUITY_ROADMAP_BINDING')
    checkpoint_issue=work.get('issue_id');state_issue=state.get('issue_id')
    if (None if checkpoint_issue is None else str(checkpoint_issue))!=(None if state_issue is None else str(state_issue)):errors.append('REMOTE_SESSION_CONTINUITY_ISSUE_BINDING')
    return errors

def _session_continuity_event_errors(event:dict[str,Any],state:dict[str,Any])->list[str]:
    checkpoint=event.get('session_continuity_projection')
    binding_keys=('session_continuity_digest','session_continuity_id','session_continuity_revision')
    if checkpoint is None:
        return ['REMOTE_SESSION_CONTINUITY_ORPHAN_BINDING'] if any(key in event for key in binding_keys) else []
    if not isinstance(checkpoint,dict):return ['REMOTE_SESSION_CONTINUITY_NOT_OBJECT']
    errors=['REMOTE_SESSION_CONTINUITY_INVALID:'+item for item in validate_checkpoint(checkpoint)]
    if event.get('session_continuity_digest')!=checkpoint.get('checkpoint_digest'):errors.append('REMOTE_SESSION_CONTINUITY_DIGEST_BINDING')
    if event.get('session_continuity_id')!=checkpoint.get('checkpoint_id'):errors.append('REMOTE_SESSION_CONTINUITY_ID_BINDING')
    if event.get('session_continuity_revision')!=checkpoint.get('checkpoint_revision'):errors.append('REMOTE_SESSION_CONTINUITY_REVISION_BINDING')
    errors.extend(_checkpoint_state_binding_errors(checkpoint,state))
    return errors

def _parse(body:str)->dict[str,Any]|None:
    if not body.startswith(PREFIX) or not body.endswith(SUFFIX):return None
    try:return json.loads(body[len(PREFIX):-len(SUFFIX)])
    except json.JSONDecodeError:return None

def verify_remote_events(events:list[dict[str,Any]])->list[str]:
    errors=[];prev=None;prev_comment=None
    for i,event in enumerate(events,1):
        if event.get('sequence')!=i:errors.append(f'REMOTE_SEQUENCE:{i}')
        if event.get('previous_hash')!=prev:errors.append(f'REMOTE_CHAIN:{i}')
        if event.get('previous_provider_object_id')!=prev_comment:errors.append(f'REMOTE_PROVIDER_ANCHOR_CHAIN:{i}')
        unsigned={k:v for k,v in event.items() if k not in {'event_hash','provider_object_id','provider_created_at','provider_actor'}}
        if event.get('event_hash')!=_hash(unsigned):errors.append(f'REMOTE_HASH:{i}')
        state=event.get('state')
        if not isinstance(state,dict) or validate_journal(state):errors.append(f'REMOTE_STATE_INVALID:{i}')
        elif event.get('state_digest')!=_hash(state):errors.append(f'REMOTE_STATE_DIGEST:{i}')
        public=event.get('work_memory_projection')
        if public is not None and set(public)-{'schema_version','brief_id','run_id','status','revision','memory_digest'}:errors.append(f'REMOTE_PRIVATE_MEMORY_LEAK:{i}')
        for item in _session_continuity_event_errors(event,state if isinstance(state,dict) else {}):errors.append(f'{item}:{i}')
        if not event.get('provider_object_id') or not event.get('provider_created_at') or not event.get('provider_actor'):errors.append(f'REMOTE_PROVIDER_ANCHOR_MISSING:{i}')
        prev=event.get('event_hash');prev_comment=event.get('provider_object_id')
    return errors

class GitHubRuntimeStore:
    def __init__(self,client:GitHubClient):self.client=client
    def ensure_issue(self)->dict[str,Any]:
        issues=self.client.issues();matches=[i for i in issues if i.get('title')==TITLE and i.get('state')=='open']
        if len(matches)>1:raise ValueError('MULTIPLE_RUNTIME_LEDGER_ISSUES')
        if matches:return matches[0]
        return self.client.create_issue(TITLE,'Public-safe ADWF checkpoint projection. Raw Work Memory is never stored in this public ledger.')
    def _anchor_rules_verified(self)->dict[str,Any]:
        try:return verify_runtime_anchor_ruleset(self.client.rulesets())
        except Exception:return {'status':'NOT_VERIFIED','readback_verified':False,'reason_codes':['RUNTIME_ANCHOR_RULESET_READBACK_FAILED']}
    def _anchor_refs(self)->dict[str,dict[str,Any]]:
        refs=self.client.matching_tag_refs(ANCHOR_PREFIX);out={}
        for ref in refs:
            name=str(ref.get('ref') or '').split('refs/tags/',1)[-1]
            if name.startswith(ANCHOR_PREFIX):out[name]=ref
        return out
    def _verify_tag_anchors(self,events:list[dict[str,Any]])->list[str]:
        errors=[];refs=self._anchor_refs();expected={_tag_name(e):e for e in events}
        extras=sorted(set(refs)-set(expected))
        if extras:errors.append('REMOTE_RUNTIME_ORPHAN_ANCHORS:'+','.join(extras[:10]))
        for name,event in expected.items():
            ref=refs.get(name)
            if not ref:errors.append('REMOTE_RUNTIME_EXTERNAL_ANCHOR_MISSING:'+name);continue
            sha=str((ref.get('object') or {}).get('sha') or '')
            if not sha:errors.append('REMOTE_RUNTIME_EXTERNAL_ANCHOR_REF_INVALID:'+name);continue
            try:obj=self.client.tag_object(sha);payload=json.loads(str(obj.get('message') or '{}'))
            except Exception:errors.append('REMOTE_RUNTIME_EXTERNAL_ANCHOR_OBJECT_INVALID:'+name);continue
            if payload.get('event_hash')!=event.get('event_hash') or str(payload.get('provider_comment_id'))!=str(event.get('provider_object_id')):
                errors.append('REMOTE_RUNTIME_EXTERNAL_ANCHOR_BINDING_MISMATCH:'+name)
        return errors
    def read(self)->tuple[dict[str,Any],list[dict[str,Any]]]:
        issue=self.ensure_issue();comments=self.client.issue_comments(int(issue['number']));events=[]
        for c in comments:
            event=_parse(str(c.get('body') or ''))
            if event:
                event={**event,'provider_object_id':str(c.get('id') or ''),'provider_created_at':c.get('created_at'),'provider_actor':(c.get('user') or {}).get('login')}
                events.append(event)
        errors=verify_remote_events(events)
        if events:errors.extend(self._verify_tag_anchors(events))
        rules=self._anchor_rules_verified()
        if events and rules.get('readback_verified') is not True:errors.append('REMOTE_RUNTIME_ANCHOR_RULESET_NOT_VERIFIED')
        if errors:raise ValueError('REMOTE_RUNTIME_LEDGER_INVALID:'+','.join(errors))
        return issue,events
    def append(self,state:dict[str,Any],work_memory:dict[str,Any]|None=None,session_checkpoint:dict[str,Any]|None=None)->dict[str,Any]:
        if validate_journal(state):raise ValueError('REMOTE_RUNTIME_STATE_INVALID')
        safe_state=public_state_projection(state)
        if validate_journal(safe_state):raise ValueError('REMOTE_RUNTIME_PUBLIC_PROJECTION_INVALID')
        checkpoint=public_session_continuity_projection(session_checkpoint)
        if checkpoint:
            binding_errors=_checkpoint_state_binding_errors(checkpoint,safe_state)
            if binding_errors:raise ValueError('REMOTE_RUNTIME_SESSION_CONTINUITY_BINDING_INVALID:'+','.join(binding_errors))
        issue,events=self.read();prev=events[-1]['event_hash'] if events else None;prev_object=events[-1].get('provider_object_id') if events else None
        checkpoint_digest=(checkpoint or {}).get('checkpoint_digest')
        if events and events[-1].get('state_digest')==_hash(safe_state) and (checkpoint is None or events[-1].get('session_continuity_digest')==checkpoint_digest):return {'status':'UNCHANGED','issue_number':issue['number'],'event':events[-1]}
        public=public_memory_projection(work_memory)
        unsigned={'schema_version':3,'sequence':len(events)+1,'run_id':safe_state['run_id'],'revision':safe_state['revision'],'state_digest':_hash(safe_state),'state':safe_state,
                  'work_memory_projection':public,'work_memory_digest':(public or {}).get('memory_digest'),'previous_hash':prev,'previous_provider_object_id':prev_object,'created_at':_now()}
        if checkpoint is not None:
            unsigned.update({'session_continuity_projection':checkpoint,'session_continuity_digest':checkpoint_digest,'session_continuity_id':checkpoint['checkpoint_id'],'session_continuity_revision':checkpoint['checkpoint_revision']})
        event={**unsigned,'event_hash':_hash(unsigned)};body=PREFIX+json.dumps(event,ensure_ascii=False,sort_keys=True,separators=(',',':'))+SUFFIX
        rules=self._anchor_rules_verified()
        if rules.get('readback_verified') is not True:raise ValueError('REMOTE_RUNTIME_ANCHOR_RULESET_REQUIRED_BEFORE_PERSIST')
        comment=self.client.add_issue_comment(int(issue['number']),body)
        if not comment.get('id') or not comment.get('created_at') or not (comment.get('user') or {}).get('login'):raise ValueError('REMOTE_RUNTIME_READBACK_MISSING')
        anchored={**event,'provider_object_id':str(comment['id']),'provider_created_at':comment.get('created_at'),'provider_actor':(comment.get('user') or {}).get('login')}
        info=self.client.repo_info();default=str(info.get('default_branch') or 'main');head=str((self.client.branch(default).get('commit') or {}).get('sha') or '')
        message=json.dumps({'event_hash':event['event_hash'],'provider_comment_id':str(comment['id']),'provider_created_at':comment.get('created_at'),'provider_actor':(comment.get('user') or {}).get('login'),'run_id':safe_state['run_id'],'revision':safe_state['revision']},sort_keys=True,separators=(',',':'))
        tag_obj=self.client.create_tag_object(_tag_name(event),head,message);tag_sha=str(tag_obj.get('sha') or '')
        if not tag_sha:raise ValueError('REMOTE_RUNTIME_EXTERNAL_ANCHOR_CREATE_FAILED')
        ref=self.client.create_tag_ref(_tag_name(event),tag_sha)
        if str((ref.get('object') or {}).get('sha') or '')!=tag_sha:raise ValueError('REMOTE_RUNTIME_EXTERNAL_ANCHOR_READBACK_FAILED')
        return {'status':'APPENDED','issue_number':issue['number'],'comment_id':comment['id'],'event':anchored,'public_projection_only':True,'external_anchor':{'tag':_tag_name(event),'tag_object_sha':tag_sha,'ruleset_verified':True}}
    def restore_latest(self,root:str|Path)->dict[str,Any]|None:
        _,events=self.read()
        if not events:return None
        state=events[-1]['state'];target=Path(root).resolve()/'.adwf-runtime'/'orchestration'/f"{state['run_id']}.json";target.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=target.name+'.',dir=target.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(state,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return state
    def restore_latest_session_continuity(self,*,actual_main_sha:str,actual_head_sha:str|None=None)->dict[str,Any]|None:
        _,events=self.read()
        for event in reversed(events):
            checkpoint=event.get('session_continuity_projection')
            if checkpoint is None:continue
            errors=validate_checkpoint(checkpoint)
            if errors:raise ValueError('REMOTE_RUNTIME_SESSION_CONTINUITY_INVALID:'+','.join(errors))
            return {'checkpoint':copy.deepcopy(checkpoint),'event_hash':event.get('event_hash'),'provider_object_id':event.get('provider_object_id'),'reconciliation':reconcile_checkpoint(checkpoint,actual_main_sha=actual_main_sha,actual_head_sha=actual_head_sha)}
        return None
