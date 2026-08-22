#!/usr/bin/env python3
"""Fail-closed consistency + generated-projection check for Pipeline IR v1.6."""
from __future__ import annotations
from pathlib import Path
import io,json,os,subprocess,sys,tarfile,tempfile
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.strict_json import load
from lib.docs_freshness import source_digest


def _emit_docs_projection()->None:
    event_path=os.environ.get('GITHUB_EVENT_PATH','')
    if not event_path or not Path(event_path).is_file():return
    event=json.loads(Path(event_path).read_text(encoding='utf-8'))
    base_sha=str(((event.get('pull_request') or {}).get('base') or {}).get('sha') or '')
    if len(base_sha)!=40:return
    archive=subprocess.run(['git','-C',str(ROOT),'archive','--format=tar','HEAD'],capture_output=True,check=False)
    if archive.returncode:raise SystemExit('DOCS_PROJECTION_ARCHIVE_FAILED')
    base_file=subprocess.run(['git','-C',str(ROOT),'show',f'{base_sha}:.adwf/scripts/validate_pipeline_ir.py'],capture_output=True,check=False)
    if base_file.returncode:raise SystemExit('DOCS_PROJECTION_BASE_READ_FAILED')
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout),mode='r:') as bundle:
            bundle.extractall(target,filter='data')
        (target/'.adwf/scripts/validate_pipeline_ir.py').write_bytes(base_file.stdout)
        registry=load(target/'.adwf/docs-registry.json')
        changed={}
        for item in registry.get('documents',[]):
            digest=source_digest(target,item.get('watched') or [])
            if digest!=item.get('source_digest'):changed[str(item.get('path') or '')]=digest
        print('ADWF_DOCS_DIGEST_PROJECTION='+json.dumps(changed,sort_keys=True,separators=(',',':')))


def main()->int:
    _emit_docs_projection()
    errors=[];ir=load(ROOT/'.adwf/pipeline-ir.json');cfg=load(ROOT/'.adwf/config.json');profile=load(ROOT/'.adwf/profiles/FREE_PUBLIC_GITHUB.json');req=load(ROOT/'.adwf/requests/github-public-standard.json')
    pairs=[(cfg.get('framework_version'),ir.get('framework_version'),'VERSION'),(cfg.get('profile'),ir.get('profile'),'PROFILE'),(cfg.get('provider',{}).get('mode'),ir.get('provider'),'PROVIDER'),(cfg.get('project',{}).get('repository_visibility'),ir.get('repository_visibility'),'VISIBILITY'),(cfg.get('ci',{}).get('default_executor'),ir.get('runner',{}).get('executor'),'EXECUTOR'),(cfg.get('ci',{}).get('hosted_runner'),ir.get('runner',{}).get('label'),'RUNNER')]
    for a,b,label in pairs:
        if a!=b:errors.append(f'{label}_DRIFT:{a!r}!={b!r}')
    if cfg.get('cost',{}).get('monetary_budget')!=0 or ir.get('cost',{}).get('projected_cost_usd')!=0:errors.append('ZERO_BUDGET_DRIFT')
    if cfg.get('ci',{}).get('larger_runners_allowed') is not False or ir.get('runner',{}).get('larger_runners')!='BLOCK':errors.append('LARGER_RUNNER_NOT_BLOCKED')
    if profile.get('runner')!='ubuntu-24.04' or profile.get('monetary_budget')!=0:errors.append('PROFILE_DRIFT')
    if req.get('runner')!='ubuntu-24.04' or req.get('provider')!='github_public_standard' or req.get('projected_cost')!=0:errors.append('PROVIDER_REQUEST_DRIFT')
    expected=ir.get('required_checks') or [];actual=cfg.get('github',{}).get('trust',{}).get('required_check_names') or []
    if expected!=actual:errors.append('REQUIRED_CHECKS_DRIFT')
    if expected!=['fast-feedback','adwf/governance-gate','adwf/trusted-gate']:errors.append('GOVERNANCE_GATE_REQUIRED')
    durability=ir.get('durability',{})
    if durability.get('hosted_runtime_store')!='GITHUB_PUBLIC_SAFE_PROJECTION' or durability.get('private_work_memory')!='LOCAL_OR_OWNER_CONTROLLED':errors.append('DURABILITY_PRIVACY_DRIFT')
    if ir.get('executors',{}).get('all_phases_registered') is not True:errors.append('EXECUTOR_REGISTRY_DRIFT')
    generated=subprocess.run([sys.executable,str(ROOT/'.adwf/scripts/generate_pipeline.py'),'--check'],cwd=ROOT,text=True,capture_output=True,check=False)
    if generated.returncode:errors.append('PIPELINE_GENERATED_PROJECTIONS_STALE')
    control=(ROOT/'.github/workflows/adwf-control.yml').read_text(encoding='utf-8');pr=(ROOT/'.github/workflows/adwf-pr.yml').read_text(encoding='utf-8')
    if 'orchestrate_event.py' in control:errors.append('LEGACY_ORCHESTRATOR_IN_PRODUCTION_PATH')
    if 'consume_agent_result.py' not in control or 'run_active_supervisor.py' not in control:errors.append('FULL_LOOP_WIRING_MISSING')
    if 'run_preview.py' not in pr:errors.append('EXACT_PREVIEW_RUNNER_MISSING')
    if 'collect_preview_attestation.py' not in control:errors.append('TRUSTED_PREVIEW_READBACK_MISSING')
    if errors:
        print('PIPELINE IR: FAIL');[print('-',e) for e in errors];return 1
    print('PIPELINE IR: PASS');return 0
if __name__=='__main__':raise SystemExit(main())
