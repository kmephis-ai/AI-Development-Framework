import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.project_gates import gate_configuration_findings


class ProjectGateConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))

    def test_framework_template_has_explicit_gate_exception(self):
        self.assertEqual(gate_configuration_findings(self.template), [])

    def test_product_bootstrap_blocks_empty_gates(self):
        config = copy.deepcopy(self.template)
        config["project"].update({"type": "service", "runtime_product": True})
        findings = gate_configuration_findings(config)
        self.assertIn("BOOTSTRAP_PR_GATE_MISSING", findings)
        self.assertIn("BOOTSTRAP_MAIN_GATE_MISSING", findings)
        self.assertIn("BOOTSTRAP_UNIT_GATE_MISSING", findings)
        self.assertIn("PRODUCT_RUNTIME_GATE_MISSING:smoke", findings)
        self.assertIn("PRODUCT_RUNTIME_GATE_MISSING:golden_paths", findings)

    def test_real_required_commands_close_bootstrap(self):
        config = copy.deepcopy(self.template)
        config["project"].update({"type": "service", "runtime_product": True})
        for name, phases in (("unit", ["pr", "main"]), ("smoke", ["runtime"]), ("golden_paths", ["runtime"])):
            config["commands"][name] = {"required": True, "command": ["python3", "-c", "pass"], "phases": phases}
        self.assertEqual(gate_configuration_findings(config), [])


if __name__ == "__main__":
    unittest.main()
