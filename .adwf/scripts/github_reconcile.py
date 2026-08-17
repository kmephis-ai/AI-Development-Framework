#!/usr/bin/env python3
"""Trusted GitHub adapter: one provider contract → state/readback/assurance."""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,sys,tempfile
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'.adwf'))
from lib.assurance_builder import build_assurance_snapshot,persist_assurance_snapshot
from lib.cost_guard import evaluate_provider
from lib.github_provider import GitHubClient
from lib.github_readback import compile_github_readback
from lib.health import active_state_path,doctor
from lib.reconciliation import reconcile_snapshot
from lib.strict_json import loads as strict_loads
from lib.workspaces import read_registry


def atomic_json(path:Path,value:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h: json.dump(value,h,ensure_ascii=False,indent=2); h.write('\n'); h.flush(); os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--quota-input'); p.add_argument('--subject-sha'); args=p.parse_args()
    repo,token=os.environ.get('GITHUB_REPOSITORY',''),os.environ.get('GITHUB_TOKEN','')
    if not repo or not token: raise SystemExit('GITHUB_REPOSITORY/GITHUB_TOKEN missing')
    config=strict_loads((ROOT/'.adwf/config.json').read_text(encoding='utf-8'))
    if config.get('provider',{}).get('mode')!='github': raise SystemExit('CANONICAL_PROVIDER_NOT_GITHUB')
    client=GitHubClient(repo,token); repository=client.repo_info(); default_branch=repository.get('default_branch'); branch=client.branch(str(default_branch))
    issues=[x for x in client.issues() if 'pull_request' not in x]; pulls=client.pulls(); runs=client.recent_runs(limit=100); rulesets=client.rulesets()
    registry=strict_loads((ROOT/'.adwf/providers.json').read_text(encoding='utf-8')); capability=config.get('cost',{}).get('default_ci_capability')
    request={'provider':capability,'mandatory_ci':True,'automated':True,'projected_cost':0,'projected_units':0,
             'repository_visibility':'PUBLIC' if repository.get('private') is False else 'PRIVATE','runner_class':'standard'}
    if args.quota_input: request.update(strict_loads(Path(args.quota_input).read_text(encoding='utf-8')))
    cost=evaluate_provider(registry,request,canonical_provider='github')
    previous=strict_loads(active_state_path(ROOT).read_text(encoding='utf-8'))
    main_sha=branch['commit']['sha']; snapshot=reconcile_snapshot(previous,config,provider='github',main_sha=main_sha,issues=issues,pulls=pulls,runs=runs,cost=cost,workspace_registry=read_registry(ROOT))
    subject=args.subject_sha or os.environ.get('ADWF_SUBJECT_SHA') or main_sha
    provider_readback,gates,evidence_records,evidence_refs=compile_github_readback(ROOT,client,subject_sha=subject,repository=repository,rulesets=rulesets)
    snapshot.setdefault('provider',{}).update({'mode':'github','observed_at':provider_readback.get('observed_at'),'readback_verified':provider_readback.get('readback_verified'),
                                               'ruleset_status':(provider_readback.get('ruleset') or {}).get('status')})
    health_now=doctor(ROOT)
    categories=health_now['categories']; health_map={
      'package_integrity':categories['package_integrity']['status'],
      'config_health':categories['config_health']['status'],
      'control_plane_health':'HEALTHY' if provider_readback.get('facts_readback_verified') else 'NOT_VERIFIED',
      'product_health':'HEALTHY' if snapshot.get('health',{}).get('product') in {'VERIFIED','HEALTHY'} else 'NOT_VERIFIED'}
    assurance=build_assurance_snapshot(ROOT,subject_sha=subject,health=health_map,gates=gates,required_gates=list(gates),
      evidence_records=evidence_records,evidence_refs=evidence_refs,provider_readback=provider_readback,cost=cost)
    if args.apply:
        atomic_json(ROOT/'.adwf-runtime/project-state.json',snapshot); atomic_json(ROOT/'.adwf-runtime/provider-readback.json',provider_readback)
        persist_assurance_snapshot(ROOT,assurance)
        print('GITHUB RECONCILIATION: APPLIED')
    else:
        print(json.dumps({'project_state':snapshot,'provider_readback':provider_readback,'assurance':assurance},ensure_ascii=False,indent=2))
    return 0 if provider_readback.get('readback_verified') else 1
if __name__=='__main__': raise SystemExit(main())
