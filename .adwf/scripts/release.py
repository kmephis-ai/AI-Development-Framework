#!/usr/bin/env python3
"""ADWF v1.6 reproducible release transaction.

`--auto` never builds a differently-versioned archive from an unchanged source
tree. It plans a semantic version bump and, with `--prepare`, updates the
versioned source files for a normal PR/gate/owner-acceptance cycle. Publication
is a separate exact-internal-version step.
"""
from __future__ import annotations
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.release_transaction import plan_auto_release,prepare_version_bump,verify_internal_version
from lib.github_provider import GitHubClient
from scripts.generate_manifest import source_files


def build_zip(output:Path,version:str)->tuple[Path,str]:
    verified=verify_internal_version(ROOT,version)
    if verified['status']!='VERIFIED':raise ValueError('RELEASE_INTERNAL_VERSION_MISMATCH:'+','.join(verified['reason_codes']))
    output.mkdir(parents=True,exist_ok=True);target=output/f"AI-Development-Framework-v{version}.zip";prefix=f"AI-Development-Framework-v{version}/"
    if any(p.is_symlink() for p in ROOT.rglob('*') if p.is_file()):raise ValueError('SYMLINKS_FORBIDDEN_IN_RELEASE')
    files=source_files(ROOT)+[ROOT/'MANIFEST.json',ROOT/'SHA256SUMS.txt']
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(set(files)):
            info=zipfile.ZipInfo(prefix+str(path.relative_to(ROOT)).replace('\\','/'),date_time=(2026,8,14,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(0o755 if os.access(path,os.X_OK) else 0o644)<<16;archive.writestr(info,path.read_bytes())
    return target,hashlib.sha256(target.read_bytes()).hexdigest()


def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--version');p.add_argument('--auto',action='store_true');p.add_argument('--changes');p.add_argument('--prepare',action='store_true');p.add_argument('--confirm',action='store_true');p.add_argument('--output',default='dist');p.add_argument('--external',action='store_true');p.add_argument('--publish-github',action='store_true');args=p.parse_args()
    internal=verify_internal_version(ROOT)
    if internal['status']!='VERIFIED':print(json.dumps(internal,ensure_ascii=False,indent=2));return 5
    if args.auto:
        if not args.changes:raise SystemExit('AUTO_RELEASE_REQUIRES_CHANGES_FILE')
        payload=json.loads(Path(args.changes).read_text(encoding='utf-8'));changes=payload.get('changes') if isinstance(payload,dict) else None
        plan=plan_auto_release(ROOT,changes or []);print(json.dumps(plan,ensure_ascii=False,indent=2))
        if plan.get('status')!='VERSION_BUMP_REQUIRED':return 5
        if not args.prepare:return 6
        prepared=prepare_version_bump(ROOT,plan['proposed_version']);print(json.dumps(prepared,ensure_ascii=False,indent=2));return 6
    version=args.version.strip() if args.version else internal['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):raise SystemExit('INVALID_VERSION')
    if version!=internal['version']:raise SystemExit('VERSION_MISMATCH')
    if not args.confirm:return 6
    if args.external and not (ROOT/'LICENSE').is_file():raise SystemExit('EXTERNAL_RELEASE_BLOCKED: владелец не выбрал LICENSE')
    checks=[[sys.executable,str(ROOT/'.adwf/scripts/generate_manifest.py'),'--check'],[sys.executable,str(ROOT/'.adwf/scripts/validate_framework.py')],[sys.executable,str(ROOT/'.adwf/adwf.py'),'self-test'],[sys.executable,str(ROOT/'.adwf/adwf.py'),'doctor','--scope','package_integrity']]
    for command in checks:
        process=subprocess.run(command,cwd=ROOT,check=False)
        if process.returncode:return process.returncode
    archive,archive_sha=build_zip((ROOT/args.output).resolve(),version);checksum=archive.with_suffix(archive.suffix+'.sha256');checksum.write_text(f"{archive_sha}  {archive.name}\n",encoding='utf-8');print(f'BUILT {archive}');print(f'SHA256 {archive_sha}')
    if args.publish_github:
        token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN');repo=os.environ.get('GITHUB_REPOSITORY','')
        if not args.external or not token or '/' not in repo:raise SystemExit('PUBLISH_REQUIRES_EXTERNAL_AND_AUTHENTICATED_GITHUB_CONTEXT')
        if not shutil.which('gh') or not shutil.which('git'):raise SystemExit('GH_OR_GIT_CLI_MISSING')
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();tag=f'v{version}';client=GitHubClient(repo,token)
        # Create/read back the remote annotated tag through the authenticated provider API;
        # never rely on checkout credentials or an unauthenticated `git push`.
        try:
            ref=client.tag_ref(tag);target=str((ref.get('object') or {}).get('sha') or '')
            if not target:raise SystemExit('REMOTE_TAG_READBACK_INVALID')
        except Exception:
            tag_obj=client.create_tag_object(tag,head,f'ADWF {tag}');tag_sha=str(tag_obj.get('sha') or '')
            if not tag_sha:raise SystemExit('ANNOTATED_TAG_CREATE_READBACK_MISSING')
            ref=client.create_tag_ref(tag,tag_sha);target=str((ref.get('object') or {}).get('sha') or '')
            if target!=tag_sha:raise SystemExit('REMOTE_TAG_REF_READBACK_MISMATCH')
        command=['gh','release','create',tag,str(archive),str(checksum),'--verify-tag','--title',f'ADWF v{version}','--notes-file',str(ROOT/'CHANGELOG.md')]
        rc=subprocess.run(command,cwd=ROOT,check=False).returncode
        if rc:return rc
        release=client.release_by_tag(tag)
        if str(release.get('tag_name') or '')!=tag:raise SystemExit('GITHUB_RELEASE_READBACK_MISMATCH')
        print('GITHUB RELEASE READBACK VERIFIED',release.get('id'))
        return 0
    return 0
if __name__=='__main__':raise SystemExit(main())
