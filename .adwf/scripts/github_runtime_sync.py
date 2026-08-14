#!/usr/bin/env python3
"""Restore/persist Runtime Supervisor state through GitHub Runtime Ledger."""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.github_provider import GitHubClient
from lib.github_runtime_store import GitHubRuntimeStore
from lib.work_memory import WorkMemoryStore

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--restore',action='store_true');p.add_argument('--persist',action='store_true');p.add_argument('--run-id');args=p.parse_args()
    repo,token=os.environ.get('GITHUB_REPOSITORY',''),os.environ.get('GITHUB_TOKEN','')
    if not repo or not token:raise SystemExit('GITHUB_REPOSITORY/GITHUB_TOKEN missing')
    store=GitHubRuntimeStore(GitHubClient(repo,token))
    output={}
    if args.restore:output['restored']=store.restore_latest(ROOT)
    if args.persist:
        active=OrchestrationJournal(ROOT).list_active(); state=OrchestrationJournal(ROOT).load(args.run_id) if args.run_id else (active[0] if len(active)==1 else None)
        if state is None:output['persisted']={'status':'NO_ACTIVE_RUN'}
        else:output['persisted']=store.append(state,WorkMemoryStore(ROOT).load())
    print(json.dumps(output,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
