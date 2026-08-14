"""GitHub-visible handoff and bounded return channel for Creative Agents.

Requests and results are low-trust work products, never evidence or authority.
The trusted Runtime Supervisor validates all subsequent provider facts.
"""
from __future__ import annotations
from typing import Any
import hashlib,json,re
from .github_provider import GitHubClient

TITLE='[ADWF] Agent Inbox';REQUEST_PREFIX='<!-- ADWF-AGENT-REQUEST v2 -->\n```json\n';RESULT_PREFIX='<!-- ADWF-AGENT-RESULT v2 -->\n```json\n';SUFFIX='\n```';CREATIVE_PHASES={'EXECUTE','RECOVERY'}

def _parse(body:str,prefix:str)->dict[str,Any]|None:
    if not body.startswith(prefix) or not body.endswith(SUFFIX):return None
    try:
        value=json.loads(body[len(prefix):-len(SUFFIX)]);return value if isinstance(value,dict) else None
    except json.JSONDecodeError:return None

class GitHubAgentInbox:
    def __init__(self,client:GitHubClient):self.client=client
    def ensure_issue(self)->dict[str,Any]:
        matches=[i for i in self.client.issues() if i.get('title')==TITLE and i.get('state')=='open']
        if len(matches)>1:raise ValueError('MULTIPLE_AGENT_INBOX_ISSUES')
        if matches:return matches[0]
        return self.client.create_issue(TITLE,'ADWF low-trust handoff queue. Comments are instructions/results, never trusted PASS evidence.')
    def publish(self,envelope:dict[str,Any],work_memory:dict[str,Any]|None)->dict[str,Any]:
        phase=str(envelope.get('phase') or '')
        if phase not in CREATIVE_PHASES:return {'status':'NOT_REQUIRED','phase':phase}
        safe_memory={k:(work_memory or {}).get(k) for k in ('brief_id','run_id','status','next_action_ru')}
        payload={'schema_version':2,'idempotency_key':envelope.get('idempotency_key'),'run_id':envelope.get('run_id'),'revision':envelope.get('revision'),'brief_id':envelope.get('brief_id'),'phase':phase,'capability':envelope.get('capability'),'subject_sha':envelope.get('subject_sha'),'risk':envelope.get('risk'),'monetary_budget_usd':0,'work_memory_projection':safe_memory,
                 'security_note':'LOW_TRUST_WORK_REQUEST_NOT_AUTHORIZATION_OR_EVIDENCE'}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();payload['request_digest']=digest
        issue=self.ensure_issue();existing=self.client.issue_comments(int(issue['number']))
        if any(digest in str(c.get('body') or '') for c in existing):return {'status':'ALREADY_PUBLISHED','issue_number':issue['number'],'request_digest':digest}
        comment=self.client.add_issue_comment(int(issue['number']),REQUEST_PREFIX+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+SUFFIX)
        if not comment.get('id'):raise ValueError('AGENT_INBOX_READBACK_MISSING')
        return {'status':'PUBLISHED','issue_number':issue['number'],'comment_id':comment['id'],'request_digest':digest}
    def results(self)->list[dict[str,Any]]:
        issue=self.ensure_issue();out=[]
        for comment in self.client.issue_comments(int(issue['number'])):
            value=_parse(str(comment.get('body') or ''),RESULT_PREFIX)
            if value is not None:out.append({**value,'provider_comment_id':comment.get('id'),'provider_actor':(comment.get('user') or {}).get('login'),'provider_created_at':comment.get('created_at')})
        return out

def validate_agent_result(value:dict[str,Any],*,request:dict[str,Any])->dict[str,Any]:
    allowed={'schema_version','idempotency_key','run_id','phase','outcome','subject_sha','branch','reason_codes','summary_ru','request_digest','provider_comment_id','provider_actor','provider_created_at'}
    unknown=set(value)-allowed
    if unknown:raise ValueError('AGENT_RESULT_FIELDS_FORBIDDEN:'+','.join(sorted(unknown)))
    if value.get('schema_version')!=2:raise ValueError('AGENT_RESULT_SCHEMA')
    for field in ('idempotency_key','run_id','phase'):
        if value.get(field)!=request.get(field):raise ValueError('AGENT_RESULT_BINDING_MISMATCH:'+field)
    if value.get('phase') not in CREATIVE_PHASES:raise ValueError('AGENT_RESULT_PHASE_FORBIDDEN')
    if value.get('outcome') not in {'PASS','FAIL','HUMAN_REQUIRED','RETRY'}:raise ValueError('AGENT_RESULT_OUTCOME_INVALID')
    sha=value.get('subject_sha')
    if value.get('outcome')=='PASS' and value.get('phase')=='EXECUTE' and (not isinstance(sha,str) or re.fullmatch(r'[0-9a-f]{40}',sha) is None):raise ValueError('AGENT_EXECUTE_COMMIT_SHA_REQUIRED')
    branch=value.get('branch')
    if branch is not None and (not isinstance(branch,str) or not branch.startswith('adwf/') or len(branch)>180):raise ValueError('AGENT_BRANCH_INVALID')
    return {'phase':value['phase'],'outcome':value['outcome'],'idempotency_key':value['idempotency_key'],'subject_sha':sha,'preview_digest':None,'evidence_refs':[],
            'reason_codes':list(value.get('reason_codes') or []),'transient':value.get('outcome')=='RETRY','cost_usd':0,'metadata':{'source':'LOW_TRUST_AGENT_RESULT','provider_comment_id':value.get('provider_comment_id'),'provider_actor':value.get('provider_actor'),'branch':branch,'summary_ru':str(value.get('summary_ru') or '')[:1000]}}
