import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.contracts import validate
from lib.autonomous_execution_state import (
    build_state,
    cas_update,
    owner_projection,
    reconcile_provider_observation,
    validate_state,
)

MAIN = 'a' * 40
HEAD = 'b' * 40
LEASE = '574233d2-6de7-43ec-8966-1d60fddb4ce2'


def state(**updates):
    value = build_state(
        repository='kmephis-ai/AI-Development-Framework',
        roadmap_id='ORCH-STATE-001',
        issue_id='155',
        lease_id=LEASE,
        lease_state='ACTIVE',
        conflict_domains=['.adwf/lib/autonomous_execution_state.py', '.adwf/tests/test_autonomous_execution_state.py'],
        main_sha=MAIN,
        head_sha=HEAD,
        pr_number=156,
        branch='agent/ORCH-STATE-001-provider-durable-state',
        evidence_refs=['github:commit:' + HEAD],
    )
    if updates:
        # Use the public CAS path so integrity is always recomputed.
        value = cas_update(value, expected_revision=0, changes=updates)
    return value


class AutonomousExecutionStateTests(unittest.TestCase):
    def test_valid_state_matches_schema(self):
        value = state()
        self.assertEqual(validate_state(value), [])
        schema = json.loads((ROOT / '.adwf/schemas/autonomous-execution-state.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(validate(value, schema), [])

    def test_tamper_is_rejected(self):
        value = state()
        value['next_permitted_action'] = 'UNVERIFIED_MUTATION'
        self.assertIn('INTEGRITY_DIGEST', validate_state(value))

    def test_stale_provider_observation_blocks_write_authority(self):
        value = state()
        result = reconcile_provider_observation(
            value,
            main_sha='c' * 40,
            head_sha=HEAD,
            pr_number=156,
            branch='agent/ORCH-STATE-001-provider-durable-state',
        )
        self.assertTrue(result['stale'])
        self.assertFalse(result['write_authorized'])

    def test_fresh_provider_observation_is_not_stale(self):
        value = state()
        result = reconcile_provider_observation(
            value,
            main_sha=MAIN,
            head_sha=HEAD,
            pr_number=156,
            branch='agent/ORCH-STATE-001-provider-durable-state',
        )
        self.assertFalse(result['stale'])
        self.assertTrue(result['write_authorized'])

    def test_duplicate_conflict_domains_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'CONFLICT_DOMAIN_DUPLICATE'):
            build_state(
                repository='kmephis-ai/AI-Development-Framework',
                roadmap_id='ORCH-STATE-001', issue_id='155', lease_id=LEASE,
                lease_state='ACTIVE', conflict_domains=['same', 'same'], main_sha=MAIN,
            )

    def test_invalid_lifecycle_combinations_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'RUNNING_BOUNDARY_CONFLICT'):
            build_state(
                repository='kmephis-ai/AI-Development-Framework',
                roadmap_id='ORCH-STATE-001', issue_id='155', lease_id=LEASE,
                lease_state='ACTIVE', conflict_domains=['domain'], main_sha=MAIN,
                execution_state='RUNNING', boundary_type='HUMAN_REQUIRED',
            )
        with self.assertRaisesRegex(ValueError, 'WAITING_CI_BOUNDARY'):
            build_state(
                repository='kmephis-ai/AI-Development-Framework',
                roadmap_id='ORCH-STATE-001', issue_id='155', lease_id=LEASE,
                lease_state='ACTIVE', conflict_domains=['domain'], main_sha=MAIN,
                execution_state='WAITING_CI', boundary_type='NONE',
            )

    def test_private_reasoning_or_secret_markers_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN_CONTENT'):
            build_state(
                repository='kmephis-ai/AI-Development-Framework',
                roadmap_id='ORCH-STATE-001', issue_id='155', lease_id=LEASE,
                lease_state='ACTIVE', conflict_domains=['domain'], main_sha=MAIN,
                blockers=['private reasoning must not be stored'],
            )
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN_CONTENT'):
            build_state(
                repository='kmephis-ai/AI-Development-Framework',
                roadmap_id='ORCH-STATE-001', issue_id='155', lease_id=LEASE,
                lease_state='ACTIVE', conflict_domains=['domain'], main_sha=MAIN,
                executor_audit={'note': 'access_token must not be persisted'},
            )

    def test_cas_revision_conflict_is_rejected(self):
        value = state()
        with self.assertRaisesRegex(ValueError, 'REVISION_CONFLICT'):
            cas_update(value, expected_revision=2, changes={'execution_state': 'RECOVERY'})

    def test_owner_projection_is_bounded_and_public_safe(self):
        value = state(
            execution_state='WAITING_CI',
            boundary_type='WAITING_EXTERNAL',
            blockers=['Exact-head CI in progress'],
            next_permitted_action='READBACK_EXACT_HEAD_CI',
            last_verified_transition='PR_OPENED',
        )
        projection = owner_projection(value)
        self.assertEqual(projection['work_item'], 'ORCH-STATE-001')
        self.assertEqual(projection['writer_lease_state'], 'ACTIVE')
        self.assertEqual(projection['blocker'], 'Exact-head CI in progress')
        self.assertNotIn('executor_audit', projection)
        self.assertNotIn('integrity_digest', projection)

    def test_identity_is_immutable_through_cas(self):
        value = state()
        with self.assertRaisesRegex(ValueError, 'IDENTITY_IMMUTABLE'):
            cas_update(value, expected_revision=0, changes={'work_identity': {'roadmap_id': 'OTHER', 'issue_id': '1'}})


if __name__ == '__main__':
    unittest.main()
