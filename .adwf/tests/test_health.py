import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.health import doctor, project_projection_health


class HealthTests(unittest.TestCase):
    def test_package_is_verified_but_template_product_is_not(self):
        result = doctor(ROOT)
        self.assertEqual(result["categories"]["package_integrity"]["status"], "VERIFIED", result)
        self.assertEqual(result["categories"]["config_health"]["status"], "VERIFIED", result)
        self.assertEqual(result["categories"]["control_plane_health"]["status"], "NOT_VERIFIED", result)
        self.assertEqual(result["categories"]["product_health"]["status"], "NOT_VERIFIED", result)
        self.assertEqual(result["overall"], "NOT_VERIFIED")

    def test_active_github_item_needs_fresh_project_readback(self):
        now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        cfg = {"provider": {"mode": "github"}, "github": {"project": {"enabled": True}},
               "orchestration": {"reconcile_ttl_minutes": 60}}
        state = {"active": {"issue": 7}, "snapshot": {"observed_at": "2026-08-13T11:30:00Z"},
                 "project_projection": {"status": "NOT_VERIFIED", "observed_at": None, "project_id": None, "item_id": None}}
        findings, not_verified = project_projection_health(cfg, state, now)
        self.assertEqual(findings, [])
        self.assertEqual(not_verified, ["PROJECT_PROJECTION_NOT_VERIFIED"])
        state["project_projection"] = {"status": "PASS", "observed_at": "2026-08-13T11:45:00Z", "project_id": "P", "item_id": "I"}
        self.assertEqual(project_projection_health(cfg, state, now), ([], []))


if __name__ == "__main__":
    unittest.main()
