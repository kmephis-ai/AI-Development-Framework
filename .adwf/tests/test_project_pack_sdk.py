from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.project_packs import ProjectPackError, detect_pack, load_packs, pack_digest, validate_pack_definition
from lib.pack_materializer import materialize_project_pack


class ProjectPackSdkTests(unittest.TestCase):
    def _root_with_pack(self, value: dict) -> Path:
        self._tmp=tempfile.TemporaryDirectory()
        root=Path(self._tmp.name)
        (root/'.adwf/schemas').mkdir(parents=True)
        (root/'.adwf/packs').mkdir(parents=True)
        shutil.copy2(ROOT/'.adwf/schemas/project-pack.schema.json',root/'.adwf/schemas/project-pack.schema.json')
        (root/'.adwf/packs/react.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return root

    def tearDown(self):
        tmp=getattr(self,'_tmp',None)
        if tmp: tmp.cleanup()

    def test_all_builtin_packs_are_strict_and_digest_stable(self):
        a=load_packs(ROOT);b=load_packs(ROOT)
        self.assertEqual(set(a),{'apps-script','edge-controller','powershell','react','vue','angular','fastapi','node','python','go'})
        self.assertEqual({k:v['digest'] for k,v in a.items()},{k:v['digest'] for k,v in b.items()})
        self.assertTrue(all(len(v['digest'])==64 for v in a.values()))

    def test_generic_detection_uses_validated_definition_and_keeps_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'package.json').write_text(json.dumps({'dependencies':{'react':'19'},'scripts':{}}),encoding='utf-8')
            out=detect_pack(p,ROOT)
            self.assertEqual(out['pack'],'react');self.assertEqual(out['candidates'][:2],['react','node'])
            self.assertEqual(out['pack_digest'],load_packs(ROOT)['react']['digest'])


    def test_apps_script_marker_wins_over_generic_node_without_network_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'appsscript.json').write_text('{"runtimeVersion":"V8"}\n',encoding='utf-8')
            (p/'package.json').write_text(json.dumps({'scripts':{'lint':'node x','test':'node x','build':'node x'}}),encoding='utf-8')
            out=detect_pack(p,ROOT)
            self.assertEqual(out['pack'],'apps-script')
            self.assertEqual(out['candidates'][:2],['apps-script','node'])
            definition=out['definition']
            self.assertEqual(definition['safety']['network'],'NONE')
            self.assertNotIn('install',definition['commands'])
            self.assertEqual(definition['preview'],{})

    def test_apps_script_pack_rejects_network_install_or_preview_expansion(self):
        definition=copy.deepcopy(load_packs(ROOT)['apps-script']['definition'])
        definition['safety']['network']='PACKAGE_REGISTRY'
        definition['commands']['install']={'command':['npm','ci'],'phases':['pr']}
        definition['preview']={'default_url':'http://127.0.0.1:4173'}
        errors=validate_pack_definition(definition,ROOT,path=Path('apps-script.json'))
        self.assertIn('APPS_SCRIPT_NETWORK_MUST_BE_NONE',errors)
        self.assertIn('APPS_SCRIPT_INSTALL_COMMAND_FORBIDDEN',errors)
        self.assertIn('APPS_SCRIPT_PREVIEW_RUNTIME_FORBIDDEN',errors)

    def test_edge_controller_marker_wins_over_node_and_has_no_external_runtime_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'edge-controller.json').write_text('{"schema_version":1}\n',encoding='utf-8')
            (p/'package.json').write_text(json.dumps({'scripts':{'lint':'node x','test':'node x','build':'node x'}}),encoding='utf-8')
            out=detect_pack(p,ROOT)
            self.assertEqual(out['pack'],'edge-controller')
            self.assertEqual(out['candidates'][:2],['edge-controller','node'])
            definition=out['definition']
            self.assertEqual(definition['safety']['network'],'NONE')
            self.assertNotIn('install',definition['commands'])
            self.assertNotIn('start',definition['commands'])
            self.assertEqual(definition['preview'],{})

    def test_edge_controller_pack_rejects_network_install_preview_or_command_expansion(self):
        definition=copy.deepcopy(load_packs(ROOT)['edge-controller']['definition'])
        definition['safety']['network']='PACKAGE_REGISTRY'
        definition['commands']['install']={'command':['npm','ci'],'phases':['pr']}
        definition['preview']={'default_url':'http://127.0.0.1:4173'}
        errors=validate_pack_definition(definition,ROOT,path=Path('edge-controller.json'))
        self.assertIn('EDGE_CONTROLLER_NETWORK_MUST_BE_NONE',errors)
        self.assertIn('EDGE_CONTROLLER_COMMAND_AUTHORITY_FORBIDDEN',errors)
        self.assertIn('EDGE_CONTROLLER_EXTERNAL_RUNTIME_FORBIDDEN',errors)

    def test_powershell_marker_is_explicit_and_has_no_external_runtime_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'.adwf-powershell.json').write_text('{"schema_version":1}\n',encoding='utf-8')
            out=detect_pack(p,ROOT)
            self.assertEqual(out['pack'],'powershell')
            self.assertEqual(out['candidates'][0],'powershell')
            definition=out['definition']
            self.assertEqual(definition['language'],'powershell')
            self.assertEqual(definition['detect']['files'],['.adwf-powershell.json'])
            self.assertEqual(definition['safety']['network'],'NONE')
            self.assertEqual(definition['commands'],{})
            self.assertEqual(definition['preview'],{})

    def test_powershell_pack_rejects_marker_network_install_or_preview_expansion(self):
        definition=copy.deepcopy(load_packs(ROOT)['powershell']['definition'])
        definition['detect']['files']=['project.ps1']
        definition['safety']['network']='LOOPBACK'
        definition['commands']['install']={'command':['pwsh','-File','bootstrap.ps1'],'phases':['bootstrap']}
        definition['preview']={'default_url':'http://127.0.0.1:8080'}
        errors=validate_pack_definition(definition,ROOT,path=Path('powershell.json'))
        self.assertIn('POWERSHELL_DETECTION_MARKER_REQUIRED',errors)
        self.assertIn('POWERSHELL_NETWORK_MUST_BE_NONE',errors)
        self.assertIn('POWERSHELL_EXTERNAL_RUNTIME_FORBIDDEN',errors)

    def test_fastapi_contains_detection_is_definition_driven(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'requirements.txt').write_text('fastapi==1.0\n',encoding='utf-8')
            out=detect_pack(p,ROOT)
            self.assertEqual(out['pack'],'fastapi');self.assertIn('python',out['candidates'])

    def test_materializer_binds_digest_and_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);framework=base/'fw';project=base/'product'
            shutil.copytree(ROOT/'.adwf',framework/'.adwf',ignore=shutil.ignore_patterns('__pycache__','tests'))
            project.mkdir();(project/'package.json').write_text(json.dumps({'dependencies':{'react':'19'},'scripts':{'build':'vite build','dev':'vite','test':'echo ok'}}));(project/'package-lock.json').write_text('{}')
            out=materialize_project_pack(project,framework,apply=False,product_name='React Consumer',default_branch='main',repository_visibility='PUBLIC')
            self.assertEqual(out['status'],'READY_TO_APPLY');self.assertEqual(out['pack'],'react')
            self.assertEqual(out['desired_profile']['project_packs']['selected_digest'],out['pack_digest']);self.assertEqual(out['desired_profile']['project']['name'],'React Consumer')
            self.assertEqual(out['safety']['monetary_budget_usd'],0);self.assertEqual(out['safety']['secrets'],'FORBIDDEN')

    def _canonical(self)->dict:
        return copy.deepcopy(load_packs(ROOT)['react']['definition'])

    def _blocked(self,value:dict,needle:str):
        root=self._root_with_pack(value)
        with self.assertRaises(ProjectPackError) as ctx: load_packs(root)
        self.assertIn(needle,str(ctx.exception))

    def test_unknown_top_level_field_blocks(self):
        d=self._canonical();d['provider']='paid-ai';self._blocked(d,'additionalProperties')

    def test_unknown_command_capability_blocks(self):
        d=self._canonical();d['commands']['deploy']={'command':['echo','x'],'phases':['main']};self._blocked(d,'additionalProperties')

    def test_shell_control_shaped_argv_blocks_even_without_shell_runner(self):
        d=self._canonical();d['commands']['unit']['command']=['npm','test','&&','curl'];self._blocked(d,'COMMAND_SHELL_CONTROL_FORBIDDEN:unit')

    def test_requires_file_traversal_blocks(self):
        d=self._canonical();d['commands']['install']['requires_file']='../outside';self._blocked(d,'COMMAND_REQUIRES_FILE_INVALID:install')

    def test_external_preview_url_blocks(self):
        d=self._canonical();d['preview']['default_url']='https://example.com';self._blocked(d,'PREVIEW_URL_NOT_LOOPBACK_HTTP')

    def test_missing_safety_blocks(self):
        d=self._canonical();d.pop('safety');self._blocked(d,'required')

    def test_nonzero_budget_blocks(self):
        d=self._canonical();d['safety']['monetary_budget_usd']=1;self._blocked(d,'const')

    def test_secret_requirement_cannot_be_declared(self):
        d=self._canonical();d['safety']['secrets']='REQUIRED';self._blocked(d,'const')

    def test_install_requires_package_registry_declaration(self):
        d=self._canonical();d['safety']['network']='LOOPBACK';self._blocked(d,'SAFETY_PACKAGE_NETWORK_REQUIRED')

    def test_requires_file_absolute_path_blocks(self):
        d=self._canonical();d['commands']['install']['requires_file']='/etc/passwd';self._blocked(d,'COMMAND_REQUIRES_FILE_INVALID:install')

    def test_digest_substitution_is_detectable(self):
        d=self._canonical();before=pack_digest(d);d['preview']['golden_paths']=['/','/safe'];after=pack_digest(d)
        self.assertNotEqual(before,after);self.assertEqual(after,hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())


if __name__=='__main__': unittest.main()
