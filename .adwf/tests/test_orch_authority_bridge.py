import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.action_executors import (
    ActionExecutorRegistry,
    ExecutorWait,
    _stage1_resources,
    _writer_branch,
    _writer_id,
    claim_executor,
    cleanup_executor,
)
from lib.durable_orchestrator import new_run, advance_run
from lib.policy_compiler import compile_policy


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


class Client:
    repo = 'o/r'
    token = 'secret'
    def __init__(self, main='a'*40):
        self.main = main
        self.closed = []
    def repo_info(self): return {'default_branch': 'main'}
    def branch(self, name): return {'commit': {'sha': self.main}}
    def close_issue(self, number):
        self.closed.append(number)
        return {'number': number, 'state': 'closed'}


class OrchAuthorityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            'phase':'CLAIM','run_id':'run-authority-001','roadmap_id':'ORCH_AUTHORITY_BRIDGE-001','issue_id':'251',
            'subject_sha':'a'*40,'delivery_sha':None,'work_branch':None,
        }
        self.key = 'k'*64
        self.envelope = {'phase':'CLAIM'}

    def root(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root/'.adwf').mkdir()
        (root/'.adwf/config.json').write_text(json.dumps({'orchestration':{
            'max_parallel_writers':1,'lease_ttl_minutes':120,'heartbeat_minutes':30
        }}))
        return td, root

    def active_lease(self, state=None, *, branch=None, base=None, worker=None):
        state = state or self.state
        now = datetime.now(timezone.utc)
        return {
            'lease_id':'11111111-1111-4111-8111-111111111111','generation':1,
            'issue_id':str(state['issue_id']),'roadmap_id':state['roadmap_id'],
            'worker_id':worker or _writer_id(state),'base_sha':base or state['subject_sha'],
            'branch':branch or _writer_branch(state),'resources':_stage1_resources(),'status':'ACTIVE',
            'claimed_at':iso(now-timedelta(minutes=1)),'heartbeat_at':iso(now-timedelta(minutes=1)),
            'expires_at':iso(now+timedelta(minutes=119)),'released_at':None,
            'provider_reconciled_at':None,'provider_reconciliation_ref':None,
        }

    def registry(self, leases, main='a'*40, revision=1):
        return {'repository':'o/r','revision':revision,'observed_main_sha':main,'max_parallel_writers':1,'leases':leases}

    def test_claim_acquires_real_provider_cas_and_declares_branch_before_execute(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        client=Client();store=MagicMock();store.read.return_value=(self.registry([],revision=0),None)
        lease=self.active_lease();store.acquire.return_value=(self.registry([lease],revision=1),lease,'tagsha')
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=claim_executor(root,self.state,self.key,self.envelope)
        self.assertNotIsInstance(out,ExecutorWait);self.assertEqual(out['outcome'],'PASS')
        self.assertEqual(out['metadata']['lease_model'],'PROVIDER_DURABLE_CAS')
        self.assertEqual(out['metadata']['branch'],_writer_branch(self.state));self.assertFalse(out['metadata']['resumed_existing'])
        kwargs=store.acquire.call_args.kwargs
        self.assertEqual(kwargs['policy_max_parallel_writers'],1);self.assertEqual(kwargs['resources'],_stage1_resources())
        self.assertEqual(kwargs['worker_id'],_writer_id(self.state));self.assertEqual(kwargs['base_sha'],'a'*40)

    def test_claim_resumes_exact_compatible_active_lease_without_second_acquire(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        client=Client();store=MagicMock();lease=self.active_lease();store.read.return_value=(self.registry([lease]),'anchor')
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=claim_executor(root,self.state,self.key,self.envelope)
        self.assertEqual(out['outcome'],'PASS');self.assertTrue(out['metadata']['resumed_existing']);store.acquire.assert_not_called()

    def test_claim_blocks_incompatible_provider_lease(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        client=Client();store=MagicMock();lease=self.active_lease(worker='other-worker');store.read.return_value=(self.registry([lease]),'anchor')
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=claim_executor(root,self.state,self.key,self.envelope)
        self.assertIsInstance(out,ExecutorWait);self.assertEqual(out.reason,'ACTIVE_PROVIDER_LEASE_INCOMPATIBLE');store.acquire.assert_not_called()

    def test_mutation_lifecycle_cannot_advance_without_live_provider_lease(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        client=Client();store=MagicMock();store.read.return_value=(self.registry([],revision=0),None)
        state={**self.state,'phase':'WORKSPACE','work_branch':_writer_branch(self.state)}
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=ActionExecutorRegistry(root).execute(state,self.key,{'phase':'WORKSPACE'})
        self.assertIsInstance(out,ExecutorWait);self.assertEqual(out.reason,'LIVE_PROVIDER_LEASE_REQUIRED')

    def test_premerge_main_drift_blocks_live_authority(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        client=Client(main='b'*40);store=MagicMock();lease=self.active_lease();store.read.return_value=(self.registry([lease],main='a'*40),'anchor')
        state={**self.state,'phase':'OPEN_PR','work_branch':_writer_branch(self.state)}
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=ActionExecutorRegistry(root).execute(state,self.key,{'phase':'OPEN_PR'})
        self.assertIsInstance(out,ExecutorWait);self.assertEqual(out.reason,'LEASE_MAIN_RECONCILIATION_REQUIRED')

    def test_postmerge_recovery_keeps_existing_lease_authority_on_delivery_main(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        delivery='b'*40;client=Client(main=delivery);store=MagicMock()
        state={**self.state,'phase':'RECOVERY','subject_sha':delivery,'delivery_sha':delivery,'work_branch':_writer_branch(self.state)}
        lease=self.active_lease(state,base='a'*40,branch=state['work_branch']);store.read.return_value=(self.registry([lease],main='a'*40),'anchor')
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=ActionExecutorRegistry(root).execute(state,self.key,{'phase':'RECOVERY'})
        self.assertIsInstance(out,ExecutorWait);self.assertEqual(out.reason,'CREATIVE_AGENT_RESULT_REQUIRED')

    def test_cleanup_releases_provider_lease_before_issue_close(self):
        td, root = self.root(); self.addCleanup(td.cleanup)
        delivery='b'*40;client=Client(main=delivery);store=MagicMock();state={**self.state,'phase':'CLEANUP','subject_sha':delivery,'delivery_sha':delivery,'work_branch':_writer_branch(self.state)}
        lease=self.active_lease(state,base='a'*40,branch=state['work_branch']);active=self.registry([lease],main='a'*40,revision=1)
        released=copy.deepcopy(active);released['revision']=2;released['observed_main_sha']=delivery;released['leases'][0]['status']='RELEASED'
        store.read.side_effect=[(active,'a1'),(released,'a2')];store.release.return_value=(released,'release-anchor')
        with patch('lib.action_executors._github',return_value=(client,{})),patch('lib.action_executors.GitHubLeaseStore',return_value=store):
            out=cleanup_executor(root,state,self.key,{'phase':'CLEANUP'})
        self.assertEqual(out['outcome'],'PASS');self.assertEqual(client.closed,[251]);store.release.assert_called_once()
        self.assertEqual(store.release.call_args.kwargs['provider_reconciliation_ref'],'github:commit:'+delivery)

    def test_claim_branch_is_durable_before_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.adwf').mkdir();policy,errors=compile_policy(ROOT);self.assertEqual(errors,[])
            (root/'.adwf/effective-policy.json').write_text(json.dumps(policy))
            run=new_run(root,roadmap_id='ORCH_AUTHORITY_BRIDGE-001',issue_id='251',risk='R1',work_type='feature',product_impact=False,owner_request_digest='d'*64,run_id='run-authority-001')
            safe={'autonomy':'A3','max_autonomous_risk':'R1','health':{'package_integrity':'VERIFIED','config_health':'VERIFIED','control_plane_health':'VERIFIED','product_health':'VERIFIED'},'gates':{'ci':'PASS','review':'PASS'},'required_gates':['ci','review'],'exact_sha':True,'evidence_fresh':True,'human_approved':False,'destructive':False,'trust_change':False,'writer_conflict':False,'provider_allowed':True,'provider_potentially_paid':False,'provider_facts_fresh':True}
            advance_run(root,run['run_id'],{'phase':'RECONCILE','outcome':'PASS','idempotency_key':'reconcile-0001','subject_sha':'a'*40,'evidence_refs':[],'reason_codes':[],'cost_usd':0,'metadata':{'issue_id':'251'}},safe)
            advance_run(root,run['run_id'],{'phase':'AUTHORIZE','outcome':'PASS','idempotency_key':'authorize-0001','subject_sha':'a'*40,'evidence_refs':[],'reason_codes':[],'cost_usd':0,'metadata':{}},safe)
            branch='adwf/orch-authority-bridge-001-abc123def456'
            state=advance_run(root,run['run_id'],{'phase':'CLAIM','outcome':'PASS','idempotency_key':'claim-00000001','subject_sha':'a'*40,'evidence_refs':[],'reason_codes':[],'cost_usd':0,'metadata':{'branch':branch}},safe)
            self.assertEqual(state['phase'],'WORKSPACE');self.assertEqual(state['work_branch'],branch)

if __name__=='__main__':unittest.main()
