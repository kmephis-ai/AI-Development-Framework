import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.github_runtime_store import GitHubRuntimeStore, public_memory_projection, verify_remote_events
from lib.github_rulesets import runtime_anchor_ruleset_payload
from lib.session_continuity import build_checkpoint, checkpoint_digest, validate_checkpoint

MAIN = 'a' * 40
HEAD = 'b' * 40


def runtime_state(*, revision=1):
    return {
        'schema_version': 1,
        'run_id': 'run-12345678',
        'roadmap_id': 'SESSION_PERSIST-001',
        'issue_id': '145',
        'risk': 'R1',
        'work_type': 'feature',
        'product_impact': False,
        'owner_request_digest': 'c' * 64,
        'phase': 'RECONCILE',
        'status': 'RUNNING',
        'cycle': 0,
        'subject_sha': HEAD,
        'preview_digest': None,
        'owner_acceptance_sha': None,
        'policy_hash': 'd' * 64,
        'attempts': {},
        'max_attempts': 3,
        'max_cycles': 10,
        'deadline_at': '2099-01-01T00:00:00Z',
        'last_failed_phase': None,
        'blockers': [],
        'monetary_budget_usd': 0,
        'events': [],
        'event_head': None,
        'revision': revision,
        'created_at': '2026-08-18T00:00:00Z',
        'updated_at': '2026-08-18T00:00:00Z',
    }


def checkpoint(*, main_sha=MAIN, head_sha=HEAD, revision=0):
    return build_checkpoint(
        checkpoint_id='SESSION_PERSIST-001:0001',
        checkpoint_revision=revision,
        project_identity='kmephis-ai/AI-Development-Framework',
        roadmap_id='SESSION_PERSIST-001',
        issue_id='145',
        main_sha=main_sha,
        pr_number=146,
        head_sha=head_sha,
        branch='agent/SESSION-PERSIST-001-runtime-ledger-continuity',
        boundary_type='EXTERNAL_WAIT',
        pending_external={'provider': 'github-actions', 'object_ref': 'run:123', 'status': 'in_progress'},
        next_permitted_action='Fresh-read exact-head CI and continue if terminal.',
        safe_handover_summary='Implementation persisted; fresh provider facts decide whether resume is safe.',
        conflict_domains=['.adwf/lib/github_runtime_store.py'],
        created_at='2026-08-18T00:00:00Z',
        updated_at='2026-08-18T00:00:00Z',
    )


class FakeRuntimeGitHub:
    def __init__(self):
        self._issues = []
        self.comments = {}
        self.cid = 0
        self.tags = {}
        self.tag_objects = {}
        self.next_tag = 0

    def issues(self):
        return list(self._issues)

    def create_issue(self, title, body):
        item = {'number': len(self._issues) + 1, 'title': title, 'body': body, 'state': 'open'}
        self._issues.append(item)
        self.comments[item['number']] = []
        return item

    def issue_comments(self, number):
        return list(self.comments.get(number, []))

    def add_issue_comment(self, number, body):
        self.cid += 1
        item = {'id': self.cid, 'body': body, 'created_at': '2026-08-18T00:00:00Z', 'user': {'login': 'owner'}}
        self.comments[number].append(item)
        return item

    def rulesets(self):
        return [{'id': 91, **runtime_anchor_ruleset_payload()}]

    def matching_tag_refs(self, prefix):
        return [value for key, value in sorted(self.tags.items()) if key.startswith(prefix)]

    def repo_info(self):
        return {'default_branch': 'main', 'visibility': 'public', 'private': False}

    def branch(self, name):
        return {'commit': {'sha': MAIN}}

    def create_tag_object(self, tag, target, message):
        self.next_tag += 1
        sha = f'{self.next_tag:040x}'
        item = {'sha': sha, 'message': message, 'object': {'sha': target}}
        self.tag_objects[sha] = item
        return item

    def create_tag_ref(self, tag, sha):
        item = {'ref': 'refs/tags/' + tag, 'object': {'sha': sha}}
        self.tags[tag] = item
        return item

    def tag_object(self, sha):
        return self.tag_objects[sha]


