import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.contracts import validate
from lib.cost_guard import ALLOWED_CLASSIFICATIONS, CAPABILITY_STATUSES


class CapabilityCostContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))

    def test_all_capabilities_have_a_valid_cost_contract(self):
        schema = json.loads((ROOT / ".adwf/schemas/capability.schema.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / ".adwf/capabilities.json").read_text(encoding="utf-8"))
        for capability in catalog["capabilities"]:
            with self.subTest(capability=capability["id"]):
                self.assertEqual(validate(capability, schema), [])
                self.assertIn(capability["cost_status"], CAPABILITY_STATUSES)
                if capability["mandatory"]:
                    self.assertFalse(capability["requires_paid_ai_api"])

    def test_enabled_providers_are_zero_money_eligible_and_no_mandatory_ai(self):
        for name, provider in self.registry["providers"].items():
            if not provider["enabled"]:
                continue
            with self.subTest(provider=name):
                self.assertIn(provider["classification"], ALLOWED_CLASSIFICATIONS)
                self.assertFalse(provider["requires_ai_api"])
                self.assertNotEqual(provider["billing_model"], "metered")

    def test_github_public_and_private_are_never_conflated(self):
        public = self.registry["providers"]["github_public_standard"]
        private = self.registry["providers"]["github_private_free_quota"]
        self.assertEqual(public["repository_visibility_scope"], "PUBLIC_ONLY")
        self.assertEqual(public["classification"], "FREE_VERIFIED")
        self.assertEqual(private["repository_visibility_scope"], "PRIVATE_ONLY")
        self.assertEqual(private["classification"], "INCLUDED_QUOTA")
        self.assertEqual(self.registry["providers"]["github_private_branch_protection"]["classification"], "PAID")

    def test_free_private_profile_never_claims_platform_enforcement(self):
        profile = json.loads((ROOT / ".adwf/profiles/FREE_PRIVATE.json").read_text(encoding="utf-8"))
        self.assertFalse(profile["platform_enforcement"]["protected_main"])
        self.assertFalse(profile["platform_enforcement"]["required_status_checks"])
        self.assertEqual(profile["github_private_branch_protection"], "PAID_CAPABILITY_BLOCKED_IN_FREE_ONLY")


if __name__ == "__main__":
    unittest.main()
