import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.contracts import validate
from lib.cost_guard import ALLOWED_CLASSIFICATIONS, CAPABILITY_STATUSES
from scripts.validate_capabilities import validate_truth_payload


class CapabilityCostContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))

    def test_all_capabilities_have_a_valid_cost_contract(self):
        schema = json.loads((ROOT / ".adwf/schemas/capability.schema.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / ".adwf/capabilities.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["truth_model_version"], 2)
        self.assertEqual(catalog["catalog_role"], "COST_AND_IMPLEMENTATION_SUMMARY")
        self.assertEqual(catalog["canonical_truth"], ".adwf/capability-traceability.json")
        for capability in catalog["capabilities"]:
            with self.subTest(capability=capability["id"]):
                self.assertEqual(validate(capability, schema), [])
                self.assertNotIn("state", capability)
                self.assertNotEqual(capability["implementation_status"], "LIVE_VERIFIED")
                self.assertIn(capability["cost_status"], CAPABILITY_STATUSES)
                if capability["mandatory"]:
                    self.assertFalse(capability["requires_paid_ai_api"])

    def test_capability_truth_v2_is_canonical_and_optional_adapter_is_orthogonal(self):
        schema = json.loads((ROOT / ".adwf/schemas/capability-traceability.schema.json").read_text(encoding="utf-8"))
        truth = json.loads((ROOT / ".adwf/capability-traceability.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(truth, schema), [])
        self.assertEqual(truth["schema_version"], 2)
        self.assertEqual(truth["truth_model_version"], 2)
        self.assertEqual(truth["role"], "CANONICAL_CAPABILITY_TRUTH")
        optional = [item for item in truth["capabilities"] if item["execution_mode"] == "OPTIONAL_ADAPTER"]
        self.assertTrue(optional)
        self.assertTrue(all(item["status"] != "OPTIONAL_ADAPTER" for item in optional))

    def test_live_verified_requires_real_live_evidence(self):
        schema = json.loads((ROOT / ".adwf/schemas/capability-traceability.schema.json").read_text(encoding="utf-8"))
        truth = json.loads((ROOT / ".adwf/capability-traceability.json").read_text(encoding="utf-8"))
        candidate = copy.deepcopy(truth)
        candidate["capabilities"][0]["status"] = "LIVE_VERIFIED"
        candidate["capabilities"][0]["live_boundary"] = "provider readback required"
        candidate["capabilities"][0]["live_evidence"] = []
        self.assertEqual(validate(candidate, schema), [])
        errors = validate_truth_payload(candidate, schema=schema, root=ROOT)
        self.assertIn("CAPABILITY_LIVE_EVIDENCE_MISSING:TRUSTED_GATE", errors)

    def test_only_provider_certified_upgrade_capabilities_are_live_verified(self):
        truth = json.loads((ROOT / ".adwf/capability-traceability.json").read_text(encoding="utf-8"))
        verified = {item["id"] for item in truth["capabilities"] if item["status"] == "LIVE_VERIFIED"}
        self.assertEqual(verified, {"CONSUMER_FRAMEWORK_UPGRADE_PLANNING", "CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION"})
        for item in truth["capabilities"]:
            if item["status"] == "LIVE_VERIFIED":
                self.assertEqual(item["live_evidence"], ["certification:CERT-UPGRADE-003-PRIHRASH-EXTERNAL"])
            elif item["status"] == "LIVE_NOT_VERIFIED":
                self.assertTrue(item["live_boundary"])
                self.assertEqual(item["live_evidence"], [])

    def test_capability_truth_validator_passes(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / ".adwf/scripts/validate_capabilities.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
