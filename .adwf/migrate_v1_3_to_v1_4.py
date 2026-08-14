#!/usr/bin/env python3
"""Transactional ADWF v1.3.0 -> v1.4.0 migration.

Safety properties:
- never makes a repository public without explicit owner confirmation;
- journal is PREPARED before target files are mutated and COMMITTED only after readback;
- interrupted PREPARED journal is rolled back before a new attempt;
- every write is atomic and policy is recompiled from target files.
"""
from __future__ import annotations
from pathlib import Path
import argparse, copy, json, os, sys
from datetime import datetime, timezone

ADWF_HOME=Path(__file__).resolve().parent
sys.path.insert(0,str(ADWF_HOME))
from migrate_v1_1_to_v1_2 import atomic_json, backup, restore  # noqa:E402
from lib.policy_compiler import compile_policy  # noqa:E402
from lib.contracts import validate  # noqa:E402
from lib.strict_json import load as strict_load  # noqa:E402

TARGET_VERSION='1.4.0'; SOURCE_VERSION='1.3.0'

def _load(path:Path)->dict:
    value=strict_load(path)
    if not isinstance(value,dict): raise ValueError(f'JSON_OBJECT_REQUIRED:{path.name}')
    return value

def _journal_path(root:Path)->Path:
    return root/'.adwf/migrations/v1.3-to-v1.4-journal.json'

def _write_journal(path:Path,value:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True); atomic_json(path,value)

