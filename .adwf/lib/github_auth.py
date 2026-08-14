"""Local GitHub authentication discovery for Executive bootstrap.

No credential is persisted by ADWF. Prefer process environment or an already
logged-in GitHub CLI credential store. The token value is never returned in
human-facing status objects.
"""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse
import os,re,shutil,subprocess


def detect_repository(root:str|Path)->str|None:
    explicit=os.environ.get('GITHUB_REPOSITORY')
    if explicit and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',explicit): return explicit
    if not shutil.which('git'): return None
    p=subprocess.run(['git','config','--get','remote.origin.url'],cwd=Path(root),capture_output=True,text=True,check=False,timeout=5)
    if p.returncode: return None
    value=p.stdout.strip()
    patterns=(r'^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$',r'^https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$')
    for pattern in patterns:
        m=re.match(pattern,value)
        if m:return m.group(1).removesuffix('.git')
    return None


def discover_token()->tuple[str|None,str]:
    for key in ('GITHUB_TOKEN','GH_TOKEN'):
        value=os.environ.get(key)
        if value:return value,f'ENV:{key}'
    if not shutil.which('gh'):return None,'NOT_CONFIGURED'
    p=subprocess.run(['gh','auth','token','--hostname','github.com'],capture_output=True,text=True,check=False,timeout=10)
    token=p.stdout.strip() if p.returncode==0 else ''
    return (token,'OS_CREDENTIAL_STORE:GH_CLI') if token else (None,'GH_CLI_NOT_AUTHENTICATED')


def auth_status(root:str|Path)->dict[str,str|bool|None]:
    repo=detect_repository(root);token,source=discover_token()
    return {'repository':repo,'authenticated':bool(token),'credential_source':source,'token_persisted_by_adwf':False}
