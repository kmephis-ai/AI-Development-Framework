import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))

from lib.action_executors import ExecutorWait,reconcile_executor
from lib.github_runtime_store import GitHubRuntimeStore,verify_remote_events
from lib.github_rulesets import runtime_anchor_ruleset_payload
from lib.provider_contracts import ProviderContractError

MAIN='a'*40


def state(issue_id='1',revision=0):
    return {
        'schema_version':1,'run_id':'stale-runtime-001','roadmap_id':'SESSION_SELFHOST_PROOF-001','issue_id':issue_id,
        'risk':'R1','work_type':'verification','product_impact':False,'owner_request_digest':'c'*64,
        'phase':'RECONCILE','status':'RUNNING','cycle':0,'subject_sha':None,'preview_digest':None,
        'owner_acceptance_sha':None,'delivery_sha':None,'pull_request_number':None,'preview_attestation_id':None,'work_branch':None,
        'policy_hash':'d'*64,'attempts':{},'max_attempts':1,'max_cycles':2,'deadline_at':'2099-01-01T00:00:00Z',
        'last_failed_phase':None,'blockers':[],'monetary_budget_usd':0,'events':[],'event_head':None,
        'revision':revision,'created_at':'2026-08-18T00:00:00Z','updated_at':'2026-08-18T00:00:00Z',
    }


class FakeGitHub:
    repo='o/r';token='secret'
    def __init__(self):
        self._issues=[];self.comments={};self.cid=0;self.tags={};self.tag_objects={};self.next_tag=0
    def issues(self):return [dict(x) for x in self._issues]
    def get(self,path):
        prefix=f'/repos/{self.repo}/issues/'
        if path.startswith(prefix):
            n=int(path[len(prefix):])
            for x in self._issues:
                if x['number']==n:return dict(x)
            raise ProviderContractError('PROVIDER_HTTP_404')
        raise AssertionError(path)
    def create_issue(self,title,body):
        x={'number':len(self._issues)+1,'title':title,'body':body,'state':'open','created_at':'2026-08-18T00:00:00Z','user':{'login':'owner'}}
        self._issues.append(x);self.comments[x['number']]=[];return dict(x)
    def close_issue(self,n):
        for x in self._issues:
            if x['number']==n:x['state']='closed';return dict(x)
        raise ProviderContractError('PROVIDER_HTTP_404')
    def issue_comments(self,n):return list(self.comments.get(n,[]))
    def add_issue_comment(self,n,body):
        self.cid+=1;x={'id':self.cid,'body':body,'created_at':'2026-08-18T00:00:00Z','user':{'login':'owner'}};self.comments[n].append(x);return x
    def rulesets(self):return [{'id':91,**runtime_anchor_ruleset_payload()}]
    def matching_tag_refs(self,prefix):return [v for k,v in sorted(self.tags.items()) if k.startswith(prefix)]
    def repo_info(self):return {'default_branch':'main','visibility':'public','private':False}
    def branch(self,name):return {'commit':{'sha':MAIN}}
    def create_tag_object(self,tag,target,message):
        self.next_tag+=1;sha=f'{self.next_tag:040x}';x={'sha':sha,'tag':tag,'message':message,'object':{'sha':target,'type':'commit'}};self.tag_objects[sha]=x;return x
    def create_tag_ref(self,tag,sha):
        if tag in self.tags:raise ProviderContractError('PROVIDER_HTTP_422')
        x={'ref':'refs/tags/'+tag,'object':{'sha':sha}};self.tags[tag]=x;return x
    def tag_ref(self,tag):
        if tag not in self.tags:raise ProviderContractError('PROVIDER_HTTP_404')
        return self.tags[tag]
    def tag_object(self,sha):return self.tag_objects[sha]


