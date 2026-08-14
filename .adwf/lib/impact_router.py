"""Cheap deterministic change-impact routing for fast CI."""
from __future__ import annotations
from pathlib import PurePosixPath
from typing import Iterable

FRAMEWORK_PREFIX=(".adwf/",".github/workflows/adwf-","START_ADWF.","ADWS.md","SPECIFICATION.md","SECURITY.md","INSTALL.md")
DOC_EXT={".md",".rst",".txt"}; UI_EXT={".html",".css",".scss",".sass",".less",".jsx",".tsx",".vue",".svelte"}
PROVIDER_HINT=("provider","github_reconcile","gitlab_reconcile","http_transport","ruleset")


def route_paths(paths: Iterable[str])->dict[str,bool|list[str]]:
    normalized=[str(PurePosixPath(str(p).replace("\\","/"))) for p in paths if str(p).strip()]
    framework=any(any(p==x.rstrip('/') or p.startswith(x) for x in FRAMEWORK_PREFIX) for p in normalized)
    docs=any(PurePosixPath(p).suffix.lower() in DOC_EXT or p.startswith("docs/") for p in normalized)
    ui=any(PurePosixPath(p).suffix.lower() in UI_EXT or any(part.lower() in {"ui","frontend","web","client","pages","components"} for part in PurePosixPath(p).parts) for p in normalized)
    provider=any(any(h in p.lower() for h in PROVIDER_HINT) for p in normalized)
    trust=any(p.startswith(".adwf/policies/") or p.startswith(".adwf/lib/policy") or p.startswith(".adwf/lib/trust") or p.startswith(".github/workflows/adwf-") for p in normalized)
    return {"paths":normalized,"framework":framework,"docs":docs,"ui":ui,"provider":provider,"trust":trust,"full_framework":framework or trust,"preview":ui}
