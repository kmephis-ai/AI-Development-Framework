"""Bounded provider-hosted source ingest bootstrap for GOV-034 Stage-2B."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import base64, binascii, hashlib, json, re, subprocess, zlib

from .github_lease_store import GitHubLeaseStore
from .github_provider import GitHubClient
from .lease_registry import _registry_lease_freshness_errors
from .provider_ops_gateway import (
    _BRANCH, _REQUEST_ID, _SHA40, _SHA256, _WORKER, _canonical, _commit_node,
    _issue_title_has_roadmap, _policy_gate, _rulesets, _tree_effect, _tree_files,
)
from .strict_json import loads as strict_loads

SOURCE_INGEST_MARKER="ADWF-PROVIDER-SOURCE-INGEST v1"
SOURCE_INGEST_ROLE="ADWF_PROVIDER_SOURCE_INGEST_V1"
SOURCE_PATHS=[".adwf/lib/provider_ops_gateway.py",".adwf/tests/test_provider_ops_gateway.py"]
ISSUE_ID=291; ROADMAP_ID="GOV-034"; MAX_ENCODED=48*1024; MAX_RAW=160*1024
LEASE_ID=re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REQUEST_FIELDS={"schema_version","role","request_id","issue_id","roadmap_id","expected_main_sha","branch","head_sha","worker_id","lease_id","lease_registry_revision","payload_zlib_base64","payload_sha256","payload_uncompressed_bytes","file_blobs","monetary_budget_usd","request_digest"}
PROVIDER_RESOURCE=[{"global":True,"kind":"provider","scope":"global","shared":True}]


def _digest(request:dict[str,Any])->str:
    return hashlib.sha256(_canonical({k:v for k,v in request.items() if k!="request_digest"}).encode()).hexdigest()


def _git_blob(raw:bytes)->str:
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()


def build_source_ingest_comment(*,request_id:str,expected_main_sha:str,branch:str,head_sha:str,worker_id:str,lease_id:str,lease_registry_revision:int,files:dict[str,str])->str:
    payload={"schema_version":1,"files":[{"path":p,"content":files[p]} for p in SOURCE_PATHS]}
    raw=_canonical(payload).encode(); encoded=base64.b64encode(zlib.compress(raw,9)).decode()
    request={"schema_version":1,"role":SOURCE_INGEST_ROLE,"request_id":request_id,"issue_id":ISSUE_ID,"roadmap_id":ROADMAP_ID,"expected_main_sha":expected_main_sha,"branch":branch,"head_sha":head_sha,"worker_id":worker_id,"lease_id":lease_id,"lease_registry_revision":lease_registry_revision,"payload_zlib_base64":encoded,"payload_sha256":hashlib.sha256(raw).hexdigest(),"payload_uncompressed_bytes":len(raw),"file_blobs":{p:_git_blob(files[p].encode()) for p in SOURCE_PATHS},"monetary_budget_usd":0}
    request["request_digest"]=_digest(request); return SOURCE_INGEST_MARKER+"\n"+_canonical(request)


def has_source_ingest_marker(body:Any)->bool:
    if not isinstance(body,str): return False
    value=body.replace("\r\n","\n").strip(); return value==SOURCE_INGEST_MARKER or value.startswith(SOURCE_INGEST_MARKER+"\n")


def _decode(request:dict[str,Any])->dict[str,bytes]:
    encoded=request["payload_zlib_base64"]
    if len(encoded.encode("ascii"))>MAX_ENCODED: raise ValueError("SOURCE_INGEST_PAYLOAD_ENCODED_TOO_LARGE")
    try: compressed=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError) as exc: raise ValueError("SOURCE_INGEST_PAYLOAD_BASE64_INVALID") from exc
    try:
        dec=zlib.decompressobj(); raw=dec.decompress(compressed,MAX_RAW+1)
        if len(raw)>MAX_RAW or dec.unconsumed_tail: raise ValueError("SOURCE_INGEST_PAYLOAD_DECOMPRESSED_TOO_LARGE")
        raw+=dec.flush()
    except zlib.error as exc: raise ValueError("SOURCE_INGEST_PAYLOAD_ZLIB_INVALID") from exc
    if len(raw)>MAX_RAW: raise ValueError("SOURCE_INGEST_PAYLOAD_DECOMPRESSED_TOO_LARGE")
    if not dec.eof or dec.unused_data: raise ValueError("SOURCE_INGEST_PAYLOAD_ZLIB_TRAILING_OR_TRUNCATED")
    if len(raw)!=request["payload_uncompressed_bytes"]: raise ValueError("SOURCE_INGEST_PAYLOAD_SIZE_MISMATCH")
    if hashlib.sha256(raw).hexdigest()!=request["payload_sha256"]: raise ValueError("SOURCE_INGEST_PAYLOAD_DIGEST_MISMATCH")
    try: text=raw.decode("utf-8"); payload=strict_loads(text)
    except UnicodeDecodeError as exc: raise ValueError("SOURCE_INGEST_PAYLOAD_UTF8_INVALID") from exc
    except (json.JSONDecodeError,ValueError) as exc: raise ValueError("SOURCE_INGEST_PAYLOAD_JSON_INVALID") from exc
    if not isinstance(payload,dict) or set(payload)!={"schema_version","files"} or payload.get("schema_version")!=1: raise ValueError("SOURCE_INGEST_PAYLOAD_FIELDS_INVALID")
    rows=payload.get("files")
    if not isinstance(rows,list) or len(rows)!=2 or any(not isinstance(x,dict) or set(x)!={"path","content"} for x in rows): raise ValueError("SOURCE_INGEST_FILES_INVALID")
    if [x.get("path") for x in rows]!=SOURCE_PATHS: raise ValueError("SOURCE_INGEST_PATHS_INVALID")
    files={}
    for row in rows:
        if not isinstance(row["content"],str): raise ValueError("SOURCE_INGEST_CONTENT_INVALID")
        data=row["content"].encode()
        if _git_blob(data)!=request["file_blobs"].get(row["path"]): raise ValueError("SOURCE_INGEST_FILE_BLOB_MISMATCH")
        files[row["path"]]=data
    if text!=_canonical(payload): raise ValueError("SOURCE_INGEST_PAYLOAD_NOT_CANONICAL")
    return files


def parse_source_ingest_comment(body:str)->tuple[dict[str,Any],dict[str,bytes]]:
    lines=body.replace("\r\n","\n").strip().split("\n")
    if len(lines)!=2 or lines[0]!=SOURCE_INGEST_MARKER: raise ValueError("SOURCE_INGEST_REQUEST_ENVELOPE_INVALID")
    try: request=strict_loads(lines[1])
    except (json.JSONDecodeError,ValueError) as exc: raise ValueError("SOURCE_INGEST_REQUEST_JSON_INVALID") from exc
    if not isinstance(request,dict) or set(request)!=REQUEST_FIELDS: raise ValueError("SOURCE_INGEST_REQUEST_FIELDS_INVALID")
    if request.get("schema_version")!=1 or request.get("role")!=SOURCE_INGEST_ROLE: raise ValueError("SOURCE_INGEST_REQUEST_IDENTITY_INVALID")
    if not isinstance(request.get("request_id"),str) or _REQUEST_ID.fullmatch(request["request_id"]) is None: raise ValueError("SOURCE_INGEST_REQUEST_ID_INVALID")
    if request.get("issue_id")!=ISSUE_ID or request.get("roadmap_id")!=ROADMAP_ID: raise ValueError("SOURCE_INGEST_WORK_ITEM_INVALID")
    if any(not isinstance(request.get(k),str) or _SHA40.fullmatch(request[k]) is None for k in ("expected_main_sha","head_sha")): raise ValueError("SOURCE_INGEST_SHA_INVALID")
    branch=request.get("branch"); worker=request.get("worker_id")
    if not isinstance(branch,str) or _BRANCH.fullmatch(branch) is None or ".." in branch or "//" in branch or "@{" in branch or branch.endswith("/"): raise ValueError("SOURCE_INGEST_BRANCH_INVALID")
    if not isinstance(worker,str) or _WORKER.fullmatch(worker) is None: raise ValueError("SOURCE_INGEST_WORKER_INVALID")
    if not isinstance(request.get("lease_id"),str) or LEASE_ID.fullmatch(request["lease_id"]) is None: raise ValueError("SOURCE_INGEST_LEASE_ID_INVALID")
    rev=request.get("lease_registry_revision"); size=request.get("payload_uncompressed_bytes")
    if isinstance(rev,bool) or not isinstance(rev,int) or rev<1: raise ValueError("SOURCE_INGEST_LEASE_REVISION_INVALID")
    if not isinstance(request.get("payload_zlib_base64"),str) or not request["payload_zlib_base64"]: raise ValueError("SOURCE_INGEST_PAYLOAD_REQUIRED")
    if not isinstance(request.get("payload_sha256"),str) or _SHA256.fullmatch(request["payload_sha256"]) is None: raise ValueError("SOURCE_INGEST_PAYLOAD_DIGEST_INVALID")
    if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=MAX_RAW: raise ValueError("SOURCE_INGEST_PAYLOAD_SIZE_INVALID")
    blobs=request.get("file_blobs")
    if not isinstance(blobs,dict) or sorted(blobs)!=SOURCE_PATHS or any(not isinstance(v,str) or _SHA40.fullmatch(v) is None for v in blobs.values()): raise ValueError("SOURCE_INGEST_FILE_BLOBS_INVALID")
    if isinstance(request.get("monetary_budget_usd"),bool) or request.get("monetary_budget_usd")!=0: raise ValueError("SOURCE_INGEST_MONETARY_BUDGET_INVALID")
    if not isinstance(request.get("request_digest"),str) or _SHA256.fullmatch(request["request_digest"]) is None or request["request_digest"]!=_digest(request): raise ValueError("SOURCE_INGEST_REQUEST_DIGEST_INVALID")
    if lines[1]!=_canonical(request): raise ValueError("SOURCE_INGEST_REQUEST_NOT_CANONICAL")
    return request,_decode(request)


def _local_head(root:Path)->str:
    p=subprocess.run(["git","rev-parse","HEAD"],cwd=root,text=True,capture_output=True,check=False,timeout=10); value=p.stdout.strip()
    if p.returncode or _SHA40.fullmatch(value) is None: raise ValueError("SOURCE_INGEST_LOCAL_HEAD_NOT_VERIFIED")
    return value


def _lease(client:GitHubClient,request:dict[str,Any],*,newer:bool=False)->tuple[dict[str,Any],int]:
    registry,_=GitHubLeaseStore(client).read(expected_main_sha=request["expected_main_sha"],policy_max_parallel_writers=1); rev=registry.get("revision")
    if isinstance(rev,bool) or not isinstance(rev,int) or (rev<request["lease_registry_revision"] if newer else rev!=request["lease_registry_revision"]): raise ValueError("SOURCE_INGEST_LEASE_REVISION_MISMATCH")
    active=[x for x in registry.get("leases") or [] if x.get("status")=="ACTIVE"]
    if len(active)!=1: raise ValueError("SOURCE_INGEST_SOLE_ACTIVE_LEASE_REQUIRED")
    lease=active[0]; expected={"lease_id":request["lease_id"],"worker_id":request["worker_id"],"issue_id":str(ISSUE_ID),"roadmap_id":ROADMAP_ID,"base_sha":request["expected_main_sha"],"branch":request["branch"],"resources":PROVIDER_RESOURCE}
    if any(lease.get(k)!=v for k,v in expected.items()): raise ValueError("SOURCE_INGEST_LEASE_IDENTITY_MISMATCH")
    if _registry_lease_freshness_errors(lease,datetime.now(timezone.utc)): raise ValueError("SOURCE_INGEST_LEASE_NOT_FRESH")
    return lease,rev


def _live(root:Path,client:GitHubClient,request:dict[str,Any],*,require_head:bool,newer:bool=False)->dict[str,Any]:
    default=str(client.repo_info().get("default_branch") or ""); main=str((client.branch(default).get("commit") or {}).get("sha") or "") if default else ""
    if main!=request["expected_main_sha"]: raise ValueError("SOURCE_INGEST_MAIN_DRIFT")
    if _local_head(root)!=main: raise ValueError("SOURCE_INGEST_TRUSTED_CHECKOUT_MAIN_MISMATCH")
    reason=_policy_gate(root)
    if reason: raise ValueError(reason.replace("PROVIDER_OPS_","SOURCE_INGEST_"))
    try:
        _rulesets(client)
    except Exception as exc:
        raise ValueError(str(exc).replace("PROVIDER_OPS_","SOURCE_INGEST_")) from exc
    issue=client.get(f"/repos/{client.repo}/issues/{ISSUE_ID}")
    if issue.get("state")!="open" or issue.get("pull_request") is not None or int(issue.get("number") or 0)!=ISSUE_ID: raise ValueError("SOURCE_INGEST_ISSUE_NOT_OPEN")
    if not _issue_title_has_roadmap(str(issue.get("title") or ""),ROADMAP_ID): raise ValueError("SOURCE_INGEST_ISSUE_ROADMAP_MISMATCH")
    branch_sha=str((client.git_ref(request["branch"]).get("object") or {}).get("sha") or "")
    if _SHA40.fullmatch(branch_sha) is None: raise ValueError("SOURCE_INGEST_BRANCH_SHA_INVALID")
    if require_head and branch_sha!=request["head_sha"]: raise ValueError("SOURCE_INGEST_HEAD_DRIFT")
    _,rev=_lease(client,request,newer=newer); return {"main":main,"branch":branch_sha,"revision":rev}


def _marker(r:dict[str,Any])->str: return "ADWF-Provider-Source-Ingest-Digest: "+r["request_digest"]

def _applied(client:GitHubClient,r:dict[str,Any],sha:str)->bool:
    if sha==r["head_sha"] or _SHA40.fullmatch(sha) is None: return False
    try:
        node=_commit_node(client,sha,{}); parent=_commit_node(client,r["head_sha"],{}); before=_tree_files(client,parent["tree_sha"]); after=_tree_files(client,node["tree_sha"])
    except Exception: return False
    return node["parents"]==[r["head_sha"]] and _marker(r) in node["message"] and [x["path"] for x in _tree_effect(before,after)]==SOURCE_PATHS and all((after.get(p) or {}).get("sha")==r["file_blobs"][p] for p in SOURCE_PATHS)


def _result(status:str,reason:str|None,r:dict[str,Any]|None,**extra:Any)->dict[str,Any]:
    out={"status":status,"mutation":False,**extra}
    if reason: out["reason"]=reason
    if r: out.update(request_id=r["request_id"],request_digest=r["request_digest"])
    return out


def process_issue_comment_source_ingest(root:Path,event:dict[str,Any],client:GitHubClient)->dict[str,Any]|None:
    comment=event.get("comment") if isinstance(event,dict) else None; body=(comment or {}).get("body") if isinstance(comment,dict) else None
    if not has_source_ingest_marker(body): return None
    try: r,files=parse_source_ingest_comment(str(body))
    except ValueError as exc: return _result("REJECTED",str(exc),None)
    if event.get("action")!="created" or str((event.get("repository") or {}).get("full_name") or "")!=client.repo or "pull_request" in (event.get("issue") or {}) or int((event.get("issue") or {}).get("number") or 0)!=ISSUE_ID: return _result("REJECTED","SOURCE_INGEST_EVENT_IDENTITY_INVALID",r)
    actor=str(((comment or {}).get("user") or {}).get("login") or "")
    if not actor or actor!=str((event.get("sender") or {}).get("login") or "") or str((comment or {}).get("author_association") or "") not in {"OWNER","MEMBER","COLLABORATOR"}: return _result("REJECTED","SOURCE_INGEST_ACTOR_INVALID",r)
    try:
        if str(client.collaborator_permission(actor).get("permission") or "").lower()!="admin": return _result("REJECTED","SOURCE_INGEST_ACTOR_ADMIN_REQUIRED",r)
        live=_live(root,client,r,require_head=False,newer=True)
    except Exception as exc: return _result("NOT_VERIFIED",str(exc),r)
    if _applied(client,r,live["branch"]): return _result("ALREADY_APPLIED",None,r,new_head_sha=live["branch"],changed_paths=SOURCE_PATHS,provider_readback=True,merge_authority=False,issue_close_authority=False,monetary_cost_usd=0)
    if live["revision"]!=r["lease_registry_revision"]: return _result("REJECTED","SOURCE_INGEST_LEASE_REVISION_DRIFT",r,provider_revision=live["revision"])
    if live["branch"]!=r["head_sha"]: return _result("REJECTED","SOURCE_INGEST_HEAD_DRIFT",r)
    try:
        parent=_commit_node(client,r["head_sha"],{}); before=_tree_files(client,parent["tree_sha"]); entries=[]
        for path in SOURCE_PATHS:
            sha=str(client.create_blob(files[path]).get("sha") or "")
            if sha!=r["file_blobs"][path]: raise ValueError("SOURCE_INGEST_PROVIDER_BLOB_SHA_MISMATCH:"+path)
            entries.append({"path":path,"mode":"100644","type":"blob","sha":sha})
        tree_sha=str(client.create_tree(base_tree_sha=parent["tree_sha"],entries=entries).get("sha") or ""); after=_tree_files(client,tree_sha)
        if [x["path"] for x in _tree_effect(before,after)]!=SOURCE_PATHS or any((after.get(p) or {}).get("sha")!=r["file_blobs"][p] for p in SOURCE_PATHS): raise ValueError("SOURCE_INGEST_CREATED_TREE_EFFECT_MISMATCH")
        message=f"GOV-034: provider-hosted bounded Stage-2 source ingest\n\nADWF-Provider-Source-Ingest-Request: {r['request_id']}\n{_marker(r)}\nADWF-Provider-Source-Ingest-Parent: {r['head_sha']}"
        new_sha=str(client.create_commit(message=message,tree_sha=tree_sha,parent_sha=r["head_sha"]).get("sha") or ""); node=_commit_node(client,new_sha,{})
        if node["parents"]!=[r["head_sha"]] or node["tree_sha"]!=tree_sha or _marker(r) not in node["message"]: raise ValueError("SOURCE_INGEST_COMMIT_READBACK_INVALID")
        _live(root,client,r,require_head=True)
    except Exception as exc: return _result("NOT_VERIFIED",str(exc),r)
    try: client.update_branch_ref(r["branch"],new_sha)
    except Exception as exc: return _result("NOT_VERIFIED","SOURCE_INGEST_BRANCH_CAS_FAILED:"+str(exc),r,orphan_commit_sha=new_sha)
    try:
        post=_live(root,client,r,require_head=False,newer=True)
        if post["main"]!=r["expected_main_sha"] or post["branch"]!=new_sha or not _applied(client,r,new_sha): raise ValueError("SOURCE_INGEST_POST_UPDATE_READBACK_FAILED")
    except Exception as exc: return _result("NOT_VERIFIED",str(exc),r,mutation=True,new_head_sha=new_sha)
    return {"status":"PASS","mutation":True,"request_id":r["request_id"],"request_digest":r["request_digest"],"base_sha":r["expected_main_sha"],"source_head_sha":r["head_sha"],"new_head_sha":new_sha,"changed_paths":SOURCE_PATHS,"lease_id":r["lease_id"],"lease_registry_revision":r["lease_registry_revision"],"provider_readback":True,"merge_authority":False,"issue_close_authority":False,"monetary_cost_usd":0}
