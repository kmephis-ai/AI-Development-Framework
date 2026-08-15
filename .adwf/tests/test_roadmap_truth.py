from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.roadmap_view import build_roadmap_view, critical_path_scores, derive_verified_progress, validate_roadmap_graph


NOW = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)


def fresh_state(product="VERIFIED"):
    head = "a" * 40
    return {
        "health": {"adwf": "VERIFIED", "product": product},
        "main": {"head": head, "health": "PASS"},
        "snapshot": {
            "source_main_sha": head,
            "observed_at": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "valid_until": (NOW + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        },
    }


class RoadmapTruthTests(unittest.TestCase):
    def test_graph_rejects_unknown_self_duplicate_and_cycle(self):
        cases = [
            ([{"roadmap_id": "A-1", "dependencies": ["B-1"]}], "UNKNOWN_DEPENDENCY"),
            ([{"roadmap_id": "A-1", "dependencies": ["A-1"]}], "SELF_DEPENDENCY"),
            ([{"roadmap_id": "A-1", "dependencies": []}, {"roadmap_id": "A-1", "dependencies": []}], "DUPLICATE_ID"),
            ([{"roadmap_id": "A-1", "dependencies": ["B-1"]}, {"roadmap_id": "B-1", "dependencies": ["A-1"]}], "DEPENDENCY_CYCLE"),
        ]
        for tasks, prefix in cases:
            with self.subTest(prefix=prefix):
                result = validate_roadmap_graph(tasks)
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(any(item.startswith(prefix) for item in result["errors"]), result)

    def test_done_without_fresh_exact_evidence_is_not_verified(self):
        tasks = [{"roadmap_id": "A-1", "state": "DONE", "product_impact": True}]
        progress = derive_verified_progress(tasks, {"health": {"adwf": "VERIFIED", "product": "VERIFIED"}}, now=NOW)
        self.assertEqual(progress["implementation"], 1.0)
        self.assertEqual(progress["verification"], 0.0)
        self.assertEqual(progress["outcome_readiness"], 0.0)
        self.assertTrue(progress["false_progress"])

    def test_broken_product_cannot_be_outcome_ready(self):
        tasks = [{"roadmap_id": "A-1", "state": "DONE", "product_impact": True}]
        state = fresh_state(product="BROKEN")
        progress = derive_verified_progress(tasks, state, now=NOW)
        self.assertEqual(progress["verification"], 1.0)
        self.assertEqual(progress["outcome_readiness"], 0.0)
        self.assertTrue(progress["false_progress"])

    def test_fresh_exact_snapshot_and_product_health_produce_truthful_axes(self):
        tasks = [{"roadmap_id": "A-1", "state": "DONE", "product_impact": True}]
        progress = derive_verified_progress(tasks, fresh_state(), now=NOW)
        self.assertEqual(progress["implementation"], 1.0)
        self.assertEqual(progress["verification"], 1.0)
        self.assertEqual(progress["outcome_readiness"], 1.0)
        self.assertFalse(progress["false_progress"])

    def test_ready_node_is_effectively_blocked_until_dependency_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".adwf").mkdir()
            (root / ".adwf" / "roadmap.json").write_text(
                '{"schema_version":1,"goals":[{"id":"G","title_ru":"G","tasks":['
                '{"roadmap_id":"BASE-1","title_ru":"Base","dependencies":[],"product_impact":false},'
                '{"roadmap_id":"NEXT-1","title_ru":"Next","dependencies":["BASE-1"],"product_impact":true}]}]}',
                encoding="utf-8",
            )
            state = fresh_state()
            state["work_items"] = [
                {"roadmap_id": "BASE-1", "state": "VERIFICATION", "dependencies": [], "product_impact": False},
                {"roadmap_id": "NEXT-1", "state": "READY", "dependencies": ["BASE-1"], "product_impact": True},
            ]
            view = build_roadmap_view(root, state, now=NOW)
            nxt = view["goals"][0]["tasks"][1]
            self.assertFalse(nxt["dependencies_resolved"])
            self.assertEqual(nxt["effective_state"], "BLOCKED")
            self.assertEqual(nxt["blocked_by"], ["BASE-1"])
            self.assertNotIn("NEXT-1", view["summary"]["ready_frontier"])

    def test_critical_path_score_is_deterministic_downstream_depth(self):
        tasks = [
            {"roadmap_id": "A-1", "dependencies": []},
            {"roadmap_id": "B-1", "dependencies": ["A-1"]},
            {"roadmap_id": "C-1", "dependencies": ["B-1"]},
            {"roadmap_id": "D-1", "dependencies": ["A-1"]},
        ]
        scores = critical_path_scores(tasks)
        self.assertEqual(scores["A-1"], 3)
        self.assertEqual(scores["B-1"], 2)
        self.assertEqual(scores["C-1"], 1)
        self.assertEqual(scores["D-1"], 1)


if __name__ == "__main__":
    unittest.main()
