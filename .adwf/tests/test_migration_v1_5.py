import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class MigrationV15Tests(unittest.TestCase):
    def fixture(self,tmp):
        repo=Path(tmp);shutil.copytree(ROOT/'.adwf',repo/'.adwf')
        c=json.loads((repo/'.adwf/config.json').read_text());st=json.loads((repo/'.adwf/project-state.json').read_text())
        c['framework_version']='1.5.0';c['schema_version']=5;c.pop('delivery',None);c.get('github',{}).get('trust',{})['required_check_names']=['fast-feedback','adwf/trusted-gate']
        c.get('runtime_supervisor',{}).pop('executor_registry',None);c.get('runtime_supervisor',{}).pop('single_ssot',None)
        c.get('release_automation',{}).pop('transactional_version_bump',None);c.get('release_automation',{}).pop('caller_version_forbidden',None)
        st['framework_version']='1.5.0'
        (repo/'.adwf/config.json').write_text(json.dumps(c));(repo/'.adwf/project-state.json').write_text(json.dumps(st))
        return repo
    def invoke(self,repo,*extra,env=None):return subprocess.run([sys.executable,str(ROOT/'.adwf/migrate_v1_5_to_v1_6.py'),'--root',str(repo),*extra],capture_output=True,text=True,env=env)
    def test_commit_and_fault_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=self.fixture(tmp);r=self.invoke(repo,'--apply');self.assertEqual(r.returncode,0,r.stdout+r.stderr);c=json.loads((repo/'.adwf/config.json').read_text());self.assertEqual(c['framework_version'],'1.6.0');self.assertEqual(c['runtime_supervisor']['single_ssot'],'DURABLE_ORCHESTRATOR');self.assertIn('adwf/governance-gate',c['github']['trust']['required_check_names'])
        with tempfile.TemporaryDirectory() as tmp:
            repo=self.fixture(tmp);env=os.environ.copy();env['ADWF_MIGRATION_FAULT_AFTER']='state';r=self.invoke(repo,'--apply',env=env);self.assertNotEqual(r.returncode,0);self.assertEqual(json.loads((repo/'.adwf/config.json').read_text())['framework_version'],'1.5.0')
if __name__=='__main__':unittest.main()
