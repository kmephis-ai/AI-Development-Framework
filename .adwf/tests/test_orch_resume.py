import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.autonomous_execution_state import build_state, cas_update
from lib.lease_registry import acquire_registry_lease, empty_lease_registry
from lib.orch_resume import decide_resume
from lib.session_continuity import build_checkpoint, checkpoint_digest

REPO = 'kmephis-ai/AI-Development-Framework'
MAIN = 'a' * 40
ALT_MAIN = 'c' * 40
HEAD = 'b' * 40
ALT_HEAD = 'd' * 40
BASE = MAIN
ROADMAP = 'ORCH_RESUME-001'
ISSUE = '245'
BRANCH = 'agent/ORCH_RESUME-001-acquire-resume-yield'
WORKER = 'chatgpt-gpt5.6-sol'
LEASE = '11111111-1111-4111-8111-111111111111'
NOW = datetime(2026, 8, 21, 9, 40, tzinfo=timezone.utc)


def resources():
    return [{'kind': 'source', 'scope': 'repo/orch-resume', 'shared': False, 'global': False}]


def registry(*, main_sha=MAIN, worker=WORKER, branch=BRANCH, now=NOW, ttl=120, ceiling=1):
    value = empty_lease_registry(REPO, main_sha, max_parallel_writers=ceiling)
    value, lease = acquire_registry_lease(
        value,
        expected_revision=0,
        observed_main_sha=main_sha,
        policy_max_parallel_writers=ceiling,
        issue_id=ISSUE,
        roadmap_id=ROADMAP,
        worker_id=worker,
        base_sha=BASE,
        branch=branch,
        resources=resources(),
        now=now,
        ttl_minutes=ttl,
        lease_id=LEASE,
    )
    return value, lease


def execution(*, main_sha=MAIN, head_sha=HEAD, branch=BRANCH, state='RUNNING', boundary='NONE', lease_id=LEASE, lease_state='ACTIVE'):
    return build_state(
        repository=REPO,
        roadmap_id=ROADMAP,
        issue_id=ISSUE,
        lease_id=lease_id,
        lease_state=lease_state,
        conflict_domains=['source:repo/orch-resume'],
        main_sha=main_sha,
        head_sha=head_sha,
        pr_number=246,
        branch=branch,
        execution_state=state,
        boundary_type=boundary,
        next_permitted_action='CONTINUE',
    )


def checkpoint(*, main_sha=MAIN, head_sha=HEAD, branch=BRANCH, lease_id=LEASE, boundary='EXECUTOR_LIMIT'):
    return build_checkpoint(
        checkpoint_id='ORCH_RESUME-001:0001',
        checkpoint_revision=1,
        project_identity=REPO,
        roadmap_id=ROADMAP,
        issue_id=ISSUE,
        lease_identity=lease_id,
        conflict_domains=['source:repo/orch-resume'],
        main_sha=main_sha,
        pr_number=246,
        head_sha=head_sha,
        branch=branch,
        boundary_type=boundary,
        next_permitted_action='Fresh-read provider facts and resume if authority matches.',
        safe_handover_summary='Bounded public-safe resume context.',
        created_at='2026-08-21T09:39:00Z',
        updated_at='2026-08-21T09:39:00Z',
    )


def decide(registry_value, **overrides):
    args = dict(
        repository=REPO,
        main_sha=MAIN,
        head_sha=HEAD,
        pr_number=246,
        branch=BRANCH,
        roadmap_id=ROADMAP,
        issue_id=ISSUE,
        expected_base_sha=BASE,
        current_worker_id=WORKER,
        policy_max_parallel_writers=1,
        lease_registry=registry_value,
        execution_state=execution(),
        checkpoint=checkpoint(),
        now=NOW,
    )
    args.update(overrides)
    return decide_resume(**args)


