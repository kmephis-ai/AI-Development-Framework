#!/usr/bin/env python3
"""Transactional ADWF v1.5.0 -> v1.6.0 trusted configuration/state migration.

Run this only after the package code has been replaced by v1.6. The migration
changes local trusted configuration/projections only; it does not mutate GitHub
rulesets, secrets, releases, deployments, repository visibility or LICENSE.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import argparse,copy,os,sys
ADWF_HOME=Path(__file__).resolve().parent;sys.path.insert(0,str(ADWF_HOME))
from migrate_v1_1_to_v1_2 import atomic_json,backup,restore
from lib.policy_compiler import compile_policy
from lib.contracts import validate
from lib.strict_json import load as strict_load
SOURCE_VERSION='1.5.0';TARGET_VERSION='1.6.0'

def _load(p:Path)->dict:
    v=strict_load(p)
    if not isinstance(v,dict):raise ValueError('JSON_OBJECT_REQUIRED:'+p.name)
    return v

def _jp(root:Path)->Path:return root/'.adwf/migrations/v1.5-to-v1.6-journal.json'
def _write(p:Path,v:dict):p.parent.mkdir(parents=True,exist_ok=True);atomic_json(p,v)
def _recover(root:Path):
    p=_jp(root)
    if not p.is_file():return
    j=_load(p)
    if j.get('status')=='PREPARED':
        manifest=Path(str(j.get('backup_manifest') or ''))
        if not manifest.is_file():raise SystemExit('MIGRATION_RECOVERY_BLOCKED:BACKUP_MANIFEST_MISSING')
        restore(root,manifest);j['status']='ROLLED_BACK';j['recovered_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write(p,j)

def migrated_config(old:dict)->dict:
    v=copy.deepcopy(old);v['framework_version']=TARGET_VERSION;v['schema_version']=5
    v.setdefault('runtime_supervisor',{}).update({'enabled':True,'work_memory':'.adwf-runtime/work-context.json','remote_store':'GITHUB_ISSUE_LEDGER','resume_after_restart':True,'action_envelopes':True,'raw_chain_of_thought_storage':False,'executor_registry':'ActionExecutorRegistry','single_ssot':'DURABLE_ORCHESTRATOR'})
    v.setdefault('project_packs',{}).update({'enabled':True,'auto_detect':True,'builtin':['python','fastapi','node','react','vue','angular','go'],'materialized':False})
    v.setdefault('release_automation',{}).update({'auto_supported':True,'owner_confirmation_required':True,'semantic_versioning':True,'tag_before_release':True,'transactional_version_bump':True,'caller_version_forbidden':True})
    v.setdefault('delivery',{'deployment_required':False,'promotion_adapter':'NONE','observation_required':False,'observation_adapter':'NONE'})
    v.setdefault('github',{}).setdefault('trust',{})['required_check_names']=['fast-feedback','adwf/governance-gate','adwf/trusted-gate']
    return v

def migrated_state(old:dict)->dict:
    v=copy.deepcopy(old);v['framework_version']=TARGET_VERSION
    v['last_reconciled_at']=None;v['last_verified_at']=None
    for k in v.setdefault('health',{}):v['health'][k]='NOT_VERIFIED'
    v['blockers']=list(dict.fromkeys(list(v.get('blockers') or [])+['v1.6 migration complete; live provider trust, preview, deployment and Product Health require fresh readback.']))
    return v

def validate_target(root:Path,cfg:dict,state:dict)->list[str]:
    out=[]
    for name,data,schema in [('CONFIG',cfg,'config.schema.json'),('STATE',state,'project-state.schema.json')]:
        out.extend(f'{name}:{x.path}:{x.code}' for x in validate(data,_load(root/'.adwf/schemas'/schema)))
    return out

def main()->int:
    a=argparse.ArgumentParser();a.add_argument('--root',default='.');a.add_argument('--apply',action='store_true');a.add_argument('--rollback');args=a.parse_args();root=Path(args.root).resolve()
    if args.rollback:
        m=Path(args.rollback).resolve()
        if not args.apply:print('ROLLBACK DRY-RUN:',m);return 0
        restore(root,m);print('ROLLBACK APPLIED');return 0
    _recover(root);cp=root/'.adwf/config.json';sp=root/'.adwf/project-state.json';pp=root/'.adwf/effective-policy.json'
    if not all(p.is_file() for p in (cp,sp,pp)):raise SystemExit('MIGRATION_SOURCE_FILES_MISSING')
    oc,os_=_load(cp),_load(sp)
    if oc.get('framework_version')==TARGET_VERSION:print('ALREADY_V1_6: migration not required.');return 0
    if oc.get('framework_version')!=SOURCE_VERSION or os_.get('framework_version')!=SOURCE_VERSION:raise SystemExit('EXPECTED_ADWF_V1_5_SOURCE')
    cfg,state=migrated_config(oc),migrated_state(os_);findings=validate_target(root,cfg,state)
    if findings:raise SystemExit('MIGRATION_PLAN_INVALID:'+','.join(findings))
    print('Plan: backup -> PREPARED -> config -> state -> Effective Policy -> readback -> COMMITTED.')
    if not args.apply:print('DRY-RUN');return 0
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');manifest=backup(root,[cp,sp,pp],f'{stamp}-v1.5-to-v1.6')
    journal={'schema_version':1,'migration':'v1.5-to-v1.6','status':'PREPARED','prepared_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'backup_manifest':str(manifest)};_write(_jp(root),journal)
    try:
        atomic_json(cp,cfg)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='config':raise RuntimeError('FAULT_AFTER_CONFIG')
        atomic_json(sp,state)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='state':raise RuntimeError('FAULT_AFTER_STATE')
        compiled,errors=compile_policy(root)
        if errors:raise ValueError('POLICY_COMPILE_FAILED:'+','.join(errors))
        atomic_json(pp,compiled)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='policy':raise RuntimeError('FAULT_AFTER_POLICY')
        findings=validate_target(root,_load(cp),_load(sp))
        if findings:raise ValueError('POST_VERIFY_FAILED:'+','.join(findings))
        journal['status']='COMMITTED';journal['committed_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write(_jp(root),journal)
    except Exception:
        restore(root,manifest);journal['status']='ROLLED_BACK';journal['rolled_back_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write(_jp(root),journal);raise
    print('APPLIED:',TARGET_VERSION,'journal COMMITTED; rollback manifest:',manifest);return 0
if __name__=='__main__':raise SystemExit(main())
