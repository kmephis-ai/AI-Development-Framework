import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.adwf'))

from lib.github_runtime_store import (
    GitHubRuntimeStore,
    ROOT_ANCHOR,
    ROOT_ROLE,
    TITLE,
    public_memory_projection,
    verify_remote_events,
)
from lib.github_rulesets import runtime_anchor_ruleset_payload
from lib.provider_contracts import ProviderContractError
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
        self.repo = 'kmephis-ai/AI-Development-Framework'
        self._issues = []
        self.comments = {}
        self.cid = 0
        self.tags = {}
        self.tag_objects = {}
        self.next_tag = 0
        self.hide_issue_list = False
        self.tag_ref_error = None
        self.competing_root_on_create = False

    def issues(self):
        if self.hide_issue_list:
            return []
        return [dict(item) for item in self._issues]

    def get(self, path):
        prefix = f'/repos/{self.repo}/issues/'
        if path.startswith(prefix):
            number = int(path[len(prefix):])
            for item in self._issues:
                if item['number'] == number:
                    return dict(item)
            raise ProviderContractError('PROVIDER_HTTP_404')
        raise AssertionError(f'UNEXPECTED_GET:{path}')

    def create_issue(self, title, body):
        item = {
            'number': len(self._issues) + 1,
            'title': title,
            'body': body,
            'state': 'open',
            'created_at': '2026-08-18T00:00:00Z',
            'user': {'login': 'owner'},
        }
        self._issues.append(item)
        self.comments[item['number']] = []
        return dict(item)

    def close_issue(self, number):
        for item in self._issues:
            if item['number'] == number:
                item['state'] = 'closed'
                return dict(item)
        raise ProviderContractError('PROVIDER_HTTP_404')

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

    def _install_competing_root(self):
        competitor = self.create_issue(TITLE, 'competing candidate')
        self.next_tag += 1
        sha = f'{self.next_tag:040x}'
        message = json.dumps(
            {'issue_number': competitor['number'], 'role': ROOT_ROLE, 'title': TITLE},
            sort_keys=True,
            separators=(',', ':'),
        )
        self.tag_objects[sha] = {'sha': sha, 'message': message, 'object': {'sha': MAIN}}
        self.tags[ROOT_ANCHOR] = {'ref': 'refs/tags/' + ROOT_ANCHOR, 'object': {'sha': sha}}
        return competitor

    def create_tag_ref(self, tag, sha):
        if tag == ROOT_ANCHOR and self.competing_root_on_create:
            self.competing_root_on_create = False
            self._install_competing_root()
            raise ProviderContractError('PROVIDER_HTTP_422')
        if tag in self.tags:
            raise ProviderContractError('PROVIDER_HTTP_422')
        item = {'ref': 'refs/tags/' + tag, 'object': {'sha': sha}}
        self.tags[tag] = item
        return item

    def tag_ref(self, tag):
        if self.tag_ref_error:
            raise ProviderContractError(self.tag_ref_error)
        if tag not in self.tags:
            raise ProviderContractError('PROVIDER_HTTP_404')
        return self.tags[tag]

    def tag_object(self, sha):
        return self.tag_objects[sha]


