from __future__ import annotations

from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
sys.path.insert(0, str(ROOT / ".adwf/tests"))
from lib.managed_surface import ManagedSurfaceError, plan_adoption, plan_detach  # noqa: E402
from lib.managed_surface_transaction import (  # noqa: E402
    apply_adoption,
    apply_detach,
    recover_detach,
)
from lib.strict_json import load as strict_load  # noqa: E402
import test_managed_surface_transaction as adoption_test_helpers  # noqa: E402


class ManagedSurfaceDetachTransactionTests(unittest.TestCase):
    def source_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        return adoption_test_helpers.ManagedSurfaceTransactionTests().source_repo()

    def adopt(self, source: Path, target: Path, revision: str) -> tuple[dict, dict]:
        plan = plan_adoption(source, target, source_revision=revision)
        result = apply_adoption(source, target, plan)
        self.assertEqual(result["status"], "COMMITTED")
        return result, result["snapshot"]

    def detach_plan(self, source: Path, target: Path, snapshot: dict) -> dict:
        plan = plan_detach(target, snapshot, framework_root=source)
        self.assertEqual(plan["status"], "READY")
        return plan

    def detach_journal(self, target: Path, transaction_id: str) -> Path:
        return target / ".adwf-runtime/managed-surface/detach-transactions" / f"{transaction_id}.json"

    def test_01_success_removes_only_managed_private_and_preserves_product_shared(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name)
            (target / "product.txt").write_text("consumer data\n", encoding="utf-8")
            _, snapshot = self.adopt(source, target, revision)
            readme = next(item for item in snapshot["entries"] if item["path"] == "README.md")
            self.assertTrue(readme["managed_by_adwf"], "shared file was created by adoption in this test")
            plan = self.detach_plan(source, target, snapshot)
            result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertFalse((target / ".adwf/private.txt").exists())
            self.assertFalse((target / ".adwf/second.txt").exists())
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "framework readme\n")
            self.assertEqual((target / "product.txt").read_text(encoding="utf-8"), "consumer data\n")
            journal = strict_load(self.detach_journal(target, result["transaction_id"]))
            readme_journal = next(item for item in journal["entries"] if item["path"] == "README.md")
            self.assertEqual(readme_journal["planned_action"], "PRESERVE_SHARED")
            self.assertEqual(readme_journal["state"], "PRESERVED")
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_02_plan_apply_drift_blocks_before_any_delete(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            victim = target / ".adwf/private.txt"
            victim.write_text("consumer changed it\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_PLAN_TARGET_STATE_CHANGED"):
                apply_detach(source, target, snapshot, plan)
            self.assertEqual(victim.read_text(encoding="utf-8"), "consumer changed it\n")
            tx_dir = target / ".adwf-runtime/managed-surface/detach-transactions"
            self.assertFalse(tx_dir.exists() and any(tx_dir.glob("*.json")))
        finally:
            temp.cleanup(); consumer.cleanup()

    @unittest.skipIf(not hasattr(Path, "symlink_to"), "symlink unsupported")
    def test_03_parent_symlink_swap_after_cas_check_blocks_without_escape_delete(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory(); outside = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            from lib import managed_surface_transaction as module
            original = module._assert_detach_plan_matches_current
            moved = target / ".adwf-original"
            outside_root = Path(outside.name)
            (outside_root / "sentinel.txt").write_text("outside survives\n", encoding="utf-8")
            swapped = {"done": False}

            def swap(*args, **kwargs):
                original(*args, **kwargs)
                if not swapped["done"]:
                    (target / ".adwf").rename(moved)
                    (target / ".adwf").symlink_to(outside_root, target_is_directory=True)
                    swapped["done"] = True

            with patch.object(module, "_assert_detach_plan_matches_current", side_effect=swap):
                result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "RECOVERY_BLOCKED")
            self.assertTrue(result["human_required"])
            self.assertIn("DETACH_PARENT_SYMLINK_FORBIDDEN", result["error"])
            self.assertEqual((outside_root / "sentinel.txt").read_text(encoding="utf-8"), "outside survives\n")
            self.assertTrue((moved / "private.txt").is_file())
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        finally:
            temp.cleanup(); consumer.cleanup(); outside.cleanup()

    def test_04_fault_after_deletes_restores_all_exact_files(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); (target / "product.txt").write_text("keep\n", encoding="utf-8")
            _, snapshot = self.adopt(source, target, revision); plan = self.detach_plan(source, target, snapshot)
            result = apply_detach(source, target, snapshot, plan, fault_after_deletes=2)
            self.assertEqual(result["status"], "ROLLED_BACK")
            for item in plan["entries"]:
                if item["action"] == "REMOVE_ELIGIBLE":
                    self.assertEqual((target / item["path"]).read_bytes(), (source / item["path"]).read_bytes(), item["path"])
            self.assertEqual((target / "product.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertTrue((target / "README.md").is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_05_concurrent_replacement_bytes_are_restored_and_human_blocked(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            from lib import managed_surface_transaction as module
            original = module.os.rename
            race_path = target / ".adwf/private.txt"
            raced = {"done": False}

            def race(src, dst):
                if not raced["done"] and Path(src) == race_path:
                    race_path.write_text("concurrent consumer bytes\n", encoding="utf-8")
                    raced["done"] = True
                return original(src, dst)

            with patch.object(module.os, "rename", side_effect=race):
                result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "RECOVERY_BLOCKED")
            self.assertTrue(result["human_required"])
            self.assertTrue(any("DETACH_RECOVERY_RESTORED_CONCURRENT_BYTES" in item for item in result["blockers"]))
            self.assertEqual(race_path.read_text(encoding="utf-8"), "concurrent consumer bytes\n")
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_06_forged_snapshot_cannot_create_delete_authority(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            forged = copy.deepcopy(snapshot); forged["transaction_id"] = "0" * 64
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_ADOPTION_TRANSACTION_NOT_FOUND"):
                apply_detach(source, target, forged, plan)
            self.assertTrue((target / ".adwf/private.txt").is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_07_forged_ready_plan_is_rejected_by_current_plan_cas(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot); forged = copy.deepcopy(plan)
            victim = next(item for item in forged["entries"] if item["action"] == "REMOVE_ELIGIBLE")
            victim["action"] = "PRESERVE_PREEXISTING"
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_PLAN_TARGET_STATE_CHANGED"):
                apply_detach(source, target, snapshot, forged)
            self.assertTrue((target / victim["path"]).is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_08_unsealed_detach_journal_tamper_is_rejected(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            first = apply_detach(source, target, snapshot, plan, fault_after_deletes=1)
            self.assertEqual(first["status"], "ROLLED_BACK")
            journal_path = self.detach_journal(target, first["transaction_id"])
            value = json.loads(journal_path.read_text(encoding="utf-8")); value["attempts"] += 100
            journal_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_TRANSACTION_JOURNAL_DIGEST_MISMATCH"):
                apply_detach(source, target, snapshot, plan)
            self.assertTrue((target / ".adwf/private.txt").is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_09_repeated_apply_after_commit_is_idempotent(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            first = apply_detach(source, target, snapshot, plan)
            second = apply_detach(source, target, snapshot, plan)
            self.assertEqual(first["status"], "COMMITTED")
            self.assertEqual(second["status"], "ALREADY_COMMITTED")
            self.assertEqual(first["transaction_id"], second["transaction_id"])
            self.assertFalse(second["write_performed"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_10_source_head_drift_blocks_detach_before_mutation(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "new source revision"], cwd=source, check=True)
            with self.assertRaisesRegex(ManagedSurfaceError, "SOURCE_REVISION_MISMATCH"):
                apply_detach(source, target, snapshot, plan)
            self.assertTrue((target / ".adwf/private.txt").is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_11_nonempty_provenance_directory_and_consumer_content_survive(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            note = target / ".adwf/consumer-note.txt"; note.write_text("do not delete\n", encoding="utf-8")
            plan = self.detach_plan(source, target, snapshot)
            result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual(note.read_text(encoding="utf-8"), "do not delete\n")
            self.assertTrue((target / ".adwf").is_dir())
            self.assertIn(".adwf", result["preserved_dirs"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_12_preexisting_exact_and_shared_are_never_removed(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name)
            (target / "README.md").write_bytes((source / "README.md").read_bytes())
            (target / ".adwf").mkdir(parents=True)
            (target / ".adwf/private.txt").write_bytes((source / ".adwf/private.txt").read_bytes())
            _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            private = next(item for item in plan["entries"] if item["path"] == ".adwf/private.txt")
            readme = next(item for item in plan["entries"] if item["path"] == "README.md")
            self.assertEqual(private["action"], "PRESERVE_PREEXISTING")
            self.assertEqual(readme["action"], "PRESERVE_PREEXISTING")
            result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual((target / ".adwf/private.txt").read_bytes(), (source / ".adwf/private.txt").read_bytes())
            self.assertEqual((target / "README.md").read_bytes(), (source / "README.md").read_bytes())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_13_explicit_recover_is_idempotent_after_rollback(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            failed = apply_detach(source, target, snapshot, plan, fault_after_deletes=1)
            self.assertEqual(failed["status"], "ROLLED_BACK")
            first = recover_detach(source, target, failed["transaction_id"])
            second = recover_detach(source, target, failed["transaction_id"])
            self.assertEqual(first["status"], "ROLLED_BACK")
            self.assertEqual(second["status"], "ROLLED_BACK")
            self.assertFalse(first["write_performed"]); self.assertFalse(second["write_performed"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_14_tampered_durable_adoption_snapshot_blocks_detach(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); adoption, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            stored = target / adoption["snapshot_path"]
            stored.write_text(stored.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_ADOPTION_SNAPSHOT_FILE_DIGEST_MISMATCH"):
                apply_detach(source, target, snapshot, plan)
            self.assertTrue((target / ".adwf/private.txt").is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_15_resealed_journal_cannot_redirect_quarantine_to_consumer_path(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); product = target / "product.txt"; product.write_text("consumer stays\n", encoding="utf-8")
            _, snapshot = self.adopt(source, target, revision)
            plan = self.detach_plan(source, target, snapshot)
            failed = apply_detach(source, target, snapshot, plan, fault_after_deletes=1)
            self.assertEqual(failed["status"], "ROLLED_BACK")
            journal_path = self.detach_journal(target, failed["transaction_id"])
            value = json.loads(journal_path.read_text(encoding="utf-8"))
            victim = next(item for item in value["entries"] if item["planned_action"] == "REMOVE_ELIGIBLE")
            victim["quarantine_path"] = "product.txt"
            victim["state"] = "QUARANTINED"
            from lib import managed_surface_transaction as module
            value["journal_sha256"] = module._transaction_journal_digest(value)
            journal_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "DETACH_TRANSACTION_QUARANTINE_PATH_MISMATCH"):
                recover_detach(source, target, failed["transaction_id"])
            self.assertEqual(product.read_text(encoding="utf-8"), "consumer stays\n")
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_16_differing_shared_file_survives_adoption_and_detach_byte_exact(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name)
            shared = target / "README.md"
            original = b"consumer shared stays byte exact\n"
            shared.write_bytes(original)
            adoption, snapshot = self.adopt(source, target, revision)
            snap = next(item for item in snapshot["entries"] if item["path"] == "README.md")
            self.assertFalse(snap["managed_by_adwf"])
            self.assertEqual(shared.read_bytes(), original)
            plan = self.detach_plan(source, target, snapshot)
            planned = next(item for item in plan["entries"] if item["path"] == "README.md")
            self.assertEqual(planned["action"], "PRESERVE_PREEXISTING")
            result = apply_detach(source, target, snapshot, plan)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual(shared.read_bytes(), original)
            self.assertFalse((target / ".adwf/private.txt").exists())
            self.assertFalse((target / ".adwf/second.txt").exists())
            self.assertTrue((target / adoption["snapshot_path"]).exists())
        finally:
            temp.cleanup(); consumer.cleanup()


if __name__ == "__main__":
    unittest.main()
