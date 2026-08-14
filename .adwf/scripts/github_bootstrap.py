#!/usr/bin/env python3
"""Owner-confirmed GitHub governance bootstrap. Default is readback/plan only."""
from __future__ import annotations
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.github_bootstrap import bootstrap_repository

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    result=bootstrap_repository(ROOT,apply=args.apply);print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('status') in {'VERIFIED','READY_TO_APPLY'} else 6
if __name__=='__main__':raise SystemExit(main())
