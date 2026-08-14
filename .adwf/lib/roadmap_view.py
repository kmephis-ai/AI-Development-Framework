"""Executive roadmap projection: goals, dependencies and three truthful progress axes."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
from .strict_json import loads as strict_loads

IMPLEMENTED={"REVIEW","VERIFICATION","DONE"}; VERIFIED={"VERIFICATION","DONE"}; DONE={"DONE"}


def _load_template(root: Path)->dict[str,Any]:
    path=root/".adwf"/"roadmap.json"
    if not path.is_file(): return {"schema_version":1,"goals":[]}
    value=strict_loads(path.read_text(encoding="utf-8")); return value if isinstance(value,dict) else {"schema_version":1,"goals":[]}


def build_roadmap_view(root: str | Path, state: dict[str,Any]) -> dict[str,Any]:
    base=Path(root).resolve(); template=_load_template(base); items=state.get("work_items") or []
    by_id={str(i.get("roadmap_id") or i.get("id")):i for i in items}
    goals=[]; all_tasks=[]
    for goal in template.get("goals") or []:
        tasks=[]
        for t in goal.get("tasks") or []:
            rid=str(t.get("roadmap_id") or ""); live=by_id.get(rid,{})
            state_name=str(live.get("state") or t.get("state") or "PLANNED")
            task={"roadmap_id":rid,"title_ru":t.get("title_ru") or live.get("title") or rid,"state":state_name,
                  "dependencies":live.get("dependencies") or t.get("dependencies") or [],"issue":live.get("number"),
                  "product_impact":live.get("product_impact",t.get("product_impact",True))}
            tasks.append(task); all_tasks.append(task)
        goals.append({"id":goal.get("id"),"title_ru":goal.get("title_ru"),"tasks":tasks})
    known={t["roadmap_id"] for t in all_tasks}
    for live in items:
        rid=str(live.get("roadmap_id") or live.get("id") or "")
        if rid and rid not in known:
            task={"roadmap_id":rid,"title_ru":live.get("title") or rid,"state":live.get("state","PLANNED"),"dependencies":live.get("dependencies") or [],"issue":live.get("number"),"product_impact":live.get("product_impact",True)}
            if not goals: goals.append({"id":"live","title_ru":"Текущая дорожная карта","tasks":[]})
            goals[-1]["tasks"].append(task); all_tasks.append(task)
    total=len(all_tasks)
    def pct(states:set[str])->float:
        return round(sum(1 for t in all_tasks if t["state"] in states)/total,3) if total else 0.0
    blocked=sum(1 for t in all_tasks if t["state"] in {"BLOCKED","RECOVERY","HUMAN_REQUIRED"})
    active=next((t for t in all_tasks if t["state"] in {"IN_PROGRESS","REVIEW","VERIFICATION","RECOVERY"}),None)
    return {"schema_version":1,"goals":goals,"summary":{"total":total,"implemented":pct(IMPLEMENTED),"verified":pct(VERIFIED),"product_done":pct(DONE),"blocked":blocked,"active":active}}
