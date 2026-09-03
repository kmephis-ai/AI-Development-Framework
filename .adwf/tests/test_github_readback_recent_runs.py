import unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))

from lib.github_readback import compile_github_readback,MAIN_GATES
from lib.github_rulesets import canonical_ruleset_payload,runtime_anchor_ruleset_payload,REQUIRED_CHECKS
from lib.provider_contracts import ProviderContractError

SUBJECT='a'*40
OTHER='b'*40
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
    def __init__(self,runs,current_main=OTHER):
        self._runs=runs
        self.current_main=current_main
        self.recent_args=[]
        self.exhaustive_called=False
        self.job_map={}
    def repo_info(self):
        return {'default_branch':'main','private':False,'visibility':'public'}
    def branch(self,name):
        self.assert_branch=name
        return {'commit':{'sha':self.current_main}}
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
        return self.job_map.get(run_id,[{'id':run_id*10,'conclusion':'success','labels':['ubuntu-24.04']}])
    def issues(self):
        return []


def valid_pr_run(**overrides):
    value={'id':321,'event':'pull_request','head_sha':SUBJECT,'name':'ADWF PR','conclusion':'success'}
    value.update(overrides)
    return value


def main_runs(**overrides):
    main={'id':401,'event':'push','head_sha':SUBJECT,'name':'ADWF Main','conclusion':'success'}
    smoke={'id':402,'event':'push','head_sha':SUBJECT,'name':'ADWF Platform Smoke','conclusion':'success'}
    for key,value in overrides.items():
        target,field=key.split('__',1)
        (main if target=='main' else smoke)[field]=value
    return [main,smoke]


class GitHubReadbackRecentRunsTests(unittest.TestCase):
    def compile(self,client):
        return compile_github_readback(
            ROOT,client,subject_sha=SUBJECT,
            repository={'visibility':'public','private':False},rulesets=rulesets(),
        )[0]

    def test_exact_subject_pr_run_uses_bounded_recent_window_not_exhaustive_history(self):
        client=FakeClient([valid_pr_run()])
        readback=self.compile(client)
        self.assertEqual(client.recent_args,[(100,'pull_request')])
        self.assertFalse(client.exhaustive_called)
        self.assertEqual(readback['profile'],'PR_MERGE_AUTHORITY')
        self.assertTrue(readback['runner_verified'])
        self.assertEqual(readback['runner'],'ubuntu-24.04')
        self.assertTrue(readback['readback_verified'])

    def test_missing_or_mismatched_recent_pr_run_fails_closed(self):
        cases=(
            [],
            [valid_pr_run(head_sha=OTHER)],
            [valid_pr_run(name='Other workflow')],
            [valid_pr_run(conclusion='failure')],
            [valid_pr_run(event='push')],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                client=FakeClient(rows)
                readback=self.compile(client)
                self.assertFalse(client.exhaustive_called)
                self.assertFalse(readback['runner_verified'])
                self.assertEqual(readback['runner'],'NOT_VERIFIED')
                self.assertFalse(readback['readback_verified'])

    def test_self_hosted_recent_pr_run_remains_disallowed(self):
        client=FakeClient([valid_pr_run()])
        client.job_map[321]=[{'id':1,'conclusion':'success','labels':['ubuntu-24.04','self-hosted']}]
        readback=self.compile(client)
        self.assertTrue(readback['larger_runner'])
        self.assertFalse(readback['runner_verified'])
        self.assertFalse(readback['readback_verified'])

    def test_recent_pr_run_provider_failure_propagates_fail_closed(self):
        client=FakeClient([])
        def fail(*,limit=100,event=None):
            raise ProviderContractError('PROVIDER_RECENT_RUNS_PAYLOAD_INVALID')
        client.recent_runs=fail
        with self.assertRaisesRegex(ProviderContractError,'PROVIDER_RECENT_RUNS_PAYLOAD_INVALID'):
            self.compile(client)
        self.assertFalse(client.exhaustive_called)

    def test_exact_current_main_uses_postmerge_operational_profile(self):
        client=FakeClient(main_runs(),current_main=SUBJECT)
        client.job_map[401]=[{'id':1,'conclusion':'success','labels':['ubuntu-24.04']}]
        client.job_map[402]=[
            {'id':2,'conclusion':'success','labels':['ubuntu-24.04']},
            {'id':3,'conclusion':'success','labels':['windows-2022']},
        ]
        readback,gates,records,refs=compile_github_readback(
            ROOT,client,subject_sha=SUBJECT,
            repository={'visibility':'public','private':False},rulesets=rulesets(),
        )
        self.assertEqual(client.recent_args,[(100,'push')])
        self.assertEqual(readback['profile'],'CANONICAL_MAIN_OPERATIONAL')
        self.assertEqual(gates,{name:'PASS' for name in MAIN_GATES})
        self.assertEqual(readback['expected_check_integration_id'],APP_ID)
        self.assertEqual(readback['runner'],'github-hosted-standard:ubuntu-24.04+windows-2022')
        self.assertTrue(readback['runner_verified'])
        self.assertTrue(readback['readback_verified'])
        self.assertEqual(len(records),2)
        self.assertEqual(len(refs),2)
        self.assertTrue(all(ref.startswith('github-run:') for ref in refs))

    def test_main_profile_requires_both_exact_sha_postmerge_workflows(self):
        cases=(
            [],
            main_runs(main__head_sha=OTHER),
            main_runs(smoke__conclusion='failure'),
        )
        for rows in cases:
            with self.subTest(rows=rows):
                client=FakeClient(rows,current_main=SUBJECT)
                readback=self.compile(client)
                self.assertEqual(readback['profile'],'CANONICAL_MAIN_OPERATIONAL')
                self.assertFalse(readback['readback_verified'])

    def test_main_profile_rejects_nonstandard_or_self_hosted_runner_labels(self):
        client=FakeClient(main_runs(),current_main=SUBJECT)
        client.job_map[401]=[{'id':1,'conclusion':'success','labels':['ubuntu-24.04','self-hosted']}]
        client.job_map[402]=[
            {'id':2,'conclusion':'success','labels':['ubuntu-24.04']},
            {'id':3,'conclusion':'success','labels':['windows-2022']},
        ]
        readback=self.compile(client)
        self.assertTrue(readback['larger_runner'])
        self.assertFalse(readback['runner_verified'])
        self.assertFalse(readback['readback_verified'])

    def test_old_merge_sha_cannot_use_main_operational_profile(self):
        client=FakeClient(main_runs(),current_main=OTHER)
        readback=self.compile(client)
        self.assertEqual(readback['profile'],'PR_MERGE_AUTHORITY')
        self.assertFalse(readback['readback_verified'])


if __name__=='__main__': unittest.main()
