import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.orchestration import continue_decision

POLICY = {
    "action": "claim", "autonomy": "A2", "risk": "R0", "max_autonomous_risk": "R1",
    "health": {"package_integrity": "VERIFIED", "config_health": "VERIFIED", "control_plane_health": "VERIFIED", "product_health": "VERIFIED"},
    "provider_allowed": True, "provider_potentially_paid": False, "projected_cost": 0,
}


class ContinueTests(unittest.TestCase):
    def active_lease(self, issue_id="X", **values):
        now = datetime.now(timezone.utc)
        lease = {"lease_id": "L", "issue_id": issue_id, "status": "ACTIVE",
                 "heartbeat_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
                 "workspace_status": "ACTIVE", "workspace_id": "WS-L"}
        lease.update(values)
        return lease

    def issue(self, ident, state="READY", priority="P1", order=1):
        leased = state in {"CLAIMED", "IN_PROGRESS", "REVIEW"}
        return {"id": ident, "roadmap_id": ident, "state": state, "priority": priority, "risk": "R1", "type": "feature", "conflict_domains": [ident], "dependencies_resolved": True, "human_required": False, "autonomy_allowed": True, "roadmap_order": order, "ready_since": "2026-08-13T00:00:00Z", "lease_id": "L" if leased else None, "workspace_id": "WS-L" if leased else None}

    def test_exactly_one_writer_is_continued(self):
        queue = {"leases": [self.active_lease()], "issues": [self.issue("X", "IN_PROGRESS")]}
        result = continue_decision(queue, POLICY)
        self.assertEqual(result["action"], "CONTINUE_EXISTING")
        self.assertEqual(result["issue"]["id"], "X")

    def test_multiple_writers_force_reconcile(self):
        leases = [{"lease_id": str(i), "issue_id": str(i), "status": "ACTIVE", "expires_at": "2099-01-01T00:00:00Z"} for i in (1, 2)]
        result = continue_decision({"leases": leases, "issues": []}, POLICY)
        self.assertEqual(result["action"], "RECONCILE")

    def test_expired_declared_active_lease_forces_reconcile(self):
        queue = {"leases": [self.active_lease(expires_at="2000-01-01T00:00:00Z")], "issues": [self.issue("X", "IN_PROGRESS")]}
        result = continue_decision(queue, POLICY)
        self.assertEqual(result["action"], "RECONCILE")
        self.assertEqual(result["reason"], "EXPIRED_OR_INVALID_ACTIVE_LEASE")

    def test_stalled_workspace_forces_reconcile(self):
        queue = {"leases": [self.active_lease(workspace_status="STALLED")], "issues": [self.issue("X", "IN_PROGRESS")]}
        result = continue_decision(queue, POLICY)
        self.assertEqual(result["action"], "RECONCILE")
        self.assertEqual(result["reason"], "WORKSPACE_NOT_ACTIVE")

    def test_review_precedes_new_ready(self):
        queue = {"leases": [self.active_lease("OLD")], "issues": [self.issue("NEW", "READY", "P0", 1), self.issue("OLD", "REVIEW", "P2", 2)]}
        result = continue_decision(queue, POLICY)
        self.assertEqual(result["action"], "CONTINUE_EXISTING")
        self.assertEqual(result["issue"]["id"], "OLD")

    def test_deterministically_claims_one_ready(self):
        queue = {"leases": [], "issues": [self.issue("B", order=2), self.issue("A", priority="P0", order=3)]}
        result = continue_decision(queue, POLICY)
        self.assertEqual(result["action"], "CLAIM_ONE_READY")
        self.assertEqual(result["issue"]["id"], "A")

    def test_unknown_health_blocks_ready(self):
        policy = dict(POLICY, health=dict(POLICY["health"], product_health="NOT_VERIFIED"))
        result = continue_decision({"leases": [], "issues": [self.issue("A")]}, policy)
        self.assertEqual(result["action"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
