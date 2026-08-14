#!/usr/bin/env python3
"""Emit deterministic CI impact flags from exact base/head diff."""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.impact_router import route_paths

def changed(base:str|None,head:str|None)->list[str]:
    if base and head:
        p=subprocess.run(['git','diff','--name-only',base,head],cwd=ROOT,capture_output=True,text=True,check=False)
        if p.returncode==0:return [x for x in p.stdout.splitlines() if x.strip()]
    return []
def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--base');p.add_argument('--head');p.add_argument('--paths-json');p.add_argument('--github-output');args=p.parse_args()
    paths=json.loads(Path(args.paths_json).read_text()) if args.paths_json else changed(args.base,args.head);result=route_paths(paths)
    output_path=args.github_output or os.environ.get('GITHUB_OUTPUT')
    if output_path:
        with open(output_path,'a',encoding='utf-8') as h:
            for key in ('framework','docs','ui','provider','trust','full_framework','preview'):h.write(f"{key}={'true' if result[key] else 'false'}\n")
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
