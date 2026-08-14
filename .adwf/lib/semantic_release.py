"""Deterministic SemVer proposal from accepted change impact."""
from __future__ import annotations
import re
from typing import Any
SEMVER=re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

def determine_bump(changes:list[dict[str,Any]])->str:
    if any(c.get("breaking") is True or str(c.get("impact",""))=="breaking" for c in changes): return "major"
    if any(str(c.get("impact","")) in {"feature","minor"} for c in changes): return "minor"
    return "patch"

def next_version(current:str,bump:str)->str:
    m=SEMVER.fullmatch(current.strip())
    if not m: raise ValueError("SEMVER_INVALID")
    a,b,c=map(int,m.groups())
    if bump=="major": return f"{a+1}.0.0"
    if bump=="minor": return f"{a}.{b+1}.0"
    if bump=="patch": return f"{a}.{b}.{c+1}"
    raise ValueError("SEMVER_BUMP_INVALID")

def release_plan(current:str,changes:list[dict[str,Any]])->dict[str,Any]:
    if not isinstance(changes,list) or not changes: raise ValueError("RELEASE_CHANGES_REQUIRED")
    bump=determine_bump(changes); version=next_version(current,bump)
    return {"status":"READY_FOR_OWNER_CONFIRMATION","current_version":current,"proposed_version":version,"bump":bump,
            "breaking_changes":sum(1 for c in changes if c.get('breaking') is True or c.get('impact')=='breaking'),
            "features":sum(1 for c in changes if c.get('impact') in {'feature','minor'}),
            "fixes":sum(1 for c in changes if c.get('impact') in {'fix','patch'}),"owner_confirmation_required":True}
