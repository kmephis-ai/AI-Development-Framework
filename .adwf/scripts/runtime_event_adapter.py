#!/usr/bin/env python3
"""Translate trusted GitHub workflow_run events into bounded Supervisor wakeups.

A successful workflow_run is NEVER converted directly into CI=PASS.  Success only
wakes the Runtime Supervisor, whose CI executor must independently read back every
required exact-head check from GitHub.  A provider-declared failed/cancelled run
may be recorded as a negative result because negative evidence cannot escalate
privilege.
"""
from __future__ import annotations
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.runtime_supervisor import RuntimeSupervisor
from lib.strict_json import loads as strict_loads


def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--event',required=True);args=p.parse_args()
    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:
        print(json.dumps({'status':'NO_SINGLE_ACTIVE_RUN','active':len(active)}));return 0
    state=active[0]
    if state.get('phase')!='CI':
        print(json.dumps({'status':'IGNORED_PHASE','phase':state.get('phase')}));return 0
    event=strict_loads(Path(args.event).read_text(encoding='utf-8'));wr=event.get('workflow_run') or {}
    sha=str(wr.get('head_sha') or ''); conclusion=str(wr.get('conclusion') or '')
    if sha and state.get('subject_sha') and sha != state.get('subject_sha'):
        print(json.dumps({'status':'IGNORED_STALE_SHA','event_sha':sha,'run_sha':state.get('subject_sha')}));return 0
    supervisor=RuntimeSupervisor(ROOT)
    if conclusion=='success':
        # No positive assertion is created here.  The CI phase executor performs
        # provider API readback of all required checks on the exact run SHA.
        try: out=supervisor.tick(state['run_id'])
        except ValueError as exc:
            print(json.dumps({'status':'WAITING_TRUSTED_READBACK','error':str(exc),'run_id':state['run_id']},ensure_ascii=False));return 0
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0
    key,_=supervisor.envelopes.ensure_request(state)
    result={'phase':'CI','outcome':'FAIL','idempotency_key':key,'subject_sha':sha or state.get('subject_sha'),
            'evidence_refs':[],'reason_codes':['CI_'+(conclusion.upper() or 'UNKNOWN')],
            'cost_usd':0,'metadata':{'source':'GITHUB_WORKFLOW_RUN_NEGATIVE_ONLY'}}
    supervisor.envelopes.write_result(key,result)
    try: out=supervisor.tick(state['run_id'])
    except ValueError as exc:
        print(json.dumps({'status':'NEGATIVE_RESULT_RECORDED','error':str(exc),'run_id':state['run_id']},ensure_ascii=False));return 0
    print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
