"""Compile live GitHub facts into provider readback and provider-attested evidence."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from typing import Any
import hashlib,json,re
from .github_rulesets import verify_rulesets,verify_runtime_anchor_ruleset,REQUIRED_CHECKS
from .policy_runtime import load_effective_policy
from .github_owner_decisions import GitHubOwnerDecisionStore

SHA=re.compile(r'^[0-9a-f]{40}$')
def _digest(v:Any)->str: return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
def _time()->tuple[str,str]:
    now=datetime.now(timezone.utc); return now.isoformat().replace('+00:00','Z'),(now+timedelta(hours=24)).isoformat().replace('+00:00','Z')

def compile_github_readback(root,client,*,subject_sha:str,repository:dict[str,Any],rulesets:list[dict[str,Any]])->tuple[dict[str,Any],dict[str,str],list[dict[str,Any]],list[str]]:
    if SHA.fullmatch(subject_sha) is None: raise ValueError('GITHUB_READBACK_SHA_INVALID')
    checks=client.list(f'/repos/{client.repo}/commits/{subject_sha}/check-runs?per_page=100',object_key='check_runs')
    matched={name:next((c for c in checks if c.get('name')==name and c.get('conclusion')=='success' and c.get('head_sha')==subject_sha and (c.get('app') or {}).get('slug')=='github-actions' and isinstance((c.get('app') or {}).get('id'),int)),None) for name in REQUIRED_CHECKS}
    gate_map={name:'PASS' if matched[name] is not None else 'NOT_VERIFIED' for name in REQUIRED_CHECKS}
    integration_ids={int((c.get('app') or {}).get('id')) for c in matched.values() if c is not None}
    expected_integration_id=next(iter(integration_ids)) if len(integration_ids)==1 and all(matched.values()) else None
    rules=verify_rulesets(rulesets,expected_integration_id=expected_integration_id);anchor_rules=verify_runtime_anchor_ruleset(rulesets)
    pr_runs=[r for r in client.recent_runs(limit=100,event='pull_request') if r.get('event')=='pull_request' and r.get('head_sha')==subject_sha and r.get('name')=='ADWF PR' and r.get('conclusion')=='success']
    runner_verified=False; runner='NOT_VERIFIED'; larger=False
    if pr_runs:
        jobs=client.jobs(int(pr_runs[0]['id']))
        labels=[str(x).lower() for j in jobs for x in (j.get('labels') or [])]
        if 'self-hosted' in labels: larger=True
        if 'ubuntu-24.04' in labels and 'self-hosted' not in labels:
            runner_verified=True; runner='ubuntu-24.04'
    visibility=str(repository.get('visibility') or ('PUBLIC' if repository.get('private') is False else 'PRIVATE')).upper()
    facts_ok=visibility=='PUBLIC' and rules['readback_verified'] and anchor_rules['readback_verified'] and expected_integration_id is not None
    ok=facts_ok and all(v=='PASS' for v in gate_map.values()) and runner_verified and not larger
    observed,expires=_time(); policy=load_effective_policy(root); records=[]; refs=[]
    for name in REQUIRED_CHECKS:
        match=matched.get(name)
        if not match: continue
        ref=f"github-check:{match.get('id')}"; refs.append(ref)
        records.append({'ref_id':ref,'subject_sha':subject_sha,'policy_hash':policy['policy_hash'],'artifact_digest':_digest(match),
          'producer':{'provider':'github','run_id':str(match.get('id')),'app_slug':((match.get('app') or {}).get('slug')),'readback_verified':True},
          'external_anchor':{'anchor_id':str(match.get('id')),'readback_verified':True},'observed_at':observed,'expires_at':expires})
    owner_decision=GitHubOwnerDecisionStore(client).latest_for_sha(subject_sha)
    readback={'provider':'github','subject_sha':subject_sha,'repository_visibility':visibility,'runner':runner,'larger_runner':larger,
      'ruleset':rules,'runtime_anchor_ruleset':anchor_rules,'expected_check_integration_id':expected_integration_id,'gates':gate_map,'facts_readback_verified':facts_ok,'runner_verified':runner_verified,'readback_verified':ok,'observed_at':observed,'evidence_refs':refs,'owner_decision':owner_decision}
    return readback,gate_map,records,refs
