import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.adwf_core import issue_quality, review_fresh, roadmap_audit


class CoreTests(unittest.TestCase):
    def test_well_specified_issue_passes(self):
        issue = {"id": "PERF-1", "roadmap_id": "PERF-1", "number": 1, "title": "Ускорить загрузку", "state": "SPECIFIED", "priority": "P1", "risk": "R1", "type": "feature", "goal": "Сократить время загрузки Dashboard до бюджета", "acceptance_criteria": ["Время <4s"], "verification_plan": ["Измерить p95"], "conflict_domains": ["dashboard-performance"], "dependencies": [], "dependencies_resolved": True, "human_required": False, "autonomy_allowed": True, "product_impact": True, "roadmap_order": 1}
        self.assertEqual(issue_quality(issue)["status"], "PASS")

    def test_incomplete_issue_never_passes(self):
        issue = {"id": "PERF-1", "roadmap_id": "PERF-1", "risk": "R1", "goal": "Сократить время загрузки Dashboard", "acceptance_criteria": ["Время <4s"], "verification_plan": ["Измерить p95"], "conflict_domains": ["ui"], "human_required": False}
        result = issue_quality(issue)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any(item.startswith("required_field_missing:") for item in result["findings"]))

    def test_missing_review_sha_is_not_verified(self):
        self.assertEqual(review_fresh(None, "abc"), "NOT_VERIFIED")

    def test_unknown_product_blocks_feature_roadmap(self):
        result = roadmap_audit({"product_health": "NOT_VERIFIED", "metrics": {"implementation": 0.1, "verification": 0.1}, "issues": []})
        self.assertEqual(result["health"], "CRITICAL")
        self.assertFalse(result["autopilot_feature_progression"])


if __name__ == "__main__":
    unittest.main()
