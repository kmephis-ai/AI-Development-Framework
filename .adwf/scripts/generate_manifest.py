#!/usr/bin/env python3
"""Deterministic integrity manifest for framework-owned trust boundary only.

Product files are intentionally excluded: changing application code must not
force a full ADWF manifest rewrite or make package integrity false-red.
"""
from __future__ import annotations
from pathlib import Path
import argparse,base64,hashlib,json,os,tempfile,zlib
ROOT=Path(__file__).resolve().parents[2]
EXCLUDED_PARTS={"__pycache__",".adwf-runtime","migrations","dist","node_modules"}
EXCLUDED_NAMES={"MANIFEST.json","SHA256SUMS.txt"}
ROOT_FILES={"VERSION","README.md","ADWS.md","AGENTS.md","SECURITY.md","SPECIFICATION.md","INSTALL.md","LICENSE_DECISION_REQUIRED.md","START_ADWF.bat","START_ADWF.sh","CHANGELOG.md","CONTROL_CENTER.md","CONTROL_CENTER.html","labels.json",".gitlab-ci.yml",".gitignore",".gitattributes"}

def digest(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def is_framework_owned(root:Path,path:Path)->bool:
    rel=path.relative_to(root); text=str(rel).replace('\\','/')
    if text.startswith('.adwf/'): return True
    if text.startswith('.github/workflows/adwf-'): return True
    if text.startswith('.github/ISSUE_TEMPLATE/') or text=='.github/pull_request_template.md': return True
    if text.startswith('examples/reference-app/'): return True
    if text in ROOT_FILES: return True
    framework_doc_prefixes=('docs/governance/','docs/framework/','docs/architecture/','docs/operations/','docs/migration/','docs/decisions/','docs/templates/')
    if text.startswith(framework_doc_prefixes): return True
    if text.startswith('docs/QUICKSTART_') or text.startswith('docs/V1_') or text=='docs/REFERENCES.md': return True
    return False

def source_files(root:Path)->list[Path]:
    return sorted((path for path in root.rglob('*') if path.is_file() and is_framework_owned(root,path)
                  and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts) and path.name not in EXCLUDED_NAMES
                  and path.suffix!='.pyc' and not path.name.endswith('.lock')),
                  key=lambda item:item.relative_to(root).as_posix())
def expected_manifest(root:Path)->dict:
    files=source_files(root); version=(root/'VERSION').read_text(encoding='utf-8').strip()
    return {'framework':'AI Development Framework','version':version,'schema_version':3,'scope':'FRAMEWORK_OWNED_TRUST_BOUNDARY',
            'file_count_excluding_manifests':len(files),'total_bytes_excluding_manifests':sum(p.stat().st_size for p in files),'files':[p.relative_to(root).as_posix() for p in files]}
def expected_sums(root:Path,manifest_text:str|None=None)->str:
    files=source_files(root)
    manifest_digest=digest(root/'MANIFEST.json') if manifest_text is None else hashlib.sha256(manifest_text.encode('utf-8')).hexdigest()
    rows=[f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in files]
    rows.append(f"{manifest_digest}  MANIFEST.json\n")
    return ''.join(sorted(rows,key=lambda row:row.split('  ',1)[1]))
def atomic_text(path:Path,text:str)->None:
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as h:h.write(text);h.flush();os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def _repair_payload(manifest_text:str,sums_text:str)->str:
    raw=json.dumps({'MANIFEST.json':manifest_text,'SHA256SUMS.txt':sums_text},ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return base64.urlsafe_b64encode(zlib.compress(raw,9)).decode('ascii')
def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(ROOT));p.add_argument('--check',action='store_true');args=p.parse_args();root=Path(args.root).resolve()
    links=[x for x in root.rglob('*') if x.is_symlink() and is_framework_owned(root,x)]
    if links: print('MANIFEST CHECK: FAIL SYMLINKS_FORBIDDEN:'+','.join(str(x.relative_to(root)) for x in links));return 1
    manifest_text=json.dumps(expected_manifest(root),ensure_ascii=False,indent=2)+'\n';sums_text=expected_sums(root,manifest_text)
    if args.check:
        errors=[]
        if not (root/'MANIFEST.json').is_file() or (root/'MANIFEST.json').read_text(encoding='utf-8')!=manifest_text:errors.append('MANIFEST_STALE')
        if not (root/'SHA256SUMS.txt').is_file() or (root/'SHA256SUMS.txt').read_text(encoding='utf-8')!=sums_text:errors.append('SHA256SUMS_STALE')
        print('MANIFEST CHECK: '+('FAIL '+','.join(errors) if errors else 'PASS'))
        if errors: print('MANIFEST_REPAIR_ZLIB_B64:'+_repair_payload(manifest_text,sums_text))
        return 1 if errors else 0
    atomic_text(root/'MANIFEST.json',manifest_text);atomic_text(root/'SHA256SUMS.txt',sums_text);print('MANIFEST/SHA256SUMS updated');return 0
if __name__=='__main__':raise SystemExit(main())
