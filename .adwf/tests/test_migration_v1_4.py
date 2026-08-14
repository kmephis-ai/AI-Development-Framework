import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class MigrationV14Tests(unittest.TestCase):
    def fixture(self,tmp):
        repo=Path(tmp); shutil.copytree(ROOT/'.adwf',repo/'.adwf')
        for n in ('config.json','project-state.json'):
            p=repo/'.adwf'/n; d=json.loads(p.read_text()); d['framework_version']='1.3.0'
            if n=='config.json': d['schema_version']=3; d.pop('pipeline_ir',None)
            p.write_text(json.dumps(d))
        return repo
    def invoke(self,repo,*extra,env=None):
        return subprocess.run([sys.executable,str(ROOT/'.adwf/migrate_v1_3_to_v1_4.py'),'--root',str(repo),*extra],capture_output=True,text=True,env=env)
    def test_publication_is_never_implicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=self.fixture(tmp); r=self.invoke(repo,'--apply'); self.assertEqual(r.returncode,6); self.assertIn('HUMAN_REQUIRED',r.stdout)
            self.assertEqual(json.loads((repo/'.adwf/config.json').read_text())['framework_version'],'1.3.0')
    def test_historical_migration_plan_remains_publication_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=self.fixture(tmp); r=self.invoke(repo,'--public-confirmed','--license-acknowledged'); self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertIn('MIGRATION_PLAN_INVALID',r.stdout+r.stderr); self.assertEqual(json.loads((repo/'.adwf/config.json').read_text())['framework_version'],'1.3.0')
if __name__=='__main__': unittest.main()
