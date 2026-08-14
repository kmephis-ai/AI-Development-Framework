"""Provider-authenticated owner decisions stored as append-only GitHub Issue comments.

The comment body is public-safe metadata only: exact commit, preview digest,
policy hash, decision, nonce and timestamp. GitHub supplies the authenticated
actor; trusted readback additionally verifies that actor has admin/maintain
repository authority. No API key or private prompt is ever stored in comments.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import hashlib, json, re, secrets
from .github_provider import GitHubClient

TITLE='[ADWF] Owner Decisions'
PREFIX='<!-- ADWF-OWNER-DECISION v1 -->\n```json\n'; SUFFIX='\n```'
SHA=re.compile(r'^[0-9a-f]{40}$'); DIGEST=re.compile(r'^[0-9a-f]{64}$')

def _now()->str:return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def _hash(v:Any)->str:return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def _parse(body:str)->dict[str,Any]|None:
    if not body.startswith(PREFIX) or not body.endswith(SUFFIX): return None
    try:
        value=json.loads(body[len(PREFIX):-len(SUFFIX)])
        return value if isinstance(value,dict) else None
    except json.JSONDecodeError:return None

class GitHubOwnerDecisionStore:
    def __init__(self,client:GitHubClient):self.client=client
    def ensure_issue(self)->dict[str,Any]:
        matches=[i for i in self.client.issues() if i.get('title')==TITLE and i.get('state')=='open']
        if len(matches)>1:raise ValueError('MULTIPLE_OWNER_DECISION_ISSUES')
        if matches:return matches[0]
        return self.client.create_issue(TITLE,'Provider-authenticated ADWF owner decisions. Technical metadata only; never edit comments manually.')
    def record(self,*,decision:str,head_sha:str,preview_digest:str,policy_hash:str,nonce:str|None=None)->dict[str,Any]:
        if decision not in {'ACCEPTED','CHANGES_REQUESTED','DEFERRED'}:raise ValueError('OWNER_DECISION_INVALID')
        if SHA.fullmatch(head_sha) is None or DIGEST.fullmatch(preview_digest) is None or DIGEST.fullmatch(policy_hash) is None:raise ValueError('OWNER_DECISION_BINDING_INVALID')
        actor=self.client.current_user();login=str(actor.get('login') or '')
        if not login:raise ValueError('OWNER_IDENTITY_READBACK_MISSING')
        permission=self.client.collaborator_permission(login);level=str(permission.get('permission') or '')
        if level not in {'admin','maintain'}:raise ValueError('OWNER_AUTHORITY_INSUFFICIENT')
        issue=self.ensure_issue(); payload={'schema_version':1,'decision':decision,'head_sha':head_sha,'preview_digest':preview_digest,
            'policy_hash':policy_hash,'nonce':nonce or secrets.token_hex(16),'actor_login':login,'recorded_at':_now()}
        payload['decision_digest']=_hash(payload)
        comment=self.client.add_issue_comment(int(issue['number']),PREFIX+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+SUFFIX)
        if not comment.get('id') or str((comment.get('user') or {}).get('login') or '')!=login:raise ValueError('OWNER_DECISION_PROVIDER_READBACK_FAILED')
        return {'readback_verified':True,'issue_number':issue['number'],'comment_id':comment['id'],**payload}
    def latest_for_sha(self,head_sha:str)->dict[str,Any]|None:
        issues=[i for i in self.client.issues() if i.get('title')==TITLE and i.get('state')=='open']
        if len(issues)!=1:return None
        comments=self.client.issue_comments(int(issues[0]['number'])); candidates=[]
        for c in comments:
            value=_parse(str(c.get('body') or ''))
            if not value or value.get('head_sha')!=head_sha:continue
            login=str((c.get('user') or {}).get('login') or '')
            if not login or login!=value.get('actor_login'):continue
            unsigned={k:v for k,v in value.items() if k!='decision_digest'}
            if value.get('decision_digest')!=_hash(unsigned):continue
            try:level=str(self.client.collaborator_permission(login).get('permission') or '')
            except Exception:continue
            if level not in {'admin','maintain'}:continue
            candidates.append({'readback_verified':True,'comment_id':c.get('id'),**value})
        if not candidates:return None
        return sorted(candidates,key=lambda x:str(x.get('recorded_at') or ''))[-1]
