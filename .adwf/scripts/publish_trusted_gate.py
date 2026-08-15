#!/usr/bin/env python3
"""Trusted default-branch controller for exact-HEAD PR certification.

The controller always executes from the protected default branch, reads changed
content through GitHub, evaluates policy from the exact PR BASE revision, and
never lets candidate code/policy authorize itself.  Routine reversible trust
support may use a base-bound Standing Owner Authorization; reserved, weakening,
unknown or stale-base changes remain human-gated or blocked.
"""
from __future__ import annotations
import argparse,base64,binascii,json,os,re,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.github_provider import GitHubClient
from lib.provider_contracts import ProviderContractError
from lib.strict_json import loads as strict_loads
from lib.trust import classify_diff,is_trust_sensitive_path,normalize_repo_path

OWNER_ATTESTATION=re.compile(r'(?mi)^\s*Owner-Attestation:\s*`?([0-9a-f]{40})`?\s*$')
SHA=re.compile(r'^[0-9a-f]{40}$')
_STATUS_MAP={'added':'A','removed':'D','modified':'M','renamed':'R','copied':'C'}


def _pull_number(live:dict[str,Any])->int|None:
    prs=live.get('pull_requests') or []
    if len(prs)!=1:return None
    try:return int(prs[0].get('number'))
    except (TypeError,ValueError):return None


def _pull_number_for_sha(client:GitHubClient,live:dict[str,Any],sha:str)->int|None:
    """Resolve the PR even when GitHub drops workflow_run.pull_requests after merge."""
    direct=_pull_number(live)
    if direct is not None:return direct
    matches=[]
    for pr in client.pulls():
        if str((pr.get('head') or {}).get('sha') or '')!=sha:continue
        try:matches.append(int(pr.get('number')))
        except (TypeError,ValueError):continue
    return matches[0] if len(matches)==1 else None


def _admin_exact_head_approval(client:GitHubClient,pr_number:int,sha:str,author_login:str)->dict[str,Any]:
    reviews=client.pull_reviews(pr_number)
    candidates=[]
    for review in reviews:
        if str(review.get('state') or '').upper()!='APPROVED':continue
        if str(review.get('commit_id') or '')!=sha:continue
        login=str((review.get('user') or {}).get('login') or '')
        if not login or login==author_login:continue
        try:perm=client.collaborator_permission(login)
        except Exception:continue
        if str(perm.get('permission') or '').lower()!='admin':continue
        candidates.append({'login':login,'review_id':review.get('id'),'commit_id':review.get('commit_id'),'kind':'ADMIN_REVIEW'})
    return {'verified':bool(candidates),'approvals':candidates}


def _owner_exact_head_attestation(client:GitHubClient,pr:dict[str,Any],sha:str)->dict[str,Any]:
    """Verify a SHA-bound owner marker from authenticated provider metadata."""
    author=str((pr.get('user') or {}).get('login') or '')
    if not author:return {'verified':False,'reason':'PR_AUTHOR_MISSING'}
    matches=OWNER_ATTESTATION.findall(str(pr.get('body') or ''))
    if matches!=[sha]:return {'verified':False,'reason':'OWNER_ATTESTATION_EXACT_HEAD_REQUIRED','matches':matches}
    try:perm=client.collaborator_permission(author)
    except Exception:return {'verified':False,'reason':'OWNER_ADMIN_PERMISSION_NOT_VERIFIED'}
    if str(perm.get('permission') or '').lower()!='admin':return {'verified':False,'reason':'OWNER_ADMIN_PERMISSION_REQUIRED'}
    return {'verified':True,'login':author,'commit_id':sha,'kind':'SOLO_MAINTAINER_OWNER_ATTESTATION'}


def _governance_authorization(client:GitHubClient,pr_number:int,pr:dict[str,Any],sha:str)->dict[str,Any]:
    author=str((pr.get('user') or {}).get('login') or '')
    review=_admin_exact_head_approval(client,pr_number,sha,author)
    if review['verified']:return {'verified':True,'mode':'ADMIN_REVIEW','evidence':review}
    owner=_owner_exact_head_attestation(client,pr,sha)
    if owner['verified']:return {'verified':True,'mode':'SOLO_MAINTAINER_OWNER_ATTESTATION','evidence':owner}
    return {'verified':False,'mode':None,'evidence':{'admin_review':review,'owner_attestation':owner}}


def _exact_sha(value:Any,code:str)->str:
    sha=str(value or '').lower()
    if SHA.fullmatch(sha) is None:raise ValueError(code)
    return sha


def _github_blob(client:GitHubClient,path:str,sha:str)->str:
    payload=client.content(normalize_repo_path(path),ref=_exact_sha(sha,'BLOB_SHA_INVALID'))
    if payload.get('type') not in {None,'file'}:raise ValueError('GITHUB_BLOB_NOT_FILE')
    if payload.get('encoding')!='base64':raise ValueError('GITHUB_BLOB_ENCODING_INVALID')
    try:raw=base64.b64decode(str(payload.get('content') or ''),validate=True)
    except (binascii.Error,ValueError) as exc:raise ValueError('GITHUB_BLOB_BASE64_INVALID') from exc
    if len(raw)>2*1024*1024:raise ValueError('GITHUB_BLOB_INSPECTION_LIMIT')
    return raw.decode('utf-8',errors='replace')


