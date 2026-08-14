import json,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'.adwf'))
from lib.strict_json import loads,DuplicateKeyError
from lib.assurance import snapshot_digest,machine_verified
from lib.evidence_resolver import resolve_evidence_refs
from lib.provider_contracts import HttpResponse,ProviderContractError,request_json
from lib.trusted_context import compile_trusted_context
from lib.owner_portal import bootstrap_plan
from lib.dashboard import _safe_url,_cost_display

class V14TrustTests(unittest.TestCase):
    now=datetime(2026,8,13,12,tzinfo=timezone.utc)
    def assurance(self,sha='a'*40,policy_hash=None):
        if policy_hash is None: policy_hash=json.loads((ROOT/'.adwf/effective-policy.json').read_text())['policy_hash']
        x={'schema_version':1,'subject_sha':sha,'policy_hash':policy_hash,'verified_at':'2026-08-13T11:00:00Z','expires_at':'2026-09-13T11:00:00Z',
           'health':{'package_integrity':'VERIFIED','config_health':'VERIFIED','control_plane_health':'VERIFIED','product_health':'VERIFIED'},
           'required_gates':['ci'],'gates':{'ci':'PASS'},'cost':{'status':'VERIFIED_ZERO','projected_cost_usd':0},
           'evidence':{'refs_resolved':True},'provider':{'readback_verified':True}}
        x['snapshot_digest']=snapshot_digest(x); return x
    def test_duplicate_json_key_is_rejected(self):
        with self.assertRaises(DuplicateKeyError): loads('{"a":1,"a":2}')
    def test_assurance_not_state_strings_controls_verified(self):
        x=self.assurance(); self.assertEqual(machine_verified(x,expected_sha='a'*40),'VERIFIED')
        x['provider']['readback_verified']=False; x['snapshot_digest']=snapshot_digest(x); self.assertEqual(machine_verified(x,expected_sha='a'*40),'NOT_VERIFIED')
    def test_caller_cannot_self_attest_positive_facts(self):
        snap=self.assurance(); rb={'subject_sha':'a'*40,'readback_verified':True,'facts_readback_verified':True,'repository_visibility':'PUBLIC','runner':'ubuntu-24.04','larger_runner':False}
        with self.assertRaisesRegex(ValueError,'CALLER_POSITIVE_FACTS_FORBIDDEN'):
            compile_trusted_context(ROOT,action='merge',risk='R1',work_type='feature',request={'subject_sha':'a'*40,'health':'VERIFIED'},assurance_snapshot=snap,provider_readback=rb)
        ctx=compile_trusted_context(ROOT,action='verify',risk='R0',work_type='verification',request={'subject_sha':'a'*40,'request_id':'R1'},assurance_snapshot=snap,provider_readback=rb)
        self.assertEqual(ctx.expected_policy_hash,snap['policy_hash']); self.assertEqual(ctx.autonomy,'A2')
    def test_provider_contract_errors_and_bounded_retry(self):
        calls=[]
        def transport(method,url,headers,body,timeout):
            calls.append(1); return HttpResponse(429 if len(calls)==1 else 200,{'Retry-After':'0'},b'{"ok":true}')
        value,_=request_json(transport,'GET','https://api.example',{},max_attempts=2); self.assertTrue(value['ok']); self.assertEqual(len(calls),2)
        with self.assertRaisesRegex(ProviderContractError,'PROVIDER_HTTP_403'):
            request_json(lambda *a:HttpResponse(403,{},b'{}'),'GET','x',{})
        with self.assertRaisesRegex(ProviderContractError,'MALFORMED_JSON'):
            request_json(lambda *a:HttpResponse(200,{},b'{'),'GET','x',{})
    def test_evidence_requires_producer_and_external_anchor(self):
        record={'ref_id':'E1','subject_sha':'a'*40,'policy_hash':'b'*64,'artifact_digest':'c'*64,'observed_at':'2026-08-13T11:00:00Z','expires_at':'2026-08-14T11:00:00Z',
                'producer':{'provider':'github','run_id':123,'readback_verified':True},'external_anchor':{'anchor_id':'check:123','readback_verified':True}}
        ok=resolve_evidence_refs([record],['E1'],subject_sha='a'*40,policy_hash='b'*64,now=self.now); self.assertTrue(ok['refs_resolved'])
        record['producer']['readback_verified']=False
        bad=resolve_evidence_refs([record],['E1'],subject_sha='a'*40,policy_hash='b'*64,now=self.now); self.assertFalse(bad['refs_resolved'])
    def test_owner_bootstrap_is_three_question_and_publication_fail_closed(self):
        self.assertEqual(bootstrap_plan('Demo','Готовый экран',public_confirmed=False,license_acknowledged=True)['status'],'HUMAN_REQUIRED')
        ready=bootstrap_plan('Demo','Готовый экран',public_confirmed=True,license_acknowledged=True); self.assertEqual(ready['questions_used'],3); self.assertEqual(ready['runner'],'ubuntu-24.04')
    def test_dashboard_url_and_cost_fail_closed(self):
        self.assertIsNone(_safe_url('//evil.example')); self.assertIsNone(_safe_url('http://evil.example')); self.assertIsNone(_safe_url('https://u:p@evil.example'))
        self.assertTrue(_safe_url('https://example.org/x')); self.assertEqual(_cost_display({'result':'BLOCK','projected_cost':0}),'BLOCK'); self.assertEqual(_cost_display({'result':'NOT_VERIFIED'}),'NOT_VERIFIED')
if __name__=='__main__': unittest.main()
