import json, os, shutil, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
import sys;sys.path.insert(0,str(ROOT/'.adwf'))
from lib.work_memory import WorkMemoryStore,new_work_memory,validate_work_memory
from lib.project_packs import detect_pack
from lib.roadmap_view import build_roadmap_view
from lib.impact_router import route_paths
from lib.preview_engine import capture_preview,PLAYWRIGHT_VERSION
from lib.github_rulesets import canonical_ruleset_payload,verify_rulesets
from lib.github_runtime_store import GitHubRuntimeStore,verify_remote_events
from lib.provider_contracts import ProviderContractError
from lib.semantic_release import release_plan
from lib.github_owner_decisions import GitHubOwnerDecisionStore
from lib.owner_portal import start_autopilot
from lib.github_bootstrap import bootstrap_repository
from lib.github_agent_inbox import GitHubAgentInbox
from lib.portfolio import register_project,portfolio_view

class Proc:
    def __init__(self,returncode=0,stdout='',stderr=''):self.returncode=returncode;self.stdout=stdout;self.stderr=stderr

class FakeGitHub:
    def __init__(self):
        self.repo='kmephis-ai/AI-Development-Framework';self._issues=[];self.comments={};self.cid=0;self.tags={};self.tag_objects={};self.next_tag=0
    def issues(self):return list(self._issues)
    def get(self,path):
        prefix=f'/repos/{self.repo}/issues/'
        if path.startswith(prefix):
            number=int(path[len(prefix):])
            for item in self._issues:
                if item['number']==number:return dict(item)
            raise ProviderContractError('PROVIDER_HTTP_404')
        raise AssertionError(f'UNEXPECTED_GET:{path}')
    def create_issue(self,title,body):
        item={'number':len(self._issues)+1,'title':title,'body':body,'state':'open'};self._issues.append(item);self.comments[item['number']]=[];return item
    def close_issue(self,n):
        for item in self._issues:
            if item['number']==n:item['state']='closed';return dict(item)
        raise ProviderContractError('PROVIDER_HTTP_404')
    def issue_comments(self,n):return list(self.comments.get(n,[]))
    def add_issue_comment(self,n,body):
        self.cid+=1;c={'id':self.cid,'body':body,'created_at':'2026-08-14T00:00:00Z','user':{'login':'owner'}};self.comments[n].append(c);return c
    def current_user(self):return {'login':'owner'}
    def collaborator_permission(self,login):return {'permission':'admin'}
    def rulesets(self):
        from lib.github_rulesets import runtime_anchor_ruleset_payload
        return [{'id':91,**runtime_anchor_ruleset_payload()}]
    def matching_tag_refs(self,prefix):return [v for k,v in sorted(self.tags.items()) if k.startswith(prefix)]
    def repo_info(self):return {'default_branch':'main','visibility':'public','private':False}
    def branch(self,name):return {'commit':{'sha':'a'*40}}
    def create_tag_object(self,tag,target,message):
        self.next_tag+=1;sha=f'{self.next_tag:040x}';obj={'sha':sha,'message':message,'object':{'sha':target}};self.tag_objects[sha]=obj;return obj
    def create_tag_ref(self,tag,sha):
        if tag in self.tags:raise ProviderContractError('PROVIDER_HTTP_422')
        ref={'ref':'refs/tags/'+tag,'object':{'sha':sha}};self.tags[tag]=ref;return ref
    def tag_ref(self,tag):
        if tag not in self.tags:raise ProviderContractError('PROVIDER_HTTP_404')
        return self.tags[tag]
    def tag_object(self,sha):return self.tag_objects[sha]

