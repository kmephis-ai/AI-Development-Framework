#!/usr/bin/env python3
"""Restore/persist public-safe runtime state through GitHub Runtime Ledger."""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.github_provider import GitHubClient
from lib.github_runtime_store import GitHubRuntimeStore
from lib.work_memory import WorkMemoryStore


def _load_json(path:str|None)->dict|None:
    if path is None:return None
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('SESSION_CONTINUITY_CHECKPOINT_NOT_OBJECT')
    return value


def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--restore',action='store_true');p.add_argument('--persist',action='store_true');p.add_argument('--run-id')
    p.add_argument('--session-checkpoint',help='Validated SessionContinuityCheckpoint JSON to persist with --persist.')
    p.add_argument('--restore-session',action='store_true',help='Return the latest verified continuity checkpoint as resume context only.')
    p.add_argument('--actual-main-sha',help='Fresh caller-supplied provider main SHA required by --restore-session.')
    p.add_argument('--actual-head-sha',help='Fresh caller-supplied provider writer HEAD SHA for --restore-session when applicable.')
    args=p.parse_args()
    if args.session_checkpoint and not args.persist:p.error('--session-checkpoint requires --persist')
    if args.restore_session and not args.actual_main_sha:p.error('--restore-session requires --actual-main-sha from fresh provider readback')
    if args.actual_head_sha and not args.restore_session:p.error('--actual-head-sha requires --restore-session')
    repo,token=os.environ.get('GITHUB_REPOSITORY',''),os.environ.get('GITHUB_TOKEN','')
    if not repo or not token:raise SystemExit('GITHUB_REPOSITORY/GITHUB_TOKEN missing')
    store=GitHubRuntimeStore(GitHubClient(repo,token))
    output={}
    if args.restore:output['restored']=store.restore_latest(ROOT)
    if args.restore_session:
        output['session_continuity']=store.restore_latest_session_continuity(actual_main_sha=args.actual_main_sha,actual_head_sha=args.actual_head_sha)
    if args.persist:
        active=OrchestrationJournal(ROOT).list_active(); state=OrchestrationJournal(ROOT).load(args.run_id) if args.run_id else (active[0] if len(active)==1 else None)
        if state is None:output['persisted']={'status':'NO_ACTIVE_RUN'}
        else:output['persisted']=store.append(state,WorkMemoryStore(ROOT).load(),session_checkpoint=_load_json(args.session_checkpoint))
    print(json.dumps(output,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
