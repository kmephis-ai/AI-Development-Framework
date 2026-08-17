from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys, unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf')); sys.path.insert(0,str(ROOT/'.adwf/scripts'))
from github_metrics_collector import collect  # noqa: E402


class FakeClient:
    def __init__(self): self.recent=[]
    def runs(self): raise AssertionError('exhaustive runs() must not be used for metrics')
    def recent_runs(self,*,limit=100,event=None):
        self.recent.append((limit,event))
        now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        return [{'id':7,'event':'pull_request','name':'ADWF PR','created_at':now,'run_started_at':now,'updated_at':now,'conclusion':'success','run_attempt':1,'head_sha':'a'*40,'pull_requests':[]}]
    def jobs(self,run_id): return []
    def pull_files(self,number): return []


class GitHubMetricsCollectorTests(unittest.TestCase):
    def test_collect_uses_bounded_recent_pull_request_window(self):
        client=FakeClient(); result=collect(client,limit=50,days=30)
        self.assertEqual(client.recent,[(100,'pull_request')])
        self.assertEqual(len(result['runs']),1)
        self.assertEqual(result['runs'][0]['run_id'],7)


if __name__=='__main__': unittest.main()