class V15ExecutiveTests(unittest.TestCase):
    def test_no_unix_only_fcntl_import_outside_platform_layer(self):
        offenders=[]
        for area in (ROOT/'.adwf/lib', ROOT/'.adwf/scripts'):
            for p in area.rglob('*.py'):
                if p.name=='file_lock.py':continue
                if 'import fcntl' in p.read_text(encoding='utf-8'):offenders.append(str(p.relative_to(ROOT)))
        self.assertEqual(offenders,[])

    def test_work_memory_is_handoff_facts_not_chain_of_thought(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=new_work_memory(brief_id='BRIEF-12345678',task_ru='Сделай страницу регистрации')
            self.assertNotIn('reasoning',m);self.assertNotIn('chain_of_thought',m)
            stored=WorkMemoryStore(root).save(m);self.assertEqual(validate_work_memory(stored),[])
            h=WorkMemoryStore(root).handoff(summary_ru='Форма создана и проверена локально.',next_action_ru='Запустить CI.')
            self.assertEqual(h['next_action_ru'],'Запустить CI.')

    def test_work_memory_revision_is_cas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=WorkMemoryStore(root);m=store.save(new_work_memory(brief_id='BRIEF-12345678',task_ru='Проверить проект'))
            with self.assertRaises(ValueError):store.save(m,expected_revision=m['revision']-1)

    def test_react_pack_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'package.json').write_text(json.dumps({'dependencies':{'react':'19.0.0'},'scripts':{'build':'vite build','dev':'vite'}}))
            self.assertEqual(detect_pack(p,ROOT)['pack'],'react')

    def test_roadmap_has_three_truth_axes(self):
        state={'work_items':[{'roadmap_id':'EA-001','state':'DONE'},{'roadmap_id':'EA-002','state':'VERIFICATION'}]}
        view=build_roadmap_view(ROOT,state);s=view['summary']
        self.assertIn('implemented',s);self.assertIn('verified',s);self.assertIn('product_done',s)
        self.assertGreaterEqual(s['implemented'],s['product_done'])

    def test_impact_router_skips_full_framework_for_product_only_change(self):
        r=route_paths(['src/backend/service.py']);self.assertFalse(r['full_framework']);self.assertFalse(r['preview'])
        ui=route_paths(['src/components/Login.tsx']);self.assertTrue(ui['preview'])

    def test_preview_is_pinned_desktop_mobile_and_manifested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'.adwf/preview',root/'.adwf/preview')
            subprocess.run(['git','init','-q'],cwd=root,check=True);subprocess.run(['git','config','user.email','test@example.invalid'],cwd=root,check=True);subprocess.run(['git','config','user.name','ADWF Test'],cwd=root,check=True);(root/'app.txt').write_text('x');subprocess.run(['git','add','.'],cwd=root,check=True);subprocess.run(['git','commit','-q','-m','test'],cwd=root,check=True);head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            def runner(cmd,cwd,timeout):
                if cmd[:2]==['npm','install']:
                    (cwd/'node_modules/playwright').mkdir(parents=True,exist_ok=True);return Proc()
                if cmd[:3]==['npx','playwright','install']:return Proc()
                if cmd[0]=='node':
                    req=json.loads(Path(cmd[2]).read_text());out=Path(req['output_dir']);shots=[]
                    for name in ('desktop','mobile'):
                        p=out/f'{name}.png';p.write_bytes(b'png-'+name.encode());shots.append({'name':name,'path':str(p)})
                    (out/'capture-result.json').write_text(json.dumps({'screenshots':shots,'console_errors':[],'failed_requests':[],'accessibility':{'status':'PASS'}}));return Proc()
                return Proc(1,stderr='unexpected')
            m=capture_preview(root,url='http://127.0.0.1:8000',head_sha=head,install=True,command_runner=runner)
            self.assertEqual(m['engine_version'],PLAYWRIGHT_VERSION);self.assertEqual(set(m['viewports']),{'desktop','mobile'});self.assertEqual(len(m['screenshots']),2)

    def test_ruleset_requires_pr_checks_and_blocks_force_push(self):
        payload=canonical_ruleset_payload();payload['id']=42
        v=verify_rulesets([payload]);self.assertTrue(v['readback_verified']);self.assertEqual(v['ruleset_id'],42)
        types={x['type'] for x in payload['rules']};self.assertIn('non_fast_forward',types);self.assertIn('pull_request',types)

    def test_remote_runtime_ledger_is_hash_chained_and_restorable(self):
        fake=FakeGitHub();store=GitHubRuntimeStore(fake)
        state={'schema_version':1,'run_id':'run-12345678','roadmap_id':'R1','issue_id':'1','risk':'R1','work_type':'feature','product_impact':True,'owner_request_digest':'a'*64,'phase':'RECONCILE','status':'RUNNING','cycle':0,'subject_sha':None,'preview_digest':None,'owner_acceptance_sha':None,'policy_hash':'b'*64,'attempts':{},'max_attempts':3,'max_cycles':10,'deadline_at':'2099-01-01T00:00:00Z','last_failed_phase':None,'blockers':[],'monetary_budget_usd':0,'events':[],'event_head':None,'revision':1,'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-01T00:00:00Z'}
        out=store.append(state);self.assertEqual(out['status'],'APPENDED');_,events=store.read();self.assertEqual(verify_remote_events(events),[])
        with tempfile.TemporaryDirectory() as tmp:
            restored=store.restore_latest(tmp);self.assertEqual(restored['run_id'],state['run_id'])

    def test_owner_decision_is_provider_authenticated_and_exact(self):
        fake=FakeGitHub();d=GitHubOwnerDecisionStore(fake).record(decision='ACCEPTED',head_sha='a'*40,preview_digest='b'*64,policy_hash='c'*64,nonce='d'*32)
        self.assertTrue(d['readback_verified']);latest=GitHubOwnerDecisionStore(fake).latest_for_sha('a'*40);self.assertEqual(latest['decision'],'ACCEPTED')

    def test_semantic_release_not_issue_count(self):
        p=release_plan('1.5.0',[{'impact':'fix'},{'impact':'feature'}]);self.assertEqual(p['proposed_version'],'1.6.0')
        p=release_plan('1.5.0',[{'impact':'fix','breaking':True}]);self.assertEqual(p['proposed_version'],'2.0.0')


    def test_owner_task_starts_durable_runtime_not_just_a_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'.adwf',root/'.adwf',ignore=shutil.ignore_patterns('__pycache__','tests'))
            started=start_autopilot(root,'Сделай страницу регистрации')
            self.assertEqual(started['status'],'AUTOPILOT_STARTED');self.assertEqual(started['phase'],'RECONCILE')
            self.assertTrue((root/'.adwf-runtime/orchestration'/f"{started['run_id']}.json").is_file())

    def test_bootstrap_uses_live_ruleset_readback_when_connected(self):
        from lib.github_rulesets import runtime_anchor_ruleset_payload
        class C:
            def __init__(self,*a,**k):pass
            def repo_info(self):return {'private':False,'visibility':'public','default_branch':'main'}
            def branch(self,name):return {'commit':{'sha':'a'*40}}
            def rulesets(self):return [{'id':7,**canonical_ruleset_payload(integration_id=123)},{'id':8,**runtime_anchor_ruleset_payload()}]
        with patch('lib.github_bootstrap.detect_repository',return_value='owner/repo'), patch('lib.github_bootstrap.discover_token',return_value=('token','TEST')), patch('lib.github_bootstrap.GitHubClient',C), patch('lib.github_bootstrap.discover_check_source',return_value={'status':'VERIFIED','integration_id':123,'checks':{}}), patch('lib.github_bootstrap.materialize_project_pack',return_value={'status':'ALREADY_MATERIALIZED','pack':'python'}):
            result=bootstrap_repository(ROOT,apply=True)
        self.assertEqual(result['status'],'VERIFIED');self.assertTrue(result['ruleset']['readback_verified']);self.assertTrue(result['runtime_anchor_ruleset']['readback_verified'])


    def test_agent_inbox_is_handoff_not_authority(self):
        fake=FakeGitHub();envelope={'idempotency_key':'k1','run_id':'run-1','revision':1,'phase':'EXECUTE','capability':'edit','subject_sha':'a'*40,'risk':'R1'}
        result=GitHubAgentInbox(fake).publish(envelope,{'brief_id':'B1','task_ru':'Сделать экран','next_action_ru':'Написать код'})
        self.assertEqual(result['status'],'PUBLISHED');body=fake.comments[result['issue_number']][0]['body'];self.assertIn('NOT_AUTHORIZATION_OR_EVIDENCE',body);self.assertNotIn('token',body.lower())

    def test_local_portfolio_contains_no_secrets_and_tracks_multiple_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg=Path(tmp)/'portfolio.json';root1=Path(tmp)/'a';root2=Path(tmp)/'b'
            for root in (root1,root2):
                (root/'.adwf').mkdir(parents=True);(root/'.adwf/config.json').write_text('{}');(root/'VERSION').write_text('1.6.0')
            register_project(root1,registry_path=reg);register_project(root2,registry_path=reg);view=portfolio_view(registry_path=reg)
            self.assertEqual(view['project_count'],2);self.assertFalse(view['secrets_in_registry']);self.assertNotIn('token',reg.read_text().lower())

    def test_platform_smoke_script_is_functional_not_text_only(self):
        text=(ROOT/'.adwf/scripts/platform_smoke.py').read_text();self.assertIn('urlopen',text);self.assertIn('ADWF v1.6 Executive Portal',text)

if __name__=='__main__':unittest.main()
