#!/usr/bin/env python3
"""Collect live GitHub Actions timings into the ADWF performance ledger."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'.adwf'))
from lib.github_auth import detect_repository, discover_token
from lib.github_provider import GitHubClient
from lib.performance_evidence import assess_performance,persist_performance
from lib.impact_router import route_paths
from lib.project_packs import detect_pack


def _ts(value):
    if not value:return None
    text=str(value);return text if text.endswith('Z') else text.replace('+00:00','Z')


def _pull_number(run:dict)->int|None:
    prs=run.get('pull_requests') or []
    if prs and isinstance(prs[0],dict) and str(prs[0].get('number') or '').isdigit():return int(prs[0]['number'])
    return None


def _impact_name(client:GitHubClient,number:int|None)->str:
    if number is None:return 'unknown'
    try:
        paths=[str(x.get('filename') or '') for x in client.pull_files(number)]
        r=route_paths(paths)
        if r['trust']:return 'trust'
        if r['provider']:return 'provider'
        if r['ui']:return 'ui'
        if r['docs'] and not r['framework']:return 'docs'
        if r['framework']:return 'framework'
        return 'product'
    except Exception:return 'unknown'


def collect(client:GitHubClient,*,limit:int=50,days:int=30)->dict:
    cutoff=datetime.now(timezone.utc)-timedelta(days=days);candidates=[]
    for run in client.runs():
        if str(run.get('event'))!='pull_request':continue
        if str(run.get('name') or '') not in {'ADWF PR','ADWF PR v1.6'}:continue
        created=run.get('created_at');started=run.get('run_started_at') or created;completed=run.get('updated_at')
        if not (created and started and completed):continue
        try:dt=datetime.fromisoformat(str(created).replace('Z','+00:00'))
        except ValueError:continue
        if dt<cutoff:continue
        conclusion=str(run.get('conclusion') or '').lower();mapped={'success':'PASS','failure':'FAIL','cancelled':'CANCELLED'}.get(conclusion)
        if not mapped:continue
        candidates.append((dt,run,mapped,started,completed))
    candidates=sorted(candidates,key=lambda x:x[0],reverse=True)[:limit]
    newest_by_pr={}
    for dt,run,*_ in candidates:
        pn=_pull_number(run)
        if pn is not None:newest_by_pr[pn]=max(dt,newest_by_pr.get(pn,dt))
    rows=[];groups={}
    for dt,run,mapped,started,completed in candidates:
        first_failure=None
        if mapped=='FAIL':
            try:
                failed=[j for j in client.jobs(int(run['id'])) if str(j.get('conclusion') or '').lower()=='failure' and j.get('completed_at')]
                if failed:first_failure=min(str(j['completed_at']) for j in failed)
            except (ValueError,KeyError):pass
        pn=_pull_number(run);impact=_impact_name(client,pn);superseded=pn is not None and newest_by_pr.get(pn,dt)>dt
        row={'queued_at':_ts(run.get('created_at')),'started_at':_ts(started),'completed_at':_ts(completed),'first_failure_at':_ts(first_failure),
             'flaky':int(run.get('run_attempt') or 1)>1,'conclusion':mapped,'run_id':run.get('id'),'head_sha':run.get('head_sha'),
             'pull_request_number':pn,'impact':impact,'superseded':superseded}
        rows.append(row);groups.setdefault(impact,[]).append(row)
    pack='NOT_VERIFIED'
    try:pack=str((detect_pack(ROOT,ROOT) or {}).get('pack') or 'NOT_VERIFIED')
    except Exception:pass
    return {'observed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'window_days':days,'project_pack':pack,'runs':rows,'groups':groups}


def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--limit',type=int,default=50);p.add_argument('--days',type=int,default=30);args=p.parse_args()
    repo=detect_repository(ROOT);token,source=discover_token()
    if not repo or not token:
        print(json.dumps({'status':'NOT_VERIFIED','reason':'GITHUB_AUTH_REQUIRED','credential_source':source}));return 0
    evidence=assess_performance(collect(GitHubClient(repo,token),limit=args.limit,days=args.days));evidence['repository']=repo;evidence['credential_source']=source
    if args.apply:persist_performance(ROOT,evidence)
    print(json.dumps(evidence,ensure_ascii=False,indent=2));return 0 if evidence['status'] in {'PASS','NOT_VERIFIED'} else 1
if __name__=='__main__':raise SystemExit(main())
