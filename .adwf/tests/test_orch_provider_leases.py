import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.contracts import validate
from lib.github_lease_store import (
    GitHubLeaseStore,
    LEASE_ANCHOR_PREFIX,
    _build_event,
    _canonical_text,
    _tag_name,
)
from lib.github_rulesets import runtime_anchor_ruleset_payload
from lib.lease_registry import (
    acquire_registry_lease,
    canonical_conflict_resources,
    conflicting_resource_keys,
    empty_lease_registry,
    release_registry_lease,
    validate_lease_registry,
)
from lib.provider_contracts import ProviderContractError

MAIN = "a" * 40
OTHER_MAIN = "b" * 40
BASE = "c" * 40


def resource(kind="source", scope="repo/src", *, shared=False, global_resource=False):
    return {"kind": kind, "scope": scope, "shared": shared, "global": global_resource}


def resources(*items):
    return canonical_conflict_resources(list(items))


def reseal_registry(value):
    payload = copy.deepcopy(value)
    payload.pop("integrity_digest", None)
    value["integrity_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


class FakeGitHubClient:
    def __init__(self, main_sha=MAIN):
        self.repo = "example/repo"
        self.main_sha = main_sha
        self.tags = {}
        self.tag_objects = {}
        self.next_object = 1000
        self.ruleset_verified = True
        self.before_create_ref = None
        self.after_create_ref = None

    def repo_info(self):
        return {"default_branch": "main"}

    def branch(self, name):
        if name != "main":
            raise ProviderContractError("PROVIDER_HTTP_404")
        return {"commit": {"sha": self.main_sha}}

    def rulesets(self):
        if not self.ruleset_verified:
            return []
        return [{"id": 91, **runtime_anchor_ruleset_payload()}]

    def matching_tag_refs(self, prefix):
        return [copy.deepcopy(value) for key, value in sorted(self.tags.items()) if key.startswith(prefix)]

    def create_tag_object(self, tag, target, message):
        self.next_object += 1
        sha = f"{self.next_object:040x}"
        item = {
            "sha": sha,
            "tag": tag,
            "message": message,
            "object": {"sha": target, "type": "commit"},
        }
        self.tag_objects[sha] = copy.deepcopy(item)
        return copy.deepcopy(item)

    def create_tag_ref(self, tag, sha):
        if self.before_create_ref is not None:
            callback, self.before_create_ref = self.before_create_ref, None
            callback(self, tag, sha)
        if tag in self.tags:
            raise ProviderContractError("PROVIDER_HTTP_422")
        item = {"ref": "refs/tags/" + tag, "object": {"sha": sha}}
        self.tags[tag] = copy.deepcopy(item)
        if self.after_create_ref is not None:
            callback, self.after_create_ref = self.after_create_ref, None
            callback(self, tag, sha)
        return copy.deepcopy(item)

    def tag_ref(self, tag):
        if tag not in self.tags:
            raise ProviderContractError("PROVIDER_HTTP_404")
        return copy.deepcopy(self.tags[tag])

    def tag_object(self, sha):
        if sha not in self.tag_objects:
            raise ProviderContractError("PROVIDER_HTTP_404")
        return copy.deepcopy(self.tag_objects[sha])

    def install_event(self, registry, previous_tag_object_sha=None):
        event = _build_event(registry, previous_tag_object_sha)
        name = _tag_name(registry["revision"])
        obj = self.create_tag_object(name, registry["observed_main_sha"], _canonical_text(event))
        if name in self.tags:
            raise AssertionError("duplicate fixture tag")
        self.tags[name] = {"ref": "refs/tags/" + name, "object": {"sha": obj["sha"]}}
        return obj["sha"]


class TypedConflictResourceTests(unittest.TestCase):
    def test_exact_parent_child_and_shared_projection_overlap(self):
        self.assertEqual(
            conflicting_resource_keys(resources(resource(scope="repo/src")), resources(resource(scope="repo/src/app.py"))),
            ["source:repo/src"],
        )
        self.assertEqual(
            conflicting_resource_keys(
                resources(resource("projection", "canonical/package-integrity", shared=True)),
                resources(resource("projection", "canonical/package-integrity", shared=True)),
            ),
            ["projection:canonical/package-integrity"],
        )

    def test_runtime_provider_and_release_targets_conflict_semantically(self):
        for kind, scope in (
            ("runtime", "consumer/prod"),
            ("provider", "github/repo/issues"),
            ("release", "framework/v2"),
        ):
            self.assertEqual(
                conflicting_resource_keys(resources(resource(kind, scope)), resources(resource(kind, scope + "/child"))),
                [f"{kind}:{scope}"],
            )

    def test_semantically_different_resources_can_be_disjoint(self):
        self.assertEqual(
            conflicting_resource_keys(resources(resource("source", "repo/src/a")), resources(resource("runtime", "consumer/prod"))),
            [],
        )
        self.assertEqual(
            conflicting_resource_keys(resources(resource("source", "repo/src/a")), resources(resource("source", "repo/docs"))),
            [],
        )

    def test_global_governance_conflicts_with_everything(self):
        global_governance = resources(resource("governance", "global", shared=True, global_resource=True))
        self.assertEqual(
            conflicting_resource_keys(global_governance, resources(resource("data", "consumer/fin-truth"))),
            ["global:global"],
        )
        with self.assertRaisesRegex(ValueError, "GLOBAL_MUST_BE_SHARED"):
            canonical_conflict_resources([resource("governance", "global", global_resource=True)])

    def test_unknown_ambiguous_duplicate_and_noncanonical_resources_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "KIND_INVALID"):
            canonical_conflict_resources([resource("mystery", "repo/src")])
        with self.assertRaisesRegex(ValueError, "SCOPE_INVALID|SCOPE_AMBIGUOUS"):
            canonical_conflict_resources([resource(scope="repo/../secret")])
        with self.assertRaisesRegex(ValueError, "DUPLICATE"):
            canonical_conflict_resources([resource(), resource()])
        unsorted = [resource("source", "repo/src"), resource("projection", "canonical/x", shared=True)]
        registry = empty_lease_registry("example/repo", MAIN)
        with self.assertRaisesRegex(ValueError, "NOT_CANONICAL"):
            acquire_registry_lease(
                registry,
                expected_revision=0,
                observed_main_sha=MAIN,
                policy_max_parallel_writers=1,
                issue_id="1",
                roadmap_id="ORCH_LEASE-001",
                worker_id="worker-a",
                base_sha=BASE,
                branch="agent/a",
                resources=unsorted,
                lease_id="11111111-1111-4111-8111-111111111111",
                now=datetime(2026, 8, 21, 4, tzinfo=timezone.utc),
            )


class LeaseRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 21, 4, tzinfo=timezone.utc)

    def acquire(self, registry, *, resource_list=None, lease_id="11111111-1111-4111-8111-111111111111", ceiling=1):
        return acquire_registry_lease(
            registry,
            expected_revision=registry["revision"],
            observed_main_sha=MAIN,
            policy_max_parallel_writers=ceiling,
            issue_id="236",
            roadmap_id="ORCH_LEASE-001",
            worker_id="worker-a",
            base_sha=BASE,
            branch="agent/orch-lease",
            resources=resource_list or resources(resource("source", "repo/lib/leases.py")),
            lease_id=lease_id,
            now=self.now,
            ttl_minutes=30,
        )

    def test_empty_registry_is_schema_valid_and_integrity_bound(self):
        registry = empty_lease_registry("example/repo", MAIN)
        self.assertEqual(validate_lease_registry(registry), [])
        schema = json.loads((ROOT / ".adwf/schemas/writer-lease-registry.schema.json").read_text())
        self.assertEqual(validate(registry, schema), [])
        tampered = dict(registry)
        tampered["revision"] = 9
        self.assertIn("LEASE_REGISTRY_INTEGRITY", validate_lease_registry(tampered))

    def test_singleton_policy_blocks_even_disjoint_second_resource(self):
        registry = empty_lease_registry("example/repo", MAIN, max_parallel_writers=1)
        registry, _ = self.acquire(registry)
        with self.assertRaisesRegex(ValueError, "ACTIVE_WRITER_EXISTS"):
            acquire_registry_lease(
                registry,
                expected_revision=registry["revision"],
                observed_main_sha=MAIN,
                policy_max_parallel_writers=1,
                issue_id="237",
                roadmap_id="OTHER-001",
                worker_id="worker-b",
                base_sha=BASE,
                branch="agent/other",
                resources=resources(resource("source", "repo/docs")),
                lease_id="22222222-2222-4222-8222-222222222222",
                now=self.now + timedelta(minutes=1),
            )

    def test_future_ceiling_model_still_blocks_overlap(self):
        registry = empty_lease_registry("example/repo", MAIN, max_parallel_writers=2)
        registry, _ = self.acquire(registry, ceiling=2)
        with self.assertRaisesRegex(ValueError, "CONFLICT_RESOURCE_BUSY"):
            acquire_registry_lease(
                registry,
                expected_revision=registry["revision"],
                observed_main_sha=MAIN,
                policy_max_parallel_writers=2,
                issue_id="237",
           roadmap_id="OTHER-001",
                worker_id="worker-b",
                base_sha=BASE,
                branch="agent/other",
                resources=resources(resource("source", "repo/lib/leases.py/child")),
                lease_id="22222222-2222-4222-8222-222222222222",
                now=self.now + timedelta(minutes=1),
            )

    def test_resealed_registry_with_overlapping_active_leases_is_invalid(self):
        registry = empty_lease_registry("example/repo", MAIN, max_parallel_writers=2)
        registry, first = self.acquire(registry, ceiling=2)
        second = copy.deepcopy(first)
        second["lease_id"] = "22222222-2222-4222-8222-222222222222"
        second["generation"] = 2
        second["issue_id"] = "237"
        second["roadmap_id"] = "OTHER-001"
        second["worker_id"] = "worker-b"
        second["branch"] = "agent/other"
        registry["leases"].append(second)
        registry["revision"] = 2
        reseal_registry(registry)
        self.assertIn("LEASE_REGISTRY_ACTIVE_RESOURCE_CONFLICT", validate_lease_registry(registry))

    def test_policy_ceiling_cannot_be_self_expanded(self):
        registry = empty_lease_registry("example/repo", MAIN, max_parallel_writers=1)
        with self.assertRaisesRegex(ValueError, "POLICY_CEILING_MISMATCH"):
            self.acquire(registry, ceiling=2)

    def test_invalid_identity_and_ttl_fail_closed(self):
        registry = empty_lease_registry("example/repo", MAIN)
        kwargs = dict(
            expected_revision=0,
            observed_main_sha=MAIN,
            policy_max_parallel_writers=1,
            issue_id="236",
            roadmap_id="ORCH_LEASE-001",
            worker_id="worker-a",
            base_sha=BASE,
            branch="agent/orch-lease",
            resources=resources(resource()),
            lease_id="11111111-1111-4111-8111-111111111111",
            now=self.now,
        )
        with self.assertRaisesRegex(ValueError, "ROADMAP_ID_INVALID"):
            acquire_registry_lease(registry, **{**kwargs, "roadmap_id": "bad-id"})
        with self.assertRaisesRegex(ValueError, "IDENTITY_INVALID"):
            acquire_registry_lease(registry, **{**kwargs, "branch": "agent/../escape"})
        with self.assertRaisesRegex(ValueError, "TTL_INVALID"):
            acquire_registry_lease(registry, **{**kwargs, "ttl_minutes": 0})

    def test_expiry_requires_provider_reconciliation_before_release_or_reclaim(self):
        registry = empty_lease_registry("example/repo", MAIN)
        registry, lease = self.acquire(registry)
        later = self.now + timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "LEASE_RECONCILIATION_REQUIRED"):
            acquire_registry_lease(
                registry,
                expected_revision=registry["revision"],
                observed_main_sha=MAIN,
                policy_max_parallel_writers=1,
                issue_id="237",
                roadmap_id="OTHER-001",
                worker_id="worker-b",
                base_sha=BASE,
                branch="agent/other",
                resources=resources(resource("source", "repo/docs")),
                lease_id="22222222-2222-4222-8222-222222222222",
                now=later,
            )
        with self.assertRaisesRegex(ValueError, "PROVIDER_RECONCILIATION_REQUIRED"):
            release_registry_lease(
                registry,
                expected_revision=registry["revision"],
                lease_id=lease["lease_id"],
                worker_id="worker-a",
                observed_main_sha=MAIN,
                provider_reconciled=False,
                provider_reconciliation_ref="github:issue/236",
                now=later,
            )
        registry = release_registry_lease(
            registry,
            expected_revision=registry["revision"],
            lease_id=lease["lease_id"],
            worker_id="worker-a",
            observed_main_sha=MAIN,
            provider_reconciled=True,
            provider_reconciliation_ref="github:issue/236@fresh",
            now=later,
        )
        registry, next_lease = acquire_registry_lease(
            registry,
            expected_revision=registry["revision"],
            observed_main_sha=MAIN,
            policy_max_parallel_writers=1,
            issue_id="237",
            roadmap_id="OTHER-001",
            worker_id="worker-b",
            base_sha=BASE,
            branch="agent/other",
            resources=resources(resource("source", "repo/docs")),
            lease_id="22222222-2222-4222-8222-222222222222",
            now=later + timedelta(minutes=1),
        )
        self.assertEqual(next_lease["generation"], 3)
        self.assertEqual(registry["leases"][0]["status"], "RELEASED")
        self.assertEqual(registry["leases"][1]["status"], "ACTIVE")


class GitHubLeaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 21, 4, tzinfo=timezone.utc)
        self.client = FakeGitHubClient()
        self.store = GitHubLeaseStore(self.client)

    def acquire(self, *, lease_id="11111111-1111-4111-8111-111111111111"):
        return self.store.acquire(
            expected_main_sha=MAIN,
            policy_max_parallel_writers=1,
            issue_id="236",
            roadmap_id="ORCH_LEASE-001",
            worker_id="worker-a",
            base_sha=BASE,
            branch="agent/orch-lease",
            resources=resources(resource("source", "repo/lib/leases.py")),
            lease_id=lease_id,
            now=self.now,
            ttl_minutes=30,
        )

    def test_empty_read_is_read_only(self):
        state, anchor = self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        self.assertEqual(state["revision"], 0)
        self.assertIsNone(anchor)
        self.assertEqual(self.client.tags, {})
        self.assertEqual(self.client.tag_objects, {})

    def test_initial_acquire_is_immutable_provider_durable_and_exact(self):
        state, lease, anchor = self.acquire()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(lease["status"], "ACTIVE")
        self.assertEqual(len(anchor), 40)
        self.assertIn(_tag_name(1), self.client.tags)
        reread, reread_anchor = self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        self.assertEqual(reread, state)
        self.assertEqual(reread_anchor, anchor)
        obj = self.client.tag_object(anchor)
        self.assertEqual(obj["object"]["sha"], MAIN)

    def test_anchor_ruleset_is_mandatory_for_read_and_write(self):
        self.client.ruleset_verified = False
        with self.assertRaisesRegex(ValueError, "ANCHOR_RULESET_NOT_VERIFIED"):
            self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        with self.assertRaisesRegex(ValueError, "ANCHOR_RULESET_NOT_VERIFIED"):
            self.acquire()
        self.assertEqual(self.client.tags, {})

    def test_stale_main_blocks_before_provider_mutation(self):
        self.client.main_sha = OTHER_MAIN
        with self.assertRaisesRegex(ValueError, "MAIN_SHA_DRIFT"):
            self.acquire()
        self.assertEqual(self.client.tags, {})
        self.assertEqual(self.client.tag_objects, {})

    def test_two_executor_same_revision_race_has_exactly_one_authoritative_winner(self):
        winner = empty_lease_registry("example/repo", MAIN)
        winner, winner_lease = acquire_registry_lease(
            winner,
            expected_revision=0,
            observed_main_sha=MAIN,
            policy_max_parallel_writers=1,
            issue_id="999",
            roadmap_id="RACE-001",
            worker_id="worker-b",
            base_sha=BASE,
            branch="agent/race-b",
            resources=resources(resource("source", "repo/lib/leases.py")),
            lease_id="22222222-2222-4222-8222-222222222222",
            now=self.now,
        )

        def install_winner(client, tag, _candidate_sha):
            self.assertEqual(tag, _tag_name(1))
            client.install_event(winner)

        self.client.before_create_ref = install_winner
        with self.assertRaisesRegex(ValueError, "LEASE_PROVIDER_CAS_LOST:revision=1"):
            self.acquire()
        observed, _ = self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        active = [lease for lease in observed["leases"] if lease["status"] == "ACTIVE"]
        self.assertEqual([lease["lease_id"] for lease in active], [winner_lease["lease_id"]])
        self.assertEqual(len(self.client.tags), 1)

    def test_provider_ref_substitution_blocks_readback(self):
        def substitute(client, tag, _sha):
            malformed = client.create_tag_object(tag, MAIN, "{}")
            client.tags[tag] = {"ref": "refs/tags/" + tag, "object": {"sha": malformed["sha"]}}

        self.client.after_create_ref = substitute
        with self.assertRaisesRegex(ValueError, "EVENT_FIELDS_INVALID|READBACK_MISMATCH"):
            self.acquire()

    def test_chain_tamper_and_sequence_gap_block_read(self):
        state = empty_lease_registry("example/repo", MAIN)
        state, _ = acquire_registry_lease(
            state,
            expected_revision=0,
            observed_main_sha=MAIN,
            policy_max_parallel_writers=1,
            issue_id="236",
            roadmap_id="ORCH_LEASE-001",
            worker_id="worker-a",
            base_sha=BASE,
            branch="agent/orch-lease",
            resources=resources(resource()),
            lease_id="11111111-1111-4111-8111-111111111111",
            now=self.now,
        )
        anchor1 = self.client.install_event(state)
        released = release_registry_lease(
            state,
            expected_revision=1,
            lease_id=state["leases"][0]["lease_id"],
            worker_id="worker-a",
            observed_main_sha=MAIN,
            provider_reconciled=True,
            provider_reconciliation_ref="github:issue/236@fresh",
            now=self.now + timedelta(minutes=2),
        )
        self.client.install_event(released, previous_tag_object_sha="f" * 40)
        with self.assertRaisesRegex(ValueError, "CHAIN_MISMATCH"):
            self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        # Remove revision 1 to prove immutable-sequence gaps are fail closed.
        self.client.tags.pop(_tag_name(1))
        with self.assertRaisesRegex(ValueError, "SEQUENCE_GAP"):
            self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)
        self.assertEqual(len(anchor1), 40)

    def test_main_drift_after_cas_never_returns_pass_and_requires_reconciliation(self):
        def drift_main(client, _tag, _sha):
            client.main_sha = OTHER_MAIN

        self.client.after_create_ref = drift_main
        with self.assertRaisesRegex(ValueError, "MAIN_SHA_DRIFT"):
            self.acquire()
        self.assertIn(_tag_name(1), self.client.tags)
        observed, _ = self.store.read(expected_main_sha=OTHER_MAIN, policy_max_parallel_writers=1)
        self.assertEqual(observed["observed_main_sha"], MAIN)
        with self.assertRaisesRegex(ValueError, "RECONCILIATION_REQUIRED"):
            self.store.acquire(
                expected_main_sha=OTHER_MAIN,
                policy_max_parallel_writers=1,
                issue_id="237",
                roadmap_id="OTHER-001",
                worker_id="worker-b",
                base_sha=MAIN,
                branch="agent/other",
                resources=resources(resource("source", "repo/docs")),
                lease_id="22222222-2222-4222-8222-222222222222",
                now=self.now + timedelta(minutes=1),
            )

    def test_heartbeat_cannot_silently_adopt_new_main(self):
        state, lease, _ = self.acquire()
        self.assertEqual(state["observed_main_sha"], MAIN)
        self.client.main_sha = OTHER_MAIN
        with self.assertRaisesRegex(ValueError, "RECONCILIATION_REQUIRED"):
            self.store.heartbeat(
                expected_main_sha=OTHER_MAIN,
                policy_max_parallel_writers=1,
                lease_id=lease["lease_id"],
                worker_id="worker-a",
                now=self.now + timedelta(minutes=5),
            )
        self.assertNotIn(_tag_name(2), self.client.tags)

    def test_release_requires_provider_reconciliation_and_can_bind_new_main(self):
        _, lease, anchor1 = self.acquire()
        self.client.main_sha = OTHER_MAIN
        with self.assertRaisesRegex(ValueError, "PROVIDER_RECONCILIATION_REQUIRED"):
            self.store.release(
                expected_main_sha=OTHER_MAIN,
                policy_max_parallel_writers=1,
                lease_id=lease["lease_id"],
                worker_id="worker-a",
                provider_reconciled=False,
                provider_reconciliation_ref="github:issue/236@fresh",
                now=self.now + timedelta(minutes=5),
            )
        released, anchor2 = self.store.release(
            expected_main_sha=OTHER_MAIN,
            policy_max_parallel_writers=1,
            lease_id=lease["lease_id"],
            worker_id="worker-a",
            provider_reconciled=True,
            provider_reconciliation_ref="github:issue/236@fresh",
            now=self.now + timedelta(minutes=5),
        )
        self.assertEqual(released["revision"], 2)
        self.assertEqual(released["observed_main_sha"], OTHER_MAIN)
        self.assertEqual(released["leases"][0]["status"], "RELEASED")
        event2 = json.loads(self.client.tag_object(anchor2)["message"])
        self.assertEqual(event2["previous_tag_object_sha"], anchor1)
        self.assertEqual(self.client.tag_object(anchor2)["object"]["sha"], OTHER_MAIN)

    def test_resealed_registry_payload_substitution_is_bound_by_immutable_event_digest(self):
        state, _lease, anchor = self.acquire()
        obj = self.client.tag_objects[anchor]
        event = json.loads(obj["message"])
        event["registry"]["leases"][0]["worker_id"] = "attacker"
        reseal_registry(event["registry"])
        # Deliberately do not reseal the outer event: immutable event integrity must detect it.
        obj["message"] = _canonical_text(event)
        with self.assertRaisesRegex(ValueError, "EVENT_INTEGRITY"):
            self.store.read(expected_main_sha=MAIN, policy_max_parallel_writers=1)


if __name__ == "__main__":
    unittest.main()