def _recover_prepared(root:Path)->None:
    jp=_journal_path(root)
    if not jp.is_file(): return
    j=_load(jp)
    if j.get('status')=='PREPARED':
        manifest=Path(str(j.get('backup_manifest') or ''))
        if not manifest.is_file(): raise SystemExit('MIGRATION_RECOVERY_BLOCKED:BACKUP_MANIFEST_MISSING')
        restore(root,manifest)
        j['status']='ROLLED_BACK'; j['recovered_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        _write_journal(jp,j)

def migrated_config(old:dict)->dict:
    v=copy.deepcopy(old)
    for key in ('executive','preview','project_packs','runtime_supervisor','release_automation','secrets'):
        v.pop(key,None)
    v['$schema']='./schemas/config.schema.json'; v['framework_version']=TARGET_VERSION; v['schema_version']=4
    v['profile']='FREE_PUBLIC_GITHUB'; v.setdefault('project',{})['repository_visibility']='PUBLIC'
    v.setdefault('provider',{})['mode']='github'; v['provider']['secondary_write_enabled']=False
    ci=v.setdefault('ci',{})
    ci.update({'default_executor':'GITHUB_HOSTED_STANDARD','hosted_runner':'ubuntu-24.04','mandatory_ai_api':False,
               'timeout_minutes':15,'cancel_superseded':True,'artifact_policy':'DISABLED_BY_DEFAULT','artifact_retention_days':1,
               'cache_policy':'DISABLED_BY_DEFAULT','larger_runners_allowed':False,'separate_trust_domains':False,
               'untrusted_runner_labels':[],'main_runner_labels':[],'trusted_runner_labels':[],
               'failure_artifacts_upload_only_on_failure':True,'failure_artifact_max_days':1})
    cost=v.setdefault('cost',{}); cost.update({'mode':'FREE_ONLY','monetary_budget':0,'unknown_provider':'BLOCK',
        'potentially_paid_provider':'BLOCK','default_ci_capability':'github_public_standard',
        'allowed_capability_statuses':['FREE_VERIFIED'],'stale_capability':'BLOCK','owner_provided_requires_attestation':True})
    gh=v.setdefault('github',{}); trust=gh.setdefault('trust',{})
    trust['required_check_names']=['fast-feedback','adwf/trusted-gate']; trust.setdefault('trusted_check_app_slugs',['github-actions'])
    trust.setdefault('trusted_reviewer_logins',[]); trust.setdefault('check_ttl_hours',24); trust.setdefault('review_ttl_hours',168)
    v.setdefault('gitlab',{})['shared_runner_quota_allowed']=False
    v['pipeline_ir']='.adwf/pipeline-ir.json'
    return v

def migrated_state(old:dict)->dict:
    v=copy.deepcopy(old); v['$schema']='./schemas/project-state.schema.json'; v['framework_version']=TARGET_VERSION
    v['profile']='FREE_PUBLIC_GITHUB'; v.setdefault('provider',{})['mode']='github'; v['provider']['observed_at']=None
    v['status']='BOOTSTRAP'; v['autonomy_level']='A1'; v['risk_ceiling']='R1'
    for k in v.setdefault('health',{}): v['health'][k]='NOT_VERIFIED'
    v['last_reconciled_at']=None; v['last_verified_at']=None
    v['blockers']=['v1.4 migration complete; GitHub visibility/rules/check provenance/deployed revision still require live provider readback.']
    return v

def validate_target(root:Path,cfg:dict,state:dict)->list[str]:
    findings=[]
    for name,data,schema_name in [('CONFIG',cfg,'config-v1.4.schema.json'),('STATE',state,'project-state-v1.4.schema.json')]:
        schema=_load(root/'.adwf/schemas'/schema_name)
        findings.extend(f'{name}:{x.path}:{x.code}' for x in validate(data,schema))
    return findings

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); ap.add_argument('--apply',action='store_true')
    ap.add_argument('--public-confirmed',action='store_true',help='Owner explicitly confirms repository may become public')
    ap.add_argument('--license-acknowledged',action='store_true',help='Owner explicitly handled LICENSE/publication terms')
    ap.add_argument('--rollback'); args=ap.parse_args(); root=Path(args.root).resolve()
    if args.rollback:
        manifest=Path(args.rollback).resolve()
        if not args.apply: print(f'ROLLBACK DRY-RUN: {manifest}'); return 0
        restore(root,manifest); print('ROLLBACK APPLIED'); return 0
    _recover_prepared(root)
    cp=root/'.adwf/config.json'; sp=root/'.adwf/project-state.json'; pp=root/'.adwf/effective-policy.json'
    if not all(p.is_file() for p in (cp,sp,pp)): raise SystemExit('MIGRATION_SOURCE_FILES_MISSING')
    old_cfg,old_state=_load(cp),_load(sp)
    if old_cfg.get('framework_version')==TARGET_VERSION:
        print('ALREADY_V1_4: migration not required.'); return 0
    if old_cfg.get('framework_version')!=SOURCE_VERSION or old_state.get('framework_version')!=SOURCE_VERSION:
        raise SystemExit('EXPECTED_ADWF_V1_3_SOURCE')
    if not (args.public_confirmed and args.license_acknowledged):
        print('HUMAN_REQUIRED: v1.4 default is FREE_PUBLIC_GITHUB. Confirm public publication and handle LICENSE before apply.')
        return 6 if args.apply else 0
    cfg,state=migrated_config(old_cfg),migrated_state(old_state)
    findings=validate_target(root,cfg,state)
    if findings: raise SystemExit('MIGRATION_PLAN_INVALID:'+','.join(findings))
    print('Plan: backup -> PREPARED journal -> config -> state -> Effective Policy -> readback -> COMMITTED.')
    if not args.apply: print('DRY-RUN'); return 0
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    manifest=backup(root,[cp,sp,pp],f'{stamp}-v1.3-to-v1.4')
    journal={'schema_version':1,'migration':'v1.3-to-v1.4','status':'PREPARED','prepared_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'backup_manifest':str(manifest)}
    _write_journal(_journal_path(root),journal)
    try:
        atomic_json(cp,cfg)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='config': raise RuntimeError('FAULT_INJECTION_AFTER_CONFIG')
        atomic_json(sp,state)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='state': raise RuntimeError('FAULT_INJECTION_AFTER_STATE')
        compiled,errors=compile_policy(root)
        if errors: raise ValueError('POLICY_COMPILE_FAILED:'+','.join(errors))
        atomic_json(pp,compiled)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='policy': raise RuntimeError('FAULT_INJECTION_AFTER_POLICY')
        findings=validate_target(root,_load(cp),_load(sp))
        if findings: raise ValueError('POST_VERIFY_FAILED:'+','.join(findings))
        journal['status']='COMMITTED'; journal['committed_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        _write_journal(_journal_path(root),journal)
    except Exception:
        restore(root,manifest); journal['status']='ROLLED_BACK'; journal['rolled_back_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); _write_journal(_journal_path(root),journal); raise
    print(f'APPLIED: {TARGET_VERSION}; journal COMMITTED; rollback manifest: {manifest}'); return 0
if __name__=='__main__': raise SystemExit(main())