def _provider_trust_classification(client:GitHubClient,pr:dict[str,Any])->dict[str,Any]:
    number=int(pr.get('number') or 0)
    if number<1:raise ValueError('PR_NUMBER_INVALID')
    base_sha=_exact_sha((pr.get('base') or {}).get('sha'),'PR_BASE_SHA_INVALID')
    head_sha=_exact_sha((pr.get('head') or {}).get('sha'),'PR_HEAD_SHA_INVALID')
    base_ref=str((pr.get('base') or {}).get('ref') or '')
    if not base_ref:raise ValueError('PR_BASE_REF_INVALID')
    policy=strict_loads(_github_blob(client,'.adwf/policies/trust-boundary.json',base_sha))
    patterns=policy.get('paths') if isinstance(policy,dict) else None
    if not isinstance(patterns,list) or not patterns:raise ValueError('BASE_TRUST_POLICY_INVALID')
    files=client.pull_files(number)
    if not files or len(files)>3000:raise ValueError('PR_DIFF_INSPECTION_INVALID')
    records=[]
    for item in files:
        path=normalize_repo_path(str(item.get('filename') or ''))
        old_path=normalize_repo_path(str(item.get('previous_filename'))) if item.get('previous_filename') else None
        status=_STATUS_MAP.get(str(item.get('status') or ''))
        if status is None:raise ValueError('PR_FILE_STATUS_UNKNOWN')
        inspect=any(is_trust_sensitive_path(candidate,patterns) for candidate in (path,old_path) if candidate)
        old_text=new_text=None
        if inspect:
            if status!='A':old_text=_github_blob(client,old_path or path,base_sha)
            if status!='D':new_text=_github_blob(client,path,head_sha)
        records.append({'path':path,'old_path':old_path,'status':status,'old_text':old_text,'new_text':new_text})
    result=classify_diff(records,policy)
    ref=client.git_ref(base_ref)
    current_sha=_exact_sha((ref.get('object') or {}).get('sha'),'CURRENT_BASE_SHA_INVALID')
    result.update({
        'base_sha':base_sha,'head_sha':head_sha,'base_ref':base_ref,
        'current_base_sha':current_sha,'base_current':current_sha==base_sha,
        'source':'GITHUB_PROVIDER_API',
    })
    return result


def evaluate_trusted_gate(client:GitHubClient,repo:str,workflow_run:dict[str,Any])->dict[str,Any]:
    run_id=workflow_run.get('id');sha=str(workflow_run.get('head_sha') or '')
    reasons=[];governance_reasons=[]
    if not run_id or SHA.fullmatch(sha) is None:return {'sha':sha,'reasons':['INVALID_WORKFLOW_RUN_IDENTITY'],'governance':{'required':False,'verified':False,'reason_codes':['INVALID_WORKFLOW_RUN_IDENTITY']}}
    live=client.get(f'/repos/{repo}/actions/runs/{run_id}')
    if str(live.get('head_sha'))!=sha:reasons.append('RUN_HEAD_SHA_MISMATCH')
    if live.get('name')!='ADWF PR':reasons.append('UNEXPECTED_WORKFLOW')
    if live.get('event')!='pull_request':reasons.append('UNTRUSTED_EVENT_SOURCE')
    if live.get('status')!='completed' or live.get('conclusion')!='success':reasons.append('FAST_FEEDBACK_NOT_PASS')
    pr_number=_pull_number_for_sha(client,live,sha)
    if pr_number is None:reasons.append('PR_READBACK_MISSING')
    checks=client.check_runs(sha)
    fast=[c for c in checks if c.get('name')=='fast-feedback' and c.get('head_sha')==sha]
    if not any(c.get('status')=='completed' and c.get('conclusion')=='success' and (c.get('app') or {}).get('slug')=='github-actions' for c in fast):
        reasons.append('FAST_FEEDBACK_PROVIDER_ATTESTATION_MISSING')

    governance={'required':False,'verified':True,'reason_codes':[],'files':[],'approval':None,'classification':None}
    if pr_number is not None:
        pr=client.pull(pr_number)
        if str((pr.get('head') or {}).get('sha') or '')!=sha:reasons.append('PR_HEAD_MOVED')
        try:classification=_provider_trust_classification(client,pr)
        except (ProviderContractError,TimeoutError,json.JSONDecodeError,ValueError):
            classification={'result':'BLOCK','human_required':False,'authorization_mode':'NORMAL','protected_files':[],'reason_codes':['TRUST_CLASSIFICATION_NOT_VERIFIED'],'base_current':False}
        governance['classification']={
            key:classification.get(key) for key in (
                'result','authorization_mode','reason_codes','manual_required_files','inspection_unverified_files',
                'standing_policy','base_sha','head_sha','base_ref','current_base_sha','base_current','source'
            )
        }
        governance['files']=classification.get('protected_files') or []
        governance['required']=bool(governance['files']) or classification.get('result')=='BLOCK'
        if governance['required']:
            if not classification.get('base_current'):
                governance['verified']=False
                governance_reasons.append('GOVERNANCE_BASE_DRIFT_REQUIRES_REBASE')
                reasons.append('TRUST_POLICY_BASE_DRIFT')
            elif classification.get('result')=='BLOCK':
                governance['verified']=False
                governance_reasons.append('GOVERNANCE_POLICY_BLOCK')
                reasons.append('TRUST_BOUNDARY_POLICY_BLOCK')
            elif classification.get('authorization_mode')=='STANDING_OWNER_POLICY':
                standing=classification.get('standing_policy') or {}
                governance['approval']={
                    'verified':True,'mode':'STANDING_OWNER_POLICY',
                    'evidence':{'policy_revision':standing.get('revision'),'policy_digest':standing.get('digest'),'base_sha':classification.get('base_sha'),'head_sha':sha},
                }
                governance['verified']=True
            elif classification.get('human_required'):
                approval=_governance_authorization(client,pr_number,pr,sha)
                governance['approval']=approval;governance['verified']=approval['verified']
                if not approval['verified']:
                    governance_reasons.append('GOVERNANCE_EXACT_HEAD_HUMAN_ATTESTATION_REQUIRED')
                    reasons.append('TRUST_BOUNDARY_CHANGE_NOT_AUTHORIZED')
            else:
                governance['verified']=True
    governance['reason_codes']=governance_reasons
    return {'sha':sha,'pr_number':pr_number,'reasons':list(dict.fromkeys(reasons)),'governance':governance}


