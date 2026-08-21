"""Canonical phase executors for the ADWF v1.6 Runtime Supervisor.

Every durable phase has exactly one registered executor. Creative work remains
replaceable and optional: the framework can call a configured agent command or
consume a bounded Agent Inbox result, while all authority/evidence is evaluated
by trusted code afterwards.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable
from datetime import datetime, timezone
import hashlib,json,os,re,subprocess,sys
from .github_auth import detect_repository,discover_token
from .github_provider import GitHubClient
from .github_lease_store import GitHubLeaseStore
from .evidence import parse_time
from .strict_json import loads as strict_loads
from .work_memory import WorkMemoryStore
from .delivery_adapters import promote_reference, observe_reference, run_command_adapter
from .ai_work_contracts import canonicalize_low_trust_claim
from .creative_agent_qualification import command_argv,load_qualified_command_adapter,sanitized_agent_environment,verify_local_command_result

SHA=re.compile(r'^[0-9a-f]{40}$')

@dataclass
class ExecutorWait:
    status:str
    reason:str
    capability:str
    details:dict[str,Any]|None=None
    def to_dict(self)->dict[str,Any]:return {'status':self.status,'reason':self.reason,'capability':self.capability,**(self.details or {})}


def _result(state:dict[str,Any],key:str,outcome:str='PASS',**kw:Any)->dict[str,Any]:
    value={'phase':state['phase'],'outcome':outcome,'idempotency_key':key,'subject_sha':kw.pop('subject_sha',state.get('subject_sha')),
           'preview_digest':kw.pop('preview_digest',state.get('preview_digest')),'evidence_refs':kw.pop('evidence_refs',[]),'reason_codes':kw.pop('reason_codes',[]),'cost_usd':0,'metadata':kw.pop('metadata',{})}
    if kw:raise ValueError('EXECUTOR_UNKNOWN_RESULT_FIELDS:'+','.join(sorted(kw)))
    return value


def _github(root:Path)->tuple[GitHubClient|None,dict[str,Any]]:
    repo=detect_repository(root);token,source=discover_token()
    if not repo or not token:return None,{'credential_source':source,'repository':repo}
    return GitHubClient(repo,token),{'credential_source':source,'repository':repo}


def _evidence_refs(root:Path)->list[str]:
    p=root/'.adwf-runtime/provider-readback.json'
    if not p.is_file():return []
    try:return list((strict_loads(p.read_text(encoding='utf-8')) or {}).get('evidence_refs') or [])
    except Exception:return []


def _memory(root:Path)->dict[str,Any]|None:
    try:return WorkMemoryStore(root).load()
    except ValueError:return None


def reconcile_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','reconcile',auth)
    info=client.repo_info();default=str(info.get('default_branch') or 'main');main_sha=str((client.branch(default).get('commit') or {}).get('sha') or '')
    if not SHA.fullmatch(main_sha):return ExecutorWait('NOT_VERIFIED','DEFAULT_BRANCH_SHA_NOT_VERIFIED','reconcile')
    issue_id=str(state.get('issue_id') or 'PENDING')
    if issue_id=='PENDING':
        memory=_memory(root) or {};task=str(memory.get('task_ru') or 'Задача владельца').strip();brief_id=state['roadmap_id']
        issue=client.create_issue('[ADWF] '+task[:90],f"ADWF Owner Intent\n\nBrief: `{brief_id}`\nRun: `{state['run_id']}`\n\n{task}\n")
        issue_id=str(issue.get('number') or '')
        if not issue_id:return ExecutorWait('NOT_VERIFIED','ISSUE_CREATE_READBACK_MISSING','reconcile')
        if memory:
            rev=memory['revision'];refs=memory.setdefault('references',{});refs.setdefault('issues',[]).append({'number':int(issue_id),'url':issue.get('html_url')});WorkMemoryStore(root).save(memory,expected_revision=rev)
    env={**os.environ,'GITHUB_REPOSITORY':client.repo,'GITHUB_TOKEN':client.token,'ADWF_SUBJECT_SHA':main_sha}
    proc=subprocess.run([sys.executable,str(root/'.adwf/scripts/github_reconcile.py'),'--apply','--subject-sha',main_sha],cwd=root,env=env,text=True,capture_output=True,check=False,timeout=120)
    if proc.returncode not in {0,1}:return ExecutorWait('NOT_VERIFIED','GITHUB_RECONCILIATION_FAILED','reconcile',{'stderr':proc.stderr[-300:]})
    return _result(state,key,subject_sha=main_sha,metadata={'issue_id':issue_id,'repository':client.repo,'default_branch':default})


def authorize_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]:
    return _result(state,key,metadata={'authorization_source':'TRUSTED_CONTEXT_COMPILER'})


def _orchestration_policy(root:Path)->dict[str,int]:
    cfg=strict_loads((root/'.adwf/config.json').read_text(encoding='utf-8'))
    orch=cfg.get('orchestration') if isinstance(cfg.get('orchestration'),dict) else {}
    ceiling=orch.get('max_parallel_writers');ttl=orch.get('lease_ttl_minutes');heartbeat=orch.get('heartbeat_minutes')
    if ceiling != 1:return {'error':'STAGE1_WRITER_CEILING_NOT_SINGLETON'}
    if isinstance(ttl,bool) or not isinstance(ttl,int) or not 1<=ttl<=240:return {'error':'LEASE_TTL_POLICY_INVALID'}
    if isinstance(heartbeat,bool) or not isinstance(heartbeat,int) or not 1<=heartbeat<=120:return {'error':'LEASE_HEARTBEAT_POLICY_INVALID'}
    return {'max_parallel_writers':ceiling,'lease_ttl_minutes':ttl,'heartbeat_minutes':heartbeat}


def _current_main(client:GitHubClient)->tuple[str,str]:
    info=client.repo_info();default=str(info.get('default_branch') or 'main');sha=str((client.branch(default).get('commit') or {}).get('sha') or '')
    if not SHA.fullmatch(sha):raise ValueError('DEFAULT_BRANCH_SHA_NOT_VERIFIED')
    return default,sha


def _writer_id(state:dict[str,Any])->str:
    run_id=str(state.get('run_id') or '')
    if not run_id:raise ValueError('WRITER_RUN_ID_REQUIRED')
    return 'adwf-runtime:'+run_id


def _writer_branch(state:dict[str,Any])->str:
    roadmap=str(state.get('roadmap_id') or '').lower().replace('_','-')
    roadmap=re.sub(r'[^a-z0-9-]+','-',roadmap).strip('-') or 'work'
    suffix=hashlib.sha256(str(state.get('run_id') or '').encode()).hexdigest()[:12]
    return f'adwf/{roadmap[:120]}-{suffix}'


def _stage1_resources()->list[dict[str,Any]]:
    # Stage 1 intentionally retains a repository-global writer domain. Typed
    # disjoint resources remain a later Multi-Writer concern after live proof.
    return [{'kind':'provider','scope':'global','shared':True,'global':True}]


def _same_lease(lease:dict[str,Any],state:dict[str,Any],branch:str,resources:list[dict[str,Any]])->bool:
    return (
        lease.get('status')=='ACTIVE' and
        lease.get('issue_id')==str(state.get('issue_id') or '') and
        lease.get('roadmap_id')==str(state.get('roadmap_id') or '') and
        lease.get('worker_id')==_writer_id(state) and
        lease.get('branch')==branch and
        lease.get('resources')==resources
    )


def _lease_fresh(lease:dict[str,Any],heartbeat_minutes:int)->bool:
    try:
        now=datetime.now(timezone.utc);heartbeat=parse_time(str(lease.get('heartbeat_at')));expires=parse_time(str(lease.get('expires_at')))
    except (TypeError,ValueError):return False
    return expires>now and heartbeat<=now and (now-heartbeat).total_seconds() <= heartbeat_minutes*60


def _require_live_writer_authority(root:Path,state:dict[str,Any],phase:str)->ExecutorWait|None:
    client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','claim',auth)
    policy=_orchestration_policy(root)
    if 'error' in policy:return ExecutorWait('NOT_VERIFIED',str(policy['error']),'claim')
    try:
        _default,current_main=_current_main(client);store=GitHubLeaseStore(client)
        registry,_anchor=store.read(expected_main_sha=current_main,policy_max_parallel_writers=policy['max_parallel_writers'])
    except Exception as exc:
        return ExecutorWait('NOT_VERIFIED','LEASE_PROVIDER_READBACK_FAILED','claim',{'contract_error':str(exc)[:300]})
    branch=str(state.get('work_branch') or '')
    matches=[x for x in registry.get('leases') or [] if _same_lease(x,state,branch,_stage1_resources())]
    if len(matches)!=1:return ExecutorWait('NOT_VERIFIED','LIVE_PROVIDER_LEASE_REQUIRED','claim',{'matches':len(matches),'registry_revision':registry.get('revision')})
    lease=matches[0]
    if not _lease_fresh(lease,int(policy['heartbeat_minutes'])):
        return ExecutorWait('NOT_VERIFIED','LEASE_RECONCILIATION_REQUIRED','claim',{'lease_id':lease.get('lease_id')})
    delivery=str(state.get('delivery_sha') or '')
    postmerge=phase in {'PROMOTE','OBSERVE','DONE','CLEANUP','RECOVERY'} and SHA.fullmatch(delivery) is not None
    if postmerge:
        if current_main!=delivery:return ExecutorWait('NOT_VERIFIED','DELIVERY_MAIN_RECONCILIATION_REQUIRED','claim',{'current_main':current_main,'delivery_sha':delivery})
        return None
    if registry.get('observed_main_sha')!=current_main or lease.get('base_sha')!=current_main:
        return ExecutorWait('NOT_VERIFIED','LEASE_MAIN_RECONCILIATION_REQUIRED','claim',{'current_main':current_main,'registry_main':registry.get('observed_main_sha'),'lease_base':lease.get('base_sha')})
    # Heartbeat before half the configured interval has elapsed, avoiding an
    # anchor per phase while keeping ordinary CI waits safely within freshness.
    heartbeat=parse_time(str(lease['heartbeat_at']));age=(datetime.now(timezone.utc)-heartbeat).total_seconds()
    if age >= max(60,int(policy['heartbeat_minutes'])*30):
        try:store.heartbeat(expected_main_sha=current_main,policy_max_parallel_writers=1,lease_id=str(lease['lease_id']),worker_id=_writer_id(state),ttl_minutes=int(policy['lease_ttl_minutes']))
        except Exception as exc:return ExecutorWait('NOT_VERIFIED','LEASE_HEARTBEAT_FAILED','claim',{'contract_error':str(exc)[:300]})
    return None


def claim_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','claim',auth)
    policy=_orchestration_policy(root)
    if 'error' in policy:return ExecutorWait('NOT_VERIFIED',str(policy['error']),'claim')
    issue_id=str(state.get('issue_id') or '');base_sha=str(state.get('subject_sha') or '');roadmap_id=str(state.get('roadmap_id') or '')
    if not issue_id.isdigit():return ExecutorWait('NOT_VERIFIED','LEASE_ISSUE_ID_REQUIRED','claim')
    if not SHA.fullmatch(base_sha):return ExecutorWait('NOT_VERIFIED','LEASE_BASE_SHA_REQUIRED','claim')
    branch=_writer_branch(state);resources=_stage1_resources();worker=_writer_id(state)
    try:
        _default,current_main=_current_main(client)
        if current_main!=base_sha:return ExecutorWait('NOT_VERIFIED','LEASE_BASE_MAIN_DRIFT','claim',{'expected':base_sha,'actual':current_main})
        store=GitHubLeaseStore(client);registry,anchor=store.read(expected_main_sha=current_main,policy_max_parallel_writers=1)
        active=[x for x in registry.get('leases') or [] if x.get('status')=='ACTIVE']
        compatible=[x for x in active if _same_lease(x,state,branch,resources) and x.get('base_sha')==base_sha]
        if active:
            if len(active)==1 and len(compatible)==1 and _lease_fresh(compatible[0],int(policy['heartbeat_minutes'])):
                lease=compatible[0]
                return _result(state,key,metadata={'lease_model':'PROVIDER_DURABLE_CAS','lease_id':lease['lease_id'],'lease_anchor':anchor,'lease_registry_revision':registry['revision'],'branch':branch,'resumed_existing':True})
            return ExecutorWait('NOT_VERIFIED','ACTIVE_PROVIDER_LEASE_INCOMPATIBLE','claim',{'active':len(active),'compatible':len(compatible),'registry_revision':registry.get('revision')})
        persisted,lease,new_anchor=store.acquire(expected_main_sha=current_main,policy_max_parallel_writers=1,issue_id=issue_id,roadmap_id=roadmap_id,worker_id=worker,base_sha=base_sha,branch=branch,resources=resources,ttl_minutes=int(policy['lease_ttl_minutes']))
        return _result(state,key,metadata={'lease_model':'PROVIDER_DURABLE_CAS','lease_id':lease['lease_id'],'lease_anchor':new_anchor,'lease_registry_revision':persisted['revision'],'branch':branch,'resumed_existing':False})
    except Exception as exc:
        return ExecutorWait('NOT_VERIFIED','LEASE_PROVIDER_ACQUIRE_FAILED','claim',{'contract_error':str(exc)[:300]})


def workspace_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]:
    return _result(state,key,metadata={'workspace_mode':'REPLACEABLE_AGENT_DISPOSABLE_WORKSPACE','trust':'LOW'})


def _run_agent_command(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|None:
    raw_command=os.environ.get('ADWF_AGENT_COMMAND','').strip()
    adapter_id=os.environ.get('ADWF_AGENT_ADAPTER_ID','').strip()
    if not raw_command and not adapter_id:return None
    if raw_command and not adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_UNQUALIFIED'])
    if raw_command and adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_OVERRIDE_FORBIDDEN'])
    try:
        adapter=load_qualified_command_adapter(root,adapter_id,state['phase'])
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_ADAPTER_UNQUALIFIED'],metadata={'contract_error':str(exc)[:300]})
    if adapter.get('kind')=='REFERENCE_DETERMINISTIC' and os.environ.get('ADWF_ALLOW_REFERENCE_AGENT')!='1':
        return _result(state,key,'FAIL',reason_codes=['REFERENCE_AGENT_RUNTIME_FORBIDDEN'])
    package=envelope.get('work_package')
    if not isinstance(package,dict):return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_MISSING'])
    if envelope.get('work_package_digest') not in {None,package.get('package_digest')}:
        return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_DIGEST_MISMATCH'])
    request=root/'.adwf-runtime/supervisor/requests'/f'{key}.json';result=root/'.adwf-runtime/supervisor/results'/f'{key}.json'
    try:
        argv=command_argv(root,adapter)
        env=sanitized_agent_environment(os.environ,request=request,result=result,state=state,adapter=adapter)
        proc=subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,check=False,timeout=int(adapter['timeout_seconds']))
    except subprocess.TimeoutExpired:
        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_TIMEOUT'])
    except (OSError,ValueError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_START_FAILED'],metadata={'contract_error':str(exc)[:300]})
    if proc.returncode:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_FAILED'],metadata={'exit_code':proc.returncode,'stderr_tail':proc.stderr[-500:]})
    if not result.is_file():return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_MISSING'])
    try:value=strict_loads(result.read_text(encoding='utf-8'))
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'],metadata={'contract_error':str(exc)[:300]})
    if not isinstance(value,dict):return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'])
    try:work_result=canonicalize_low_trust_claim(value,package=package)
    except ValueError as exc:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_CONTRACT_INVALID'],metadata={'contract_error':str(exc)[:300]})
    local_errors=verify_local_command_result(root,package,work_result)
    if local_errors:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_LOCAL_BINDING_INVALID'],metadata={'binding_errors':local_errors})
    return {'phase':state['phase'],'outcome':work_result['outcome'],'idempotency_key':key,'subject_sha':work_result.get('head_sha'),
            'preview_digest':state.get('preview_digest'),'evidence_refs':[],'reason_codes':work_result['reason_codes'],
            'transient':work_result['outcome']=='RETRY','cost_usd':0,'metadata':{'source':'LOW_TRUST_AGENT_COMMAND','adapter_id':adapter['id'],'adapter_version':adapter['version'],'ai_work_result':work_result}}


def creative_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    result=_run_agent_command(root,state,key,envelope)
    if result is not None:return result
    return ExecutorWait('WAITING_AGENT','CREATIVE_AGENT_RESULT_REQUIRED',envelope.get('capability') or 'edit',{'idempotency_key':key,'agent_inbox_supported':True})


def open_pr_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    sha=str(state.get('subject_sha') or '')
    if not SHA.fullmatch(sha):return ExecutorWait('WAITING_AGENT','AGENT_COMMIT_SHA_REQUIRED','open_pr')
    client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','open_pr',auth)
    pulls=[p for p in client.pulls() if str((p.get('head') or {}).get('sha') or '')==sha and p.get('state')=='open']
    memory=_memory(root) or {}
    if not pulls:
        branches=(memory.get('references') or {}).get('branches') or []
        branch=str(state.get('work_branch') or '') or next((str(x.get('name')) for x in reversed(branches) if isinstance(x,dict) and x.get('sha')==sha and x.get('name')),None)
        if not branch:return ExecutorWait('WAITING_AGENT','AGENT_BRANCH_REFERENCE_REQUIRED','open_pr',{'subject_sha':sha})
        info=client.repo_info();base=str(info.get('default_branch') or 'main');task=str(memory.get('task_ru') or 'ADWF change')
        pr=client.create_pull(title='[ADWF] '+task[:90],body=f"Automated ADWF run `{state['run_id']}` for brief `{state['roadmap_id']}`.",head=branch,base=base);pulls=[pr]
    pr=pulls[0];number=int(pr.get('number'))
    if memory:
        rev=memory['revision'];refs=memory.setdefault('references',{});existing=refs.setdefault('pull_requests',[])
        if not any((x.get('number') if isinstance(x,dict) else x)==number for x in existing):existing.append({'number':number,'url':pr.get('html_url'),'head_sha':sha})
        WorkMemoryStore(root).save(memory,expected_revision=rev)
    return _result(state,key,subject_sha=sha,metadata={'pull_request_number':number,'pull_request_url':pr.get('html_url')})


def ci_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    sha=str(state.get('subject_sha') or '');client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','test',auth)
    checks=client.check_runs(sha);required={'fast-feedback','adwf/trusted-gate','adwf/governance-gate'}
    passed={};app_ids=set()
    for c in checks:
        if c.get('name') not in required or c.get('head_sha')!=sha or c.get('status')!='completed' or c.get('conclusion')!='success':continue
        app=c.get('app') or {}
        if app.get('slug')!='github-actions' or not isinstance(app.get('id'),int):continue
        passed[str(c['name'])]=c;app_ids.add(int(app['id']))
    missing=sorted(required-set(passed))
    if missing or len(app_ids)!=1:return ExecutorWait('WAITING_CI','PROVIDER_ATTESTED_REQUIRED_CHECKS_PENDING','test',{'missing':missing,'check_app_ids':sorted(app_ids)})
    refs=_evidence_refs(root)
    if not refs:return ExecutorWait('NOT_VERIFIED','CI_EVIDENCE_REFS_REQUIRED','test',{'required_checks':sorted(required)})
    return _result(state,key,subject_sha=sha,evidence_refs=refs,metadata={'required_checks':sorted(required),'check_app_id':next(iter(app_ids))})



def review_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    sha=str(state.get('subject_sha') or '');client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','review',auth)
    refs=_evidence_refs(root);reviews=[]
    prn=state.get('pull_request_number')
    if prn:
        try:reviews=client.pull_reviews(int(prn))
        except Exception:reviews=[]
    approved=[r for r in reviews if str(r.get('state') or '').upper()=='APPROVED' and str(r.get('commit_id') or '')==sha]
    # The FREE_ONLY path never requires a paid AI reviewer. Deterministic exact-HEAD
    # checks are sufficient; an independent human/AI approval, when present, is
    # recorded as additional evidence rather than a mandatory paid dependency.
    return _result(state,key,subject_sha=sha,evidence_refs=refs,metadata={'exact_head_reviews':len(approved),'optional_ai_review_required':False,'not_applicable_reason':None if refs else 'REVIEW_COVERED_BY_TRUSTED_DETERMINISTIC_GATES'})

def preview_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    sha=str(state.get('subject_sha') or '');att=root/'.adwf-runtime/preview-attestation.json'
    if not att.is_file():return ExecutorWait('WAITING_PREVIEW','EXACT_REVISION_PREVIEW_ATTESTATION_REQUIRED','verify',{'subject_sha':sha})
    value=strict_loads(att.read_text(encoding='utf-8'))
    if value.get('head_sha')!=sha or value.get('source_attestation',{}).get('verified') is not True:return ExecutorWait('NOT_VERIFIED','PREVIEW_EXACT_REVISION_NOT_ATTESTED','verify')
    provider_att=value.get('provider_attestation') if isinstance(value.get('provider_attestation'),dict) else {}
    local_exact=False
    try:
        head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,capture_output=True,check=False).stdout.strip()
        local_exact=(head==sha and value.get('source_attestation',{}).get('mode')=='LOCAL_EXACT_GIT_HEAD')
    except Exception:local_exact=False
    provider_exact=(provider_att.get('provider')=='github' and provider_att.get('provider_readback') is True and provider_att.get('head_sha')==sha and isinstance(provider_att.get('workflow_run_id'),int) and isinstance(provider_att.get('job_id'),int))
    if not (local_exact or provider_exact):return ExecutorWait('NOT_VERIFIED','PREVIEW_TRUSTED_PROVENANCE_REQUIRED','verify')
    digest=str(value.get('preview_digest') or '')
    if not re.fullmatch(r'[0-9a-f]{64}',digest):return ExecutorWait('NOT_VERIFIED','PREVIEW_DIGEST_INVALID','verify')
    refs=list(value.get('evidence_refs') or _evidence_refs(root))
    return _result(state,key,subject_sha=sha,preview_digest=digest,evidence_refs=refs,metadata={'attestation_id':value.get('attestation_id')})


def owner_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->ExecutorWait:
    return ExecutorWait('WAITING_OWNER','PROVIDER_ATTESTED_OWNER_DECISION_REQUIRED','owner_accept',{'subject_sha':state.get('subject_sha'),'preview_digest':state.get('preview_digest')})


def merge_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    sha=str(state.get('subject_sha') or '');client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','merge',auth)
    pulls=[p for p in client.pulls() if str((p.get('head') or {}).get('sha') or '')==sha and p.get('state')=='open']
    if len(pulls)!=1:return ExecutorWait('NOT_VERIFIED','SINGLE_EXACT_HEAD_PR_REQUIRED','merge',{'matches':len(pulls)})
    checks=client.check_runs(sha);required={'fast-feedback','adwf/governance-gate','adwf/trusted-gate'};passed={};app_ids=set()
    for c in checks:
        if c.get('name') not in required or c.get('head_sha')!=sha or c.get('status')!='completed' or c.get('conclusion')!='success':continue
        app=c.get('app') or {}
        if app.get('slug')!='github-actions' or not isinstance(app.get('id'),int):continue
        passed[c['name']]=c;app_ids.add(app['id'])
    if set(passed)!=required or len(app_ids)!=1:return ExecutorWait('NOT_VERIFIED','ALL_PROVIDER_ATTESTED_REQUIRED_GATES_REQUIRED','merge',{'missing':sorted(required-set(passed)),'check_app_ids':sorted(app_ids)})
    merged=client.merge_pull(int(pulls[0]['number']),sha=sha,method='squash')
    if merged.get('merged') is not True:return ExecutorWait('NOT_VERIFIED','MERGE_READBACK_NOT_CONFIRMED','merge',{'message':merged.get('message')})
    return _result(state,key,subject_sha=sha,evidence_refs=_evidence_refs(root),metadata={'merge_sha':merged.get('sha'),'pull_request_number':pulls[0]['number']})


def promote_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    cfg=strict_loads((root/'.adwf/config.json').read_text(encoding='utf-8')); delivery=cfg.get('delivery') or {};adapter=str(delivery.get('promotion_adapter') or 'NONE');sha=str(state.get('subject_sha') or '')
    if adapter=='REFERENCE_LOCAL':
        att=promote_reference(root,sha);return _result(state,key,subject_sha=sha,metadata={'promotion':'COMPLETED','adapter':'REFERENCE_LOCAL','artifact_digest':att['artifact_digest'],'production_verified':False,'not_applicable_reason':'REFERENCE_LOCAL_NON_PRODUCTION_DELIVERY'})
    if adapter=='COMMAND':
        command=os.environ.get('ADWF_PROMOTE_COMMAND','').strip()
        if not command:return ExecutorWait('HUMAN_REQUIRED','DEPLOYMENT_COMMAND_REQUIRED','promote')
        out=run_command_adapter(command,root,sha,timeout=900,kind='promotion')
        if out['status']=='FAIL':return _result(state,key,'FAIL',reason_codes=['PROMOTION_COMMAND_FAILED'],metadata=out)
        if out['status']!='PASS':return ExecutorWait('NOT_VERIFIED',str(out.get('reason') or 'PROMOTION_ATTESTATION_REQUIRED'),'promote')
        return _result(state,key,subject_sha=sha,evidence_refs=list(out['evidence_refs']),metadata={'promotion':'COMPLETED','adapter':'COMMAND','artifact_digest':out['artifact_digest'],'provider_readback':True,'readback_id':out.get('readback_id')})
    if delivery.get('deployment_required') is True:return ExecutorWait('HUMAN_REQUIRED','DEPLOYMENT_ADAPTER_REQUIRED','promote')
    return _result(state,key,subject_sha=sha,metadata={'not_applicable_reason':'DEPLOYMENT_EXPLICITLY_NOT_REQUIRED'})


def observe_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    cfg=strict_loads((root/'.adwf/config.json').read_text(encoding='utf-8')); delivery=cfg.get('delivery') or {};adapter=str(delivery.get('observation_adapter') or 'NONE');sha=str(state.get('subject_sha') or '')
    if adapter=='REFERENCE_LOCAL':
        out=observe_reference(root,sha)
        if out['status']!='PASS':return _result(state,key,'FAIL',reason_codes=[str(out.get('reason') or 'OBSERVATION_FAILED')],metadata=out)
        return _result(state,key,subject_sha=sha,metadata={'observation':'PASS','adapter':'REFERENCE_LOCAL','artifact_digest':out['artifact_digest'],'production_verified':False,'not_applicable_reason':'REFERENCE_LOCAL_NON_PRODUCTION_OBSERVATION'})
    if adapter=='COMMAND':
        command=os.environ.get('ADWF_OBSERVE_COMMAND','').strip()
        if not command:return ExecutorWait('HUMAN_REQUIRED','OBSERVATION_COMMAND_REQUIRED','observe')
        out=run_command_adapter(command,root,sha,timeout=300,kind='observation')
        if out['status']=='FAIL':return _result(state,key,'FAIL',reason_codes=['OBSERVATION_FAILED'],metadata=out)
        if out['status']!='PASS':return ExecutorWait('NOT_VERIFIED',str(out.get('reason') or 'OBSERVATION_ATTESTATION_REQUIRED'),'observe')
        return _result(state,key,subject_sha=sha,evidence_refs=list(out['evidence_refs']),metadata={'observation':'PASS','adapter':'COMMAND','artifact_digest':out['artifact_digest'],'provider_readback':True,'readback_id':out.get('readback_id')})
    if delivery.get('observation_required') is True:return ExecutorWait('HUMAN_REQUIRED','OBSERVATION_ADAPTER_REQUIRED','observe')
    return _result(state,key,subject_sha=sha,metadata={'not_applicable_reason':'OBSERVATION_EXPLICITLY_NOT_REQUIRED'})


def done_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    # Provider lease release is part of terminal correctness. Defer Issue close
    # until CLEANUP has reconciled and released the authoritative lease.
    return _result(state,key,subject_sha=state.get('subject_sha'),evidence_refs=_evidence_refs(root),metadata={'issue_close_deferred_to_cleanup':True,'not_applicable_reason':None if _evidence_refs(root) else 'NO_EXTERNAL_CLOSE_EVIDENCE_REQUIRED'})


def cleanup_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
    client,auth=_github(root)
    if client is None:return ExecutorWait('HUMAN_REQUIRED','GITHUB_CONNECTION_REQUIRED','cleanup',auth)
    policy=_orchestration_policy(root)
    if 'error' in policy:return ExecutorWait('NOT_VERIFIED',str(policy['error']),'cleanup')
    try:
        _default,current_main=_current_main(client);delivery=str(state.get('delivery_sha') or '')
        if not SHA.fullmatch(delivery) or current_main!=delivery:return ExecutorWait('NOT_VERIFIED','CLEANUP_DELIVERY_MAIN_REQUIRED','cleanup',{'current_main':current_main,'delivery_sha':delivery})
        store=GitHubLeaseStore(client);registry,_anchor=store.read(expected_main_sha=current_main,policy_max_parallel_writers=1);branch=str(state.get('work_branch') or '')
        matches=[x for x in registry.get('leases') or [] if _same_lease(x,state,branch,_stage1_resources())]
        if len(matches)!=1:return ExecutorWait('NOT_VERIFIED','CLEANUP_ACTIVE_LEASE_REQUIRED','cleanup',{'matches':len(matches)})
        lease=matches[0];released,release_anchor=store.release(expected_main_sha=current_main,policy_max_parallel_writers=1,lease_id=str(lease['lease_id']),worker_id=_writer_id(state),provider_reconciled=True,provider_reconciliation_ref='github:commit:'+current_main)
        reread,_=store.read(expected_main_sha=current_main,policy_max_parallel_writers=1)
        if any(x.get('status')=='ACTIVE' and x.get('lease_id')==lease['lease_id'] for x in reread.get('leases') or []):return ExecutorWait('NOT_VERIFIED','LEASE_RELEASE_READBACK_ACTIVE','cleanup')
        issue=str(state.get('issue_id') or '');issue_closed=False
        if issue.isdigit():
            closed=client.close_issue(int(issue));issue_closed=str(closed.get('state') or '')=='closed'
            if not issue_closed:return ExecutorWait('NOT_VERIFIED','ISSUE_CLOSE_READBACK_NOT_CONFIRMED','cleanup')
        return _result(state,key,metadata={'cleanup':'RUNTIME_PROJECTIONS_RETAINED_AUDIT_LOG_IMMUTABLE','lease_id':lease['lease_id'],'lease_release_anchor':release_anchor,'lease_registry_revision':released['revision'],'provider_reconciliation_ref':'github:commit:'+current_main,'issue_closed':issue_closed})
    except Exception as exc:return ExecutorWait('NOT_VERIFIED','LEASE_PROVIDER_RELEASE_FAILED','cleanup',{'contract_error':str(exc)[:300]})


def next_executor(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]:
    return _result(state,key,'ROADMAP_COMPLETE',metadata={'queue_handled_by_owner_intent_service':True})

REGISTRY:dict[str,Callable[[Path,dict[str,Any],str,dict[str,Any]],dict[str,Any]|ExecutorWait]]={
 'RECONCILE':reconcile_executor,'AUTHORIZE':authorize_executor,'CLAIM':claim_executor,'WORKSPACE':workspace_executor,
 'EXECUTE':creative_executor,'OPEN_PR':open_pr_executor,'CI':ci_executor,'REVIEW':review_executor,'PREVIEW':preview_executor,
 'OWNER_ACCEPTANCE':owner_executor,'MERGE':merge_executor,'PROMOTE':promote_executor,'OBSERVE':observe_executor,'DONE':done_executor,
 'CLEANUP':cleanup_executor,'NEXT':next_executor,'RECOVERY':creative_executor,
}

class ActionExecutorRegistry:
    def __init__(self,root:str|Path):self.root=Path(root).resolve()
    def phases(self)->tuple[str,...]:return tuple(REGISTRY)
    def execute(self,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|ExecutorWait:
        phase=str(state.get('phase'))
        executor=REGISTRY.get(phase)
        if executor is None:raise ValueError('PHASE_EXECUTOR_MISSING:'+phase)
        if phase in {'WORKSPACE','EXECUTE','OPEN_PR','CI','REVIEW','PREVIEW','OWNER_ACCEPTANCE','MERGE','PROMOTE','OBSERVE','DONE','CLEANUP','RECOVERY'}:
            blocked=_require_live_writer_authority(self.root,state,phase)
            if blocked is not None:return blocked
        return executor(self.root,state,key,envelope)
