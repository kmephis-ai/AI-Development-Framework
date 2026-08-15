"""Deterministic delivery adapters for ADWF v1.6.

`REFERENCE_LOCAL` is intentionally small and local.  It proves the complete
promotion -> exact artifact -> observation contract without pretending that a
local directory is a production cloud deployment.  Real products use a
COMMAND/provider adapter and must return an exact-revision attestation.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import hashlib,json,os,shutil,subprocess,shlex,tempfile
from .strict_json import loads as strict_loads


def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()

def _tree_digest(root:Path)->tuple[str,list[dict[str,str]]]:
    rows=[];h=hashlib.sha256()
    for p in sorted(x for x in root.rglob('*') if x.is_file()):
        rel=p.relative_to(root).as_posix();dig=_sha(p);rows.append({'path':rel,'sha256':dig});h.update((rel+'\0'+dig+'\n').encode())
    return h.hexdigest(),rows

def _atomic(path:Path,value:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def promote_reference(root:str|Path,subject_sha:str,*,source_dir:str='examples/reference-app')->dict[str,Any]:
    base=Path(root).resolve();src=(base/source_dir).resolve()
    if not src.is_dir():raise ValueError('REFERENCE_APP_MISSING')
    target=base/'.adwf-runtime/deployments'/subject_sha
    if target.exists():shutil.rmtree(target)
    shutil.copytree(src,target)
    digest,files=_tree_digest(target)
    att={'schema_version':1,'adapter':'REFERENCE_LOCAL','environment':'LOCAL_REFERENCE_ONLY','production_verified':False,
         'source_sha':subject_sha,'artifact_digest':digest,'files':files,'deployment_path':str(target.relative_to(base))}
    _atomic(base/'.adwf-runtime/deployment-attestation.json',att);return att

def observe_reference(root:str|Path,subject_sha:str)->dict[str,Any]:
    base=Path(root).resolve();p=base/'.adwf-runtime/deployment-attestation.json'
    if not p.is_file():return {'status':'NOT_VERIFIED','reason':'DEPLOYMENT_ATTESTATION_MISSING'}
    att=json.loads(p.read_text(encoding='utf-8'));target=base/str(att.get('deployment_path') or '')
    if att.get('source_sha')!=subject_sha or not target.is_dir():return {'status':'NOT_VERIFIED','reason':'DEPLOYED_REVISION_MISMATCH'}
    digest,files=_tree_digest(target)
    if digest!=att.get('artifact_digest'):return {'status':'FAIL','reason':'DEPLOYED_ARTIFACT_DRIFT','observed_digest':digest}
    index=target/'index.html';healthy=index.is_file() and 'data-adwf-health="ok"' in index.read_text(encoding='utf-8')
    return {'status':'PASS' if healthy else 'FAIL','adapter':'REFERENCE_LOCAL','source_sha':subject_sha,'artifact_digest':digest,
            'health_marker_verified':healthy,'production_verified':False,'files':len(files)}

def run_command_adapter(command:str,root:str|Path,subject_sha:str,*,timeout:int,kind:str)->dict[str,Any]:
    """Execute a project adapter but trust only its structured exact-revision readback.

    Exit code 0 is never sufficient for delivery correctness. The adapter must
    write ADWF_ADAPTER_ATTESTATION with exact SHA, digest and provider readback.
    """
    base=Path(root).resolve();out=base/'.adwf-runtime/delivery'/f'{kind}-result.json';out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():out.unlink()
    env={**os.environ,'ADWF_SUBJECT_SHA':subject_sha,'ADWF_ADAPTER_ATTESTATION':str(out),'ADWF_ADAPTER_KIND':kind}
    p=subprocess.run(shlex.split(command,posix=os.name!='nt'),cwd=base,env=env,text=True,encoding='utf-8',errors='replace',capture_output=True,check=False,timeout=timeout)
    result={'status':'PASS' if p.returncode==0 else 'FAIL','exit_code':p.returncode,'stdout_tail':p.stdout[-500:],'stderr_tail':p.stderr[-500:]}
    if p.returncode!=0:return result
    if not out.is_file():return {**result,'status':'NOT_VERIFIED','reason':'DELIVERY_ATTESTATION_MISSING'}
    try:att=strict_loads(out.read_text(encoding='utf-8'))
    except Exception:return {**result,'status':'NOT_VERIFIED','reason':'DELIVERY_ATTESTATION_INVALID_JSON'}
    digest=str(att.get('artifact_digest') or '')
    refs=att.get('evidence_refs') if isinstance(att.get('evidence_refs'),list) else []
    if att.get('status')!='PASS' or att.get('subject_sha')!=subject_sha or att.get('provider_readback') is not True or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest) or not refs:
        return {**result,'status':'NOT_VERIFIED','reason':'DELIVERY_ATTESTATION_NOT_EXACT','attestation':att}
    return {**result,'status':'PASS','subject_sha':subject_sha,'artifact_digest':digest,'provider_readback':True,'evidence_refs':refs,'readback_id':att.get('readback_id'),'attestation_path':str(out.relative_to(base))}