class SessionContinuityPersistenceTests(unittest.TestCase):
    def test_valid_checkpoint_is_persisted_and_verified_by_provider_readback(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        value = checkpoint()
        result = store.append(runtime_state(), session_checkpoint=value)
        self.assertEqual(result['status'], 'APPENDED')
        _, events = store.read()
        self.assertEqual(verify_remote_events(events), [])
        persisted = events[-1]['session_continuity_projection']
        self.assertEqual(persisted['checkpoint_digest'], value['checkpoint_digest'])
        self.assertEqual(events[-1]['session_continuity_id'], value['checkpoint_id'])
        self.assertEqual(events[-1]['session_continuity_revision'], value['checkpoint_revision'])

    def test_invalid_or_private_checkpoint_is_rejected_before_provider_write(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        value = checkpoint()
        value['safe_handover_summary'] = 'Tampered after digest.'
        with self.assertRaisesRegex(ValueError, 'REMOTE_RUNTIME_SESSION_CONTINUITY_INVALID'):
            store.append(runtime_state(), session_checkpoint=value)
        self.assertEqual(fake.cid, 0)

        value = checkpoint()
        value['safe_handover_summary'] = 'access token sk-abcdefghijklmnopqrstuvwxyz123456'
        value['checkpoint_digest'] = checkpoint_digest(value)
        with self.assertRaisesRegex(ValueError, 'CONTINUITY_FORBIDDEN_SECRET_LIKE_TEXT'):
            store.append(runtime_state(), session_checkpoint=value)
        self.assertEqual(fake.cid, 0)

    def test_schema_strictness_rejects_unknown_public_field(self):
        value = checkpoint()
        value['innocent_but_undeclared'] = 'still not allowed'
        value['checkpoint_digest'] = checkpoint_digest(value)
        self.assertTrue(any(item.startswith('CONTINUITY_SCHEMA:$.innocent_but_undeclared:additionalProperties') for item in validate_checkpoint(value)))

    def test_checkpoint_work_identity_must_bind_to_runtime_state(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        value = checkpoint()
        value['work_identity']['roadmap_id'] = 'OTHER-WORK'
        value['checkpoint_digest'] = checkpoint_digest(value)
        with self.assertRaisesRegex(ValueError, 'REMOTE_SESSION_CONTINUITY_ROADMAP_BINDING'):
            store.append(runtime_state(), session_checkpoint=value)
        self.assertEqual(fake.cid, 0)

    def test_fresh_session_restore_is_context_only_and_stale_facts_require_resolution(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        store.append(runtime_state(), session_checkpoint=checkpoint())

        matching = store.restore_latest_session_continuity(actual_main_sha=MAIN, actual_head_sha=HEAD)
        self.assertIsNotNone(matching)
        self.assertFalse(matching['reconciliation']['provider_authority'])
        self.assertFalse(matching['reconciliation']['stale'])
        self.assertEqual(matching['reconciliation']['next_step'], 'RESUME_CONTEXT_ONLY')

        stale = store.restore_latest_session_continuity(actual_main_sha='e' * 40, actual_head_sha='f' * 40)
        self.assertTrue(stale['reconciliation']['stale'])
        self.assertFalse(stale['reconciliation']['provider_authority'])
        self.assertEqual(stale['reconciliation']['next_step'], 'FRESH_AUTHORITY_RESOLUTION_REQUIRED')

    def test_legacy_runtime_event_without_checkpoint_remains_readable(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        store.append(runtime_state())
        _, events = store.read()
        self.assertNotIn('session_continuity_projection', events[-1])
        self.assertEqual(verify_remote_events(events), [])
        self.assertIsNone(store.restore_latest_session_continuity(actual_main_sha=MAIN, actual_head_sha=HEAD))

    def test_remote_tamper_and_private_work_memory_fields_fail_closed(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        memory = {
            'schema_version': 1,
            'brief_id': 'BRIEF-12345678',
            'run_id': 'run-12345678',
            'status': 'ACTIVE',
            'revision': 1,
            'memory_digest': 'a' * 64,
            'task_ru': 'must never enter public projection',
            'session_handoffs': [{'private': 'never'}],
        }
        result = store.append(runtime_state(), work_memory=memory, session_checkpoint=checkpoint())
        projection = result['event']['work_memory_projection']
        self.assertEqual(set(projection), {'schema_version', 'brief_id', 'run_id', 'status', 'revision', 'memory_digest'})
        self.assertEqual(projection, public_memory_projection(memory))

        event = copy.deepcopy(result['event'])
        event['session_continuity_projection']['safe_handover_summary'] = 'changed without checkpoint digest update'
        errors = verify_remote_events([event])
        self.assertTrue(any('REMOTE_SESSION_CONTINUITY_INVALID:CONTINUITY_DIGEST' in item for item in errors))


if __name__ == '__main__':
    unittest.main()