class SessionContinuityPersistenceTests(unittest.TestCase):
    def test_valid_checkpoint_is_persisted_and_verified_by_provider_readback(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        value = checkpoint()
        result = store.append(runtime_state(), session_checkpoint=value)
        self.assertEqual(result['status'], 'APPENDED')
        self.assertIn(ROOT_ANCHOR, fake.tags)
        _, events = store.read()
        self.assertEqual(verify_remote_events(events), [])
        persisted = events[-1]['session_continuity_projection']
        self.assertEqual(persisted['checkpoint_digest'], value['checkpoint_digest'])
        self.assertEqual(events[-1]['session_continuity_id'], value['checkpoint_id'])
        self.assertEqual(events[-1]['session_continuity_revision'], value['checkpoint_revision'])

    def test_read_without_ledger_is_read_only_and_does_not_create_issue(self):
        fake = FakeRuntimeGitHub()
        issue, events = GitHubRuntimeStore(fake).read()
        self.assertIsNone(issue)
        self.assertEqual(events, [])
        self.assertEqual(fake._issues, [])
        self.assertEqual(fake.tags, {})

    def test_fresh_restore_uses_root_when_issue_list_lags(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        first = store.append(runtime_state(), session_checkpoint=checkpoint())
        self.assertEqual(first['issue_number'], 1)
        fake.hide_issue_list = True

        fresh = GitHubRuntimeStore(fake)
        issue, events = fresh.read()
        self.assertEqual(issue['number'], 1)
        self.assertEqual(len(events), 1)
        restored = fresh.restore_latest_session_continuity(actual_main_sha=MAIN, actual_head_sha=HEAD)
        self.assertEqual(restored['checkpoint']['checkpoint_digest'], checkpoint()['checkpoint_digest'])
        self.assertEqual(len(fake._issues), 1)

    def test_competing_creator_root_election_closes_loser_before_append(self):
        fake = FakeRuntimeGitHub()
        fake.competing_root_on_create = True
        store = GitHubRuntimeStore(fake)

        result = store.append(runtime_state(), session_checkpoint=checkpoint())

        self.assertEqual(result['issue_number'], 2)
        self.assertEqual(fake._issues[0]['state'], 'closed')
        self.assertEqual(fake._issues[1]['state'], 'open')
        self.assertEqual(fake.comments[1], [])
        self.assertEqual(len(fake.comments[2]), 1)
        root = store._root_issue()
        self.assertEqual(root['number'], 2)
        open_ledgers = [item for item in fake._issues if item['title'] == TITLE and item['state'] == 'open']
        self.assertEqual([item['number'] for item in open_ledgers], [2])

    def test_legacy_unique_ledger_is_adopted_without_rewriting_existing_events(self):
        fake = FakeRuntimeGitHub()
        legacy = fake.create_issue(TITLE, 'legacy')
        store = GitHubRuntimeStore(fake)

        result = store.append(runtime_state(), session_checkpoint=checkpoint())

        self.assertEqual(result['issue_number'], legacy['number'])
        self.assertEqual(store._root_issue()['number'], legacy['number'])
        self.assertEqual(len(fake._issues), 1)
        _, events = store.read()
        self.assertEqual(len(events), 1)

    def test_multiple_legacy_ledgers_without_root_fail_closed(self):
        fake = FakeRuntimeGitHub()
        fake.create_issue(TITLE, 'legacy-a')
        fake.create_issue(TITLE, 'legacy-b')
        store = GitHubRuntimeStore(fake)

        with self.assertRaisesRegex(ValueError, 'MULTIPLE_RUNTIME_LEDGER_ISSUES'):
            store.append(runtime_state(), session_checkpoint=checkpoint())
        self.assertNotIn(ROOT_ANCHOR, fake.tags)
        self.assertEqual(fake.cid, 0)

    def test_root_absence_does_not_mask_provider_uncertainty(self):
        fake = FakeRuntimeGitHub()
        fake.tag_ref_error = 'PROVIDER_HTTP_403'
        store = GitHubRuntimeStore(fake)

        with self.assertRaisesRegex(ProviderContractError, 'PROVIDER_HTTP_403'):
            store.append(runtime_state(), session_checkpoint=checkpoint())
        self.assertEqual(fake._issues, [])
        self.assertEqual(fake.cid, 0)

    def test_root_anchor_is_not_counted_as_orphan_event_anchor(self):
        fake = FakeRuntimeGitHub()
        store = GitHubRuntimeStore(fake)
        store.append(runtime_state(), session_checkpoint=checkpoint())
        issue, events = store.read()
        self.assertEqual(issue['number'], 1)
        self.assertEqual(len(events), 1)
        self.assertIn(ROOT_ANCHOR, fake.tags)
        self.assertEqual(store._verify_tag_anchors(events), [])

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
