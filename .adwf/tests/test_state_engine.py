import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.state_engine import apply_transition, evaluate_transition


class StateEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.machine = json.loads((ROOT / ".adwf/state-machine.json").read_text(encoding="utf-8"))

    def test_ready_claim_requires_every_precondition_true(self):
        predicates = {name: True for name in self.machine["preconditions"]["READY->CLAIMED"]}
        predicates["lease_absent"] = None
        result = evaluate_transition({"state": "READY"}, "CLAIMED", self.machine, predicates, expected_state="READY")
        self.assertEqual(result.result, "BLOCK")
        self.assertIn("lease_absent", result.missing_preconditions)

    def test_compare_and_set_expected_state(self):
        result = evaluate_transition({"state": "READY"}, "CLAIMED", self.machine, {}, expected_state="SPECIFIED")
        self.assertEqual(result.reason_codes, ("EXPECTED_STATE_MISMATCH",))

    def test_done_needs_runtime_evidence(self):
        required = self.machine["preconditions"]["VERIFICATION->DONE"]
        predicates = {name: True for name in required}
        predicates["runtime_evidence_if_required"] = False
        _, result = apply_transition({"state": "VERIFICATION"}, "DONE", self.machine, predicates)
        self.assertEqual(result.result, "BLOCK")

    def test_valid_transition_applies(self):
        predicates = {name: True for name in self.machine["preconditions"]["REVIEW->VERIFICATION"]}
        item, result = apply_transition({"state": "REVIEW", "id": "X"}, "VERIFICATION", self.machine, predicates)
        self.assertEqual(result.result, "ALLOW")
        self.assertEqual(item["state"], "VERIFICATION")


if __name__ == "__main__":
    unittest.main()