class RuntimeLedgerStaleReconciliationTests(unittest.TestCase):
    def test_restore_blocks_closed_provider_work_instead_of_resurrecting_running_state(self):
        fake=FakeGitHub();work=fake.create_issue('work','');store=GitHubRuntimeStore(fake)
        original=state(str(work['number']))
        store.append(original);fake.close_issue(work['number'])
        with tempfile.TemporaryDirectory() as tmp:
            restored=store.restore_latest(tmp)
            self.assertEqual(restored['status'],'BLOCKED')
            self.assertEqual(restored['blockers'],['REMOTE_RUNTIME_WORK_ITEM_PROVIDER_TERMINAL'])
            self.assertEqual(restored['revision'],original['revision']+1)
            from lib.durable_orchestrator import OrchestrationJournal
            self.assertEqual(OrchestrationJournal(tmp).list_active(),[])
        _,events=store.read();self.assertEqual(len(events),1);self.assertEqual(events[0]['state']['status'],'RUNNING')

    def test_reconciled_terminal_state_can_be_appended_as_second_immutable_event(self):
        fake=FakeGitHub();work=fake.create_issue('work','');store=GitHubRuntimeStore(fake)
        store.append(state(str(work['number'])));fake.close_issue(work['number'])
        with tempfile.TemporaryDirectory() as tmp:restored=store.restore_latest(tmp)
        out=store.append(restored);self.assertEqual(out['status'],'APPENDED')
        _,events=store.read();self.assertEqual(len(events),2);self.assertEqual(events[-1]['state']['status'],'BLOCKED')
        self.assertEqual(verify_remote_events(events),[])

    def test_reconcile_executor_refuses_closed_issue_before_any_reconcile_subprocess(self):
        fake=FakeGitHub();work=fake.create_issue('work','');fake.close_issue(work['number'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.adwf').mkdir();(root/'.adwf/config.json').write_text(json.dumps({'orchestration':{'max_parallel_writers':1,'lease_ttl_minutes':120,'heartbeat_minutes':30}}))
            current={**state(str(work['number'])),'phase':'RECONCILE'}
            with patch('lib.action_executors._github',return_value=(fake,{})),patch('lib.action_executors.subprocess.run') as run:
                out=reconcile_executor(root,current,'k'*64,{'phase':'RECONCILE'})
            self.assertIsInstance(out,ExecutorWait);self.assertEqual(out.reason,'WORK_ITEM_PROVIDER_TERMINAL');run.assert_not_called()


    def test_terminal_restore_marker_selects_exact_blocked_run_for_persist(self):
        spec=importlib.util.spec_from_file_location('github_runtime_sync_test',ROOT/'.adwf/scripts/github_runtime_sync.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from lib.durable_orchestrator import OrchestrationJournal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module.ROOT=root;module.RESTORE_MARKER=root/'.adwf-runtime/github-runtime-restore.json'
            blocked={**state('1'),'status':'BLOCKED','blockers':['REMOTE_RUNTIME_WORK_ITEM_PROVIDER_TERMINAL']}
            saved=OrchestrationJournal(root).save(blocked);module._write_restore_marker(saved)
            selected=module._select_persist_state(None)
            self.assertEqual(selected['run_id'],saved['run_id']);self.assertEqual(selected['status'],'BLOCKED')

    def test_invalid_restore_marker_fails_closed(self):
        spec=importlib.util.spec_from_file_location('github_runtime_sync_invalid_marker',ROOT/'.adwf/scripts/github_runtime_sync.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module.ROOT=root;module.RESTORE_MARKER=root/'.adwf-runtime/github-runtime-restore.json'
            module.RESTORE_MARKER.parent.mkdir(parents=True);module.RESTORE_MARKER.write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'REMOTE_RUNTIME_RESTORE_MARKER_INVALID'):module._marker_run_id()

    def test_restore_fails_closed_when_active_work_item_readback_is_unavailable(self):
        fake=FakeGitHub();work=fake.create_issue('work','');store=GitHubRuntimeStore(fake);store.append(state(str(work['number'])))
        fake._issues=[x for x in fake._issues if x['number']!=work['number']]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,'REMOTE_RUNTIME_WORK_ITEM_READBACK_FAILED'):
            store.restore_latest(tmp)

if __name__=='__main__':unittest.main()
