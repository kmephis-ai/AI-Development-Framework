from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import copy, sys, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_fixture import A, B, prepared, seal_inventory  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError, _safe_rel, _verify_revision, build_upgrade_compatibility  # noqa: E402


class ConsumerUpgradeAdversarialTests(unittest.TestCase):
    def compat(self, source, target, consumer, snapshot):
        with patch("lib.consumer_upgrade._verify_revision", return_value=None):
            return build_upgrade_compatibility(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot)

    def test_01_stale_source_snapshot_revision_fails_closed(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            stale = copy.deepcopy(snapshot); stale["source_revision"] = "c" * 40
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_SNAPSHOT_SOURCE_REVISION_STALE"):
                self.compat(source, target, consumer, stale)
        finally: temp.cleanup()

    def test_02_substituted_target_revision_is_rejected(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            calls = [SimpleNamespace(returncode=0, stdout="c" * 40 + "\n"), SimpleNamespace(returncode=0, stdout="")]
            with patch("lib.consumer_upgrade.subprocess.run", side_effect=calls):
                with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_REVISION_MISMATCH"): _verify_revision(root, B)
        finally: temp.cleanup()

    def test_03_consumer_drift_after_snapshot_blocks(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            (consumer / ".adwf/private.txt").write_text("consumer drift\n", encoding="utf-8")
            result = self.compat(source, target, consumer, snapshot)
            self.assertEqual(result["status"], "BLOCK")
            self.assertTrue(any(item["code"] == "UPGRADE_CONSUMER_DRIFT" for item in result["findings"]))
        finally: temp.cleanup()

    def test_04_target_addition_cannot_claim_consumer_owned_content(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            (target / ".adwf/new-target.txt").write_text("framework\n", encoding="utf-8")
            (consumer / ".adwf/new-target.txt").write_text("consumer\n", encoding="utf-8"); seal_inventory(target)
            result = self.compat(source, target, consumer, snapshot)
            entry = next(item for item in result["path_entries"] if item["path"] == ".adwf/new-target.txt")
            self.assertEqual(entry["action"], "PRESERVE_CONSUMER_COLLISION"); self.assertEqual(result["status"], "BLOCK")
        finally: temp.cleanup()

    def test_05_path_traversal_and_type_ambiguity_block(self):
        with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_PATH_INVALID"): _safe_rel("../escape")
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            from lib import consumer_upgrade as module
            original = module._path_state
            def fake_state(root, rel): return ("SYMLINK", None) if rel == ".adwf/private.txt" else original(root, rel)
            with patch("lib.consumer_upgrade._verify_revision", return_value=None), patch("lib.consumer_upgrade._path_state", side_effect=fake_state):
                result = build_upgrade_compatibility(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot)
            self.assertEqual(result["status"], "BLOCK")
            self.assertTrue(any(item["code"] == "UPGRADE_PATH_TYPE_AMBIGUITY" for item in result["findings"]))
        finally: temp.cleanup()

if __name__ == "__main__": unittest.main()
