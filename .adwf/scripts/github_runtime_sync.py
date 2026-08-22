#!/usr/bin/env python3
"""Restore/persist public-safe runtime state through GitHub Runtime Ledger."""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,sys,tempfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.github_provider import GitHubClient
from lib.github_runtime_store import GitHubRuntimeStore
from lib.work_memory import WorkMemoryStore

RESTORE_MARKER=ROOT/'.adwf-runtime'/'github-runtime-restore.json'

def _write_restore_marker(state:dict|None)->None:
    RESTORE_MARKER.parent.mkdir(parents=True,exist_ok=True)
    if state is None:
        RESTORE_MARKER.unlink(missing_ok=True);return
    payload=json.dumps({'run_id':state['run_id']},sort_keys=True)+'\n'
    fd,tmp=tempfile.mkstemp(prefix=RESTORE_MARKER.name+'.',dir=RESTORE_MARKER.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:h.write(payload);h.flush();os.fsync(h.fileno())
        os.replace(tmp,RESTORE_MARKER)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def _marker_run_id()->str|None:
    if not RESTORE_MARKER.is_file():return None
    try:value=json.loads(RESTORE_MARKER.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):raise ValueError('REMOTE_RUNTIME_RESTORE_MARKER_INVALID') from None
    run_id=value.get('run_id') if isinstance(value,dict) else None
    if not isinstance(run_id,str) or not run_id:raise ValueError('REMOTE_RUNTIME_RESTORE_MARKER_INVALID')
    return run_id

def _select_persist_state(run_id:str|None)->dict|None:
    journal=OrchestrationJournal(ROOT)
    if run_id:return journal.load(run_id)
    active=journal.list_active()
    if len(active)==1:return active[0]
    if len(active)>1:raise ValueError('MULTIPLE_ACTIVE_ORCHESTRATION_RUNS')
    restored_id=_marker_run_id()
    if not restored_id:return None
    state=journal.load(restored_id)
    if state.get('status') not in {'COMPLETE','BLOCKED'}:
        raise ValueError('REMOTE_RUNTIME_TERMINAL_MARKER_STATE_INVALID')
    return state


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
    if args.restore:
        output['restored']=store.restore_latest(ROOT);_write_restore_marker(output['restored'])
    if args.restore_session:
        output['session_continuity']=store.restore_latest_session_continuity(actual_main_sha=args.actual_main_sha,actual_head_sha=args.actual_head_sha)
    if args.persist:
        state=_select_persist_state(args.run_id)
        if state is None:output['persisted']={'status':'NO_ACTIVE_RUN'}
        else:
            output['persisted']=store.append(state,WorkMemoryStore(ROOT).load(),session_checkpoint=_load_json(args.session_checkpoint))
            if output['persisted'].get('status') in {'APPENDED','UNCHANGED'}:RESTORE_MARKER.unlink(missing_ok=True)
    print(json.dumps(output,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
