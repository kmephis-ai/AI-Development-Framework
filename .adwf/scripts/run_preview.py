#!/usr/bin/env python3
"""Start the exact checked-out project revision and capture Playwright preview."""
from __future__ import annotations
from pathlib import Path
import argparse,json,subprocess,sys,time,urllib.request,os,signal,base64
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.project_packs import commands_for_pack
from lib.preview_engine import capture_preview

def wait(url:str,timeout:int=60)->None:
    end=time.time()+timeout
    while time.time()<end:
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status<500:return
        except Exception:time.sleep(.5)
    raise ValueError('PREVIEW_SERVER_NOT_READY')

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--install-playwright',action='store_true');p.add_argument('--baseline-url');p.add_argument('--output');args=p.parse_args()
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();pack=commands_for_pack(ROOT,ROOT);definition=pack.get('definition') or {};commands=pack.get('commands') or {};preview=pack.get('preview') or {};start=commands.get('start') or {}
    command=start.get('command') if start.get('available') is True else None;url=preview.get('default_url')
    if not command or not url:print(json.dumps({'status':'NOT_APPLICABLE','reason':'PROJECT_PACK_HAS_NO_PREVIEW_START','pack':pack.get('pack')}));return 0
    install=commands.get('install') or {}
    if install.get('available') is True and install.get('command'):
        proc=subprocess.run(install['command'],cwd=ROOT,check=False)
        if proc.returncode:return proc.returncode
    server=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        wait(str(url));manifest=capture_preview(ROOT,url=str(url),head_sha=head,baseline_url=args.baseline_url,output_dir=args.output,install=args.install_playwright)
        marker={'schema_version':1,'head_sha':manifest['head_sha'],'preview_digest':manifest['preview_digest'],'attestation_id':manifest['attestation_id'],'source_attestation':manifest['source_attestation'],'runtime_environment':manifest['runtime_environment'],'screenshot_digests':[x.get('sha256') for x in manifest.get('screenshots') or []],'accessibility_status':(manifest.get('accessibility') or {}).get('status')}
        encoded=base64.urlsafe_b64encode(json.dumps(marker,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
        print(json.dumps(manifest,ensure_ascii=False,indent=2));print('ADWF_PREVIEW_ATTESTATION_V1='+encoded);return 0
    finally:
        if server.poll() is None:
            try:os.killpg(server.pid,signal.SIGTERM)
            except Exception:server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill()
if __name__=='__main__':raise SystemExit(main())
