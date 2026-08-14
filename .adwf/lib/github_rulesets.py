"""Build and fail-closed verify canonical public-GitHub governance ruleset."""
from __future__ import annotations
from typing import Any
REQUIRED_CHECKS=('fast-feedback','adwf/governance-gate','adwf/trusted-gate')

def canonical_ruleset_payload(*,integration_id:int|None=None)->dict[str,Any]:
    checks=[]
    for context in REQUIRED_CHECKS:
        item={'context':context}
        if integration_id is not None:item['integration_id']=int(integration_id)
        checks.append(item)
    return {'name':'ADWF protected main','target':'branch','enforcement':'active','bypass_actors':[],
      'conditions':{'ref_name':{'include':['~DEFAULT_BRANCH'],'exclude':[]}},'rules':[
        {'type':'deletion'},{'type':'non_fast_forward'},
        {'type':'pull_request','parameters':{'dismiss_stale_reviews_on_push':True,'require_code_owner_review':False,'require_last_push_approval':False,'required_approving_review_count':0,'required_review_thread_resolution':False}},
        {'type':'required_status_checks','parameters':{'strict_required_status_checks_policy':True,'do_not_enforce_on_create':False,'required_status_checks':checks}}
      ]}

def verify_rulesets(rulesets:list[dict[str,Any]],*,expected_integration_id:int|None=None)->dict[str,Any]:
    reasons=[];matched=None
    for rs in rulesets:
        local=[]
        if rs.get('target')!='branch':local.append('RULESET_TARGET')
        if rs.get('enforcement')!='active':local.append('RULESET_ENFORCEMENT')
        if (rs.get('bypass_actors') or [])!=[]:local.append('RULESET_BYPASS_ACTORS_PRESENT')
        conditions=rs.get('conditions') or {};refs=(conditions.get('ref_name') or {}).get('include') or []
        if '~DEFAULT_BRANCH' not in refs:local.append('RULESET_DEFAULT_BRANCH_CONDITION')
        types={r.get('type') for r in rs.get('rules') or []};checks={};strict=False
        for r in rs.get('rules') or []:
            if r.get('type')=='required_status_checks':
                params=r.get('parameters') or {};strict=params.get('strict_required_status_checks_policy') is True
                for x in params.get('required_status_checks') or []:
                    if x.get('context'):checks[str(x.get('context'))]=x.get('integration_id')
        if not {'deletion','non_fast_forward','pull_request','required_status_checks'}.issubset(types):local.append('RULESET_PROTECTIONS_MISSING')
        if not set(REQUIRED_CHECKS).issubset(checks):local.append('RULESET_REQUIRED_CHECKS_MISSING')
        if expected_integration_id is not None and any(int(checks.get(c) or -1)!=int(expected_integration_id) for c in REQUIRED_CHECKS):local.append('RULESET_CHECK_SOURCE_MISMATCH')
        if not strict:local.append('RULESET_STRICT_STATUS_POLICY_MISSING')
        if not local:matched=rs;break
        reasons.extend(local)
    if matched is None:reasons.append('CANONICAL_RULESET_NOT_VERIFIED')
    return {'status':'VERIFIED' if matched else 'NOT_VERIFIED','readback_verified':bool(matched),'required_checks':list(REQUIRED_CHECKS),'expected_integration_id':expected_integration_id,'ruleset_id':matched.get('id') if matched else None,'reason_codes':list(dict.fromkeys(reasons))}

def discover_check_source(client,*,max_pull_heads:int=25)->dict[str,Any]:
    """Prove that all required contexts have recently succeeded from one GitHub app."""
    observed:dict[str,int]={};heads=[]
    try:
        info=client.repo_info();default=str(info.get('default_branch') or 'main');sha=str((client.branch(default).get('commit') or {}).get('sha') or '')
        if sha:heads.append(sha)
    except Exception:pass
    try:
        for pr in client.pulls()[:max_pull_heads]:
            sha=str((pr.get('head') or {}).get('sha') or '')
            if sha and sha not in heads:heads.append(sha)
    except Exception:pass
    for sha in heads[:max_pull_heads]:
        try:checks=client.check_runs(sha)
        except Exception:continue
        for c in checks:
            name=str(c.get('name') or '')
            if name not in REQUIRED_CHECKS or c.get('status')!='completed' or c.get('conclusion')!='success':continue
            app=c.get('app') or {}
            if app.get('slug')!='github-actions' or not isinstance(app.get('id'),int):continue
            observed.setdefault(name,int(app['id']))
        if set(observed)==set(REQUIRED_CHECKS):break
    ids=set(observed.values())
    return {'status':'VERIFIED' if set(observed)==set(REQUIRED_CHECKS) and len(ids)==1 else 'NOT_VERIFIED','checks':observed,'integration_id':next(iter(ids)) if len(ids)==1 else None,'reason_codes':[] if set(observed)==set(REQUIRED_CHECKS) and len(ids)==1 else ['REQUIRED_CHECK_CONTEXTS_NOT_SEEDED_FROM_ONE_TRUSTED_APP']}


def runtime_anchor_ruleset_payload()->dict[str,Any]:
    return {'name':'ADWF immutable runtime anchors','target':'tag','enforcement':'active','bypass_actors':[],
      'conditions':{'ref_name':{'include':['refs/tags/adwf-runtime-anchor-*'],'exclude':[]}},
      'rules':[{'type':'deletion'},{'type':'update'}]}

def verify_runtime_anchor_ruleset(rulesets:list[dict[str,Any]])->dict[str,Any]:
    reasons=[];matched=None
    for rs in rulesets:
        local=[]
        if rs.get('name')!='ADWF immutable runtime anchors':continue
        if rs.get('target')!='tag':local.append('ANCHOR_RULESET_TARGET')
        if rs.get('enforcement')!='active':local.append('ANCHOR_RULESET_ENFORCEMENT')
        if (rs.get('bypass_actors') or [])!=[]:local.append('ANCHOR_RULESET_BYPASS_ACTORS_PRESENT')
        refs=((rs.get('conditions') or {}).get('ref_name') or {}).get('include') or []
        if 'refs/tags/adwf-runtime-anchor-*' not in refs:local.append('ANCHOR_RULESET_PATTERN')
        types={r.get('type') for r in rs.get('rules') or []}
        if not {'deletion','update'}.issubset(types):local.append('ANCHOR_RULESET_IMMUTABILITY_RULES_MISSING')
        if not local:matched=rs;break
        reasons.extend(local)
    if matched is None:reasons.append('RUNTIME_ANCHOR_RULESET_NOT_VERIFIED')
    return {'status':'VERIFIED' if matched else 'NOT_VERIFIED','readback_verified':bool(matched),'ruleset_id':matched.get('id') if matched else None,'reason_codes':list(dict.fromkeys(reasons))}
