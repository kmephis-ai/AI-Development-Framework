import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.policy import DecisionContext, evaluate_permission
from lib.policy_compiler import compile_policy
from lib.policy_runtime import verify_policy_ir


SAFE = {
    "package_integrity": "VERIFIED",
    "config_health": "VERIFIED",
    "control_plane_health": "VERIFIED",
    "product_health": "VERIFIED",
}


class ExecutablePolicyTests(unittest.TestCase):
    def test_compiled_policy_is_executable_and_hash_bound(self):
        policy, errors = compile_policy(ROOT)
        self.assertEqual(errors, [])
        self.assertEqual(verify_policy_ir(policy), [])
        decision = evaluate_permission(
            DecisionContext(action="claim", autonomy="A2", health=SAFE, expected_policy_hash=policy["policy_hash"]),
            policy,
        )
        self.assertEqual(decision.result, "ALLOW")
        self.assertEqual(decision.policy_hash, policy["policy_hash"])

    def test_tampered_policy_is_rejected_by_verifier(self):
        policy, _ = compile_policy(ROOT)
        tampered = copy.deepcopy(policy)
        tampered["rules"]["action_min_autonomy"]["merge"] = "A1"
        self.assertIn("POLICY_HASH_MISMATCH", verify_policy_ir(tampered))
        self.assertEqual(
            evaluate_permission(DecisionContext(action="merge", autonomy="A1", health=SAFE), tampered).result,
            "BLOCK",
        )

    def test_stale_policy_hash_blocks_action(self):
        policy, _ = compile_policy(ROOT)
        decision = evaluate_permission(
            DecisionContext(action="claim", autonomy="A2", health=SAFE, expected_policy_hash="0" * 64),
            policy,
        )
        self.assertEqual(decision.result, "BLOCK")
        self.assertIn("POLICY_HASH_MISMATCH", decision.reason_codes)

    def test_provider_facts_must_be_fresh(self):
        policy, _ = compile_policy(ROOT)
        decision = evaluate_permission(
            DecisionContext(action="claim", autonomy="A2", health=SAFE, provider_facts_fresh=False),
            policy,
        )
        self.assertEqual(decision.result, "BLOCK")
        self.assertIn("PROVIDER_FACTS_NOT_FRESH", decision.reason_codes)


if __name__ == "__main__":
    unittest.main()
