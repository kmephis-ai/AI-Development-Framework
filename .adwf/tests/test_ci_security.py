import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf/scripts"))
from validate_ci import validate_github, validate_runtime_hygiene


class CISecurityTests(unittest.TestCase):
    def test_all_ci_files_pass_static_supply_chain_policy(self):
        result = subprocess.run([sys.executable, str(ROOT / ".adwf/scripts/validate_ci.py")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_four_top_level_github_workflows(self):
        names = sorted(path.name for path in (ROOT / ".github/workflows").glob("*.yml"))
        self.assertEqual(names, ["adwf-control.yml", "adwf-main.yml", "adwf-platform-smoke.yml", "adwf-pr.yml", "adwf-release.yml"])

    def test_hosted_or_larger_runner_is_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            workflow = Path(temporary) / "unregistered.yml"
            workflow.write_text("jobs:\n  paid:\n    runs-on: ubuntu-latest-16-cores\n    timeout-minutes: 5\n", encoding="utf-8")
            self.assertIn("unregistered.yml:NONSTANDARD_OR_UNKNOWN_RUNNER:ubuntu-latest-16-cores", validate_github(workflow, {"actions": {}}))

    def test_runtime_artifact_cannot_enter_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("/.adwf-runtime/\n", encoding="utf-8")
            (root / "MANIFEST.json").write_text('{"files":[".adwf-runtime/secret.json"]}\n', encoding="utf-8")
            self.assertIn("MANIFEST.json:ADWF_RUNTIME_PACKAGED", validate_runtime_hygiene(root))

    def test_runtime_ignore_is_mandatory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
            self.assertIn("ADWF_RUNTIME_NOT_IGNORED", validate_runtime_hygiene(root))


if __name__ == "__main__":
    unittest.main()
