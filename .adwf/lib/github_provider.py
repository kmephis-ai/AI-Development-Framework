"""Single GitHub API boundary for trusted ADWF controllers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
import base64,re
from .http_transport import urllib_transport
from .provider_contracts import ProviderContractError, Transport, request_json, request_list_pages, request_value, request_bytes, mutate_with_readback

@dataclass
class GitHubClient:
    repo: str
    token: str
    transport: Transport = urllib_transport
    api_base: str = "https://api.github.com"

    @property
    def headers(self)->dict[str,str]:
        return {"Authorization":f"Bearer {self.token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"adwf-v1.6"}
    def url(self,path:str)->str: return self.api_base.rstrip("/")+path
    def get(self,path:str)->dict[str,Any]: return request_json(self.transport,"GET",self.url(path),self.headers,timeout=20)[0]
    def post(self,path:str,payload:dict[str,Any])->dict[str,Any]: return request_json(self.transport,"POST",self.url(path),self.headers,payload,timeout=20,max_attempts=1)[0]
    def patch(self,path:str,payload:dict[str,Any])->dict[str,Any]: return request_json(self.transport,"PATCH",self.url(path),self.headers,payload,timeout=20,max_attempts=1)[0]
    def put(self,path:str,payload:dict[str,Any])->dict[str,Any]: return request_json(self.transport,"PUT",self.url(path),self.headers,payload,timeout=20,max_attempts=1)[0]
    def list(self,path:str,*,object_key:str|None=None,max_pages:int=10)->list[dict[str,Any]]:
        return request_list_pages(self.transport,self.url(path),self.headers,timeout=20,max_pages=max_pages,object_key=object_key)
    def repo_info(self)->dict[str,Any]: return self.get(f"/repos/{self.repo}")
    def branch(self,name:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/branches/{quote(name,safe='')}")
    def issues(self)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/issues?state=all&per_page=100")
    def pulls(self)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/pulls?state=all&sort=updated&direction=desc&per_page=100")
    def runs(self)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/actions/runs?per_page=100",object_key="workflow_runs")
    def recent_runs(self,*,limit:int=100,event:str|None=None)->list[dict[str,Any]]:
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1 <= limit <= 100:
            raise ProviderContractError("PROVIDER_RECENT_RUNS_LIMIT_INVALID")
        query=f"per_page={limit}"
        if event is not None:
            if not isinstance(event,str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}",event) is None:
                raise ProviderContractError("PROVIDER_RECENT_RUNS_EVENT_INVALID")
            query += "&event=" + quote(event,safe="")
        value=self.get(f"/repos/{self.repo}/actions/runs?{query}")
        rows=value.get("workflow_runs")
        if not isinstance(rows,list) or any(not isinstance(item,dict) for item in rows) or len(rows) > limit:
            raise ProviderContractError("PROVIDER_RECENT_RUNS_PAYLOAD_INVALID")
        return rows
    def jobs(self,run_id:int)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=100",object_key="jobs")
    def job_logs(self,job_id:int)->bytes: return request_bytes(self.transport,"GET",self.url(f"/repos/{self.repo}/actions/jobs/{int(job_id)}/logs"),self.headers,timeout=30,max_attempts=2)[0]
    def issue_comments(self,number:int)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/issues/{number}/comments?per_page=100")

    def git_ref(self,branch:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/git/ref/heads/{quote(branch,safe='')}")
    def create_ref(self,branch:str,sha:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/git/refs",{"ref":f"refs/heads/{branch}","sha":sha})
    def git_commit(self,sha:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/git/commits/{quote(sha,safe='')}")
    def git_tree(self,sha:str,*,recursive:bool=False)->dict[str,Any]:
        suffix='?recursive=1' if recursive else ''
        return self.get(f"/repos/{self.repo}/git/trees/{quote(sha,safe='')}{suffix}")
    def git_blob(self,sha:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/git/blobs/{quote(sha,safe='')}")
    def create_blob(self,content:bytes)->dict[str,Any]:
        if not isinstance(content,(bytes,bytearray)): raise ProviderContractError("GITHUB_GIT_BLOB_CONTENT_INVALID")
        encoded=base64.b64encode(bytes(content)).decode('ascii')
        return self.post(f"/repos/{self.repo}/git/blobs",{"content":encoded,"encoding":"base64"})
    def create_tree(self,*,base_tree_sha:str,entries:list[dict[str,Any]])->dict[str,Any]:
        if not isinstance(entries,list) or not entries: raise ProviderContractError("GITHUB_GIT_TREE_ENTRIES_INVALID")
        return self.post(f"/repos/{self.repo}/git/trees",{"base_tree":base_tree_sha,"tree":entries})
    def create_commit(self,*,message:str,tree_sha:str,parent_sha:str)->dict[str,Any]:
        if not isinstance(message,str) or not message.strip(): raise ProviderContractError("GITHUB_GIT_COMMIT_MESSAGE_INVALID")
        return self.post(f"/repos/{self.repo}/git/commits",{"message":message,"tree":tree_sha,"parents":[parent_sha]})
    def update_branch_ref(self,branch:str,sha:str)->dict[str,Any]:
        return self.patch(f"/repos/{self.repo}/git/refs/heads/{quote(branch,safe='')}",{"sha":sha,"force":False})
    def tag_ref(self,tag:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/git/ref/tags/{quote(tag,safe='')}")
    def matching_tag_refs(self,prefix:str)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/git/matching-refs/tags/{quote(prefix,safe='')}")
    def tag_object(self,sha:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/git/tags/{sha}")
    def create_tag_object(self,tag:str,sha:str,message:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/git/tags",{"tag":tag,"message":message,"object":sha,"type":"commit"})
    def create_tag_ref(self,tag:str,tag_object_sha:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/git/refs",{"ref":f"refs/tags/{tag}","sha":tag_object_sha})
    def release_by_tag(self,tag:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/releases/tags/{quote(tag,safe='')}")
    def content(self,path:str,*,ref:str|None=None)->dict[str,Any]:
        suffix=f"?ref={quote(ref,safe='')}" if ref else ''
        return self.get(f"/repos/{self.repo}/contents/{quote(path,safe='/')}{suffix}")
    def put_text_file(self,path:str,content:str,*,branch:str,message:str)->dict[str,Any]:
        encoded=base64.b64encode(content.encode('utf-8')).decode('ascii');payload={"message":message,"content":encoded,"branch":branch}
        try:
            current=self.content(path,ref=branch)
            if current.get('sha'):payload['sha']=current['sha']
        except Exception:
            pass
        return self.put(f"/repos/{self.repo}/contents/{quote(path,safe='/')}",payload)
    def pull(self,number:int)->dict[str,Any]: return self.get(f"/repos/{self.repo}/pulls/{int(number)}")
    def pull_files(self,number:int)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/pulls/{int(number)}/files?per_page=100")
    def pull_reviews(self,number:int)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/pulls/{int(number)}/reviews?per_page=100")
    def check_runs(self,sha:str)->list[dict[str,Any]]: return self.list(f"/repos/{self.repo}/commits/{sha}/check-runs?per_page=100",object_key="check_runs")
    def workflow_dispatch(self,workflow:str,ref:str,inputs:dict[str,str]|None=None)->dict[str,Any]:
        self.post(f"/repos/{self.repo}/actions/workflows/{quote(workflow,safe='')}/dispatches",{"ref":ref,"inputs":inputs or {}}); return {"status":"DISPATCHED","workflow":workflow,"ref":ref}
    def repository_dispatch(self,event_type:str,client_payload:dict[str,Any])->dict[str,Any]:
        self.post(f"/repos/{self.repo}/dispatches",{"event_type":event_type,"client_payload":client_payload}); return {"status":"DISPATCHED","event_type":event_type}
    def merge_pull(self,number:int,*,sha:str,method:str="squash")->dict[str,Any]:
        return request_json(self.transport,"PUT",self.url(f"/repos/{self.repo}/pulls/{int(number)}/merge"),self.headers,{"sha":sha,"merge_method":method},timeout=20,max_attempts=1)[0]
    def create_issue(self,title:str,body:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/issues",{"title":title,"body":body})
    def create_pull(self,*,title:str,body:str,head:str,base:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/pulls",{"title":title,"body":body,"head":head,"base":base})
    def close_issue(self,number:int)->dict[str,Any]: return self.patch(f"/repos/{self.repo}/issues/{int(number)}",{"state":"closed"})
    def add_issue_comment(self,number:int,body:str)->dict[str,Any]: return self.post(f"/repos/{self.repo}/issues/{number}/comments",{"body":body})
    def current_user(self)->dict[str,Any]: return self.get("/user")
    def collaborator_permission(self,login:str)->dict[str,Any]: return self.get(f"/repos/{self.repo}/collaborators/{quote(login,safe='')}/permission")
    def rulesets(self)->list[dict[str,Any]]:
        summaries=self.list(f"/repos/{self.repo}/rulesets?per_page=100")
        details=[]
        for item in summaries:
            rid=item.get("id")
            if rid is not None: details.append(self.get(f"/repos/{self.repo}/rulesets/{rid}"))
        return details
    def create_ruleset(self,payload:dict[str,Any],*,idempotency_key:str)->tuple[dict[str,Any],dict[str,Any]]:
        mutation,_=request_json(self.transport,"POST",self.url(f"/repos/{self.repo}/rulesets"),{**self.headers,"Idempotency-Key":idempotency_key},payload,timeout=20,max_attempts=1)
        rid=mutation.get("id")
        if rid is None: raise ValueError("GITHUB_RULESET_CREATE_ID_MISSING")
        readback=self.get(f"/repos/{self.repo}/rulesets/{rid}")
        return mutation,readback
    def update_ruleset(self,ruleset_id:int,payload:dict[str,Any])->dict[str,Any]:
        request_json(self.transport,"PUT",self.url(f"/repos/{self.repo}/rulesets/{int(ruleset_id)}"),self.headers,payload,timeout=20,max_attempts=1)
        return self.get(f"/repos/{self.repo}/rulesets/{int(ruleset_id)}")
