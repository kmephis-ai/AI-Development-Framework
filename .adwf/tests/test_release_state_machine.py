import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.state_engine import evaluate_transition


class ReleaseStateMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.machine = json.loads((ROOT / ".adwf/release-state-machine.json").read_text(encoding="utf-8"))

    def test_visual_product_cannot_skip_owner_acceptance(self):
        result = evaluate_transition(
            {"state": "PREVIEWED"}, "STAGED", self.machine,
            {"promotion_authorized": True}, expected_state="PREVIEWED",
        )
        self.assertEqual(result.result, "BLOCK")

    def test_acceptance_requires_exact_sha_and_preview_digest(self):
        blocked = evaluate_transition(
            {"state": "PREVIEWED"}, "OWNER_ACCEPTED", self.machine,
            {"owner_acceptance_exact_sha": True}, expected_state="PREVIEWED",
        )
        allowed = evaluate_transition(
            {"state": "PREVIEWED"}, "OWNER_ACCEPTED", self.machine,
            {"owner_acceptance_exact_sha": True, "owner_acceptance_preview_digest": True},
            expected_state="PREVIEWED",
        )
        self.assertEqual(blocked.result, "BLOCK")
        self.assertEqual(allowed.result, "ALLOW")

    def test_build_success_cannot_promote_directly(self):
        result = evaluate_transition(
            {"state": "BUILT"}, "PROMOTED", self.machine, {}, expected_state="BUILT"
        )
        self.assertEqual(result.result, "BLOCK")

    def test_failed_runtime_can_roll_back_only_to_verified_target(self):
        blocked = evaluate_transition(
            {"state": "PAUSED"}, "ROLLED_BACK", self.machine,
            {"rollback_authorized": True}, expected_state="PAUSED",
        )
        allowed = evaluate_transition(
            {"state": "PAUSED"}, "ROLLED_BACK", self.machine,
            {"rollback_authorized": True, "rollback_target_verified": True},
            expected_state="PAUSED",
        )
        self.assertEqual(blocked.result, "BLOCK")
        self.assertEqual(allowed.result, "ALLOW")


if __name__ == "__main__":
    unittest.main()
