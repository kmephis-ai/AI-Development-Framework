#!/usr/bin/env python3
"""Cross-platform functional smoke: import CLI, start localhost portal, GET /, stop."""
from __future__ import annotations
from pathlib import Path
import os,subprocess,sys,time,urllib.request
ROOT=Path(__file__).resolve().parents[2]

def main()->int:
    env=dict(os.environ); env['PYTHONDONTWRITEBYTECODE']='1'
    help_run=subprocess.run([sys.executable,str(ROOT/'.adwf/adwf.py'),'--help'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    if help_run.returncode!=0:
        print(help_run.stderr); return 1
    port=18765
    proc=subprocess.Popen([sys.executable,str(ROOT/'.adwf/adwf.py'),'dashboard','serve','--bind','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.time()+20; body=''
        while time.time()<deadline:
            if proc.poll() is not None:
                out,err=proc.communicate(timeout=2); print(out); print(err); return 1
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=1) as response:
                    body=response.read().decode('utf-8');
                    if response.status==200: break
            except OSError: time.sleep(.25)
        else: return 1
        required=['ADWF v1.6 Executive Portal','ПРОДОЛЖИТЬ','Дорожная карта']
        missing=[x for x in required if x not in body]
        if missing:
            print('PORTAL_CONTENT_MISSING:'+','.join(missing)); return 1
        print('PLATFORM SMOKE: PASS'); return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill()
if __name__=='__main__': raise SystemExit(main())
