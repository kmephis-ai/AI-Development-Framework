"""Trusted controller wake-up port used by local owner actions.

The port never stores credentials. It discovers an already authenticated GitHub
credential and sends only immutable identifiers to the default-branch workflow.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .github_auth import detect_repository, discover_token
from .github_provider import GitHubClient


def wake_controller(root:str|Path, *, run_id:str, reason:str, request_id:str)->dict[str,Any]:
    repo=detect_repository(root); token,source=discover_token()
    if not repo or not token:
        return {'status':'HUMAN_REQUIRED','reason':'GITHUB_AUTH_REQUIRED_FOR_CONTROLLER_WAKEUP','repository':repo,'credential_source':source}
    client=GitHubClient(repo,token); info=client.repo_info(); ref=str(info.get('default_branch') or 'main')
    inputs={'run_id':str(run_id),'reason':str(reason)[:64],'request_id':str(request_id)[:128]}
    client.workflow_dispatch('adwf-control.yml',ref,inputs)
    return {'status':'DISPATCHED','repository':repo,'ref':ref,'run_id':run_id,'reason':reason,'credential_source':source}
