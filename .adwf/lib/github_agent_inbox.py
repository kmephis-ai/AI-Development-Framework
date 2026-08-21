"""GitHub-visible bounded handoff and return channel for Creative Agents.

Public requests expose only a safe AIWorkPackage projection. Agent returns are
low-trust claims that must bind to the exact package; trusted Runtime Supervisor
and provider evidence remain the authority for phase advancement.
"""
from __future__ import annotations
from typing import Any
import hashlib,json
from .github_provider import GitHubClient
from .ai_work_contracts import CREATIVE_PHASES, canonicalize_low_trust_claim, compile_work_package

TITLE='[ADWF] Agent Inbox';REQUEST_PREFIX='<!-- ADWF-AGENT-REQUEST v3 -->\n```json\n';RESULT_PREFIX='<!-- ADWF-AGENT-RESULT v3 -->\n```json\n';SUFFIX='\n```'

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
        package=envelope.get('work_package')
        if not isinstance(package,dict):
            compatibility_state={'run_id':envelope.get('run_id'),'roadmap_id':envelope.get('brief_id') or (work_memory or {}).get('brief_id') or 'LEGACY',
                                 'issue_id':envelope.get('issue_id') or 'PENDING','revision':envelope.get('revision',0),'phase':phase,
                                 'work_type':envelope.get('work_type') or 'feature','risk':envelope.get('risk') or 'R1','subject_sha':envelope.get('subject_sha')}
            package=compile_work_package(compatibility_state,work_memory)
        elif envelope.get('work_package_digest')!=package.get('package_digest'):
            raise ValueError('AGENT_REQUEST_WORK_PACKAGE_INVALID')
        safe_memory={k:(work_memory or {}).get(k) for k in ('brief_id','run_id','status','next_action_ru')}
        projection={k:package.get(k) for k in ('package_id','package_digest','base_sha','phase','work_type','risk','allowed_write_surfaces','forbidden_write_surfaces','required_evidence','monetary_budget_usd')}
        payload={'schema_version':3,'idempotency_key':envelope.get('idempotency_key'),'run_id':envelope.get('run_id'),'revision':envelope.get('revision'),'brief_id':envelope.get('brief_id'),'phase':phase,'capability':envelope.get('capability'),'subject_sha':envelope.get('subject_sha'),'work_branch':envelope.get('work_branch'),'risk':envelope.get('risk'),'monetary_budget_usd':0,'work_package_projection':projection,'work_memory_projection':safe_memory,
                 'security_note':'LOW_TRUST_WORK_REQUEST_NOT_AUTHORIZATION_OR_EVIDENCE'}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();payload['request_digest']=digest
        issue=self.ensure_issue();existing=self.client.issue_comments(int(issue['number']))
        if any(digest in str(c.get('body') or '') for c in existing):return {'status':'ALREADY_PUBLISHED','issue_number':issue['number'],'request_digest':digest}
        comment=self.client.add_issue_comment(int(issue['number']),REQUEST_PREFIX+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+SUFFIX)
        if not comment.get('id'):raise ValueError('AGENT_INBOX_READBACK_MISSING')
        return {'status':'PUBLISHED','issue_number':issue['number'],'comment_id':comment['id'],'request_digest':digest,'package_digest':package['package_digest']}
    def results(self)->list[dict[str,Any]]:
        issue=self.ensure_issue();out=[]
        for comment in self.client.issue_comments(int(issue['number'])):
            value=_parse(str(comment.get('body') or ''),RESULT_PREFIX)
            if value is not None:out.append({**value,'provider_comment_id':comment.get('id'),'provider_actor':(comment.get('user') or {}).get('login'),'provider_created_at':comment.get('created_at')})
        return out

def validate_agent_result(value:dict[str,Any],*,request:dict[str,Any])->dict[str,Any]:
    allowed={'schema_version','idempotency_key','run_id','phase','outcome','subject_sha','head_sha','branch','reason_codes','summary_ru',
             'package_id','package_digest','base_sha','changed_paths','verification_claims','evidence_claims','provider_comment_id','provider_actor','provider_created_at'}
    unknown=set(value)-allowed
    if unknown:raise ValueError('AGENT_RESULT_FIELDS_FORBIDDEN:'+','.join(sorted(unknown)))
    if value.get('schema_version')!=3:raise ValueError('AGENT_RESULT_SCHEMA')
    for field in ('idempotency_key','run_id','phase'):
        if value.get(field)!=request.get(field):raise ValueError('AGENT_RESULT_BINDING_MISMATCH:'+field)
    if value.get('phase') not in CREATIVE_PHASES:raise ValueError('AGENT_RESULT_PHASE_FORBIDDEN')
    package=request.get('work_package')
    if not isinstance(package,dict) or request.get('work_package_digest')!=package.get('package_digest'):raise ValueError('AGENT_RESULT_WORK_PACKAGE_MISSING')
    work_result=canonicalize_low_trust_claim(value,package=package)
    branch=value.get('branch');expected_branch=request.get('work_branch')
    if branch is not None and (not isinstance(branch,str) or not branch.startswith('adwf/') or len(branch)>180):raise ValueError('AGENT_BRANCH_INVALID')
    if expected_branch and branch!=expected_branch:raise ValueError('AGENT_BRANCH_BINDING_MISMATCH')
    return {'phase':value['phase'],'outcome':work_result['outcome'],'idempotency_key':value['idempotency_key'],'subject_sha':work_result.get('head_sha'),'preview_digest':None,'evidence_refs':[],
            'reason_codes':work_result['reason_codes'],'transient':work_result['outcome']=='RETRY','cost_usd':0,'metadata':{'source':'LOW_TRUST_AGENT_RESULT','provider_comment_id':value.get('provider_comment_id'),'provider_actor':value.get('provider_actor'),'branch':branch,'summary_ru':work_result['summary_ru'],'ai_work_result':work_result}}
