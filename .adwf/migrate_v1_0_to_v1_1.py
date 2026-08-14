#!/usr/bin/env python3
import argparse,json,shutil
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); ap.add_argument('--apply',action='store_true'); a=ap.parse_args()
    root=Path(a.root).resolve(); cfg=root/'.adwf/config.json'; state=root/'.adwf/project-state.json'
    if not cfg.exists(): raise SystemExit('v1.0 config not found')
    old=json.loads(cfg.read_text(encoding='utf-8'))
    plan=['framework_version 1.0.x → 1.1.0','добавить language/orchestration/reality/roadmap_quality/cost policy','project-state schema_version → 2','не повышать autonomy и risk ceiling','не включать фиктивный PASS для product gates']
    print('Migration plan:'); [print(' - '+x) for x in plan]
    if not a.apply: print('\nDRY-RUN: use --apply'); return
    backup=root/'.adwf/migration-backup-v1.0'; backup.mkdir(parents=True,exist_ok=True)
    shutil.copy2(cfg,backup/'config.json')
    if state.exists(): shutil.copy2(state,backup/'project-state.json')
    old['framework_version']='1.1.0'; old.setdefault('language',{'human_facing':'ru','machine_facing':'en'})
    old.setdefault('orchestration',{'enabled':True,'max_parallel_writers':2,'merge_integration':'SERIALIZED','lease_ttl_minutes':120})
    old.setdefault('reality',{'baseline_certification_required':True,'golden_paths_required_for_product_projects':True,'reality_check_every_significant_prs':5})
    old.setdefault('roadmap_quality',{'enabled':True,'verification_gap_warn':0.15,'verification_gap_block':0.30,'false_progress_detection':True})
    old.setdefault('cost',{'mode':'FREE_ONLY','artifact_retention_days':7,'cancel_superseded':True})
    cfg.write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if state.exists():
        s=json.loads(state.read_text(encoding='utf-8')); s['framework_version']='1.1.0'; s['schema_version']=2
        s.setdefault('health',{'product':'NOT_VERIFIED','roadmap':'NOT_VERIFIED','architecture':'NOT_VERIFIED','debt':'NOT_VERIFIED','adwf':'NOT_VERIFIED'})
        s.setdefault('progress',{'implementation':0.0,'verification':0.0,'product_readiness':0.0,'verification_gap':0.0})
        state.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('APPLIED. Run adwf doctor + self-test + Baseline/Reality Check before progression.')
if __name__=='__main__': main()
