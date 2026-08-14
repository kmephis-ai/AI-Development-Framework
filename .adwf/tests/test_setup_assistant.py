import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.setup_assistant import build_setup_plan, detect_project


class SetupAssistantTests(unittest.TestCase):
    def registry(self, classification="APPROVED_FREE"):
        return {
            "hard_budget_usd": 0,
            "allow_overage": False,
            "allow_credit_purchase": False,
            "providers": {
                "local": {
                    "classification": classification,
                    "enabled": True,
                    "plane": "ci",
                    "provider_mode": "local",
                    "mandatory_ci_allowed": True,
                    "requires_paid_ai_api": False,
                    "monetary_cost": 0,
                }
            },
        }

    def test_node_scripts_become_argv_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"lint": "eslint .", "test": "vitest", "build": "vite build"}
            }), encoding="utf-8")
            result = detect_project(root)
            self.assertEqual(result["commands"]["unit"]["command"], ["npm", "run", "test"])
            self.assertEqual(result["commands"]["build"]["command"], ["npm", "run", "build"])

    def test_unknown_provider_blocks_and_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_setup_plan(tmp, self.registry(), capability="missing", canonical_provider="local")
            self.assertIn("PROVIDER_NOT_CONFIRMED_ZERO_COST", plan["blockers"])
            self.assertFalse(plan["write_performed"])
            self.assertFalse(plan["ai_api_required"])

    def test_missing_commands_asks_only_one_human_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_setup_plan(tmp, self.registry(), capability="local", canonical_provider="local")
            self.assertEqual(len(plan["questions"]), 1)
            self.assertIn("NO_VERIFIABLE_PROJECT_COMMANDS", plan["blockers"])


if __name__ == "__main__":
    unittest.main()
