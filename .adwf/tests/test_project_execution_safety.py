from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))
from lib.consumer_profile import PROFILE_REL, seal_profile
from lib.pack_materializer import materialize_project_pack
from lib.project_execution import (
    NETWORK_ENFORCEMENT,
    ProjectExecutionError,
    ProjectExecutionSession,
    load_bound_project_pack,
    validate_execution_evidence,
)


class ProjectExecutionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.adwf/schemas').mkdir(parents=True)
        (self.root / '.adwf/packs').mkdir(parents=True)
        for name in ('config.schema.json', 'project-pack.schema.json', 'project-execution-evidence.schema.json', 'consumer-profile.schema.json'):
            shutil.copy2(ROOT / '.adwf/schemas' / name, self.root / '.adwf/schemas' / name)
        for path in (ROOT / '.adwf/packs').glob('*.json'):
            shutil.copy2(path, self.root / '.adwf/packs' / path.name)
        shutil.copy2(ROOT / '.adwf/config.json', self.root / '.adwf/config.json')
        config = json.loads((self.root / '.adwf/config.json').read_text(encoding='utf-8'))
        config['project'].update({'name': 'Execution Safety Fixture', 'type': 'node-test', 'runtime_product': True})
        (self.root / '.adwf/config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (self.root / 'package.json').write_text(json.dumps({'name': 'fixture', 'scripts': {'test': 'unused'}}) + '\n', encoding='utf-8')
        (self.root / 'README.md').write_text('canonical\n', encoding='utf-8')
        self._set_probe("import os,sys\nsys.exit(97 if 'VERY_SECRET_TOKEN' in os.environ else 0)\n")
        self._materialize()
        subprocess.run(['git', 'init', '-b', 'main'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'adwf@example.invalid'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'ADWF Test'], cwd=self.root, check=True)
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'fixture'], cwd=self.root, check=True, capture_output=True)
        self.head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        self.tree = subprocess.run(['git', 'rev-parse', 'HEAD^{tree}'], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        self.temp.cleanup()

    def _set_probe(self, body: str) -> None:
        (self.root / 'probe.py').write_text(body, encoding='utf-8')
        pack_path = self.root / '.adwf/packs/node.json'
        pack = json.loads(pack_path.read_text(encoding='utf-8'))
        pack['commands'] = {'unit': {'command': ['python', 'probe.py'], 'requires_file': 'probe.py', 'phases': ['pr', 'main']}}
        pack['preview'] = {}
        pack['safety']['network'] = 'NONE'
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def _materialize(self) -> None:
        result = materialize_project_pack(self.root, self.root, apply=True)
        self.assertEqual(result['status'], 'APPLIED')

    def _binding(self):
        return load_bound_project_pack(self.root, self.root)

    def _replace_probe_and_commit(self, body: str) -> None:
        self._set_probe(body)
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'probe update'], cwd=self.root, check=True, capture_output=True)
        self.head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        self.tree = subprocess.run(['git', 'rev-parse', 'HEAD^{tree}'], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def test_minimal_environment_does_not_inherit_secret_like_variable(self):
        old = os.environ.get('VERY_SECRET_TOKEN')
        os.environ['VERY_SECRET_TOKEN'] = 'do-not-inherit'
        try:
            binding = self._binding()
            with ProjectExecutionSession(self.root, self.root, binding, purpose='test') as session:
                observation = session.run('unit', binding['commands']['unit']['command'])
                self.assertEqual(observation.process.returncode, 0)
                self.assertEqual(observation.safety_status, 'PASS')
                self.assertNotIn('VERY_SECRET_TOKEN', session.environment_names)
            self.assertIsNotNone(session.evidence)
            self.assertFalse(session.evidence['secret_like_inherited'])
        finally:
            if old is None:
                os.environ.pop('VERY_SECRET_TOKEN', None)
            else:
                os.environ['VERY_SECRET_TOKEN'] = old

    def test_disposable_clone_preserves_canonical_checkout_on_tracked_mutation(self):
        self._replace_probe_and_commit("from pathlib import Path\nPath('README.md').write_text('mutated\\n')\n")
        binding = self._binding()
        with ProjectExecutionSession(self.root, self.root, binding, purpose='mutation') as session:
            self.assertEqual(session.head_sha, self.head)
            self.assertEqual(session.tree_sha, self.tree)
            observation = session.run('unit', binding['commands']['unit']['command'])
            self.assertEqual(observation.process.returncode, 0)
            self.assertEqual(observation.safety_status, 'BLOCK')
            self.assertIn('PROJECT_COMMAND_TRACKED_MUTATION', observation.reason_codes)
            self.assertEqual((self.root / 'README.md').read_text(encoding='utf-8'), 'canonical\n')
        self.assertEqual(session.evidence['canonical_source_integrity'], 'PASS')
        self.assertEqual(session.evidence['outcome'], 'BLOCK')

    def test_untracked_tool_output_is_isolated_and_not_promoted_to_canonical_tree(self):
        self._replace_probe_and_commit("from pathlib import Path\nPath('tool-output.txt').write_text('ok\\n')\n")
        binding = self._binding()
        with ProjectExecutionSession(self.root, self.root, binding, purpose='tool-output') as session:
            observation = session.run('unit', binding['commands']['unit']['command'])
            self.assertEqual(observation.safety_status, 'PASS')
            self.assertGreaterEqual(observation.untracked_output_count, 1)
            self.assertFalse((self.root / 'tool-output.txt').exists())
        self.assertEqual(session.evidence['outcome'], 'PASS')

    def test_stale_pack_digest_blocks_before_execution(self):
        path = self.root / PROFILE_REL
        profile = json.loads(path.read_text(encoding='utf-8'))
        profile['project_pack_digest'] = '0' * 64
        profile = seal_profile(profile)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ProjectExecutionError, 'CONSUMER_PROFILE_PACK_DIGEST_MISMATCH'):
            self._binding()

    def test_safety_projection_mismatch_blocks_before_execution(self):
        path = self.root / PROFILE_REL
        profile = json.loads(path.read_text(encoding='utf-8'))
        profile['project_packs']['safety']['network'] = 'LOOPBACK'
        profile = seal_profile(profile)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ProjectExecutionError, 'CONSUMER_PROFILE_PACK_PROJECTION_MISMATCH'):
            self._binding()

    def test_dirty_or_ambiguous_canonical_source_is_blocked(self):
        (self.root / 'untracked.txt').write_text('dirty\n', encoding='utf-8')
        binding = self._binding()
        with self.assertRaisesRegex(ProjectExecutionError, 'PROJECT_SOURCE_NOT_CLEAN'):
            with ProjectExecutionSession(self.root, self.root, binding, purpose='dirty'):
                pass

    def test_pack_command_substitution_is_blocked(self):
        binding = self._binding()
        with ProjectExecutionSession(self.root, self.root, binding, purpose='substitution') as session:
            with self.assertRaisesRegex(ProjectExecutionError, 'PROJECT_COMMAND_PACK_BINDING_MISMATCH'):
                session.run('unit', ['python', '-c', 'print(1)'])

    def test_evidence_is_exact_bound_self_sealed_and_network_truth_is_not_overclaimed(self):
        binding = self._binding()
        with ProjectExecutionSession(self.root, self.root, binding, purpose='evidence') as session:
            observation = session.run('unit', binding['commands']['unit']['command'])
            self.assertEqual(observation.process.returncode, 0)
        evidence = copy.deepcopy(session.evidence)
        self.assertEqual(evidence['head_sha'], self.head)
        self.assertEqual(evidence['tree_sha'], self.tree)
        self.assertEqual(evidence['pack_digest'], binding['pack_digest'])
        self.assertEqual(evidence['network_enforcement'], NETWORK_ENFORCEMENT)
        self.assertEqual(validate_execution_evidence(evidence, self.root, expected_head=self.head, expected_pack_digest=binding['pack_digest']), [])
        evidence['outcome'] = 'BLOCK'
        self.assertIn('PROJECT_EXECUTION_EVIDENCE_DIGEST_MISMATCH', validate_execution_evidence(evidence, self.root))

    def test_controlled_long_running_process_shutdown_is_not_false_failure(self):
        binding = self._binding()
        command = ['python', '-c', 'import time; time.sleep(30)']
        with ProjectExecutionSession(self.root, self.root, binding, purpose='server') as session:
            process = session.popen('fixture-server', command, pack_bound=False)
            process.terminate(); process.wait(timeout=5)
            observation = session.record_process('fixture-server', command, process, pack_bound=False, expected_termination=True)
            self.assertEqual(observation.safety_status, 'PASS')
            self.assertNotIn('PROJECT_COMMAND_NONZERO_EXIT', observation.reason_codes)
        self.assertEqual(validate_execution_evidence(session.evidence, self.root), [])

    def test_pack_cannot_expand_secret_or_environment_authority(self):
        pack_path = self.root / '.adwf/packs/node.json'
        pack = json.loads(pack_path.read_text(encoding='utf-8'))
        pack['safety']['secrets'] = 'REQUIRED'
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with self.assertRaises(Exception):
            self._binding()


if __name__ == '__main__':
    unittest.main()
