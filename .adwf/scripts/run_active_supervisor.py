#!/usr/bin/env python3
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.runtime_supervisor import RuntimeSupervisor

def main()->int:
    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:print(json.dumps({'status':'NO_SINGLE_ACTIVE_RUN','active':len(active)}));return 0
    result=RuntimeSupervisor(ROOT).tick(active[0]['run_id']);print(json.dumps(result,ensure_ascii=False,indent=2));return 1 if result.get('status') in {'BLOCK','BLOCKED'} else 0
if __name__=='__main__':raise SystemExit(main())
