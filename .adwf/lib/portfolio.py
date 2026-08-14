"""Local Executive Portfolio registry for multiple ADWF projects.

Only local paths and non-secret executive status are stored. Provider tokens,
logs and Work Memory are never copied into the portfolio registry.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import json,os,tempfile
from .file_lock import exclusive_file_lock
from .strict_json import loads as strict_loads


def _now()->str:return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def default_registry()->Path:
    override=os.environ.get('ADWF_PORTFOLIO_FILE')
    return Path(override).expanduser().resolve() if override else (Path.home()/'.adwf'/'portfolio.json').resolve()

def _read(path:Path)->dict[str,Any]:
    if not path.is_file():return {'schema_version':1,'projects':[]}
    value=strict_loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version')!=1 or not isinstance(value.get('projects'),list):raise ValueError('PORTFOLIO_REGISTRY_INVALID')
    return value

def _write(path:Path,value:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True);lock=path.with_suffix('.lock')
    with exclusive_file_lock(lock):
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
            try:os.chmod(tmp,0o600)
            except OSError:pass
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)

def register_project(root:str|Path,*,registry_path:Path|None=None)->dict[str,Any]:
    base=Path(root).resolve();version=(base/'VERSION').read_text(encoding='utf-8').strip() if (base/'VERSION').is_file() else 'UNKNOWN'
    if not (base/'.adwf/config.json').is_file():raise ValueError('NOT_ADWF_PROJECT')
    path=registry_path or default_registry();value=_read(path);projects=[p for p in value['projects'] if Path(str(p.get('path',''))).resolve()!=base]
    projects.append({'path':str(base),'name':base.name,'framework_version':version,'last_seen_at':_now()});value['projects']=projects[-100:];_write(path,value)
    return {'status':'REGISTERED','project':projects[-1],'registry':str(path)}

def _project_status(path:Path)->dict[str,Any]:
    result={'path':str(path),'name':path.name,'available':path.is_dir(),'framework_version':'UNKNOWN','product_status':'NOT_VERIFIED','active_phase':None}
    if not path.is_dir():return result
    try:result['framework_version']=(path/'VERSION').read_text(encoding='utf-8').strip()
    except OSError:pass
    state_path=path/'.adwf-runtime/project-state.json'
    if not state_path.is_file():state_path=path/'.adwf/project-state.json'
    try:
        state=strict_loads(state_path.read_text(encoding='utf-8'));result['product_status']=str((state.get('health') or {}).get('product') or 'NOT_VERIFIED')
    except (OSError,ValueError,json.JSONDecodeError):pass
    orch=path/'.adwf-runtime/orchestration'
    if orch.is_dir():
        for f in sorted(orch.glob('*.json')):
            try:
                run=strict_loads(f.read_text(encoding='utf-8'))
                if run.get('status') in {'RUNNING','RECOVERY','RETRY_WAIT','HUMAN_REQUIRED'}:result['active_phase']=run.get('phase');break
            except Exception:continue
    return result

def portfolio_view(*,registry_path:Path|None=None)->dict[str,Any]:
    path=registry_path or default_registry();value=_read(path);projects=[]
    for item in value.get('projects') or []:
        p=Path(str(item.get('path') or '')).expanduser()
        if not p.is_absolute():continue
        status=_project_status(p.resolve());status['last_seen_at']=item.get('last_seen_at');projects.append(status)
    return {'schema_version':1,'project_count':len(projects),'projects':projects,'registry':str(path),'secrets_in_registry':False}
