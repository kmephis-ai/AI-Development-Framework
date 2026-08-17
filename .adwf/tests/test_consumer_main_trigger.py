import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / ".adwf" / "scripts" / "generate_pipeline.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("adwf_generate_pipeline", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConsumerMainTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_generator()
        ir = json.loads((ROOT / ".adwf" / "pipeline-ir.json").read_text(encoding="utf-8"))
        cls.main = cls.module.render(ir)[".github/workflows/adwf-main.yml"]

    def test_normal_push_and_manual_paths_remain_available(self):
        self.assertIn("  push:\n    branches: [main]\n", self.main)
        self.assertIn("  workflow_dispatch:\n", self.main)

    def test_consumer_main_verification_completion_is_bounded_trigger(self):
        self.assertIn(
            '  workflow_run:\n    workflows: ["Main Verification"]\n    types: [completed]\n',
            self.main,
        )
        self.assertIn(
            "if: github.event_name != 'workflow_run' || (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == github.event.repository.default_branch)",
            self.main,
        )

    def test_workflow_run_uses_exact_upstream_subject_sha(self):
        subject = "${{ github.event_name == 'workflow_run' && github.event.workflow_run.head_sha || github.sha }}"
        self.assertIn(f"group: adwf-main-{subject}", self.main)
        self.assertIn(f"ref: {subject}", self.main)
        self.assertGreaterEqual(self.main.count(f"ADWF_SUBJECT_SHA: {subject}"), 2)
        self.assertIn('delegate --phase main --subject-sha "$ADWF_SUBJECT_SHA"', self.main)

    def test_workflow_run_does_not_reuse_push_anchor(self):
        self.assertIn(
            "ADWF_ANCHOR_SHA: ${{ github.event_name == 'push' && github.event.before || '' }}",
            self.main,
        )

    def test_generated_projection_is_current(self):
        generated = ROOT / ".github" / "workflows" / "adwf-main.yml"
        self.assertEqual(generated.read_text(encoding="utf-8"), self.main)


if __name__ == "__main__":
    unittest.main()
