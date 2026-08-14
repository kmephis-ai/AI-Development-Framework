#!/usr/bin/env python3
"""Read exact PR job logs through GitHub API and bridge preview provenance into trusted runtime."""
from __future__ import annotations
from pathlib import Path
import argparse,base64,hashlib,json,os,re,sys,tempfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.github_auth import detect_repository,discover_token
from lib.github_provider import GitHubClient
from lib.strict_json import loads as strict_loads
SHA=re.compile(r'^[0-9a-f]{40}$');DIGEST=re.compile(r'^[0-9a-f]{64}$');PREFIX='ADWF_PREVIEW_ATTESTATION_V1='

def _atomic(path:Path,value:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def _decode(logs:bytes)->dict|None:
    try:text=logs.decode('utf-8','replace')
    except Exception:return None
    markers=[]
    for line in text.splitlines():
        pos=line.find(PREFIX)
        if pos>=0:markers.append(line[pos+len(PREFIX):].strip())
    if not markers:return None
    raw=markers[-1];raw += '='*((4-len(raw)%4)%4)
    try:value=strict_loads(base64.urlsafe_b64decode(raw.encode()).decode('utf-8'))
    except Exception:raise ValueError('PREVIEW_LOG_MARKER_INVALID')
    return value if isinstance(value,dict) else None

def collect(client:GitHubClient,event:dict)->dict:
    run=event.get('workflow_run') if isinstance(event.get('workflow_run'),dict) else {}
    if run.get('name')!='ADWF PR' or run.get('event')!='pull_request' or run.get('status')!='completed' or run.get('conclusion')!='success':
        return {'status':'NOT_APPLICABLE','reason':'NOT_SUCCESSFUL_ADWF_PR_RUN'}
    sha=str(run.get('head_sha') or '');rid=run.get('id')
    if SHA.fullmatch(sha) is None or not isinstance(rid,int):return {'status':'NOT_VERIFIED','reason':'WORKFLOW_RUN_BINDING_INVALID'}
    # Job-log content is not trusted merely because GitHub stored it. First
    # require the default-branch trusted/governance checks for this exact HEAD;
    # those checks prove the preview harness was not silently self-weakened.
    required={'fast-feedback','adwf/governance-gate','adwf/trusted-gate'};passed={};app_ids=set()
    for check in client.check_runs(sha):
        if check.get('name') not in required or check.get('head_sha')!=sha or check.get('status')!='completed' or check.get('conclusion')!='success':continue
        app=check.get('app') or {}
        if app.get('slug')!='github-actions' or not isinstance(app.get('id'),int):continue
        passed[str(check['name'])]=check;app_ids.add(int(app['id']))
    if set(passed)!=required or len(app_ids)!=1:return {'status':'NOT_VERIFIED','reason':'TRUSTED_PREVIEW_HARNESS_NOT_ATTESTED','missing':sorted(required-set(passed)),'check_app_ids':sorted(app_ids),'head_sha':sha}
    matches=[]
    for job in client.jobs(rid):
        if str(job.get('name') or '')!='fast-feedback' or job.get('status')!='completed' or job.get('conclusion')!='success':continue
        jid=job.get('id')
        if not isinstance(jid,int):continue
        marker=_decode(client.job_logs(jid))
        if marker is not None:matches.append((jid,marker))
    if len(matches)!=1:return {'status':'NOT_VERIFIED','reason':'SINGLE_PREVIEW_LOG_MARKER_REQUIRED','matches':len(matches),'head_sha':sha}
    jid,value=matches[0]
    if value.get('schema_version')!=1 or value.get('head_sha')!=sha or DIGEST.fullmatch(str(value.get('preview_digest') or '')) is None:return {'status':'NOT_VERIFIED','reason':'PREVIEW_MARKER_BINDING_INVALID','head_sha':sha}
    source=value.get('source_attestation') if isinstance(value.get('source_attestation'),dict) else {}
    if source.get('verified') is not True or source.get('head_sha')!=sha:return {'status':'NOT_VERIFIED','reason':'PREVIEW_SOURCE_ATTESTATION_INVALID','head_sha':sha}
    expected=hashlib.sha256((sha+str(value['preview_digest'])+json.dumps(source,sort_keys=True)).encode()).hexdigest()
    if value.get('attestation_id')!=expected:return {'status':'NOT_VERIFIED','reason':'PREVIEW_ATTESTATION_ID_INVALID','head_sha':sha}
    provider=ROOT/'.adwf-runtime/provider-readback.json';refs=[]
    if provider.is_file():
        try:refs=list(strict_loads(provider.read_text(encoding='utf-8')).get('evidence_refs') or [])
        except Exception:refs=[]
    out={'schema_version':1,'attestation_id':expected,'head_sha':sha,'preview_digest':value['preview_digest'],'source_attestation':source,'runtime_environment':value.get('runtime_environment') or {},'screenshot_digests':value.get('screenshot_digests') or [],'accessibility_status':value.get('accessibility_status'),'evidence_refs':refs,
         'provider_attestation':{'provider':'github','workflow_run_id':rid,'job_id':jid,'provider_readback':True,'head_sha':sha}}
    _atomic(ROOT/'.adwf-runtime/preview-attestation.json',out)
    return {'status':'VERIFIED','head_sha':sha,'preview_digest':value['preview_digest'],'workflow_run_id':rid,'job_id':jid}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--event',required=True);args=p.parse_args();event=strict_loads(Path(args.event).read_text(encoding='utf-8'))
    repo=detect_repository(ROOT);token,_=discover_token()
    if not repo or not token:print(json.dumps({'status':'HUMAN_REQUIRED','reason':'GITHUB_CONNECTION_REQUIRED'}));return 2
    result=collect(GitHubClient(repo,token),event);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status'] in {'VERIFIED','NOT_APPLICABLE'} else 1
if __name__=='__main__':raise SystemExit(main())
