"""Read-only projections derived from the Durable Orchestrator SSOT."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json,os,tempfile
from .strict_json import loads as strict_loads
from .work_memory import WorkMemoryStore


def _atomic(path:Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def sync_project_state(root:str|Path,run:dict[str,Any])->dict[str,Any]:
    base=Path(root).resolve();path=base/'.adwf-runtime/project-state.json'
    state=strict_loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    memory=WorkMemoryStore(base).load()
    state['durable_projection']={'authoritative_source':'DURABLE_ORCHESTRATOR','run_id':run['run_id'],'run_revision':run['revision'],'brief_id':run['roadmap_id'],'phase':run['phase'],'status':run['status'],'subject_sha':run.get('subject_sha'),'preview_digest':run.get('preview_digest'),'event_head':run.get('event_head')}
    state['active']={'roadmap_id':run['roadmap_id'],'issue_id':run.get('issue_id'),'phase':run['phase'],'run_id':run['run_id']}
    state.setdefault('orchestration',{})['authority']='DURABLE_ORCHESTRATOR'
    state['orchestration']['phase']=run['phase'];state['orchestration']['status']=run['status'];state['orchestration']['revision']=run['revision']
    if memory:
        state.setdefault('owner_experience',{})['product_brief']=memory.get('product_brief') or state.get('owner_experience',{}).get('product_brief')
        state['owner_experience']['next_action_ru']=memory.get('next_action_ru')
    _atomic(path,state);return state
