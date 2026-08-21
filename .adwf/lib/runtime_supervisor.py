"""ADWF v1.6 Executive Autopilot Runtime Supervisor.

The supervisor is now an executor, not only a dispatcher. Deterministic phases
are run through ActionExecutorRegistry. Creative phases use a replaceable agent
adapter and wait without granting authority when no adapter is configured.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib,json,os,subprocess,sys,tempfile

from .durable_orchestrator import OrchestrationJournal,ACTION_BY_PHASE,advance_run_trusted
from .strict_json import loads as strict_loads
from .trusted_context import compile_trusted_context
from .work_memory import WorkMemoryStore
from .action_executors import ActionExecutorRegistry,ExecutorWait
from .ai_work_contracts import CREATIVE_PHASES, compile_work_package
from .durable_projection import sync_project_state
from .github_auth import detect_repository, discover_token


def _now()->str:return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def _key(run_id:str,phase:str,revision:int)->str:return hashlib.sha256(f"{run_id}:{phase}:{revision}".encode()).hexdigest()

@dataclass
class SupervisorStatus:
    run_id:str;phase:str;status:str;revision:int;waiting_for:str|None;next_action_ru:str
    def to_dict(self)->dict[str,Any]:return self.__dict__.copy()

class ActionEnvelopeStore:
    def __init__(self,root:str|Path):
        self.root=Path(root).resolve();self.base=self.root/'.adwf-runtime'/'supervisor';(self.base/'requests').mkdir(parents=True,exist_ok=True);(self.base/'results').mkdir(parents=True,exist_ok=True)
    def request_path(self,key:str)->Path:return self.base/'requests'/f'{key}.json'
    def result_path(self,key:str)->Path:return self.base/'results'/f'{key}.json'
    def ensure_request(self,state:dict[str,Any])->tuple[str,Path]:
        key=_key(state['run_id'],state['phase'],int(state['revision']));path=self.request_path(key)
        if not path.exists():
            payload={'schema_version':3,'idempotency_key':key,'run_id':state['run_id'],'revision':state['revision'],'brief_id':state['roadmap_id'],'phase':state['phase'],'capability':ACTION_BY_PHASE[state['phase']],
              'subject_sha':state.get('subject_sha'),'delivery_sha':state.get('delivery_sha'),'work_branch':state.get('work_branch'),'preview_digest':state.get('preview_digest'),'risk':state['risk'],'work_type':state['work_type'],'monetary_budget_usd':0,'created_at':_now()}
            if state['phase'] in CREATIVE_PHASES:
                memory=WorkMemoryStore(self.root).load()
                package=compile_work_package(state,memory)
                payload['work_package']=package;payload['work_package_digest']=package['package_digest']
            self._atomic(path,payload)
        return key,path
    def _atomic(self,path:Path,value:dict[str,Any])->None:
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as h:json.dump(value,h,ensure_ascii=False,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    def write_result(self,key:str,value:dict[str,Any])->Path:
        if value.get('idempotency_key')!=key:raise ValueError('SUPERVISOR_RESULT_IDEMPOTENCY_MISMATCH')
        path=self.result_path(key)
        if path.is_file():
            existing=strict_loads(path.read_text(encoding='utf-8'))
            if existing!=value:raise ValueError('SUPERVISOR_RESULT_IDEMPOTENCY_CONFLICT')
            return path
        self._atomic(path,value);return path
    def result(self,key:str)->dict[str,Any]|None:
        path=self.result_path(key)
        if not path.is_file():return None
        value=strict_loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError('SUPERVISOR_RESULT_INVALID')
        return value

class RuntimeSupervisor:
    def __init__(self,root:str|Path):
        self.root=Path(root).resolve();self.journal=OrchestrationJournal(self.root);self.envelopes=ActionEnvelopeStore(self.root);self.executors=ActionExecutorRegistry(self.root)
    def _trusted_inputs(self)->tuple[dict[str,Any],dict[str,Any]]:
        assurance_path=self.root/'.adwf-runtime/assurance/current.json';provider_path=self.root/'.adwf-runtime/provider-readback.json'
        if not assurance_path.is_file() or not provider_path.is_file():raise ValueError('TRUSTED_RUNTIME_INPUTS_NOT_READY')
        return strict_loads(assurance_path.read_text(encoding='utf-8')),strict_loads(provider_path.read_text(encoding='utf-8'))
    def _refresh_trusted_inputs(self,subject_sha:str,*,force:bool=False)->None:
        if len(subject_sha)!=40:return
        if not force:
            try:
                assurance,provider=self._trusted_inputs()
                if assurance.get('subject_sha')==subject_sha and provider.get('subject_sha')==subject_sha:return
            except ValueError:pass
        repo=detect_repository(self.root);token,_source=discover_token()
        if not repo or not token:return
        env={**os.environ,'GITHUB_REPOSITORY':repo,'GITHUB_TOKEN':token,'ADWF_SUBJECT_SHA':subject_sha}
        proc=subprocess.run([sys.executable,str(self.root/'.adwf/scripts/github_reconcile.py'),'--apply','--subject-sha',subject_sha],cwd=self.root,env=env,text=True,capture_output=True,check=False,timeout=120)
        if proc.returncode not in {0,1}:raise ValueError('TRUSTED_RECONCILIATION_FAILED')
    def status(self,run_id:str)->SupervisorStatus:
        state=self.journal.load(run_id);key,_=self.envelopes.ensure_request(state);result=self.envelopes.result(key);memory=WorkMemoryStore(self.root).load()
        nxt=(memory or {}).get('next_action_ru') or f"Выполнить этап {state['phase']}";waiting=None if result else ACTION_BY_PHASE[state['phase']]
        return SupervisorStatus(run_id,state['phase'],state['status'],state['revision'],waiting,nxt)
    def _advance(self,state:dict[str,Any],result:dict[str,Any])->dict[str,Any]:
        subject_sha=str(result.get('subject_sha') or state.get('subject_sha') or '')
        self._refresh_trusted_inputs(subject_sha,force=state.get('phase') in {'OWNER_ACCEPTANCE','MERGE'})
        assurance,provider=self._trusted_inputs()
        request={'request_id':result['idempotency_key'],'subject_sha':subject_sha,'preview_digest':result.get('preview_digest') or state.get('preview_digest')}
        operational='recovery' if state['phase']=='RECOVERY' else ('verification' if state['phase'] in {'CI','REVIEW','PREVIEW','OBSERVE'} else state['work_type'])
        context=compile_trusted_context(self.root,action=ACTION_BY_PHASE[state['phase']],risk=state['risk'],work_type=operational,request=request,assurance_snapshot=assurance,provider_readback=provider)
        advanced=advance_run_trusted(self.root,state['run_id'],result,context);sync_project_state(self.root,advanced)
        memory=WorkMemoryStore(self.root).load()
        if memory and memory.get('run_id')==state['run_id']:
            rev=memory['revision'];memory['status']='RECOVERY' if advanced['status']=='RECOVERY' else ('DONE' if advanced['status']=='COMPLETE' else ('WAITING_OWNER' if advanced['phase']=='OWNER_ACCEPTANCE' else ('WAITING_CI' if advanced['phase']=='CI' else 'ACTIVE')))
            memory['next_action_ru']='Работа завершена.' if advanced['status']=='COMPLETE' else f"Следующий этап: {advanced['phase']}"
            WorkMemoryStore(self.root).save(memory,expected_revision=rev)
        return advanced
    def tick(self,run_id:str,*,max_steps:int=12)->dict[str,Any]:
        transitions=[]
        for _ in range(max(1,min(int(max_steps),32))):
            state=self.journal.load(run_id)
            if state['status'] in {'COMPLETE','BLOCKED'}:return {'status':state['status'],'run':state,'transitions':transitions}
            key,request_path=self.envelopes.ensure_request(state);result=self.envelopes.result(key)
            if result is None:
                envelope=strict_loads(request_path.read_text(encoding='utf-8'));produced=self.executors.execute(state,key,envelope)
                if isinstance(produced,ExecutorWait):
                    return {**produced.to_dict(),'phase':state['phase'],'run_id':run_id,'request':str(request_path),'idempotency_key':key,'transitions':transitions}
                self.envelopes.write_result(key,produced);result=produced
            if result.get('idempotency_key')!=key:raise ValueError('SUPERVISOR_RESULT_IDEMPOTENCY_MISMATCH')
            advanced=self._advance(state,result);transitions.append({'from':state['phase'],'outcome':result.get('outcome'),'to':advanced.get('phase'),'revision':advanced.get('revision')})
            if advanced.get('status') in {'COMPLETE','BLOCKED','HUMAN_REQUIRED','RECOVERY','RETRY_WAIT'}:
                return {'status':advanced['status'],'run':advanced,'transitions':transitions}
        state=self.journal.load(run_id);return {'status':'STEP_BUDGET_REACHED','run':state,'transitions':transitions}
