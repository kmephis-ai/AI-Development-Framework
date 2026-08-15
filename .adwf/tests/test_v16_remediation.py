import json,shutil,subprocess,sys,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.action_executors import ActionExecutorRegistry
from lib.durable_orchestrator import PHASES,new_run,OrchestrationJournal,validate_journal
from lib.trust_boundary import classify_changed_files
from lib.github_rulesets import canonical_ruleset_payload,verify_rulesets,runtime_anchor_ruleset_payload,verify_runtime_anchor_ruleset
from lib.github_runtime_store import public_state_projection
from lib.owner_intent_service import start_or_queue
from lib.work_memory import WorkMemoryStore
from lib.pack_materializer import materialize_project_pack
from lib.delivery_adapters import promote_reference,observe_reference,run_command_adapter
from lib.performance_evidence import assess_performance
from lib.release_transaction import verify_internal_version,plan_auto_release,prepare_version_bump
from scripts.publish_trusted_gate import evaluate_trusted_gate
from scripts.collect_preview_attestation import collect as collect_preview_attestation
import base64,hashlib
from unittest import mock
from lib.runtime_supervisor import RuntimeSupervisor

class GateClient:
    def __init__(self,files,approved=False):
        self.files=files;self.approved=approved;self.repo='o/r'
        self.policy=json.loads((ROOT/'.adwf/policies/trust-boundary.json').read_text(encoding='utf-8'))
    def get(self,path):return {'id':1,'head_sha':'a'*40,'name':'ADWF PR','event':'pull_request','status':'completed','conclusion':'success','pull_requests':[{'number':7}]}
    def check_runs(self,sha):return [{'name':'fast-feedback','head_sha':sha,'status':'completed','conclusion':'success','app':{'slug':'github-actions','id':123}}]
    def pull(self,n):return {'number':n,'base':{'sha':'b'*40,'ref':'main'},'head':{'sha':'a'*40},'user':{'login':'author'}}
    def pull_files(self,n):return [{'filename':x,'status':'modified'} for x in self.files]
    def content(self,path,ref=None):
        text=json.dumps(self.policy) if path=='.adwf/policies/trust-boundary.json' else 'guard = True\n'
        return {'type':'file','encoding':'base64','content':base64.b64encode(text.encode()).decode()}
    def git_ref(self,branch):return {'object':{'sha':'b'*40}}
    def pull_reviews(self,n):return [{'state':'APPROVED','commit_id':'a'*40,'user':{'login':'owner'},'id':2}] if self.approved else []
    def collaborator_permission(self,login):return {'permission':'admin'}

