"""Two-phase semantic release transaction for ADWF v1.6.

The internal VERSION is the only release identity.  Preparing a new version
updates every trusted version-bearing projection before a normal PR/gate cycle;
publication refuses any cross-file drift.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json,re,subprocess,sys
from .semantic_release import release_plan
from .strict_json import loads as strict_loads

SEMVER=re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')
VERSION_FILES=('VERSION','.adwf/config.json','.adwf/pipeline-ir.json','.adwf/preview/package.json')
VERSIONED_JSON=(
 '.adwf/state-machine.json','.adwf/project-state.json','.adwf/project-layout.json','.adwf/release-state-machine.json',
 '.adwf/architecture-invariants.json','.adwf/debt-ledger.json','.adwf/baseline.json','.adwf/docs-registry.json',
 '.adwf/capabilities.json','.adwf/autonomy-matrix.json','.adwf/golden-paths.json','.adwf/state-label-map.json',
 '.adwf/reports/adversarial-results.json','.adwf/reports/release-evidence.json',
 '.adwf/policies/evidence.json','.adwf/policies/orchestration.json','.adwf/policies/reality.json',
 '.adwf/policies/roadmap-quality.json','.adwf/policies/trust-boundary.json',
)

def internal_versions(root:str|Path)->dict[str,str]:
    base=Path(root).resolve();values={'VERSION':(base/'VERSION').read_text(encoding='utf-8').strip()}
    values['.adwf/config.json']=str(strict_loads((base/'.adwf/config.json').read_text(encoding='utf-8')).get('framework_version') or '')
    values['.adwf/pipeline-ir.json']=str(strict_loads((base/'.adwf/pipeline-ir.json').read_text(encoding='utf-8')).get('framework_version') or '')
    values['.adwf/preview/package.json']=str(strict_loads((base/'.adwf/preview/package.json').read_text(encoding='utf-8')).get('version') or '')
    return values

def verify_internal_version(root:str|Path,expected:str|None=None)->dict[str,Any]:
    base=Path(root).resolve();versions=internal_versions(base);unique=set(versions.values());reasons=[]
    if len(unique)!=1:reasons.append('INTERNAL_VERSION_DIVERGENCE')
    version=versions['VERSION']
    if not SEMVER.fullmatch(version):reasons.append('INTERNAL_VERSION_INVALID')
    if expected is not None and version!=expected:reasons.append('EXPECTED_VERSION_MISMATCH')
    # Trusted policy/state version fields must agree too; they are part of the release identity.
    for rel in VERSIONED_JSON:
        path=base/rel
        if not path.is_file():continue
        try:value=strict_loads(path.read_text(encoding='utf-8'))
        except Exception:reasons.append('VERSIONED_JSON_UNREADABLE:'+rel);continue
        for field in ('version','framework_version','state_machine_version'):
            if field in value and str(value[field])!=version:reasons.append(f'VERSION_DRIFT:{rel}:{field}')
        if rel.endswith('release-evidence.json'):
            side=str(value.get('archive_checksum_sidecar') or '')
            if side and side!=f'AI-Development-Framework-v{version}.zip.sha256':reasons.append('RELEASE_EVIDENCE_SIDECAR_DRIFT')
    return {'status':'VERIFIED' if not reasons else 'BLOCK','version':version,'files':versions,'reason_codes':list(dict.fromkeys(reasons))}

def plan_auto_release(root:str|Path,changes:list[dict[str,Any]])->dict[str,Any]:
    current=verify_internal_version(root)
    if current['status']!='VERIFIED':return {'status':'BLOCK','reason_codes':current['reason_codes'],'internal':current}
    plan=release_plan(current['version'],changes)
    return {**plan,'status':'VERSION_BUMP_REQUIRED','current_version':current['version'],'transaction':'VERSION_BUMP_PR -> GATES -> OWNER_ACCEPTANCE -> MERGE -> BUILD_ONCE -> TAG -> RELEASE'}

def _bump_json(path:Path,old:str,new:str)->None:
    value=strict_loads(path.read_text(encoding='utf-8'))
    def walk(obj):
        if isinstance(obj,dict):
            for k,v in list(obj.items()):
                if k in {'version','framework_version','state_machine_version'} and v==old:obj[k]=new
                elif isinstance(v,(dict,list)):walk(v)
        elif isinstance(obj,list):
            for x in obj:walk(x)
    walk(value)
    if path.name=='release-evidence.json' and value.get('archive_checksum_sidecar')==f'AI-Development-Framework-v{old}.zip.sha256':
        value['archive_checksum_sidecar']=f'AI-Development-Framework-v{new}.zip.sha256'
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def prepare_version_bump(root:str|Path,new_version:str)->dict[str,Any]:
    if not SEMVER.fullmatch(str(new_version)):raise ValueError('INVALID_VERSION')
    base=Path(root).resolve();current=verify_internal_version(base)
    if current['status']!='VERIFIED':raise ValueError('INTERNAL_VERSION_DIVERGENCE:'+','.join(current['reason_codes']))
    old=current['version']
    if old==new_version:return {'status':'ALREADY_PREPARED','version':new_version,'files':list(VERSION_FILES)}
    (base/'VERSION').write_text(new_version+'\n',encoding='utf-8')
    for rel,key in (('.adwf/config.json','framework_version'),('.adwf/pipeline-ir.json','framework_version'),('.adwf/preview/package.json','version')):
        path=base/rel;value=strict_loads(path.read_text(encoding='utf-8'));value[key]=new_version;path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for rel in VERSIONED_JSON:
        path=base/rel
        if path.is_file():_bump_json(path,old,new_version)
    changelog=base/'CHANGELOG.md';text=changelog.read_text(encoding='utf-8') if changelog.is_file() else '# Changelog\n'
    marker=f'## v{new_version} — version bump transaction\n\n- Semantic version prepared by ADWF; release is not published until gates, owner acceptance and exact-SHA merge pass.\n\n'
    if marker not in text:
        first=text.find('\n') if text.startswith('#') else -1;text=(text[:first+1]+'\n'+marker+text[first+1:]) if first>=0 else marker+text;changelog.write_text(text,encoding='utf-8')
    # Effective Policy and workflows are deterministic projections of the newly versioned sources.
    subprocess.run([sys.executable,str(base/'.adwf/scripts/compile_policy.py'),'--write'],cwd=base,check=True)
    subprocess.run([sys.executable,str(base/'.adwf/scripts/generate_pipeline.py')],cwd=base,check=True)
    subprocess.run([sys.executable,str(base/'.adwf/scripts/generate_manifest.py')],cwd=base,check=True)
    verified=verify_internal_version(base,new_version)
    if verified['status']!='VERIFIED':raise ValueError('VERSION_BUMP_POSTCONDITION_FAILED:'+','.join(verified['reason_codes']))
    return {'status':'VERSION_BUMP_PREPARED','from_version':old,'version':new_version,'files':[ *VERSION_FILES,*VERSIONED_JSON,'CHANGELOG.md','.adwf/effective-policy.json','MANIFEST.json','SHA256SUMS.txt'],'release_published':False}
