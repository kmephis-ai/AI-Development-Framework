"""Production AssuranceSnapshot builder. No caller-provided positive facts."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
import json, os, tempfile
from .assurance import snapshot_digest,validate_assurance_snapshot
from .evidence_resolver import resolve_evidence_refs
from .file_lock import exclusive_file_lock
from .policy_runtime import load_effective_policy


def build_assurance_snapshot(root:str|Path,*,subject_sha:str,health:dict[str,str],gates:dict[str,str],required_gates:list[str],
                             evidence_records:list[dict[str,Any]],evidence_refs:list[str],provider_readback:dict[str,Any],cost:dict[str,Any],ttl_minutes:int=60)->dict[str,Any]:
    policy=load_effective_policy(root); now=datetime.now(timezone.utc)
    evidence=resolve_evidence_refs(evidence_records,evidence_refs,subject_sha=subject_sha,policy_hash=policy["policy_hash"],now=now)
    provider_ok=provider_readback.get("readback_verified") is True and provider_readback.get("subject_sha")==subject_sha
    cost_zero=cost.get("result")=="ALLOW" and float(cost.get("projected_cost_usd",cost.get("projected_cost",0)) or 0)==0
    snapshot={"schema_version":1,"subject_sha":subject_sha,"policy_hash":policy["policy_hash"],"health":dict(health),"gates":dict(gates),
      "required_gates":list(required_gates),"evidence":evidence,
      "provider":{**provider_readback,"readback_verified":provider_ok},
      "cost":{"status":"VERIFIED_ZERO" if cost_zero else "NOT_VERIFIED","projected_cost_usd":0.0 if cost_zero else None},
      "verified_at":now.isoformat().replace('+00:00','Z'),"expires_at":(now+timedelta(minutes=max(1,min(int(ttl_minutes),1440)))).isoformat().replace('+00:00','Z')}
    snapshot["snapshot_digest"]=snapshot_digest(snapshot)
    errors=validate_assurance_snapshot(snapshot,expected_sha=subject_sha,expected_policy_hash=policy["policy_hash"])
    if errors: raise ValueError("ASSURANCE_BUILD_INVALID:"+",".join(errors))
    return snapshot


def persist_assurance_snapshot(root:str|Path,snapshot:dict[str,Any])->Path:
    base=Path(root).resolve()/".adwf-runtime"/"assurance"; base.mkdir(parents=True,exist_ok=True); target=base/"current.json"; lock=target.with_suffix('.lock')
    with exclusive_file_lock(lock):
        fd,tmp=tempfile.mkstemp(prefix=target.name+'.',dir=base)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as h: json.dump(snapshot,h,ensure_ascii=False,indent=2); h.write('\n'); h.flush(); os.fsync(h.fileno())
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    return target
