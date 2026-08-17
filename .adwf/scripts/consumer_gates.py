#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'.adwf'))
from lib.consumer_gates import load_binding

def main()->int:
 p=argparse.ArgumentParser(description='Validate ADWF Consumer Native Gate Binding v1'); p.add_argument('--project-root',default=str(ROOT)); a=p.parse_args()
 try: b=load_binding(Path(a.project_root),ROOT)
 except Exception as e: print('CONSUMER NATIVE GATES: BLOCKED:'+str(e)); return 1
 print(json.dumps({'status':'VALID','repository':b['consumer_repository'],'required_phases':b['required_phases'],'mutation_authority':b['mutation_authority']},ensure_ascii=False,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
