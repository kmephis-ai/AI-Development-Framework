import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))

from lib.github_readback import compile_github_readback
from lib.github_rulesets import canonical_ruleset_payload,runtime_anchor_ruleset_payload,REQUIRED_CHECKS
from lib.provider_contracts import ProviderContractError

SUBJECT='a'*40
APP_ID=15368


def checks():
    return [
        {'id':100+i,'name':name,'conclusion':'success','head_sha':SUBJECT,'app':{'slug':'github-actions','id':APP_ID}}
        for i,name in enumerate(REQUIRED_CHECKS)
    ]


def rulesets():
    return [
        {'id':7,**canonical_ruleset_payload(integration_id=APP_ID)},
        {'id':8,**runtime_anchor_ruleset_payload()},
    ]


class FakeClient:
    repo='owner/repo'
    def __init__(self,runs):
        self._runs=runs
        self.recent_args=[]
        self.exhaustive_called=False
    def list(self,path,*,object_key=None,max_pages=10):
        if '/check-runs?' in path:
            return checks()
        raise AssertionError(f'unexpected list: {path}')
    def recent_runs(self,*,limit=100,event=None):
        self.recent_args.append((limit,event))
        return list(self._runs)
    def runs(self):
        self.exhaustive_called=True
        raise ProviderContractError('PROVIDER_PAGINATION_BUDGET_EXCEEDED')
    def jobs(self,run_id):
        return [{'id':run_id*10,'labels':['ubuntu-24.04']}]
    def issues(self):
        return []


def valid_run(**overrides):
    value={'id':321,'event':'pull_request','head_sha':SUBJECT,'name':'ADWF PR','conclusion':'success'}
    value.update(overrides)
    return value


class GitHubReadbackRecentRunsTests(unittest.TestCase):
    def compile(self,client):
        return compile_github_readback(
            ROOT,client,subject_sha=SUBJECT,
            repository={'visibility':'public','private':False},rulesets=rulesets(),
        )[0]

    def test_exact_subject_pr_run_uses_bounded_recent_window_not_exhaustive_history(self):
        client=FakeClient([valid_run()])
        readback=self.compile(client)
        self.assertEqual(client.recent_args,[(100,'pull_request')])
        self.assertFalse(client.exhaustive_called)
        self.assertTrue(readback['runner_verified'])
        self.assertEqual(readback['runner'],'ubuntu-24.04')
        self.assertTrue(readback['readback_verified'])

    def test_missing_or_mismatched_recent_run_fails_closed(self):
        cases=(
            [],
            [valid_run(head_sha='b'*40)],
            [valid_run(name='Other workflow')],
            [valid_run(conclusion='failure')],
            [valid_run(event='push')],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                client=FakeClient(rows)
                readback=self.compile(client)
                self.assertFalse(client.exhaustive_called)
                self.assertFalse(readback['runner_verified'])
                self.assertEqual(readback['runner'],'NOT_VERIFIED')
                self.assertFalse(readback['readback_verified'])

    def test_self_hosted_recent_run_remains_disallowed(self):
        client=FakeClient([valid_run()])
        client.jobs=lambda run_id:[{'id':1,'labels':['ubuntu-24.04','self-hosted']}]
        readback=self.compile(client)
        self.assertTrue(readback['larger_runner'])
        self.assertFalse(readback['runner_verified'])
        self.assertFalse(readback['readback_verified'])

    def test_recent_run_provider_failure_propagates_fail_closed(self):
        client=FakeClient([])
        def fail(*,limit=100,event=None):
            raise ProviderContractError('PROVIDER_RECENT_RUNS_PAYLOAD_INVALID')
        client.recent_runs=fail
        with self.assertRaisesRegex(ProviderContractError,'PROVIDER_RECENT_RUNS_PAYLOAD_INVALID'):
            self.compile(client)
        self.assertFalse(client.exhaustive_called)

if __name__=='__main__': unittest.main()