class OrchResumeTests(unittest.TestCase):
    def test_exact_matching_active_authority_returns_resume_existing_without_granting_write(self):
        value, _ = registry()
        result = decide(value)
        self.assertEqual(result['decision'], 'RESUME_EXISTING')
        self.assertEqual(result['lease_id'], LEASE)
        self.assertFalse(result['provider_write_authorized'])

    def test_session_accelerator_never_changes_authority_decision(self):
        value, _ = registry()
        baseline = decide(value, session_accelerator_present=False)
        accelerated = decide(value, session_accelerator_present=True)
        self.assertEqual(baseline, accelerated)

    def test_checkpoint_without_provider_lease_cannot_resume(self):
        value = empty_lease_registry(REPO, MAIN, max_parallel_writers=1)
        result = decide(
            value,
            execution_state=None,
            checkpoint=checkpoint(),
            next_work_authorized=True,
        )
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'RESUME_CONTEXT_WITHOUT_ACTIVE_LEASE')

    def test_stale_execution_observation_requires_reconcile(self):
        value, _ = registry()
        result = decide(value, execution_state=execution(main_sha=ALT_MAIN))
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'EXECUTION_PROVIDER_OBSERVATION_STALE')

    def test_stale_checkpoint_head_requires_reconcile(self):
        value, _ = registry()
        result = decide(value, checkpoint=checkpoint(head_sha=ALT_HEAD))
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'CHECKPOINT_PROVIDER_OBSERVATION_STALE')

    def test_expired_active_lease_requires_reconcile_not_replacement(self):
        value, _ = registry(now=NOW - timedelta(hours=3), ttl=120)
        result = decide(value)
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'ACTIVE_LEASE_STALE_OR_EXPIRED')

    def test_lease_work_identity_mismatch_blocks(self):
        value, _ = registry(branch='agent/other-authority')
        result = decide(value)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'LEASE_WORK_IDENTITY_MISMATCH')

    def test_different_worker_requires_explicit_handoff(self):
        value, _ = registry(worker='scheduled-executor')
        result = decide(value)
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'LEASE_OWNER_HANDOFF_REQUIRED')

    def test_waiting_ci_with_fresh_in_progress_status_yields(self):
        value, _ = registry()
        state = execution(state='WAITING_CI', boundary='WAITING_EXTERNAL', lease_state='SUSPENDED')
        cp = checkpoint(boundary='EXTERNAL_WAIT')
        result = decide(value, execution_state=state, checkpoint=cp, external_status='in_progress')
        self.assertEqual(result['decision'], 'YIELD')
        self.assertEqual(result['reason'], 'EXTERNAL_WAIT_IN_PROGRESS')

    def test_completed_external_wait_can_resume_existing(self):
        value, _ = registry()
        state = execution(state='WAITING_CI', boundary='WAITING_EXTERNAL', lease_state='SUSPENDED')
        cp = checkpoint(boundary='EXTERNAL_WAIT')
        result = decide(value, execution_state=state, checkpoint=cp, external_status='success')
        self.assertEqual(result['decision'], 'RESUME_EXISTING')

    def test_human_required_blocks(self):
        value, _ = registry()
        state = execution(state='HUMAN_REQUIRED', boundary='HUMAN_REQUIRED', lease_state='SUSPENDED')
        result = decide(value, execution_state=state, human_boundary_active=True)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'HUMAN_BOUNDARY_ACTIVE')

    def test_clean_no_lease_can_only_request_acquisition_when_next_work_authorized(self):
        value = empty_lease_registry(REPO, MAIN, max_parallel_writers=1)
        result = decide(
            value,
            execution_state=None,
            checkpoint=None,
            next_work_authorized=True,
        )
        self.assertEqual(result['decision'], 'ACQUIRE_NEW')
        self.assertFalse(result['provider_write_authorized'])

    def test_terminal_work_reconciles_next_never_resumes_old_writer(self):
        value, _ = registry()
        result = decide(value, work_terminal=True)
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'WORK_TERMINAL_RECONCILE_NEXT')

    def test_tampered_execution_state_blocks_before_resume(self):
        value, _ = registry()
        state = execution()
        state['next_permitted_action'] = 'TAMPERED'
        result = decide(value, execution_state=state)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'EXECUTION_STATE_INVALID')

    def test_tampered_checkpoint_blocks_before_resume(self):
        value, _ = registry()
        cp = checkpoint()
        cp['safe_handover_summary'] = 'tampered after digest'
        result = decide(value, checkpoint=cp)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'SESSION_CHECKPOINT_INVALID')

    def test_tampered_registry_blocks_before_resume(self):
        value, _ = registry()
        value['observed_main_sha'] = ALT_MAIN
        result = decide(value)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'LEASE_REGISTRY_INVALID')

    def test_valid_active_registry_bound_to_old_main_requires_reconcile(self):
        value, _ = registry(main_sha=ALT_MAIN)
        result = decide(value)
        self.assertEqual(result['decision'], 'RECONCILE')
        self.assertEqual(result['reason'], 'ACTIVE_LEASE_REGISTRY_MAIN_STALE')

    def test_stage1_rejects_writer_ceiling_above_one(self):
        value = empty_lease_registry(REPO, MAIN, max_parallel_writers=2)
        result = decide(
            value,
            policy_max_parallel_writers=2,
            execution_state=None,
            checkpoint=None,
            next_work_authorized=True,
        )
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'STAGE1_REQUIRES_SINGLETON_WRITER_CEILING')

    def test_execution_lease_identity_mismatch_blocks(self):
        value, _ = registry()
        state = execution(lease_id='22222222-2222-4222-8222-222222222222')
        result = decide(value, execution_state=state)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'EXECUTION_LEASE_IDENTITY_MISMATCH')

    def test_checkpoint_lease_identity_mismatch_blocks(self):
        value, _ = registry()
        cp = checkpoint(lease_id='22222222-2222-4222-8222-222222222222')
        result = decide(value, checkpoint=cp)
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertEqual(result['reason'], 'CHECKPOINT_LEASE_IDENTITY_MISMATCH')


if __name__ == '__main__':
    unittest.main()
