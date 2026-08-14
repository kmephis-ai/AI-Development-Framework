import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.leases import claim, heartbeat, iso, reconcile


class LeaseTests(unittest.TestCase):
    SHA = "a" * 40
    def queue(self):
        return {"leases": [], "issues": [{"id": "X", "roadmap_id": "RM-1", "state": "READY", "risk": "R1", "type": "feature", "conflict_domains": ["ui"], "dependencies_resolved": True, "human_required": False, "autonomy_allowed": True, "ready_since": "2026-08-13T09:00:00Z"}]}

    def test_claim_changes_exact_issue_and_creates_ttl(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        queue, lease = claim(self.queue(), "X", "writer-1", self.SHA, now=now, ttl_minutes=30, permission_allowed=True)
        self.assertEqual(queue["issues"][0]["state"], "IN_PROGRESS")
        self.assertEqual(lease["expires_at"], "2026-08-13T10:30:00Z")
        self.assertEqual(lease["workspace_id"], "rm-1-issue-x")
        self.assertEqual(lease["branch"], "adwf/rm-1-issue-x")

    def test_second_writer_is_blocked(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        queue, _ = claim(self.queue(), "X", "writer-1", self.SHA, now=now, permission_allowed=True)
        queue["issues"].append({"id": "Y", "roadmap_id": "RM-2", "state": "READY", "conflict_domains": ["docs"], "dependencies_resolved": True, "human_required": False, "autonomy_allowed": True, "ready_since": "2026-08-13T09:00:00Z"})
        with self.assertRaisesRegex(ValueError, "ACTIVE_WRITER_EXISTS"):
            claim(queue, "Y", "writer-2", self.SHA, now=now, permission_allowed=True)

    def test_expired_lease_enters_recovery(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        queue = {"leases": [{"lease_id": "L", "issue_id": "X", "status": "ACTIVE", "expires_at": iso(now - timedelta(minutes=1))}], "issues": [{"id": "X", "state": "IN_PROGRESS"}]}
        updated, expired = reconcile(queue, now=now)
        self.assertEqual(expired, ["L"])
        self.assertEqual(updated["issues"][0]["state"], "RECOVERY")

    def test_wrong_worker_cannot_heartbeat(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        _, lease = claim(self.queue(), "X", "writer-1", self.SHA, now=now, permission_allowed=True)
        with self.assertRaisesRegex(ValueError, "LEASE_OWNER_MISMATCH"):
            heartbeat(lease, "writer-2", now=now + timedelta(minutes=1))

    def test_claim_without_permission_is_blocked(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "PERMISSION_NOT_ALLOWED"):
            claim(self.queue(), "X", "writer-1", self.SHA, now=now)

    def test_short_or_non_hex_base_sha_is_blocked(self):
        now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "WORKER_OR_SHA_MISSING"):
            claim(self.queue(), "X", "writer-1", "abc1234", now=now, permission_allowed=True)


if __name__ == "__main__":
    unittest.main()
