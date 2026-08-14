#!/usr/bin/env python3
"""Publish the current creative Action Envelope to GitHub Agent Inbox."""
from __future__ import annotations
from pathlib import Path
import json,os,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.durable_orchestrator import OrchestrationJournal
from lib.runtime_supervisor import RuntimeSupervisor
from lib.github_provider import GitHubClient
from lib.github_agent_inbox import GitHubAgentInbox
from lib.work_memory import WorkMemoryStore

def main()->int:
    active=OrchestrationJournal(ROOT).list_active()
    if len(active)!=1:print(json.dumps({'status':'NO_SINGLE_ACTIVE_RUN','active':len(active)}));return 0
    state=active[0];sup=RuntimeSupervisor(ROOT);key,path=sup.envelopes.ensure_request(state)
    envelope=json.loads(path.read_text(encoding='utf-8'));repo=os.environ.get('GITHUB_REPOSITORY','');token=os.environ.get('GITHUB_TOKEN','')
    if not repo or not token:raise SystemExit('GITHUB_REPOSITORY/GITHUB_TOKEN missing')
    result=GitHubAgentInbox(GitHubClient(repo,token)).publish(envelope,WorkMemoryStore(ROOT).load());print(json.dumps(result,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
