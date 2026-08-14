"""Performance evidence ledger and fail-closed CI budgets for ADWF v1.6.

Every number carries its own evidence status.  Missing samples, missing
superseded-run data or unknown grouping never becomes a green metric.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json, os, tempfile
from .metrics import summarize_ci

TARGET_MAX={
    'p95_duration_seconds':90.0,
    'p95_time_to_first_failure_seconds':45.0,
    'flake_rate':0.01,
}
TARGET_MIN={'superseded_cancellation_rate':0.95}
MIN_SAMPLES=30


def _atomic(path:Path,value:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:
            json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _metric(summary:dict[str,Any],key:str,target:float,*,minimum:bool=False,samples:int)->dict[str,Any]:
    value=summary.get(key)
    # Cancellation has a different denominator: only known superseded runs.
    evidence_samples=int(summary.get('superseded_runs') or 0) if key=='superseded_cancellation_rate' else samples
    if summary.get('status')!='VERIFIED' or evidence_samples < (10 if key=='superseded_cancellation_rate' else MIN_SAMPLES) or value is None:
        status='NOT_VERIFIED'
    elif minimum:
        status='PASS' if float(value)>=target else 'REGRESSION'
    else:
        status='PASS' if float(value)<=target else 'REGRESSION'
    return {'status':status,'value':value,('target_min' if minimum else 'target_max'):target,'samples':evidence_samples}


def _group_summaries(payload:dict[str,Any],now:datetime|None)->dict[str,Any]:
    out={}
    groups=payload.get('groups') or {}
    if not isinstance(groups,dict):return {'status':'NOT_VERIFIED','reason':'GROUPS_INVALID'}
    for name,rows in sorted(groups.items()):
        if not isinstance(rows,list):continue
        sub={'observed_at':payload.get('observed_at'),'runs':rows}
        s=summarize_ci(sub,now=now)
        out[name]={'status':s.get('status'),'samples':s.get('runs'),'p95_duration_seconds':s.get('p95_duration_seconds'),'p95_queue_seconds':s.get('p95_queue_seconds'),'p95_time_to_first_failure_seconds':s.get('p95_time_to_first_failure_seconds'),'flake_rate':s.get('flake_rate')}
    return out


def assess_performance(payload:dict[str,Any],*,now:datetime|None=None)->dict[str,Any]:
    summary=summarize_ci(payload,now=now);samples=int(summary.get('runs') or 0);metrics={}
    for key,target in TARGET_MAX.items():metrics[key]=_metric(summary,key,target,samples=samples)
    for key,target in TARGET_MIN.items():metrics[key]=_metric(summary,key,target,minimum=True,samples=samples)
    # Queue is provider-controlled, therefore evidence is displayed but never
    # used to fail the framework budget.
    metrics['p95_queue_seconds']={'status':'PASS' if summary.get('status')=='VERIFIED' and samples>=MIN_SAMPLES and summary.get('p95_queue_seconds') is not None else 'NOT_VERIFIED','value':summary.get('p95_queue_seconds'),'target':'OBSERVE_SEPARATELY','samples':samples}
    hard=[v for k,v in metrics.items() if k!='p95_queue_seconds']
    overall='REGRESSION' if any(x['status']=='REGRESSION' for x in hard) else ('PASS' if hard and all(x['status']=='PASS' for x in hard) else 'NOT_VERIFIED')
    pack=str(payload.get('project_pack') or '').strip()
    per_pack={}
    if pack and pack!='NOT_VERIFIED':
        per_pack[pack]={
            'status':summary.get('status'),'samples':summary.get('runs'),
            'p95_duration_seconds':summary.get('p95_duration_seconds'),
            'p95_queue_seconds':summary.get('p95_queue_seconds'),
            'p95_time_to_first_failure_seconds':summary.get('p95_time_to_first_failure_seconds'),
            'flake_rate':summary.get('flake_rate'),
            'superseded_cancellation_rate':summary.get('superseded_cancellation_rate'),
        }
    return {
        'schema_version':2,'status':overall,'minimum_samples':MIN_SAMPLES,'window_days':payload.get('window_days'),
        'project_pack':pack or 'NOT_VERIFIED','summary':summary,'metrics':metrics,
        'per_impact':_group_summaries(payload,now),'per_pack':per_pack,
        'grouping_status':'VERIFIED' if payload.get('groups') and per_pack else 'NOT_VERIFIED'
    }


def persist_performance(root:str|Path,evidence:dict[str,Any])->Path:
    path=Path(root)/'.adwf-runtime/metrics/current.json';_atomic(path,evidence);return path
