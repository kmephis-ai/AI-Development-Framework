import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.adwf_core import evaluate_adversarial_case


class AdversarialTests(unittest.TestCase):
    def test_all_negative_canaries_are_rejected(self):
        fixture_dir = ROOT / ".adwf/tests/fixtures"
        names = [
            "false_progress_100_done_broken.json", "stale_review.json", "blocked_dependency_ready.json",
            "trust_downgrade.json", "agent_conflict.json", "dependency_cycle.json", "architecture_drift.json",
            "debt_budget.json", "oversized_issue.json", "r4_automerge.json", "false_pass_no_evidence.json",
            "split_brain.json",
        ]
        for name in names:
            with self.subTest(name=name):
                case = json.loads((fixture_dir / name).read_text(encoding="utf-8"))
                accepted, detail = evaluate_adversarial_case(case)
                self.assertTrue(accepted, detail)


if __name__ == "__main__":
    unittest.main()

