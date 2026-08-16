#!/usr/bin/env python3
"""Validate every built-in Project Pack against the formal SDK contract."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.project_packs import load_packs, ProjectPackError

def main()->int:
    try:
        packs=load_packs(ROOT)
    except ProjectPackError as exc:
        print('PROJECT PACK SDK: FAIL')
        print('- '+str(exc))
        return 1
    print(f'PROJECT PACK SDK: PASS; packs={len(packs)}')
    for name in sorted(packs):
        print(f'- {name}: {packs[name]["digest"]}')
    return 0

if __name__=='__main__': raise SystemExit(main())
