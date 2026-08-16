from __future__ import annotations

from pathlib import Path
import copy
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_fixture import B, seal_inventory  # noqa: E402
from consumer_upgrade_transaction_fixture import prepared_transaction  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError, build_upgrade_compatibility, plan_consumer_upgrade  # noqa: E402
from lib.consumer_upgrade_transaction import SimulatedUpgradeCrash, apply_upgrade, recover_upgrade  # noqa: E402

C = "c" * 40


class UpgradeTransactionAdversarialTests(unittest.TestCase):
    def apply(self, source, target, consumer, compatibility, plan, snapshot, **kwargs):
        with patch("lib.consumer_upgrade_transaction._verify_revision", return_value=None):
            return apply_upgrade(source, target, consumer, compatibility, plan, snapshot, **kwargs)

    def recover(self, source, target, consumer, txid):
        with patch("lib.consumer_upgrade_transaction._verify_revision", return_value=None):
            return recover_upgrade(source, target, consumer, txid)

    def assert_a(self, source, consumer):
        self.assertEqual((consumer / ".adwf/private.txt").read_bytes(), (source / ".adwf/private.txt").read_bytes())
        self.assertEqual((consumer / ".adwf/remove-me.txt").read_bytes(), (source / ".adwf/remove-me.txt").read_bytes())
        self.assertFalse((consumer / ".adwf/new-target.txt").exists())

    def test_13_target_checkout_substitution_blocks_before_transaction_write(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        try:
            calls = [None, ConsumerUpgradeError("UPGRADE_REVISION_MISMATCH")]
            with patch("lib.consumer_upgrade_transaction._verify_revision", side_effect=calls):
                with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_REVISION_MISMATCH"):
                    apply_upgrade(source, target, consumer, compatibility, plan, snapshot)
            self.assertFalse((consumer / ".adwf-runtime/consumer-upgrade").exists())
        finally:
            temp.cleanup()

    def test_14_b_only_create_collision_and_symlink_are_fail_closed_before_write(self):
        for kind in ("directory", "symlink"):
            with self.subTest(kind=kind):
                temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
                external = tempfile.TemporaryDirectory()
                try:
                    path = consumer / ".adwf/new-target.txt"
                    if kind == "directory":
                        path.mkdir()
                    else:
                        path.symlink_to(Path(external.name) / "outside.txt")
                    with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_CREATE_AUTHORITY_INVALID"):
                        self.apply(source, target, consumer, compatibility, plan, snapshot)
                    self.assertFalse((consumer / ".adwf-runtime/consumer-upgrade").exists())
                    if kind == "symlink":
                        self.assertTrue(path.is_symlink())
                        self.assertFalse((Path(external.name) / "outside.txt").exists())
                finally:
                    external.cleanup(); temp.cleanup()

    def test_15_remove_specific_crash_recovers_exact_a(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        try:
            rel = ".adwf/remove-me.txt"
            with self.assertRaises(SimulatedUpgradeCrash):
                self.apply(source, target, consumer, compatibility, plan, snapshot, fault_at="after_remove:" + rel)
            txid = next((consumer / ".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            result = self.recover(source, target, consumer, txid)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assert_a(source, consumer)
        finally:
            temp.cleanup()

    def test_16_crash_after_b_snapshot_before_commit_removes_owned_snapshot_and_recovers_a(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        try:
            with self.assertRaises(SimulatedUpgradeCrash):
                self.apply(source, target, consumer, compatibility, plan, snapshot, fault_at="after_snapshot")
            txid = next((consumer / ".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            snapshot_path = consumer / ".adwf-runtime/consumer-upgrade/snapshots" / f"{txid}.snapshot.json"
            self.assertTrue(snapshot_path.is_file())
            result = self.recover(source, target, consumer, txid)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertFalse(snapshot_path.exists())
            self.assert_a(source, consumer)
        finally:
            temp.cleanup()

    def test_17_changed_shared_path_never_becomes_mutation_authority(self):
        temp, source, target, consumer, snapshot, _, _ = prepared_transaction(ROOT)
        try:
            (target / "README.md").write_text("shared-v2 forbidden\n", encoding="utf-8")
            seal_inventory(target)
            with patch("lib.consumer_upgrade._verify_revision", return_value=None):
                compatibility = build_upgrade_compatibility(source, target, consumer, source_revision="a" * 40, target_revision=B, snapshot=snapshot)
                plan = plan_consumer_upgrade(source, target, consumer, source_revision="a" * 40, target_revision=B, snapshot=snapshot)
            self.assertEqual(compatibility["status"], "HUMAN_REQUIRED")
            self.assertEqual(plan["status"], "HUMAN_REQUIRED")
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_REQUIRES_READY_PLAN"):
                self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertEqual((consumer / "README.md").read_text(encoding="utf-8"), "shared-v1\n")
        finally:
            temp.cleanup()

    def test_18_committed_b_snapshot_is_trusted_source_provenance_for_chained_b_to_c(self):
        temp, source_a, target_b, consumer, snapshot_a, compatibility_ab, plan_ab = prepared_transaction(ROOT)
        base = Path(temp.name)
        target_c = base / "target-c"
        try:
            committed_ab = self.apply(source_a, target_b, consumer, compatibility_ab, plan_ab, snapshot_a)
            self.assertEqual(committed_ab["status"], "COMMITTED")
            snapshot_b = committed_ab["snapshot"]
            shutil.copytree(target_b, target_c)
            (target_c / ".adwf/private.txt").write_text("private-v3\n", encoding="utf-8")
            seal_inventory(target_c)
            with patch("lib.consumer_upgrade._verify_revision", return_value=None):
                compatibility_bc = build_upgrade_compatibility(target_b, target_c, consumer, source_revision=B, target_revision=C, snapshot=snapshot_b)
                plan_bc = plan_consumer_upgrade(target_b, target_c, consumer, source_revision=B, target_revision=C, snapshot=snapshot_b)
            self.assertEqual(compatibility_bc["status"], "PASS")
            self.assertEqual(plan_bc["status"], "READY")
            committed_bc = self.apply(target_b, target_c, consumer, compatibility_bc, plan_bc, snapshot_b)
            self.assertEqual(committed_bc["status"], "COMMITTED")
            self.assertEqual((consumer / ".adwf/private.txt").read_bytes(), (target_c / ".adwf/private.txt").read_bytes())
            self.assertEqual(committed_bc["snapshot"]["source_revision"], C)
        finally:
            temp.cleanup()

    def test_19_parent_symlink_on_new_framework_path_blocks_before_upgrade_runtime_write(self):
        temp, source, target, consumer, snapshot, _, _ = prepared_transaction(ROOT)
        external = tempfile.TemporaryDirectory()
        try:
            nested = target / "upgrade-new/nested.txt"
            nested.parent.mkdir(); nested.write_text("nested-v2\n", encoding="utf-8"); seal_inventory(target)
            with patch("lib.consumer_upgrade._verify_revision", return_value=None):
                compatibility = build_upgrade_compatibility(source, target, consumer, source_revision="a" * 40, target_revision=B, snapshot=snapshot)
                plan = plan_consumer_upgrade(source, target, consumer, source_revision="a" * 40, target_revision=B, snapshot=snapshot)
            self.assertEqual(plan["status"], "READY")
            (consumer / "upgrade-new").symlink_to(Path(external.name), target_is_directory=True)
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_CREATE_AUTHORITY_INVALID"):
                self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertFalse((consumer / ".adwf-runtime/consumer-upgrade").exists())
            self.assertEqual(list(Path(external.name).iterdir()), [])
        finally:
            external.cleanup(); temp.cleanup()

    def test_20_quarantine_parent_symlink_during_recovery_is_preserved_and_blocks(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        external = tempfile.TemporaryDirectory()
        try:
            rel = ".adwf/private.txt"
            with self.assertRaises(SimulatedUpgradeCrash):
                self.apply(source, target, consumer, compatibility, plan, snapshot, fault_at="after_remove:" + rel)
            txid = next((consumer / ".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            qparent = consumer / ".adwf-runtime/consumer-upgrade/quarantine" / txid / "files" / ".adwf"
            shutil.rmtree(qparent)
            outside_parent = Path(external.name) / "outside"; outside_parent.mkdir()
            outside_file = outside_parent / "private.txt"
            outside_file.write_bytes((source / rel).read_bytes())
            qparent.symlink_to(outside_parent, target_is_directory=True)
            before = outside_file.read_bytes()
            result = self.recover(source, target, consumer, txid)
            self.assertEqual(result["status"], "RECOVERY_BLOCKED")
            self.assertEqual(outside_file.read_bytes(), before)
            self.assertTrue(qparent.is_symlink())
        finally:
            external.cleanup(); temp.cleanup()


if __name__ == "__main__":
    unittest.main()
