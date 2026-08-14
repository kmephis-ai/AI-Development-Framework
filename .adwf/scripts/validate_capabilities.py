#!/usr/bin/env python3
"""Fail-closed production-path traceability for ADWF v1.6 capability claims."""
from __future__ import annotations
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.contracts import validate
from lib.strict_json import load

ALLOWED={'IMPLEMENTED','LIVE_NOT_VERIFIED','OPTIONAL_ADAPTER','NOT_IMPLEMENTED'}

def _path_exists(value:str)->bool:
    p=ROOT/value
    return p.is_file() or p.is_dir()

def main()->int:
    errors=[]
    trace=load(ROOT/'.adwf/capability-traceability.json');schema=load(ROOT/'.adwf/schemas/capability-traceability.schema.json')
    errors.extend(f'SCHEMA:{x.path}:{x.code}' for x in validate(trace,schema))
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    if trace.get('framework_version')!=version:errors.append('CAPABILITY_VERSION_DRIFT')
    seen=set()
    for cap in trace.get('capabilities') or []:
        cid=str(cap.get('id') or '')
        if cid in seen:errors.append('CAPABILITY_DUPLICATE:'+cid)
        seen.add(cid)
        status=cap.get('status')
        if status not in ALLOWED:errors.append('CAPABILITY_STATUS_INVALID:'+cid)
        for field in ('entrypoints','production_paths','verification'):
            vals=cap.get(field) or []
            if status in {'IMPLEMENTED','LIVE_NOT_VERIFIED','OPTIONAL_ADAPTER'} and not vals:errors.append(f'CAPABILITY_{field.upper()}_EMPTY:{cid}')
            for rel in vals:
                if not _path_exists(str(rel)):errors.append(f'CAPABILITY_PATH_MISSING:{cid}:{rel}')
        if status in {'LIVE_NOT_VERIFIED','OPTIONAL_ADAPTER'} and not str(cap.get('live_boundary') or '').strip():errors.append('CAPABILITY_LIVE_BOUNDARY_MISSING:'+cid)
    required={'TRUSTED_GATE','DURABLE_FULL_LOOP','OWNER_WAKEUP_CONTINUE','SINGLE_SSOT','ACTIVE_TASK_IDENTITY','EXACT_SHA_PREVIEW','TRANSACTIONAL_AUTO_RELEASE','PROJECT_PACKS','PUBLIC_SAFE_RUNTIME_LEDGER','RULESET_READBACK','PIPELINE_IR_GENERATION','PERFORMANCE_PLANE','AGENT_RETURN_WAKEUP','DELIVERY_OBSERVATION','WINDOWS_HOSTED_SMOKE'}
    if not required.issubset(seen):errors.append('CAPABILITY_REQUIRED_MISSING:'+','.join(sorted(required-seen)))
    control=(ROOT/'.github/workflows/adwf-control.yml').read_text(encoding='utf-8')
    for needle in ('publish_trusted_gate.py','run_active_supervisor.py','consume_agent_result.py','github_runtime_sync.py','github_metrics_collector.py'):
        if needle not in control:errors.append('CAPABILITY_PRODUCTION_WIRING_MISSING:'+needle)
    if 'orchestrate_event.py' in control:errors.append('CAPABILITY_LEGACY_ORCHESTRATOR_STILL_WIRED')
    if errors:
        print('CAPABILITY TRACEABILITY: FAIL');[print('-',e) for e in errors];return 1
    print('CAPABILITY TRACEABILITY: PASS');return 0
if __name__=='__main__':raise SystemExit(main())
