import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.policy import DecisionContext, evaluate_permission

SAFE = {"package_integrity": "VERIFIED", "config_health": "VERIFIED", "control_plane_health": "VERIFIED", "product_health": "VERIFIED"}


class PolicyMatrixTests(unittest.TestCase):
    def context(self, **overrides):
        value = {"action": "claim", "autonomy": "A2", "risk": "R0", "max_autonomous_risk": "R1", "health": SAFE}
        value.update(overrides)
        return DecisionContext.from_dict(value)

    def test_complete_a0_a4_r0_r4_matrix(self):
        for autonomy in [f"A{i}" for i in range(5)]:
            for risk in [f"R{i}" for i in range(5)]:
                with self.subTest(autonomy=autonomy, risk=risk):
                    result = evaluate_permission(self.context(autonomy=autonomy, risk=risk)).result
                    expected = "ALLOW" if autonomy in {"A2", "A3"} and risk in {"R0", "R1"} else "HUMAN_REQUIRED"
                    self.assertEqual(result, expected)

    def test_unknown_product_blocks_feature(self):
        health = dict(SAFE, product_health="NOT_VERIFIED")
        result = evaluate_permission(self.context(health=health))
        self.assertEqual(result.result, "BLOCK")
        self.assertIn("PRODUCT_HEALTH_BLOCKS_FEATURE", result.reason_codes)

    def test_recovery_allowed_when_product_broken(self):
        health = dict(SAFE, product_health="BROKEN")
        result = evaluate_permission(self.context(health=health, work_type="recovery"))
        self.assertEqual(result.result, "ALLOW")

    def test_merge_needs_exact_fresh_gates(self):
        result = evaluate_permission(self.context(action="merge", autonomy="A3", gates={"ci": "PASS", "review": "PASS"}, required_gates=["ci", "review"], exact_sha=False, evidence_fresh=True))
        self.assertEqual(result.result, "BLOCK")
        self.assertIn("SHA_NOT_EXACT", result.reason_codes)

    def test_paid_provider_stays_blocked_after_human_approval(self):
        result = evaluate_permission(self.context(provider_potentially_paid=True, human_approved=True))
        self.assertEqual(result.result, "BLOCK")

    def test_destructive_is_human_gated(self):
        result = evaluate_permission(self.context(action="delete", autonomy="A3", destructive=True))
        self.assertEqual(result.result, "HUMAN_REQUIRED")

    def test_unknown_context_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_DECISION_CONTEXT_FIELDS"):
            DecisionContext.from_dict({"action": "inspect", "surprise": True})


if __name__ == "__main__":
    unittest.main()
