"""Owner-confirmed GitHub bootstrap with staged seed PR, pack PR and ruleset readback.

Activation order is fail-closed:
1) seed the required check contexts on an unprotected PR;
2) prove all contexts came from the same GitHub Actions app;
3) activate/read back the canonical ruleset;
4) generate a governance bootstrap PR for the detected Project Pack.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import hashlib,json,shutil,subprocess,sys,tempfile
from .github_auth import detect_repository,discover_token
from .github_provider import GitHubClient
from .github_rulesets import canonical_ruleset_payload,verify_rulesets,discover_check_source,runtime_anchor_ruleset_payload,verify_runtime_anchor_ruleset
from .pack_materializer import materialize_project_pack
from .strict_json import loads as strict_loads

SEED_BRANCH='adwf/bootstrap-seed-v1-6';SEED_FILE='ADWF_BOOTSTRAP_SEED.md'

def _ensure_branch(client:GitHubClient,branch:str,sha:str)->None:
    try:client.git_ref(branch);return
    except Exception:client.create_ref(branch,sha)

def _open_pr_for_branch(client:GitHubClient,branch:str)->dict[str,Any]|None:
    for pr in client.pulls():
        if pr.get('state')=='open' and str((pr.get('head') or {}).get('ref') or '')==branch:return pr
    return None

def _framework_self_host(root:Path)->bool:
    """Return true only for the ADWF framework repository itself.

    Project Pack detection describes a product runtime. The framework package is
    intentionally not a runtime product, so absence of a product pack is a
    valid NOT_APPLICABLE state rather than HUMAN_REQUIRED.
    """
    try:cfg=strict_loads((root/'.adwf/config.json').read_text(encoding='utf-8'))
    except Exception:return False
    project=cfg.get('project') if isinstance(cfg,dict) else {}
    return isinstance(project,dict) and project.get('type')=='framework' and project.get('runtime_product') is False

def ensure_seed_pr(client:GitHubClient,default_branch:str,default_sha:str)->dict[str,Any]:
    existing=_open_pr_for_branch(client,SEED_BRANCH)
    if existing:return {'status':'WAITING_SEED_CHECKS','pull_request_number':existing.get('number'),'pull_request_url':existing.get('html_url'),'branch':SEED_BRANCH}
    _ensure_branch(client,SEED_BRANCH,default_sha)
    body='''# ADWF bootstrap seed\n\nThis non-governance marker exists only to seed the canonical `fast-feedback`, `adwf/governance-gate` and `adwf/trusted-gate` check contexts before the protection ruleset is activated.\n'''
    client.put_text_file(SEED_FILE,body,branch=SEED_BRANCH,message='chore(adwf): seed required checks')
    pr=client.create_pull(title='[ADWF] Seed protected check contexts',body='Safe bootstrap seed. No trust-boundary file is changed.',head=SEED_BRANCH,base=default_branch)
    return {'status':'SEED_PR_CREATED','pull_request_number':pr.get('number'),'pull_request_url':pr.get('html_url'),'branch':SEED_BRANCH}

def _pack_projection_files(root:Path,desired_config:dict[str,Any])->dict[str,str]:
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)/'repo';shutil.copytree(root,target,ignore=shutil.ignore_patterns('.git','.adwf-runtime','dist','node_modules','__pycache__'))
        (target/'.adwf/config.json').write_text(json.dumps(desired_config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        subprocess.run([sys.executable,str(target/'.adwf/scripts/compile_policy.py'),'--write'],cwd=target,check=True,capture_output=True,text=True)
        subprocess.run([sys.executable,str(target/'.adwf/scripts/generate_manifest.py')],cwd=target,check=True,capture_output=True,text=True)
        return {rel:(target/rel).read_text(encoding='utf-8') for rel in ('.adwf/config.json','.adwf/effective-policy.json','MANIFEST.json','SHA256SUMS.txt')}

def ensure_pack_pr(client:GitHubClient,root:Path,default_branch:str,default_sha:str,plan:dict[str,Any])->dict[str,Any]:
    if plan.get('status') in {'HUMAN_REQUIRED','NOT_APPLICABLE'}:return {'status':'NOT_APPLICABLE','reason':plan.get('reason') or 'PROJECT_PACK_NOT_DETECTED'}
    if plan.get('status')=='ALREADY_MATERIALIZED':return {'status':'ALREADY_MATERIALIZED','pack':plan.get('pack')}
    pack=str(plan.get('pack') or 'generic');branch=f'adwf/project-pack-{pack}-v1-6'
    existing=_open_pr_for_branch(client,branch)
    if existing:return {'status':'GOVERNANCE_PR_WAITING_OWNER_APPROVAL','pack':pack,'pull_request_number':existing.get('number'),'pull_request_url':existing.get('html_url'),'branch':branch}
    desired=plan.get('desired_config')
    if not isinstance(desired,dict):return {'status':'NOT_VERIFIED','reason':'PROJECT_PACK_DESIRED_CONFIG_MISSING'}
    files=_pack_projection_files(root,desired);_ensure_branch(client,branch,default_sha)
    for rel,text in files.items():client.put_text_file(rel,text,branch=branch,message=f'chore(adwf): materialize {pack} project pack')
    pr=client.create_pull(title=f'[ADWF] Materialize {pack} Project Pack',body='Generated governance bootstrap PR. It materializes deterministic project commands/preview settings; exact-HEAD owner/admin approval is required because trusted config changes.',head=branch,base=default_branch)
    return {'status':'GOVERNANCE_PR_CREATED','pack':pack,'pull_request_number':pr.get('number'),'pull_request_url':pr.get('html_url'),'branch':branch,'owner_approval_required':True}

def bootstrap_repository(root:str|Path,*,apply:bool)->dict[str,Any]:
    base=Path(root).resolve();pack_plan=materialize_project_pack(base,base,apply=False)
    if pack_plan.get('status')=='HUMAN_REQUIRED' and _framework_self_host(base):
        pack_plan={'status':'NOT_APPLICABLE','reason':'FRAMEWORK_SELF_HOST_PROJECT_PACK_NOT_APPLICABLE','write_performed':False}
    repo=detect_repository(base);token,source=discover_token()
    if not repo:return {'status':'HUMAN_REQUIRED','reason':'GITHUB_REPOSITORY_NOT_DETECTED','credential_source':source,'project_pack':pack_plan}
    if not token:return {'status':'HUMAN_REQUIRED','reason':'GITHUB_AUTH_REQUIRED','repository':repo,'credential_source':source,'project_pack':pack_plan}
    client=GitHubClient(repo,token);info=client.repo_info();visibility=str(info.get('visibility') or ('private' if info.get('private') else 'public')).upper();default=str(info.get('default_branch') or 'main')
    if visibility!='PUBLIC':return {'status':'HUMAN_REQUIRED','reason':'PUBLIC_REPOSITORY_REQUIRED','repository':repo,'visibility':visibility,'project_pack':pack_plan}
    default_sha=str((client.branch(default).get('commit') or {}).get('sha') or '')
    source_proof=discover_check_source(client)
    if source_proof['status']!='VERIFIED':
        seed=ensure_seed_pr(client,default,default_sha) if apply else {'status':'READY_TO_CREATE_SEED_PR'}
        return {'status':'WAITING_SEED_CHECKS','repository':repo,'visibility':'PUBLIC','reason':'REQUIRED_CHECKS_MUST_RUN_SUCCESSFULLY_BEFORE_RULESET_ACTIVATION','check_source':source_proof,'seed':seed,'project_pack':pack_plan,'credential_source':source}
    integration_id=int(source_proof['integration_id']);all_rules=client.rulesets();existing=verify_rulesets(all_rules,expected_integration_id=integration_id);anchor_rules=verify_runtime_anchor_ruleset(all_rules)
    if not existing['readback_verified']:
        payload=canonical_ruleset_payload(integration_id=integration_id)
        if not apply:return {'status':'READY_TO_APPLY','repository':repo,'visibility':'PUBLIC','ruleset_plan':payload,'current':existing,'check_source':source_proof,'project_pack':pack_plan,'credential_source':source}
        named=next((x for x in all_rules if x.get('name')=='ADWF protected main' and x.get('id') is not None),None)
        if named:readback=client.update_ruleset(int(named['id']),payload)
        else:
            key=hashlib.sha256((repo+':adwf-ruleset-v1.6:'+str(integration_id)).encode()).hexdigest();_,readback=client.create_ruleset(payload,idempotency_key=key)
        existing=verify_rulesets([readback],expected_integration_id=integration_id)
        if not existing['readback_verified']:
            return {'status':'NOT_VERIFIED','repository':repo,'ruleset':existing,'check_source':source_proof,'project_pack':pack_plan,'credential_source':source}
    if not anchor_rules['readback_verified']:
        if not apply:return {'status':'READY_TO_APPLY','repository':repo,'visibility':'PUBLIC','ruleset':existing,'runtime_anchor_ruleset_plan':runtime_anchor_ruleset_payload(),'check_source':source_proof,'project_pack':pack_plan,'credential_source':source}
        named_anchor=next((x for x in client.rulesets() if x.get('name')=='ADWF immutable runtime anchors' and x.get('id') is not None),None)
        payload_anchor=runtime_anchor_ruleset_payload()
        if named_anchor:anchor_readback=client.update_ruleset(int(named_anchor['id']),payload_anchor)
        else:
            akey=hashlib.sha256((repo+':adwf-runtime-anchor-ruleset-v1.6').encode()).hexdigest();_,anchor_readback=client.create_ruleset(payload_anchor,idempotency_key=akey)
        anchor_rules=verify_runtime_anchor_ruleset([anchor_readback])
        if not anchor_rules['readback_verified']:
            return {'status':'NOT_VERIFIED','repository':repo,'ruleset':existing,'runtime_anchor_ruleset':anchor_rules,'check_source':source_proof,'project_pack':pack_plan,'credential_source':source}
    if pack_plan.get('status')=='ALREADY_MATERIALIZED':
        pack_pr={'status':'ALREADY_MATERIALIZED','pack':pack_plan.get('pack')};status='VERIFIED'
    elif pack_plan.get('status')=='NOT_APPLICABLE':
        pack_pr={'status':'NOT_APPLICABLE','reason':pack_plan.get('reason')};status='VERIFIED'
    else:
        pack_pr=ensure_pack_pr(client,base,default,default_sha,pack_plan) if apply else {'status':'READY_TO_CREATE_GOVERNANCE_PR'}
        status='WAITING_OWNER_GOVERNANCE_APPROVAL' if pack_pr.get('status') in {'GOVERNANCE_PR_WAITING_OWNER_APPROVAL','GOVERNANCE_PR_CREATED'} else ('READY_TO_APPLY' if not apply else 'NOT_VERIFIED')
    return {'status':status if existing['readback_verified'] and anchor_rules['readback_verified'] else 'NOT_VERIFIED','repository':repo,'visibility':'PUBLIC','ruleset':existing,'runtime_anchor_ruleset':anchor_rules,'check_source':source_proof,'project_pack':pack_plan,'project_pack_pr':pack_pr,'credential_source':source}
