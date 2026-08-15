import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.trust import classify_diff
from lib.trust_boundary import classify_changed_files

POLICY = {
    "paths": [".adwf/**", ".github/workflows/adwf-*.yml"],
    "weakening_is_risk": "R4",
    "weakening_requires_human": True,
    "self_modification_in_feature_pr": "FORBIDDEN",
}


class GeneratedProjectionTrustTests(unittest.TestCase):
    def test_feature_plus_generated_projections_is_human_gated_not_deadlocked(self):
        result = classify_diff(
            [
                {"path": "README.md", "status": "M"},
                {"path": ".adwf/docs-registry.json", "status": "M", "old_text": "{}", "new_text": "{}"},
                {"path": "MANIFEST.json", "status": "M", "old_text": "{}", "new_text": "{}"},
                {"path": "SHA256SUMS.txt", "status": "M", "old_text": "old\n", "new_text": "new\n"},
            ],
            POLICY,
        )
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertTrue(result["human_required"])
        self.assertNotIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])
        self.assertEqual(
            result["generated_projection_files"],
            [".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt"],
        )
        self.assertEqual(result["authoritative_protected_files"], [])
        self.assertEqual(result["feature_files"], ["README.md"])

    def test_feature_plus_authoritative_protected_source_remains_blocked(self):
        result = classify_diff(
            [
                {"path": "README.md", "status": "M"},
                {
                    "path": ".adwf/lib/trust.py",
                    "status": "M",
                    "old_text": "guard = True\n",
                    "new_text": "guard = True\n",
                },
                {"path": "MANIFEST.json", "status": "M", "old_text": "{}", "new_text": "{}"},
            ],
            POLICY,
        )
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])
        self.assertIn(".adwf/lib/trust.py", result["authoritative_protected_files"])

    def test_generated_projection_only_remains_r4_human_required(self):
        result = classify_diff(
            [{"path": "MANIFEST.json", "status": "M", "old_text": "{}", "new_text": "{}"}],
            POLICY,
        )
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertEqual(result["required_risk"], "R4")
        self.assertEqual(result["required_work_type"], "GOV")
        self.assertTrue(result["human_required"])

    def test_gitattributes_is_not_a_generated_projection_exception(self):
        result = classify_diff(
            [
                {"path": "README.md", "status": "M"},
                {
                    "path": ".gitattributes",
                    "status": "M",
                    "old_text": "* text=auto\n",
                    "new_text": "* text=auto\n",
                },
            ],
            POLICY,
        )
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])
        self.assertIn(".gitattributes", result["authoritative_protected_files"])

    def test_trusted_controller_requires_governance_for_generated_projections(self):
        result = classify_changed_files(
            ["README.md", ".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt"]
        )
        self.assertTrue(result["trust_boundary_changed"])
        self.assertEqual(
            result["trust_boundary_files"],
            [".adwf/docs-registry.json", "MANIFEST.json", "SHA256SUMS.txt"],
        )

    def test_trusted_controller_covers_authoritative_sources_and_gitattributes(self):
        protected = [
            ".adwf/lib/trust.py",
            ".adwf/policies/trust-boundary.json",
            ".github/workflows/adwf-pr.yml",
            ".gitattributes",
            "SECURITY.md",
            "docs/governance/TRUST_AND_SELF_AUDIT.md",
        ]
        result = classify_changed_files(["README.md", *protected])
        self.assertTrue(result["trust_boundary_changed"])
        self.assertEqual(result["trust_boundary_files"], sorted(protected))

    def test_trusted_controller_leaves_normal_feature_outside_boundary(self):
        result = classify_changed_files(["README.md", "src/product.py"])
        self.assertFalse(result["trust_boundary_changed"])
        self.assertEqual(result["trust_boundary_files"], [])
        self.assertEqual(result["classification"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
