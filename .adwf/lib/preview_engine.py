"""Built-in Playwright preview adapter with exact-revision attestation."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable
from urllib.parse import urlparse
import hashlib,json,os,re,shutil,subprocess,tempfile,platform

SHA=re.compile(r'^[0-9a-f]{40}$');PLAYWRIGHT_VERSION='1.62.0';VIEWPORTS={'desktop':{'width':1440,'height':900},'mobile':{'width':390,'height':844}}

def validate_preview_url(url:str)->str:
    value=str(url).strip();parsed=urlparse(value);loopback=parsed.hostname in {'127.0.0.1','localhost','::1'}
    if parsed.username or parsed.password:raise ValueError('PREVIEW_URL_CREDENTIALS_FORBIDDEN')
    if parsed.scheme=='https' and parsed.hostname:return value
    if parsed.scheme=='http' and loopback:return value
    raise ValueError('PREVIEW_URL_MUST_BE_HTTPS_OR_LOOPBACK')

def _sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def _run(command:list[str],cwd:Path,timeout:int=180)->subprocess.CompletedProcess[str]:return subprocess.run(command,cwd=cwd,text=True,capture_output=True,timeout=timeout,check=False)
def _git_head(root:Path)->str|None:
    p=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,capture_output=True,check=False)
    return p.stdout.strip() if p.returncode==0 and SHA.fullmatch(p.stdout.strip()) else None

def _source_attestation(project:Path,url:str,head_sha:str,provided:dict[str,Any]|None)->dict[str,Any]:
    parsed=urlparse(url);loopback=parsed.hostname in {'127.0.0.1','localhost','::1'}
    if loopback:
        actual=_git_head(project)
        if actual!=head_sha:raise ValueError('PREVIEW_LOCAL_GIT_HEAD_MISMATCH')
        tree=subprocess.run(['git','rev-parse','HEAD^{tree}'],cwd=project,text=True,capture_output=True,check=False).stdout.strip()
        return {'mode':'LOCAL_EXACT_GIT_HEAD','verified':True,'head_sha':head_sha,'tree_sha':tree if len(tree)==40 else None}
    att=provided or {}
    if att.get('provider_readback') is not True or att.get('deployed_sha')!=head_sha or not att.get('deployment_id'):
        raise ValueError('REMOTE_PREVIEW_DEPLOYED_REVISION_ATTESTATION_REQUIRED')
    return {'mode':'REMOTE_PROVIDER_ATTESTED','verified':True,'head_sha':head_sha,'deployment_id':att.get('deployment_id'),'provider':att.get('provider'),'readback_id':att.get('readback_id')}

def capture_preview(root:str|Path,*,url:str,head_sha:str,baseline_url:str|None=None,output_dir:str|Path|None=None,install:bool=False,
                    command_runner:Callable[[list[str],Path,int],subprocess.CompletedProcess[str]]|None=None,source_attestation:dict[str,Any]|None=None)->dict[str,Any]:
    if SHA.fullmatch(str(head_sha)) is None:raise ValueError('PREVIEW_SHA_INVALID')
    target_url=validate_preview_url(url);base_url=validate_preview_url(baseline_url) if baseline_url else None
    project=Path(root).resolve();attestation=_source_attestation(project,target_url,head_sha,source_attestation)
    out=Path(output_dir or project/'.adwf-runtime'/'preview'/head_sha[:12]).resolve();out.mkdir(parents=True,exist_ok=True);node_dir=project/'.adwf'/'preview';runner=command_runner or _run
    if shutil.which('node') is None or shutil.which('npm') is None:raise ValueError('PLAYWRIGHT_NODE_RUNTIME_MISSING')
    module=node_dir/'node_modules'/'playwright'
    if not module.exists():
        if not install:raise ValueError('PLAYWRIGHT_NOT_INSTALLED_USE_INSTALL')
        proc=runner(['npm','install','--no-audit','--no-fund','--no-save',f'playwright@{PLAYWRIGHT_VERSION}'],node_dir,180)
        if proc.returncode:raise ValueError('PLAYWRIGHT_INSTALL_FAILED:'+(proc.stderr or proc.stdout)[-400:])
        proc=runner(['npx','playwright','install','chromium'],node_dir,240)
        if proc.returncode:raise ValueError('PLAYWRIGHT_BROWSER_INSTALL_FAILED:'+(proc.stderr or proc.stdout)[-400:])
    spec={'url':target_url,'baseline_url':base_url,'output_dir':str(out),'viewports':VIEWPORTS};spec_path=out/'request.json';spec_path.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    proc=runner(['node',str(node_dir/'capture.mjs'),str(spec_path)],node_dir,180)
    if proc.returncode:raise ValueError('PLAYWRIGHT_CAPTURE_FAILED:'+(proc.stderr or proc.stdout)[-800:])
    result_path=out/'capture-result.json'
    if not result_path.is_file():raise ValueError('PLAYWRIGHT_RESULT_MISSING')
    result=json.loads(result_path.read_text(encoding='utf-8'));shots=[]
    for item in result.get('screenshots') or []:
        p=Path(item['path'])
        if not p.is_file():raise ValueError('PREVIEW_SCREENSHOT_MISSING')
        shots.append({**item,'sha256':_sha(p)})
    runtime={'playwright_version':PLAYWRIGHT_VERSION,'browser_version':result.get('browser_version'),'node_version':result.get('node_version'),'platform':result.get('platform'),'arch':result.get('arch')}
    pinned=all(runtime.values()) and attestation.get('verified') is True
    now=datetime.now(timezone.utc);manifest={'schema_version':3,'engine':'playwright','engine_version':PLAYWRIGHT_VERSION,'head_sha':head_sha,'url':target_url,'baseline_url':base_url,'viewports':VIEWPORTS,'screenshots':shots,
      'console_errors':result.get('console_errors') or [],'failed_requests':result.get('failed_requests') or [],'accessibility':result.get('accessibility') or {'status':'NOT_VERIFIED'},'source_attestation':attestation,'runtime_environment':runtime,
      'created_at':now.isoformat().replace('+00:00','Z'),'valid_until':(now+timedelta(hours=24)).isoformat().replace('+00:00','Z'),'pixel_environment_pinned':bool(pinned)}
    manifest['preview_digest']=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();manifest['attestation_id']=hashlib.sha256((head_sha+manifest['preview_digest']+json.dumps(attestation,sort_keys=True)).encode()).hexdigest()
    (out/'preview-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    refs=[];provider=project/'.adwf-runtime/provider-readback.json'
    if provider.is_file():
        try:refs=list(json.loads(provider.read_text(encoding='utf-8')).get('evidence_refs') or [])
        except Exception:refs=[]
    att_file={'schema_version':1,'attestation_id':manifest['attestation_id'],'head_sha':head_sha,'preview_digest':manifest['preview_digest'],'source_attestation':attestation,'runtime_environment':runtime,'evidence_refs':refs,'manifest_path':str((out/'preview-manifest.json').relative_to(project)) if project in (out/'preview-manifest.json').parents else str(out/'preview-manifest.json')}
    runtime_path=project/'.adwf-runtime/preview-attestation.json';runtime_path.parent.mkdir(parents=True,exist_ok=True);runtime_path.write_text(json.dumps(att_file,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest
