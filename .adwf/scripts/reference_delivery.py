#!/usr/bin/env python3
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.delivery_adapters import promote_reference,observe_reference

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('action',choices=['promote','observe']);p.add_argument('--sha',required=True);a=p.parse_args()
    value=promote_reference(ROOT,a.sha) if a.action=='promote' else observe_reference(ROOT,a.sha)
    print(json.dumps(value,ensure_ascii=False,indent=2));return 0 if value.get('status','PASS')=='PASS' or a.action=='promote' else 1
if __name__=='__main__':raise SystemExit(main())
