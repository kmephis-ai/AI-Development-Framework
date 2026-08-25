import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib import health


class Gov031TrustBootIntegrityTests(unittest.TestCase):
    def test_docs_staleness_remains_broken_config_but_not_boot_integrity(self):
        with mock.patch.object(health, "check_docs", return_value=["DOCUMENT_STALE:README.md"]):
            config = health.config_health(ROOT)
            boot = health.trust_boot_integrity(ROOT)
        self.assertEqual(config["status"], "BROKEN", config)
        self.assertIn("DOCUMENT_STALE:README.md", config["findings"])
        self.assertEqual(boot, {"status": "VERIFIED", "findings": []})

    def test_trust_policy_weakening_still_blocks_boot_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", ".adwf-runtime", "__pycache__", "*.pyc"))
            path = copy / ".adwf/policies/trust-boundary.json"
            policy = json.loads(path.read_text(encoding="utf-8"))
            policy["weakening_requires_human"] = False
            path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = health.trust_boot_integrity(copy)
        self.assertEqual(result["status"], "BROKEN", result)
        self.assertIn("TRUST_POLICY_WEAKENED", result["findings"])

    def test_generated_controller_uses_boot_integrity_before_publication(self):
        control = (ROOT / ".github/workflows/adwf-control.yml").read_text(encoding="utf-8")
        marker = "      - name: Restore public-safe durable checkpoint"
        pre_publication = control.split(marker, 1)[0]
        self.assertIn("doctor --scope trust_boot_integrity", pre_publication)
        self.assertNotIn("doctor --scope config_health", pre_publication)
        self.assertIn("doctor --scope config_health", (ROOT / ".github/workflows/adwf-main.yml").read_text(encoding="utf-8"))
        self.assertIn("doctor --scope config_health", (ROOT / ".github/workflows/adwf-pr.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
