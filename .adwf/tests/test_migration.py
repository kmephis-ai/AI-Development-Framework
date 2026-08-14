import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MigrationTests(unittest.TestCase):
    def test_v1_1_migration_clamps_autonomy_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".adwf").mkdir()
            old_config = {"framework_version": "1.1.0", "profile": "FREE_PRIVATE", "project": {"name": "XX", "default_branch": "main", "type": "node"}, "policy": {"fail_mode": "CLOSED", "default_autonomy": "A3", "max_autonomous_risk": "R1"}, "orchestration": {}, "reality": {}, "roadmap_quality": {}, "commands": {"unit": {"required": True, "command": "npm test"}}}
            old_state = {"framework_version": "1.1.0", "orchestration": {"writers_active": 0}, "progress": {}}
            (repo / ".adwf/config.json").write_text(json.dumps(old_config), encoding="utf-8")
            (repo / ".adwf/project-state.json").write_text(json.dumps(old_state), encoding="utf-8")
            script = ROOT / ".adwf/migrate_v1_1_to_v1_2.py"
            applied = subprocess.run([sys.executable, str(script), "--root", str(repo), "--apply"], capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            new = json.loads((repo / ".adwf/config.json").read_text(encoding="utf-8"))
            self.assertEqual(new["framework_version"], "1.2.1")
            self.assertEqual(new["policy"]["active_autonomy"], "A1")
            self.assertEqual(new["runtime"]["node_major"], 24)
            self.assertEqual(new["commands"]["unit"]["command"], [])
            repeated = subprocess.run([sys.executable, str(script), "--root", str(repo), "--apply"], capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            self.assertIn("ALREADY_V1_2", repeated.stdout)
            manifests = list((repo / ".adwf/migrations").glob("*-v1.1-to-v1.2/manifest.json"))
            self.assertEqual(len(manifests), 1)
            rolled = subprocess.run([sys.executable, str(script), "--root", str(repo), "--rollback", str(manifests[0]), "--apply"], capture_output=True, text=True)
            self.assertEqual(rolled.returncode, 0, rolled.stdout + rolled.stderr)
            restored = json.loads((repo / ".adwf/config.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["framework_version"], "1.1.0")

    def test_active_writer_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".adwf").mkdir()
            (repo / ".adwf/config.json").write_text(json.dumps({"framework_version": "1.1.0"}), encoding="utf-8")
            (repo / ".adwf/project-state.json").write_text(json.dumps({"orchestration": {"writers_active": 1}}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / ".adwf/migrate_v1_1_to_v1_2.py"), "--root", str(repo), "--apply"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ACTIVE_WRITER_BLOCKS_MIGRATION", result.stdout + result.stderr)

    def test_v1_2_0_patch_is_transactional_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".adwf").mkdir()
            config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
            state = json.loads((ROOT / ".adwf/project-state.json").read_text(encoding="utf-8"))
            config["framework_version"] = "1.2.0"
            config["project"].pop("runtime_product")
            config["commands"].pop("golden_paths")
            state["framework_version"] = "1.2.0"
            state["health"].pop("security")
            state.pop("work_items")
            state["active"].pop("state")
            state["workspace"].pop("expires_at")
            state.pop("project_projection")
            (repo / ".adwf/config.json").write_text(json.dumps(config), encoding="utf-8")
            (repo / ".adwf/project-state.json").write_text(json.dumps(state), encoding="utf-8")
            script = ROOT / ".adwf/migrate_v1_1_to_v1_2.py"
            applied = subprocess.run([sys.executable, str(script), "--root", str(repo), "--apply"], capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            patched_config = json.loads((repo / ".adwf/config.json").read_text(encoding="utf-8"))
            patched_state = json.loads((repo / ".adwf/project-state.json").read_text(encoding="utf-8"))
            self.assertEqual(patched_config["framework_version"], "1.2.1")
            self.assertIn("golden_paths", patched_config["commands"])
            self.assertIn("security", patched_state["health"])
            self.assertIn("work_items", patched_state)
            self.assertIn("state", patched_state["active"])
            self.assertIn("expires_at", patched_state["workspace"])
            self.assertEqual(patched_state["project_projection"]["status"], "N/A")
            repeated = subprocess.run([sys.executable, str(script), "--root", str(repo), "--apply"], capture_output=True, text=True)
            self.assertIn("ALREADY_V1_2", repeated.stdout)
            manifests = list((repo / ".adwf/migrations").glob("*-v1.2.0-to-v1.2.1/manifest.json"))
            self.assertEqual(len(manifests), 1)


if __name__ == "__main__":
    unittest.main()
