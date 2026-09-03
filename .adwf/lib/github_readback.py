"""Compile live GitHub facts into provider readback and provider-attested evidence."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from typing import Any
import hashlib,json,re
from .github_rulesets import verify_rulesets,verify_runtime_anchor_ruleset,REQUIRED_CHECKS
from .policy_runtime import load_effective_policy
from .github_owner_decisions import GitHubOwnerDecisionStore

SHA=re.compile(r'^[0-9a-f]{40}$')
MAIN_GATES=('adwf-main','platform-smoke')
def _digest(v:Any)->str: return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def _time()->tuple[str,str]:
    now=datetime.now(timezone.utc); return now.isoformat().replace('+00:00','Z'),(now+timedelta(hours=24)).isoformat().replace('+00:00','Z')

def _ruleset_integration_id(rulesets:list[dict[str,Any]])->int|None:
    for rs in rulesets:
        if rs.get('name')!='ADWF protected main' or rs.get('target')!='branch' or rs.get('enforcement')!='active' or (rs.get('bypass_actors') or [])!=[]:
            continue
        checks:dict[str,Any]={}
        for rule in rs.get('rules') or []:
            if rule.get('type')!='required_status_checks': continue
            for item in (rule.get('parameters') or {}).get('required_status_checks') or []:
                if item.get('context') in REQUIRED_CHECKS:
                    checks[str(item['context'])]=item.get('integration_id')
        if set(checks)!=set(REQUIRED_CHECKS): continue
        ids={value for value in checks.values() if isinstance(value,int)}
        if len(ids)==1 and all(isinstance(checks.get(name),int) for name in REQUIRED_CHECKS):
            return int(next(iter(ids)))
    return None

def _standard_job_labels(jobs:list[dict[str,Any]])->tuple[set[str],bool]:
    labels={str(label).lower() for job in jobs for label in (job.get('labels') or [])}
    unsafe='self-hosted' in labels
    return labels,unsafe

def compile_github_readback(root,client,*,subject_sha:str,repository:dict[str,Any],rulesets:list[dict[str,Any]])->tuple[dict[str,Any],dict[str,str],list[dict[str,Any]],list[str]]:
    if SHA.fullmatch(subject_sha) is None: raise ValueError('GITHUB_READBACK_SHA_INVALID')
    info=client.repo_info();default_branch=str(info.get('default_branch') or 'main')
    current_main=str((client.branch(default_branch).get('commit') or {}).get('sha') or '')
    is_canonical_main=SHA.fullmatch(current_main) is not None and subject_sha==current_main

    checks=client.list(f'/repos/{client.repo}/commits/{subject_sha}/check-runs?per_page=100',object_key='check_runs')
    matched={name:next((c for c in checks if c.get('name')==name and c.get('conclusion')=='success' and c.get('head_sha')==subject_sha and (c.get('app') or {}).get('slug')=='github-actions' and isinstance((c.get('app') or {}).get('id'),int)),None) for name in REQUIRED_CHECKS}
    matched_ids={int((c.get('app') or {}).get('id')) for c in matched.values() if c is not None}
    pr_integration_id=next(iter(matched_ids)) if len(matched_ids)==1 and all(matched.values()) else None
    ruleset_integration_id=_ruleset_integration_id(rulesets)
    expected_integration_id=ruleset_integration_id if is_canonical_main else pr_integration_id
    rules=verify_rulesets(rulesets,expected_integration_id=expected_integration_id);anchor_rules=verify_runtime_anchor_ruleset(rulesets)

    runner_verified=False;runner='NOT_VERIFIED';larger=False;records=[];refs=[]
    observed,expires=_time();policy=load_effective_policy(root)
    profile='CANONICAL_MAIN_OPERATIONAL' if is_canonical_main else 'PR_MERGE_AUTHORITY'
    gate_map:dict[str,str]

    if is_canonical_main:
        push_runs=[r for r in client.recent_runs(limit=100,event='push') if r.get('event')=='push' and r.get('head_sha')==subject_sha and r.get('conclusion')=='success']
        main_run=next((r for r in push_runs if r.get('name')=='ADWF Main'),None)
        smoke_run=next((r for r in push_runs if r.get('name')=='ADWF Platform Smoke'),None)
        gate_map={
            MAIN_GATES[0]:'PASS' if main_run is not None else 'NOT_VERIFIED',
            MAIN_GATES[1]:'PASS' if smoke_run is not None else 'NOT_VERIFIED',
        }
        main_jobs=client.jobs(int(main_run['id'])) if main_run is not None else []
        smoke_jobs=client.jobs(int(smoke_run['id'])) if smoke_run is not None else []
        main_labels,main_unsafe=_standard_job_labels(main_jobs)
        smoke_labels,smoke_unsafe=_standard_job_labels(smoke_jobs)
        larger=main_unsafe or smoke_unsafe
        main_jobs_ok=bool(main_jobs) and all(j.get('conclusion')=='success' for j in main_jobs) and main_labels=={'ubuntu-24.04'}
        smoke_jobs_ok=bool(smoke_jobs) and all(j.get('conclusion')=='success' for j in smoke_jobs) and smoke_labels=={'ubuntu-24.04','windows-2022'}
        runner_verified=main_jobs_ok and smoke_jobs_ok and not larger
        if runner_verified: runner='github-hosted-standard:ubuntu-24.04+windows-2022'
        for run,gate in ((main_run,MAIN_GATES[0]),(smoke_run,MAIN_GATES[1])):
            if run is None: continue
            jobs=main_jobs if gate==MAIN_GATES[0] else smoke_jobs
            ref=f"github-run:{run.get('id')}";refs.append(ref)
            records.append({'ref_id':ref,'subject_sha':subject_sha,'policy_hash':policy['policy_hash'],'artifact_digest':_digest({'run':run,'jobs':jobs}),
              'producer':{'provider':'github','run_id':str(run.get('id')),'app_slug':'github-actions','readback_verified':True},
              'external_anchor':{'anchor_id':str(run.get('id')),'readback_verified':True},'observed_at':observed,'expires_at':expires})
    else:
        gate_map={name:'PASS' if matched[name] is not None else 'NOT_VERIFIED' for name in REQUIRED_CHECKS}
        pr_runs=[r for r in client.recent_runs(limit=100,event='pull_request') if r.get('event')=='pull_request' and r.get('head_sha')==subject_sha and r.get('name')=='ADWF PR' and r.get('conclusion')=='success']
        if pr_runs:
            jobs=client.jobs(int(pr_runs[0]['id']))
            labels,unsafe=_standard_job_labels(jobs);larger=unsafe
            if labels=={'ubuntu-24.04'} and not unsafe and all(j.get('conclusion')=='success' for j in jobs):
                runner_verified=True;runner='ubuntu-24.04'
        for name in REQUIRED_CHECKS:
            match=matched.get(name)
            if not match: continue
            ref=f"github-check:{match.get('id')}";refs.append(ref)
            records.append({'ref_id':ref,'subject_sha':subject_sha,'policy_hash':policy['policy_hash'],'artifact_digest':_digest(match),
              'producer':{'provider':'github','run_id':str(match.get('id')),'app_slug':((match.get('app') or {}).get('slug')),'readback_verified':True},
              'external_anchor':{'anchor_id':str(match.get('id')),'readback_verified':True},'observed_at':observed,'expires_at':expires})

    visibility=str(repository.get('visibility') or ('PUBLIC' if repository.get('private') is False else 'PRIVATE')).upper()
    facts_ok=visibility=='PUBLIC' and rules['readback_verified'] and anchor_rules['readback_verified'] and expected_integration_id is not None
    ok=facts_ok and all(v=='PASS' for v in gate_map.values()) and runner_verified and not larger
    owner_decision=GitHubOwnerDecisionStore(client).latest_for_sha(subject_sha)
    readback={'provider':'github','subject_sha':subject_sha,'profile':profile,'default_branch':default_branch,'canonical_main_sha':current_main,
      'repository_visibility':visibility,'runner':runner,'larger_runner':larger,'ruleset':rules,'runtime_anchor_ruleset':anchor_rules,
      'expected_check_integration_id':expected_integration_id,'gates':gate_map,'facts_readback_verified':facts_ok,'runner_verified':runner_verified,
      'readback_verified':ok,'observed_at':observed,'evidence_refs':refs,'owner_decision':owner_decision}
    return readback,gate_map,records,refs