def _publish(client:GitHubClient,name:str,sha:str,passed:bool,title:str,summary:str)->None:
    """Publish one trusted decision fail-closed through both provider transports."""
    conclusion='success' if passed else 'failure'
    client.post(f'/repos/{client.repo}/statuses/{sha}',{'state':'failure','context':name,'description':'BLOCK: trusted gate publication incomplete'})
    client.post(f'/repos/{client.repo}/check-runs',{'name':name,'head_sha':sha,'status':'completed','conclusion':conclusion,'output':{'title':title,'summary':summary[:65000]}})
    client.post(f'/repos/{client.repo}/statuses/{sha}',{'state':conclusion,'context':name,'description':summary[:140]})


def workflow_run_from_event(client:GitHubClient,event:dict[str,Any])->dict[str,Any]:
    wr=event.get('workflow_run') or {}
    if wr:return wr
    pr=event.get('pull_request') or {};sha=str((pr.get('head') or {}).get('sha') or '');number=pr.get('number') or event.get('number')
    if SHA.fullmatch(sha) is None:return {}
    for run in client.runs():
        if str(run.get('name') or '')!='ADWF PR' or str(run.get('event') or '')!='pull_request':continue
        if str(run.get('head_sha') or '')!=sha:continue
        prs=run.get('pull_requests') or []
        if number and prs and not any(str(x.get('number'))==str(number) for x in prs if isinstance(x,dict)):continue
        if run.get('status')=='completed' and run.get('conclusion')=='success':return run
    return {}


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--event',required=True);a=ap.parse_args()
    token=os.environ.get('GITHUB_TOKEN');repo=os.environ.get('GITHUB_REPOSITORY','')
    if not token or '/' not in repo:print('TRUSTED_GATE: BLOCK: missing authenticated provider context');return 2
    event=strict_loads(Path(a.event).read_text(encoding='utf-8'));client=GitHubClient(repo,token);wr=workflow_run_from_event(client,event)
    result=evaluate_trusted_gate(client,repo,wr);sha=result.get('sha') or ''
    if SHA.fullmatch(sha) is None:print('TRUSTED_GATE: BLOCK: invalid workflow_run identity');return 2
    gov=result['governance'];approval=gov.get('approval') or {}
    if not gov['required']:
        gov_summary='PASS: no trust-boundary changes.'
    elif gov['verified'] and approval.get('mode')=='STANDING_OWNER_POLICY':
        gov_summary='PASS: AUTO-AUTHORIZED BY STANDING POLICY from exact trusted BASE.'
    elif gov['verified']:
        gov_summary='PASS: exact-HEAD human authorization verified.'
    else:
        gov_summary='BLOCK: '+', '.join(gov['reason_codes'])
    _publish(client,'adwf/governance-gate',sha,gov['verified'],'ADWF governance trust-boundary gate',gov_summary)
    reasons=result['reasons'];passed=not reasons
    summary='PASS: provider-attested exact SHA and trusted BASE evaluator boundary.' if passed else 'BLOCK: '+', '.join(reasons)
    _publish(client,'adwf/trusted-gate',sha,passed,'ADWF trusted exact-HEAD gate',summary)
    print(json.dumps({'status':'PASS' if passed else 'BLOCK',**result},ensure_ascii=False));return 0 if passed else 1
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ProviderContractError,TimeoutError,json.JSONDecodeError,ValueError) as exc:
        print('TRUSTED_GATE: NOT_VERIFIED:',type(exc).__name__,file=sys.stderr);raise SystemExit(2)
