"""Materialize a detected Project Pack into the canonical ADWF config."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json,os,tempfile
from .project_packs import commands_for_pack
from .strict_json import loads as strict_loads

CONFIG_COMMANDS={'lint','unit','integration','build','smoke','golden_paths','e2e'}

def _atomic(path:Path,value:dict[str,Any])->None:
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def materialize_project_pack(project_root:str|Path,framework_root:str|Path,*,apply:bool=False)->dict[str,Any]:
    project=Path(project_root).resolve();framework=Path(framework_root).resolve();pack=commands_for_pack(project,framework)
    if not pack.get('pack'):return {'status':'HUMAN_REQUIRED','reason':'PROJECT_PACK_NOT_DETECTED','write_performed':False}
    cfg_path=framework/'.adwf/config.json';original=strict_loads(cfg_path.read_text(encoding='utf-8'));cfg=json.loads(json.dumps(original));changed=[]
    for name,entry in (pack.get('commands') or {}).items():
        if name not in CONFIG_COMMANDS or entry.get('available') is not True or not entry.get('command'):continue
        desired={'required':True,'command':entry['command'],'phases':entry.get('phases') or ['pr']}
        if cfg.setdefault('commands',{}).get(name)!=desired:cfg['commands'][name]=desired;changed.append(f'commands.{name}')
    pp=cfg.setdefault('project_packs',{})
    desired_runtime={name:{'command':entry.get('command'),'available':entry.get('available',False),'phases':entry.get('phases') or []} for name,entry in (pack.get('commands') or {}).items() if name in {'install','start'}}
    desired_preview=pack.get('preview') or {}
    for key,value in [('selected',pack['pack']),('materialized',True),('runtime_commands',desired_runtime),('preview',desired_preview)]:
        if pp.get(key)!=value:changed.append(f'project_packs.{key}')
        pp[key]=value
    if not changed:
        return {'status':'ALREADY_MATERIALIZED','pack':pack['pack'],'changed':[],'preview':desired_preview,'write_performed':False,'config_path':str(cfg_path),'desired_config':cfg}
    if apply:_atomic(cfg_path,cfg)
    return {'status':'APPLIED' if apply else 'READY_TO_APPLY','pack':pack['pack'],'changed':changed,'preview':desired_preview,'write_performed':apply,'config_path':str(cfg_path),'desired_config':cfg if not apply else None}
