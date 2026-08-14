"""Built-in project packs: deterministic stack detection and reusable commands."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
from .strict_json import loads as strict_loads

PACK_ORDER=("react","vue","angular","fastapi","node","python","go")


def load_packs(root: str | Path) -> dict[str, dict[str, Any]]:
    base=Path(root).resolve()/".adwf"/"packs"; out={}
    for path in sorted(base.glob("*.json")):
        value=strict_loads(path.read_text(encoding="utf-8"))
        if value.get("id") != path.stem: raise ValueError(f"PROJECT_PACK_ID_MISMATCH:{path.name}")
        out[path.stem]=value
    return out


def _package(root: Path)->dict[str,Any]:
    p=root/"package.json"
    if not p.is_file(): return {}
    try:
        value=strict_loads(p.read_text(encoding="utf-8")); return value if isinstance(value,dict) else {}
    except (ValueError,json.JSONDecodeError): return {}


def detect_pack(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    root=Path(project_root).resolve(); packs=load_packs(framework_root); package=_package(root)
    deps={**(package.get("dependencies") or {}),**(package.get("devDependencies") or {})}
    candidates=[]
    if "react" in deps: candidates.append("react")
    if "vue" in deps: candidates.append("vue")
    if "@angular/core" in deps: candidates.append("angular")
    if (root/"pyproject.toml").is_file() or (root/"requirements.txt").is_file():
        text=""
        for p in (root/"pyproject.toml",root/"requirements.txt"):
            if p.is_file(): text += p.read_text(encoding="utf-8",errors="ignore").lower()
        if "fastapi" in text: candidates.append("fastapi")
        candidates.append("python")
    if package: candidates.append("node")
    if (root/"go.mod").is_file(): candidates.append("go")
    chosen=next((name for name in PACK_ORDER if name in candidates and name in packs),None)
    return {"pack":chosen,"candidates":list(dict.fromkeys(candidates)),"confidence":"HIGH" if chosen else "LOW","definition":packs.get(chosen) if chosen else None}


def commands_for_pack(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    detected=detect_pack(project_root,framework_root); definition=detected.get("definition") or {}
    commands=json.loads(json.dumps(definition.get("commands") or {}))
    project=Path(project_root).resolve(); package=_package(project); scripts=package.get("scripts") or {}
    # Never invent a Node command that the project does not expose.
    for name, entry in list(commands.items()):
        script=entry.get("requires_script")
        required_file=entry.get('requires_file')
        if script and script not in scripts:
            entry["available"]=False
        elif required_file and not (project/str(required_file)).is_file():
            entry["available"]=False
        else:
            entry["available"]=True
    return {**detected,"commands":commands,"preview":definition.get("preview") or {}}
