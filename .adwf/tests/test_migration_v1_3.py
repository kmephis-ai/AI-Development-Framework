import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MigrationV13Tests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        repo = Path(directory)
        shutil.copytree(ROOT / ".adwf", repo / ".adwf")
        config_path = repo / ".adwf/config.json"
        state_path = repo / ".adwf/project-state.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        config["framework_version"] = "1.2.1"
        state["framework_version"] = "1.2.1"
        config["project"].pop("repository_visibility")
        for name in ("larger_runners_allowed", "failure_artifacts_upload_only_on_failure", "failure_artifact_max_days"):
            config["ci"].pop(name)
        for name in ("allowed_capability_statuses", "stale_capability", "owner_provided_requires_attestation"):
            config["cost"].pop(name)
        config["github"].pop("trust")
        state.pop("incident_knowledge")
        state.pop("safe_healing")
        state.pop("owner_experience")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return repo

    def test_transactional_apply_idempotency_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.fixture(tmp)
            script = ROOT / ".adwf/migrate_v1_2_to_v1_3.py"
            applied = subprocess.run(
                [sys.executable, str(script), "--root", str(repo), "--apply"],
                capture_output=True, text=True,
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            config = json.loads((repo / ".adwf/config.json").read_text(encoding="utf-8"))
            state = json.loads((repo / ".adwf/project-state.json").read_text(encoding="utf-8"))
            policy = json.loads((repo / ".adwf/effective-policy.json").read_text(encoding="utf-8"))
            self.assertEqual(config["framework_version"], "1.3.0")
            self.assertEqual(config["policy"]["active_autonomy"], "A1")
            self.assertFalse(config["ci"]["larger_runners_allowed"])
            self.assertEqual(config["github"]["trust"]["trusted_reviewer_logins"], [])
            self.assertEqual(state["owner_experience"]["acceptance"]["status"], "PENDING")
            self.assertEqual(state["health"]["product"], "NOT_VERIFIED")
            self.assertEqual(policy["framework_version"], "1.3.0")
            repeated = subprocess.run(
                [sys.executable, str(script), "--root", str(repo), "--apply"],
                capture_output=True, text=True,
            )
            self.assertEqual(repeated.returncode, 0)
            self.assertIn("ALREADY_V1_3", repeated.stdout)
            manifests = list((repo / ".adwf/migrations").glob("*-v1.2-to-v1.3/manifest.json"))
            self.assertEqual(len(manifests), 1)
            rolled = subprocess.run(
                [sys.executable, str(script), "--root", str(repo), "--rollback", str(manifests[0]), "--apply"],
                capture_output=True, text=True,
            )
            self.assertEqual(rolled.returncode, 0, rolled.stdout + rolled.stderr)
            restored = json.loads((repo / ".adwf/config.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["framework_version"], "1.2.1")

    def test_active_work_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.fixture(tmp)
            state_path = repo / ".adwf/project-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["orchestration"]["writers_active"] = 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / ".adwf/migrate_v1_2_to_v1_3.py"), "--root", str(repo), "--apply"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MIGRATION_BLOCKED:ACTIVE_WRITER", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
