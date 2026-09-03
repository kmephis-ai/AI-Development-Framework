import copy
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.assurance import snapshot_digest
from lib.lease_registry import acquire_registry_lease, empty_lease_registry
from lib.orch_next import bootstrap_next_run, selector_queue
from lib.policy_runtime import load_effective_policy
from lib.work_memory import WorkMemoryStore

SHA = "5f8d34de822f6fe372780cb8a87ed7322c60ab9e"


class OrchNextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".adwf").mkdir()
        (self.root / ".adwf/effective-policy.json").write_text(
            (ROOT / ".adwf/effective-policy.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.policy = load_effective_policy(self.root)
        now = datetime.now(timezone.utc)
        self.state = {
            "status": "ACTIVE",
            "main": {"head": SHA, "health": "PASS"},
            "health": {"adwf": "VERIFIED", "product": "VERIFIED"},
            "queue": {"ready": 1, "in_progress": 0, "review": 0, "blocked": 0, "human_required": 0},
            "work_items": [self.issue()],
            "snapshot": {
                "observed_at": now.isoformat().replace("+00:00", "Z"),
                "valid_until": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
                "source_main_sha": SHA,
                "evidence_digest": "a" * 64,
            },
        }
        self.registry = empty_lease_registry("kmephis-ai/AI-Development-Framework", SHA, max_parallel_writers=1)
        self.readback = {
            "subject_sha": SHA,
            "facts_readback_verified": True,
            "repository_visibility": "PUBLIC",
            "larger_runner": False,
        }
        self.assurance = {
            "schema_version": 1,
            "subject_sha": SHA,
            "policy_hash": self.policy["policy_hash"],
            "health": {
                "package_integrity": "VERIFIED",
                "config_health": "VERIFIED",
                "control_plane_health": "HEALTHY",
                "product_health": "VERIFIED",
            },
            "gates": {},
            "required_gates": [],
            "evidence": {"refs_resolved": True},
            "provider": {"readback_verified": True},
            "cost": {"status": "VERIFIED_ZERO", "projected_cost_usd": 0.0},
            "verified_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        }
        self.assurance["snapshot_digest"] = snapshot_digest(self.assurance)

    def tearDown(self):
        self.tmp.cleanup()

    def issue(self, *, human_required=False):
        return {
            "id": "NEXT-001", "roadmap_id": "NEXT-001", "number": 999, "title": "Next",
            "state": "READY", "priority": "P0", "risk": "R1", "type": "feature",
            "goal": "Implement the next safe bounded outcome.", "conflict_domains": ["framework:next"],
            "dependencies": [], "dependencies_resolved": True, "human_required": human_required,
            "autonomy_allowed": True, "product_impact": False, "roadmap_order": 1,
            "critical_path_score": 10, "ready_since": "2026-09-03T00:00:00Z",
            "lease_id": None, "workspace_id": None,
        }

    def kwargs(self):
        return dict(project_state=self.state, lease_registry=self.registry,
                    assurance_snapshot=self.assurance, provider_readback=self.readback)

    def test_queue_shape_uses_work_items_not_counter_queue(self):
        queue = selector_queue(self.state, self.registry)
        self.assertEqual(queue["issues"][0]["roadmap_id"], "NEXT-001")
        self.assertEqual(queue["leases"], [])
        self.assertNotIn("ready", queue)

    def test_bootstraps_exactly_one_run_and_work_memory(self):
        result = bootstrap_next_run(self.root, **self.kwargs())
        self.assertEqual(result["status"], "RUN_CREATED")
        self.assertTrue(result["durable_run_created"])
        self.assertFalse(result["provider_claim_mutation"])
        memory = WorkMemoryStore(self.root).load()
        self.assertEqual(memory["run_id"], result["run_id"])
        self.assertEqual(memory["references"]["issues"], [999])

    def test_repeat_is_idempotent(self):
        first = bootstrap_next_run(self.root, **self.kwargs())
        second = bootstrap_next_run(self.root, **self.kwargs())
        self.assertEqual(first["status"], "RUN_CREATED")
        self.assertEqual(second["status"], "ACTIVE_RUN_EXISTS")
        self.assertEqual(second["run_id"], first["run_id"])

    def test_two_threads_create_only_one_run(self):
        barrier = threading.Barrier(2)
        results = []
        def worker():
            barrier.wait()
            results.append(bootstrap_next_run(self.root, **self.kwargs()))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(item["status"] == "RUN_CREATED" for item in results), 1)
        self.assertEqual(sum(item["status"] == "ACTIVE_RUN_EXISTS" for item in results), 1)

    def test_human_required_ready_item_blocks_without_run(self):
        self.state["work_items"] = [self.issue(human_required=True)]
        result = bootstrap_next_run(self.root, **self.kwargs())
        self.assertEqual(result["status"], "BLOCK")
        self.assertFalse(result["durable_run_created"])

    def test_no_work_is_clean_idle(self):
        self.state["work_items"] = []
        self.state["queue"]["ready"] = 0
        result = bootstrap_next_run(self.root, **self.kwargs())
        self.assertEqual(result["status"], "NO_ROADMAP_WORK")
        self.assertFalse(result["durable_run_created"])

    def test_active_provider_writer_blocks_bootstrap(self):
        now = datetime.now(timezone.utc)
        registry, _ = acquire_registry_lease(
            self.registry, expected_revision=0, observed_main_sha=SHA, policy_max_parallel_writers=1,
            issue_id="309", roadmap_id="ORCH_NEXT-001", worker_id="adwf-runtime:test",
            base_sha=SHA, branch="adwf/orch-next-test",
            resources=[{"kind":"source","scope":".adwf/lib/orch_next.py","shared":False,"global":False}], now=now,
            lease_id="632303be-6fa6-4148-9b75-96114bbf29b2",
        )
        result = bootstrap_next_run(self.root, project_state=self.state, lease_registry=registry,
                                    assurance_snapshot=self.assurance, provider_readback=self.readback)
        self.assertEqual(result["status"], "WRITER_BUSY")
        self.assertFalse(result["durable_run_created"])

    def test_stale_snapshot_blocks(self):
        self.state["snapshot"]["valid_until"] = "2000-01-01T00:00:00Z"
        result = bootstrap_next_run(self.root, **self.kwargs())
        self.assertEqual(result["status"], "BLOCK")
        self.assertIn("STALE", result["reason"])


if __name__ == "__main__":
    unittest.main()
