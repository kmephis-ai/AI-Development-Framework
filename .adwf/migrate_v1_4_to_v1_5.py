#!/usr/bin/env python3
"""Transactional ADWF v1.4.0 -> v1.5.0 configuration/state migration.

The package code must already be upgraded to v1.5. This migration only upgrades
trusted configuration/state, creates a PREPARED journal before mutations, and
rolls back on any failure. It never changes repository visibility, LICENSE,
provider permissions, secrets, or production resources.
"""
from __future__ import annotations
from pathlib import Path
import argparse, copy, os, sys
from datetime import datetime, timezone

ADWF_HOME=Path(__file__).resolve().parent
sys.path.insert(0,str(ADWF_HOME))
from migrate_v1_1_to_v1_2 import atomic_json, backup, restore  # noqa:E402
from lib.policy_compiler import compile_policy  # noqa:E402
from lib.contracts import validate  # noqa:E402
from lib.strict_json import load as strict_load  # noqa:E402

SOURCE_VERSION='1.4.0'; TARGET_VERSION='1.5.0'

def _load(path:Path)->dict:
    value=strict_load(path)
    if not isinstance(value,dict): raise ValueError(f'JSON_OBJECT_REQUIRED:{path.name}')
    return value

def _journal_path(root:Path)->Path:return root/'.adwf/migrations/v1.4-to-v1.5-journal.json'
def _write_journal(path:Path,value:dict)->None:path.parent.mkdir(parents=True,exist_ok=True);atomic_json(path,value)

def _recover(root:Path)->None:
    jp=_journal_path(root)
    if not jp.is_file():return
    j=_load(jp)
    if j.get('status')=='PREPARED':
        manifest=Path(str(j.get('backup_manifest') or ''))
        if not manifest.is_file():raise SystemExit('MIGRATION_RECOVERY_BLOCKED:BACKUP_MANIFEST_MISSING')
        restore(root,manifest);j['status']='ROLLED_BACK';j['recovered_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write_journal(jp,j)

def migrated_config(old:dict)->dict:
    v=copy.deepcopy(old);v['framework_version']=TARGET_VERSION;v['schema_version']=5
    v.setdefault('policy',{})['requested_autonomy']='A3';v['policy']['active_autonomy']='A2'
    v['executive']={'mode':'SIMPLE','primary_action':'CONTINUE','technical_details_default_hidden':True,'roadmap_embedded':True,'simple_status':True}
    v['preview']={'enabled':True,'engine':'PLAYWRIGHT','engine_version':'1.62.0','baseline_optional':True,'install_mode':'EXACT_PINNED','viewports':{'desktop':{'width':1440,'height':900},'mobile':{'width':390,'height':844}}}
    v['project_packs']={'enabled':True,'auto_detect':True,'builtin':['python','fastapi','node','react','vue','angular','go']}
    v['runtime_supervisor']={'enabled':True,'work_memory':'.adwf-runtime/work-context.json','remote_store':'GITHUB_ISSUE_LEDGER','resume_after_restart':True,'action_envelopes':True,'raw_chain_of_thought_storage':False}
    v['release_automation']={'auto_supported':True,'owner_confirmation_required':True,'semantic_versioning':True,'tag_before_release':True}
    v['secrets']={'mandatory_ai_secret':False,'hosted_store':'GITHUB_ACTIONS_SECRETS','local_store':'OS_KEYRING','encrypted_runtime_file_allowed':False}
    return v

def migrated_state(old:dict)->dict:
    v=copy.deepcopy(old);v['framework_version']=TARGET_VERSION
    v['status']='BOOTSTRAP'
    v['last_reconciled_at']=None;v['last_verified_at']=None
    for k in v.setdefault('health',{}):v['health'][k]='NOT_VERIFIED'
    v['blockers']=['v1.5 migration complete; live GitHub ruleset/readback, Preview, deployment and Product Health require fresh provider evidence.']
    return v

def validate_target(root:Path,cfg:dict,state:dict)->list[str]:
    findings=[]
    for name,data,schema_name in [('CONFIG',cfg,'config.schema.json'),('STATE',state,'project-state.schema.json')]:
        schema=_load(root/'.adwf/schemas'/schema_name)
        findings.extend(f'{name}:{x.path}:{x.code}' for x in validate(data,schema))
    return findings

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--root',default='.');ap.add_argument('--apply',action='store_true');ap.add_argument('--rollback');args=ap.parse_args();root=Path(args.root).resolve()
    if args.rollback:
        manifest=Path(args.rollback).resolve()
        if not args.apply:print(f'ROLLBACK DRY-RUN: {manifest}');return 0
        restore(root,manifest);print('ROLLBACK APPLIED');return 0
    _recover(root)
    cp=root/'.adwf/config.json';sp=root/'.adwf/project-state.json';pp=root/'.adwf/effective-policy.json'
    if not all(p.is_file() for p in (cp,sp,pp)):raise SystemExit('MIGRATION_SOURCE_FILES_MISSING')
    old_cfg,old_state=_load(cp),_load(sp)
    if old_cfg.get('framework_version')==TARGET_VERSION:print('ALREADY_V1_5: migration not required.');return 0
    if old_cfg.get('framework_version')!=SOURCE_VERSION or old_state.get('framework_version')!=SOURCE_VERSION:raise SystemExit('EXPECTED_ADWF_V1_4_SOURCE')
    cfg,state=migrated_config(old_cfg),migrated_state(old_state);findings=validate_target(root,cfg,state)
    if findings:raise SystemExit('MIGRATION_PLAN_INVALID:'+','.join(findings))
    print('Plan: backup -> PREPARED -> config -> state -> Effective Policy -> readback -> COMMITTED.')
    if not args.apply:print('DRY-RUN');return 0
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');manifest=backup(root,[cp,sp,pp],f'{stamp}-v1.4-to-v1.5')
    journal={'schema_version':1,'migration':'v1.4-to-v1.5','status':'PREPARED','prepared_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'backup_manifest':str(manifest)};_write_journal(_journal_path(root),journal)
    try:
        atomic_json(cp,cfg)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='config':raise RuntimeError('FAULT_INJECTION_AFTER_CONFIG')
        atomic_json(sp,state)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='state':raise RuntimeError('FAULT_INJECTION_AFTER_STATE')
        compiled,errors=compile_policy(root)
        if errors:raise ValueError('POLICY_COMPILE_FAILED:'+','.join(errors))
        atomic_json(pp,compiled)
        if os.environ.get('ADWF_MIGRATION_FAULT_AFTER')=='policy':raise RuntimeError('FAULT_INJECTION_AFTER_POLICY')
        findings=validate_target(root,_load(cp),_load(sp))
        if findings:raise ValueError('POST_VERIFY_FAILED:'+','.join(findings))
        journal['status']='COMMITTED';journal['committed_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write_journal(_journal_path(root),journal)
    except Exception:
        restore(root,manifest);journal['status']='ROLLED_BACK';journal['rolled_back_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');_write_journal(_journal_path(root),journal);raise
    print(f'APPLIED: {TARGET_VERSION}; journal COMMITTED; rollback manifest: {manifest}');return 0
if __name__=='__main__':raise SystemExit(main())
