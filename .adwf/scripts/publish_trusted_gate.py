#!/usr/bin/env python3
"""Trusted default-branch controller for PR HEAD certification.

v1.6 closes the self-attestation gap: a successful PR workflow is never enough
when the PR changes ADWF evaluators, policy or trusted workflows. The default-
branch controller reads the PR diff itself through GitHub API and requires an
exact-HEAD approval from a repository admin for governance changes.
"""
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.github_provider import GitHubClient
from lib.provider_contracts import ProviderContractError
from lib.strict_json import loads as strict_loads
from lib.trust_boundary import classify_changed_files


def _pull_number(live:dict[str,Any])->int|None:
    prs=live.get('pull_requests') or []
    if len(prs)!=1:return None
    try:return int(prs[0].get('number'))
    except (TypeError,ValueError):return None


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
        candidates.append({'login':login,'review_id':review.get('id'),'commit_id':review.get('commit_id')})
    return {'verified':bool(candidates),'approvals':candidates}


def evaluate_trusted_gate(client:GitHubClient,repo:str,workflow_run:dict[str,Any])->dict[str,Any]:
    run_id=workflow_run.get('id');sha=str(workflow_run.get('head_sha') or '')
    reasons=[];governance_reasons=[]
    if not run_id or len(sha)!=40:return {'sha':sha,'reasons':['INVALID_WORKFLOW_RUN_IDENTITY'],'governance':{'required':False,'verified':False,'reason_codes':['INVALID_WORKFLOW_RUN_IDENTITY']}}
    live=client.get(f'/repos/{repo}/actions/runs/{run_id}')
    if str(live.get('head_sha'))!=sha:reasons.append('RUN_HEAD_SHA_MISMATCH')
    if live.get('name')!='ADWF PR':reasons.append('UNEXPECTED_WORKFLOW')
    if live.get('event')!='pull_request':reasons.append('UNTRUSTED_EVENT_SOURCE')
    if live.get('status')!='completed' or live.get('conclusion')!='success':reasons.append('FAST_FEEDBACK_NOT_PASS')
    pr_number=_pull_number(live)
    if pr_number is None:reasons.append('PR_READBACK_MISSING')
    checks=client.check_runs(sha)
    fast=[c for c in checks if c.get('name')=='fast-feedback' and c.get('head_sha')==sha]
    if not any(c.get('status')=='completed' and c.get('conclusion')=='success' and (c.get('app') or {}).get('slug')=='github-actions' for c in fast):
        reasons.append('FAST_FEEDBACK_PROVIDER_ATTESTATION_MISSING')

    governance={'required':False,'verified':True,'reason_codes':[],'files':[],'approval':None}
    if pr_number is not None:
        pr=client.pull(pr_number)
        if str((pr.get('head') or {}).get('sha') or '')!=sha:reasons.append('PR_HEAD_MOVED')
        files=[str(item.get('filename') or '') for item in client.pull_files(pr_number)]
        classification=classify_changed_files(files)
        governance['files']=classification['trust_boundary_files']
        governance['required']=classification['trust_boundary_changed']
        if classification['trust_boundary_changed']:
            author=str((pr.get('user') or {}).get('login') or '')
            approval=_admin_exact_head_approval(client,pr_number,sha,author)
            governance['approval']=approval
            governance['verified']=approval['verified']
            if not approval['verified']:
                governance_reasons.append('GOVERNANCE_ADMIN_EXACT_HEAD_APPROVAL_REQUIRED')
                reasons.append('TRUST_BOUNDARY_CHANGE_NOT_AUTHORIZED')
    governance['reason_codes']=governance_reasons
    return {'sha':sha,'pr_number':pr_number,'reasons':reasons,'governance':governance}


def _publish(client:GitHubClient,name:str,sha:str,passed:bool,title:str,summary:str)->None:
    client.post(f'/repos/{client.repo}/check-runs',{'name':name,'head_sha':sha,'status':'completed','conclusion':'success' if passed else 'failure','output':{'title':title,'summary':summary[:65000]}})



def workflow_run_from_event(client:GitHubClient,event:dict[str,Any])->dict[str,Any]:
    wr=event.get('workflow_run') or {}
    if wr:return wr
    pr=event.get('pull_request') or {}
    sha=str((pr.get('head') or {}).get('sha') or '')
    number=pr.get('number') or event.get('number')
    if len(sha)!=40:return {}
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
    if len(sha)!=40:print('TRUSTED_GATE: BLOCK: invalid workflow_run identity');return 2
    gov=result['governance'];gov_summary='PASS: no trust-boundary changes.' if not gov['required'] else ('PASS: exact-HEAD repository-admin approval verified.' if gov['verified'] else 'BLOCK: '+', '.join(gov['reason_codes']))
    _publish(client,'adwf/governance-gate',sha,gov['verified'],'ADWF governance trust-boundary gate',gov_summary)
    reasons=result['reasons'];passed=not reasons
    summary='PASS: provider-attested exact SHA and trusted evaluator boundary.' if passed else 'BLOCK: '+', '.join(reasons)
    _publish(client,'adwf/trusted-gate',sha,passed,'ADWF trusted exact-HEAD gate',summary)
    print(json.dumps({'status':'PASS' if passed else 'BLOCK',**result},ensure_ascii=False));return 0 if passed else 1
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ProviderContractError,TimeoutError,json.JSONDecodeError,ValueError) as exc:
        print('TRUSTED_GATE: NOT_VERIFIED:',type(exc).__name__,file=sys.stderr);raise SystemExit(2)