class V16RemediationTests(unittest.TestCase):
    def test_cross_platform_generated_paths_are_posix(self):
        from lib.policy_compiler import compile_policy
        compiled, errors = compile_policy(ROOT)
        self.assertEqual(errors, [])
        self.assertTrue(all("\\" not in item["path"] for item in compiled["sources"]))

    def test_gitattributes_is_integrity_protected_support(self):
        from lib.trust import classify_diff
        policy = {
            "paths": [".adwf/**", ".github/workflows/adwf-*.yml"],
            "weakening_is_risk": "R4",
            "weakening_requires_human": True,
            "self_modification_in_feature_pr": "FORBIDDEN",
        }
        result = classify_diff(
            [{
                "path": ".gitattributes",
                "status": "A",
                "old_text": None,
                "new_text": "* text=auto eol=lf\n",
            }],
            policy,
        )
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertEqual(result["feature_files"], [])
        self.assertIn(".gitattributes", result["protected_files"])

    def test_repository_text_line_endings_are_canonicalized(self):
        attrs=(ROOT/'.gitattributes').read_text(encoding='utf-8')
        self.assertIn('* text=auto eol=lf',attrs)
        for rel in ('.adwf/docs-registry.json','.adwf/effective-policy.json','MANIFEST.json','SHA256SUMS.txt'):
            self.assertNotIn(b'\r\n',(ROOT/rel).read_bytes(),rel)
    def test_every_durable_phase_has_one_canonical_executor(self):
        self.assertEqual(set(ActionExecutorRegistry(ROOT).phases()),set(PHASES))

    def test_trust_boundary_change_cannot_self_attest(self):
        c=GateClient(['.github/workflows/adwf-pr.yml'],approved=False)
        r=evaluate_trusted_gate(c,'o/r',{'id':1,'head_sha':'a'*40})
        self.assertIn('TRUST_BOUNDARY_CHANGE_NOT_AUTHORIZED',r['reasons']);self.assertTrue(r['governance']['required'])
        c=GateClient(['.github/workflows/adwf-pr.yml'],approved=True);r=evaluate_trusted_gate(c,'o/r',{'id':1,'head_sha':'a'*40})
        self.assertEqual(r['reasons'],[]);self.assertTrue(r['governance']['verified'])

    def test_trust_boundary_classifier_covers_evaluators_policy_and_workflows(self):
        r=classify_changed_files(['.adwf/scripts/validate_ci.py','.adwf/policies/evidence.json','.github/workflows/adwf-pr.yml','src/app.py'])
        self.assertTrue(r['trust_boundary_changed']);self.assertGreaterEqual(len(r['trust_boundary_files']),3)

    def test_ruleset_rejects_bypass_and_wrong_check_source(self):
        good={'id':1,**canonical_ruleset_payload(integration_id=123)}
        self.assertTrue(verify_rulesets([good],expected_integration_id=123)['readback_verified'])
        bad=json.loads(json.dumps(good));bad['bypass_actors']=[{'actor_id':1,'actor_type':'RepositoryRole','bypass_mode':'always'}]
        self.assertFalse(verify_rulesets([bad],expected_integration_id=123)['readback_verified'])
        self.assertFalse(verify_rulesets([good],expected_integration_id=999)['readback_verified'])

    def test_runtime_anchor_ruleset_is_no_bypass_and_immutable(self):
        r={'id':2,**runtime_anchor_ruleset_payload()};v=verify_runtime_anchor_ruleset([r]);self.assertTrue(v['readback_verified'])
        self.assertEqual(r['bypass_actors'],[]);self.assertEqual({x['type'] for x in r['rules']},{'deletion','update'})

    def test_new_task_during_active_run_queues_without_mutating_active_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'.adwf',root/'.adwf',ignore=shutil.ignore_patterns('__pycache__','tests'))
            a=start_or_queue(root,'Сделай первый экран',wake=False);mem1=WorkMemoryStore(root).load();b=start_or_queue(root,'Сделай второй экран',wake=False);mem2=WorkMemoryStore(root).load()
            self.assertEqual(a['status'],'AUTOPILOT_STARTED');self.assertEqual(b['status'],'QUEUED_NEW_TASK');self.assertEqual(mem1['brief_id'],mem2['brief_id']);self.assertNotEqual(b['brief']['brief_id'],mem2['brief_id'])

    def test_public_runtime_projection_strips_arbitrary_private_text(self):
        state={'schema_version':1,'run_id':'run-12345678','roadmap_id':'B1','issue_id':'1','risk':'R1','work_type':'feature','product_impact':True,'owner_request_digest':'a'*64,'phase':'RECONCILE','status':'RUNNING','cycle':0,'subject_sha':None,'preview_digest':None,'owner_acceptance_sha':None,'delivery_sha':None,'pull_request_number':None,'preview_attestation_id':None,'work_branch':None,'policy_hash':'b'*64,'attempts':{},'max_attempts':3,'max_cycles':10,'deadline_at':'2099-01-01T00:00:00Z','last_failed_phase':None,'blockers':['customer secret words','SAFE_CODE'],'monetary_budget_usd':0,'events':[{'sequence':1,'event_id':'e','idempotency_key':'abcdefgh','phase':'RECONCILE','outcome':'PASS','subject_sha':None,'evidence_refs':[],'reason_codes':[],'decision':{'result':'ALLOW','reason_codes':[],'policy_hash':'b'*64},'cost_usd':0,'metadata':{'stderr_tail':'private secret','issue_id':'1'},'occurred_at':'2026-08-14T00:00:00Z','previous_event_hash':None,'event_hash':'old'}],'event_head':'old','revision':1,'created_at':'2026-08-14T00:00:00Z','updated_at':'2026-08-14T00:00:00Z'}
        p=public_state_projection(state);blob=json.dumps(p,ensure_ascii=False)
        self.assertNotIn('customer secret words',blob);self.assertNotIn('private secret',blob);self.assertIn('SAFE_CODE',blob);self.assertEqual(validate_journal(p),[])

    def test_project_pack_materializes_canonical_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);framework=base/'fw';project=base/'product';shutil.copytree(ROOT/'.adwf',framework/'.adwf',ignore=shutil.ignore_patterns('__pycache__','tests'));project.mkdir()
            (project/'package.json').write_text(json.dumps({'dependencies':{'react':'19.0.0'},'scripts':{'build':'vite build','dev':'vite','test':'echo ok'}}));(project/'package-lock.json').write_text('{}')
            r=materialize_project_pack(project,framework,apply=True);self.assertEqual(r['status'],'APPLIED');cfg=json.loads((framework/'.adwf/config.json').read_text());self.assertEqual(cfg['project_packs']['selected'],'react');self.assertTrue(cfg['project_packs']['materialized']);self.assertTrue(cfg['project_packs']['preview'])
            r2=materialize_project_pack(project,framework,apply=False);self.assertEqual(r2['status'],'ALREADY_MATERIALIZED')

    def test_reference_delivery_promotes_exact_revision_and_observes_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'examples',root/'examples');sha='a'*40
            a=promote_reference(root,sha);o=observe_reference(root,sha);self.assertEqual(o['status'],'PASS');self.assertEqual(o['source_sha'],sha);self.assertEqual(o['artifact_digest'],a['artifact_digest']);self.assertFalse(o['production_verified'])
            (root/a['deployment_path']/'index.html').write_text('tampered');self.assertEqual(observe_reference(root,sha)['status'],'FAIL')

    def test_command_delivery_requires_structured_exact_revision_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);script=root/'adapter.py';sha='a'*40
            script.write_text("import json,os,pathlib;pathlib.Path(os.environ['ADWF_ADAPTER_ATTESTATION']).write_text(json.dumps({'status':'PASS','subject_sha':os.environ['ADWF_SUBJECT_SHA'],'artifact_digest':'b'*64,'provider_readback':True,'evidence_refs':['github-check:1'],'readback_id':'dep-1'}))")
            out=run_command_adapter(f'{sys.executable} {script}',root,sha,timeout=10,kind='promotion');self.assertEqual(out['status'],'PASS');self.assertEqual(out['subject_sha'],sha);self.assertTrue(out['provider_readback'])
            script.write_text("print('ok')")
            out=run_command_adapter(f'{sys.executable} {script}',root,sha,timeout=10,kind='promotion');self.assertEqual(out['status'],'NOT_VERIFIED');self.assertEqual(out['reason'],'DELIVERY_ATTESTATION_MISSING')

    def test_performance_evidence_covers_window_queue_cancellation_flake_and_groups(self):
        now=datetime.now(timezone.utc);runs=[]
        for i in range(30):
            q=now-timedelta(minutes=40-i);st=q+timedelta(seconds=2);end=st+timedelta(seconds=20);sup=i<10
            runs.append({'queued_at':q.isoformat().replace('+00:00','Z'),'started_at':st.isoformat().replace('+00:00','Z'),'completed_at':end.isoformat().replace('+00:00','Z'),'first_failure_at':(st+timedelta(seconds=5)).isoformat().replace('+00:00','Z') if i==29 else None,'flaky':False,'conclusion':'CANCELLED' if sup else ('FAIL' if i==29 else 'PASS'),'superseded':sup})
        payload={'observed_at':now.isoformat().replace('+00:00','Z'),'window_days':30,'project_pack':'python','runs':runs,'groups':{'product':runs}}
        e=assess_performance(payload,now=now);self.assertEqual(e['status'],'PASS');self.assertEqual(e['metrics']['superseded_cancellation_rate']['status'],'PASS');self.assertEqual(e['metrics']['p95_queue_seconds']['status'],'PASS');self.assertEqual(e['window_days'],30);self.assertIn('product',e['per_impact']);self.assertIn('python',e['per_pack']);self.assertEqual(e['grouping_status'],'VERIFIED')

    def test_auto_release_is_version_bump_transaction_not_mismatched_archive(self):
        current=verify_internal_version(ROOT);self.assertEqual(current['status'],'VERIFIED');plan=plan_auto_release(ROOT,[{'impact':'fix'}]);self.assertEqual(plan['status'],'VERSION_BUMP_REQUIRED');self.assertEqual(plan['proposed_version'],'1.6.1')
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'repo';shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__','.adwf-runtime','dist'))
            prepared=prepare_version_bump(target,'1.6.1');self.assertEqual(prepared['status'],'VERSION_BUMP_PREPARED');self.assertEqual(verify_internal_version(target,'1.6.1')['status'],'VERIFIED');self.assertFalse(any(target.glob('AI-Development-Framework-v1.6.1.zip')))

    def test_pipeline_is_generated_and_legacy_controller_not_used(self):
        r=subprocess.run([sys.executable,str(ROOT/'.adwf/scripts/generate_pipeline.py'),'--check'],cwd=ROOT,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        control=(ROOT/'.github/workflows/adwf-control.yml').read_text();self.assertNotIn('orchestrate_event.py',control);self.assertIn('run_active_supervisor.py',control);self.assertIn('pull_request_review',control)

    def test_windows_and_linux_functional_smoke_are_mandatory_matrix(self):
        w=(ROOT/'.github/workflows/adwf-platform-smoke.yml').read_text();self.assertIn('ubuntu-24.04',w);self.assertIn('windows-2022',w);self.assertIn('platform_smoke.py',w)

    def test_owner_acceptance_forces_fresh_provider_readback_even_when_sha_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'.adwf',root/'.adwf',ignore=shutil.ignore_patterns('__pycache__','tests'));(root/'.adwf-runtime/assurance').mkdir(parents=True);sha='a'*40
            (root/'.adwf-runtime/assurance/current.json').write_text(json.dumps({'subject_sha':sha}))
            (root/'.adwf-runtime/provider-readback.json').write_text(json.dumps({'subject_sha':sha}))
            sup=RuntimeSupervisor(root)
            with mock.patch('lib.runtime_supervisor.detect_repository',return_value='o/r'),mock.patch('lib.runtime_supervisor.discover_token',return_value=('t','test')),mock.patch('lib.runtime_supervisor.subprocess.run') as run:
                run.return_value=type('R',(),{'returncode':0,'stdout':'','stderr':''})()
                sup._refresh_trusted_inputs(sha,force=True);self.assertEqual(run.call_count,1)
                run.reset_mock();sup._refresh_trusted_inputs(sha,force=False);self.assertEqual(run.call_count,0)

    def test_preview_marker_crosses_untrusted_job_to_trusted_controller_by_exact_run(self):
        sha='a'*40;source={'mode':'LOCAL_EXACT_GIT_HEAD','verified':True,'head_sha':sha,'tree_sha':'b'*40};digest='c'*64
        aid=hashlib.sha256((sha+digest+json.dumps(source,sort_keys=True)).encode()).hexdigest()
        marker={'schema_version':1,'head_sha':sha,'preview_digest':digest,'attestation_id':aid,'source_attestation':source,'runtime_environment':{'playwright_version':'1.62.0'},'screenshot_digests':['d'*64],'accessibility_status':'PASS'}
        encoded=base64.urlsafe_b64encode(json.dumps(marker,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
        class C:
            def check_runs(self,head):return [{'name':n,'head_sha':head,'status':'completed','conclusion':'success','app':{'slug':'github-actions','id':123}} for n in ('fast-feedback','adwf/governance-gate','adwf/trusted-gate')]
            def jobs(self,rid):return [{'id':22,'name':'fast-feedback','status':'completed','conclusion':'success'}]
            def job_logs(self,jid):return ('line\nADWF_PREVIEW_ATTESTATION_V1='+encoded+'\n').encode()
        event={'workflow_run':{'id':11,'name':'ADWF PR','event':'pull_request','status':'completed','conclusion':'success','head_sha':sha}}
        runtime=ROOT/'.adwf-runtime';runtime.mkdir(exist_ok=True)
        provider=runtime/'provider-readback.json';provider.write_text(json.dumps({'evidence_refs':['github-check:1']}))
        try:
            r=collect_preview_attestation(C(),event);self.assertEqual(r['status'],'VERIFIED')
            att=json.loads((runtime/'preview-attestation.json').read_text());self.assertEqual(att['head_sha'],sha);self.assertTrue(att['provider_attestation']['provider_readback'])
        finally:
            for f in (provider,runtime/'preview-attestation.json'):
                if f.exists():f.unlink()

    def test_capability_claims_are_machine_traceable_to_production_paths(self):
        r=subprocess.run([sys.executable,str(ROOT/'.adwf/scripts/validate_capabilities.py')],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        trace=json.loads((ROOT/'.adwf/capability-traceability.json').read_text(encoding='utf-8'))
        by_id={x['id']:x for x in trace['capabilities']}
        self.assertEqual(by_id['TRUSTED_GATE']['status'],'IMPLEMENTED')
        self.assertEqual(by_id['WINDOWS_HOSTED_SMOKE']['status'],'LIVE_NOT_VERIFIED')
        self.assertEqual(by_id['DURABLE_FULL_LOOP']['status'],'LIVE_NOT_VERIFIED')
        self.assertEqual(by_id['DURABLE_FULL_LOOP']['execution_mode'],'OPTIONAL_ADAPTER')

if __name__=='__main__':unittest.main()
