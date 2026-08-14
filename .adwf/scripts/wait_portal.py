#!/usr/bin/env python3
"""Wait for the loopback Executive Portal and verify its identity before opening a browser."""
from __future__ import annotations
import argparse,time,urllib.request

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8765/');p.add_argument('--expect',default='ADWF v1.6 Executive Portal');p.add_argument('--timeout',type=float,default=15.0);args=p.parse_args()
    if not args.url.startswith(('http://127.0.0.1:','http://localhost:')):raise SystemExit('LOOPBACK_URL_REQUIRED')
    deadline=time.monotonic()+max(1.0,min(args.timeout,60.0));last='NOT_READY'
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(args.url,timeout=1.5) as r:
                body=r.read(200_000).decode('utf-8','replace')
                if r.status==200 and args.expect in body:return 0
                last='IDENTITY_MISMATCH'
        except Exception as exc:last=type(exc).__name__
        time.sleep(.25)
    print(f'ADWF_PORTAL_NOT_READY:{last}')
    return 1
if __name__=='__main__':raise SystemExit(main())
