"""Provider-attested evidence resolution for trusted v1.6 decisions.

Legacy hash-chain evidence can establish integrity. This resolver adds producer
and provider readback requirements before any reference contributes VERIFIED.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import re
SHA=re.compile(r'^[0-9a-f]{40}$'); DIGEST=re.compile(r'^[0-9a-f]{64}$')

def _time(value:str)->datetime:
    dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('EVIDENCE_TIME_NAIVE')
    return dt.astimezone(timezone.utc)

def resolve_evidence_refs(records:list[dict[str,Any]], refs:list[str], *, subject_sha:str,
                          policy_hash:str, now:datetime|None=None)->dict[str,Any]:
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not refs:
        return {'status':'NOT_VERIFIED','refs_resolved':False,'reason_codes':['EVIDENCE_REFS_REQUIRED'],'resolved':[]}
    if SHA.fullmatch(subject_sha) is None or DIGEST.fullmatch(policy_hash) is None:
        return {'status':'NOT_VERIFIED','refs_resolved':False,'reason_codes':['TRUST_BINDING_INVALID'],'resolved':[]}
    by_id={str(r.get('ref_id')):r for r in records if isinstance(r,dict) and r.get('ref_id')}
    reasons=[]; resolved=[]
    for ref in refs:
        r=by_id.get(str(ref))
        if not r: reasons.append(f'EVIDENCE_REF_MISSING:{ref}'); continue
        if r.get('subject_sha')!=subject_sha: reasons.append(f'EVIDENCE_SHA_MISMATCH:{ref}')
        if r.get('policy_hash')!=policy_hash: reasons.append(f'EVIDENCE_POLICY_MISMATCH:{ref}')
        if not DIGEST.fullmatch(str(r.get('artifact_digest') or '')): reasons.append(f'EVIDENCE_DIGEST_INVALID:{ref}')
        producer=r.get('producer') if isinstance(r.get('producer'),dict) else {}
        if producer.get('provider') not in {'github','gitlab','local_trusted'}: reasons.append(f'EVIDENCE_PRODUCER_INVALID:{ref}')
        if producer.get('readback_verified') is not True: reasons.append(f'EVIDENCE_PRODUCER_NOT_ATTESTED:{ref}')
        if producer.get('provider') in {'github','gitlab'} and not producer.get('run_id'): reasons.append(f'EVIDENCE_RUN_ID_MISSING:{ref}')
        anchor=r.get('external_anchor') if isinstance(r.get('external_anchor'),dict) else {}
        if anchor.get('readback_verified') is not True or not anchor.get('anchor_id'): reasons.append(f'EVIDENCE_EXTERNAL_ANCHOR_MISSING:{ref}')
        try:
            observed=_time(r.get('observed_at')); expires=_time(r.get('expires_at'))
            if expires<=observed or expires<=now: reasons.append(f'EVIDENCE_STALE:{ref}')
        except (TypeError,ValueError): reasons.append(f'EVIDENCE_TIME_INVALID:{ref}')
        if not any(code.endswith(':'+str(ref)) for code in reasons): resolved.append(str(ref))
    status='VERIFIED' if len(resolved)==len(refs) and not reasons else 'NOT_VERIFIED'
    return {'status':status,'refs_resolved':status=='VERIFIED','reason_codes':list(dict.fromkeys(reasons)),'resolved':resolved}
