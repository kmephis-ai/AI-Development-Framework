"""Consumer-owned native CI gate binding and exact provider readback.

Delegation is evidence binding only: this contract cannot mutate workflows,
rulesets, deployments, secrets, or product files.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import copy, hashlib, json, os, re, tempfile
from .contracts import validate
from .strict_json import loads as strict_loads
from .consumer_profile import PROFILE_REL, load_consumer_profile
from .consumer_installation import RECORD_REL, load_record
from .consumer_operational import BINDING_REL, load_binding as load_operational_binding

GATES_REL = ".adwf-consumer/gates.json"
SCHEMA_REL = ".adwf/schemas/consumer-gates.schema.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PHASES = ("pr", "main", "runtime")

class ConsumerGateError(ValueError): pass

def _canonical(v: Any)->bytes: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()
def _sha(v: Any)->str: return hashlib.sha256(_canonical(v)).hexdigest()
def _file_sha(p: Path)->str: return hashlib.sha256(p.read_bytes()).hexdigest()
def seal_binding(v: dict[str,Any])->dict[str,Any]:
    out=copy.deepcopy(v); out["binding_sha256"]=_sha({k:x for k,x in out.items() if k!="binding_sha256"}); return out

def _schema(root: Path)->dict[str,Any]:
    v=strict_loads((root/SCHEMA_REL).read_text(encoding="utf-8"))
    if not isinstance(v,dict): raise ConsumerGateError("CONSUMER_GATES_SCHEMA_OBJECT_REQUIRED")
    return v

def _base_bindings(project: Path, framework: Path)->tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    profile=load_consumer_profile(project,framework,required=True); assert profile is not None
    install=load_record(project,framework); ops=load_operational_binding(project,framework)
    return profile,install,ops

def validate_binding(binding: dict[str,Any], project_root: str|Path, framework_root: str|Path)->None:
    project=Path(project_root).resolve(); framework=Path(framework_root).resolve()
    if validate(binding,_schema(framework)): raise ConsumerGateError("CONSUMER_GATES_SCHEMA_MISMATCH")
    if binding.get("binding_sha256") != _sha({k:v for k,v in binding.items() if k!="binding_sha256"}): raise ConsumerGateError("CONSUMER_GATES_DIGEST_MISMATCH")
    profile,install,ops=_base_bindings(project,framework)
    expected_repo=install["consumer"]["repository"]
    if binding.get("consumer_repository") != expected_repo or ops.get("consumer_repository") != expected_repo: raise ConsumerGateError("CONSUMER_GATES_REPOSITORY_MISMATCH")
    expected={"consumer_profile_sha256":_file_sha(project/PROFILE_REL),"installation_record_sha256":_file_sha(project/RECORD_REL),"operational_binding_sha256":_file_sha(project/BINDING_REL)}
    for k,v in expected.items():
        if binding.get(k)!=v: raise ConsumerGateError("CONSUMER_GATES_STALE_BINDING:"+k)
    if binding.get("mutation_authority")!="NONE_BINDING_IS_REFERENCE_ONLY" or binding.get("safety")!={"monetary_budget_usd":0,"secrets":"FORBIDDEN"}: raise ConsumerGateError("CONSUMER_GATES_AUTHORITY_INVALID")
    req=binding.get("required_phases") or []
    for phase in req:
        if not binding.get("phases",{}).get(phase): raise ConsumerGateError("CONSUMER_GATES_REQUIRED_PHASE_EMPTY:"+str(phase))

def build_binding(project_root: str|Path, framework_root: str|Path, *, phases: dict[str,list[dict[str,Any]]], required_phases: list[str])->dict[str,Any]:
    project=Path(project_root).resolve(); framework=Path(framework_root).resolve(); _,install,ops=_base_bindings(project,framework)
    raw={"$schema":SCHEMA_REL,"schema_version":1,"role":"CONSUMER_NATIVE_GATE_BINDING","consumer_repository":install["consumer"]["repository"],"consumer_profile_sha256":_file_sha(project/PROFILE_REL),"installation_record_sha256":_file_sha(project/RECORD_REL),"operational_binding_sha256":_file_sha(project/BINDING_REL),"phases":{p:copy.deepcopy(phases.get(p,[])) for p in PHASES},"required_phases":list(required_phases),"safety":{"monetary_budget_usd":0,"secrets":"FORBIDDEN"},"mutation_authority":"NONE_BINDING_IS_REFERENCE_ONLY"}
    out=seal_binding(raw); validate_binding(out,project,framework); return out

def write_binding(binding: dict[str,Any], project_root: str|Path, framework_root: str|Path)->Path:
    project=Path(project_root).resolve(); validate_binding(binding,project,framework_root); target=project/GATES_REL
    if target.is_symlink() or (target.exists() and not target.is_file()): raise ConsumerGateError("CONSUMER_GATES_TARGET_INVALID")
    if target.exists():
        if strict_loads(target.read_text(encoding="utf-8"))==binding: return target
        raise ConsumerGateError("CONSUMER_GATES_FOREIGN_OR_DRIFTED")
    target.parent.mkdir(parents=True,exist_ok=True); payload=json.dumps(binding,ensure_ascii=False,indent=2)+"\n"
    fd,tmp=tempfile.mkstemp(prefix=target.name+".",dir=target.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as h: h.write(payload); h.flush(); os.fsync(h.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return target

def load_binding(project_root: str|Path, framework_root: str|Path)->dict[str,Any]:
    p=Path(project_root).resolve()/GATES_REL
    if not p.is_file() or p.is_symlink(): raise ConsumerGateError("CONSUMER_GATES_BINDING_REQUIRED")
    v=strict_loads(p.read_text(encoding="utf-8"))
    if not isinstance(v,dict): raise ConsumerGateError("CONSUMER_GATES_OBJECT_REQUIRED")
    validate_binding(v,project_root,framework_root); return v

def resolve_provider_phase(project_root: str|Path, framework_root: str|Path, client: Any, *, subject_sha: str, phase: str)->dict[str,Any]:
    if SHA40.fullmatch(subject_sha or "") is None: raise ConsumerGateError("CONSUMER_GATES_SUBJECT_SHA_INVALID")
    if phase not in PHASES: raise ConsumerGateError("CONSUMER_GATES_PHASE_INVALID")
    binding=load_binding(project_root,framework_root); declarations=binding["phases"].get(phase) or []
    if phase in binding["required_phases"] and not declarations: raise ConsumerGateError("CONSUMER_GATES_REQUIRED_PHASE_EMPTY:"+phase)
    checks=client.check_runs(subject_sha); matched=[]; failures=[]
    for decl in declarations:
        candidates=[c for c in checks if c.get("name")==decl["check_name"] and c.get("head_sha")==subject_sha and (c.get("app") or {}).get("slug")==decl["app_slug"] and (c.get("app") or {}).get("id")==decl["app_id"]]
        if len(candidates)!=1: failures.append("AMBIGUOUS_OR_MISSING:"+decl["check_name"]); continue
        c=candidates[0]
        if c.get("status")!="completed" or c.get("conclusion")!="success": failures.append("NOT_SUCCESS:"+decl["check_name"]); continue
        matched.append({"check_name":decl["check_name"],"check_run_id":c.get("id"),"app_slug":decl["app_slug"],"app_id":decl["app_id"]})
    status="VERIFIED" if not failures and (bool(declarations) or phase not in binding["required_phases"]) else "NOT_VERIFIED"
    return {"status":status,"phase":phase,"subject_sha":subject_sha,"consumer_repository":binding["consumer_repository"],"matched":matched,"failures":failures,"mutation_authority":"NONE_BINDING_IS_REFERENCE_ONLY"}
