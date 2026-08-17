import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.contracts import validate
from lib.session_continuity import build_checkpoint, checkpoint_digest, reconcile_checkpoint, validate_checkpoint

MAIN = 'a' * 40
HEAD = 'b' * 40


def checkpoint(**updates):
    data = build_checkpoint(
        checkpoint_id='SESSION-CORE-001:0001',
        checkpoint_revision=0,
        project_identity='kmephis-ai/AI-Development-Framework',
        roadmap_id='SESSION-CORE-001',
        issue_id='142',
        main_sha=MAIN,
        pr_number=143,
        head_sha=HEAD,
        branch='agent/SESSION-CORE-001-wunb-continuity-contract',
        boundary_type='EXTERNAL_WAIT',
        pending_external={'provider':'github-actions','object_ref':'run:1','status':'in_progress'},
        next_permitted_action='Fresh-read exact-head CI and continue if terminal.',
        safe_handover_summary='Implementation committed; exact-head CI is the next provider fact.',
        conflict_domains=['.adwf/instructions/**', '.adwf/lib/session_continuity.py'],
        created_at='2026-08-18T00:00:00Z',
        updated_at='2026-08-18T00:00:00Z',
    )
    data.update(updates)
    data['checkpoint_digest'] = checkpoint_digest(data)
    return data


class SessionContinuityTests(unittest.TestCase):
    def test_valid_checkpoint_matches_schema(self):
        value = checkpoint()
        self.assertEqual(validate_checkpoint(value), [])
        schema = json.loads((ROOT / '.adwf/schemas/session-continuity-checkpoint.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(validate(value, schema), [])

    def test_digest_tamper_is_rejected(self):
        value = checkpoint()
        value['safe_handover_summary'] = 'Tampered after digest.'
        self.assertIn('CONTINUITY_DIGEST', validate_checkpoint(value))

    def test_private_reasoning_and_secret_like_text_are_rejected(self):
        value = checkpoint()
        value['safe_handover_summary'] = 'token sk-abcdefghijklmnopqrstuvwxyz123456'
        value['checkpoint_digest'] = checkpoint_digest(value)
        errors = validate_checkpoint(value)
        self.assertTrue(any(item.startswith('CONTINUITY_FORBIDDEN_SECRET_LIKE_TEXT') for item in errors))

        value = checkpoint()
        value['private_reasoning_trace'] = 'hidden'
        value['checkpoint_digest'] = checkpoint_digest(value)
        errors = validate_checkpoint(value)
        self.assertTrue(any(item.startswith('CONTINUITY_FORBIDDEN_FIELD') for item in errors))

    def test_commit_pr_merge_are_not_natural_boundaries(self):
        for boundary in ('COMMIT', 'PR_CREATED', 'MERGE'):
            value = checkpoint(boundary_type=boundary)
            value['checkpoint_digest'] = checkpoint_digest(value)
            self.assertIn('CONTINUITY_NOT_NATURAL_BOUNDARY', validate_checkpoint(value))

    def test_stale_observation_never_becomes_provider_authority(self):
        value = checkpoint()
        result = reconcile_checkpoint(value, actual_main_sha='c' * 40, actual_head_sha='d' * 40)
        self.assertTrue(result['stale'])
        self.assertFalse(result['provider_authority'])
        self.assertEqual(result['next_step'], 'FRESH_AUTHORITY_RESOLUTION_REQUIRED')

    def test_core_instruction_contract_enforces_wunb(self):
        core = (ROOT / '.adwf/instructions/CORE.md').read_text(encoding='utf-8')
        self.assertIn('WORK_UNTIL_NATURAL_BOUNDARY', core)
        self.assertIn('commit, открытие PR', core)
        self.assertIn('не являются Natural Boundary', core)
        self.assertIn('stale checkpoint', core.lower())


if __name__ == '__main__':
    unittest.main()
