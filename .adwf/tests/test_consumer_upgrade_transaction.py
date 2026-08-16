from __future__ import annotations
from pathlib import Path
import copy, json, sys, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_transaction_fixture import prepared_transaction  # noqa: E402
from lib.consumer_profile import PROFILE_REL, load_consumer_profile  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError  # noqa: E402
from lib.consumer_upgrade_transaction import (  # noqa: E402
    SimulatedUpgradeCrash, UpgradeTransactionStore, apply_upgrade, recover_upgrade, rollback_upgrade,
)


class UpgradeTransactionTests(unittest.TestCase):
    def patched(self): return patch("lib.consumer_upgrade_transaction._verify_revision", return_value=None)

    def apply(self, s, t, c, comp, plan, snap, **kwargs):
        with self.patched(): return apply_upgrade(s, t, c, comp, plan, snap, **kwargs)

    def recover(self, s, t, c, txid):
        with self.patched(): return recover_upgrade(s, t, c, txid)

    def rollback(self, s, t, c, txid):
        with self.patched(): return rollback_upgrade(s, t, c, txid)

    def assert_a(self, source, consumer):
        self.assertEqual((consumer / ".adwf/private.txt").read_bytes(), (source / ".adwf/private.txt").read_bytes())
        self.assertEqual((consumer / ".adwf/remove-me.txt").read_bytes(), (source / ".adwf/remove-me.txt").read_bytes())
        self.assertFalse((consumer / ".adwf/new-target.txt").exists())
        profile = load_consumer_profile(consumer, source, required=True); self.assertIsNotNone(profile)

    def assert_b(self, target, consumer):
        self.assertEqual((consumer / ".adwf/private.txt").read_bytes(), (target / ".adwf/private.txt").read_bytes())
        self.assertEqual((consumer / ".adwf/new-target.txt").read_bytes(), (target / ".adwf/new-target.txt").read_bytes())
        self.assertFalse((consumer / ".adwf/remove-me.txt").exists())
        profile = load_consumer_profile(consumer, target, required=True); self.assertIsNotNone(profile)

    def test_01_apply_commit_is_exact_b_and_idempotent(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            result = self.apply(s,t,c,comp,plan,snap); self.assertEqual(result["status"], "COMMITTED"); self.assert_b(t,c)
            again = self.apply(s,t,c,comp,plan,snap); self.assertEqual(again["status"], "ALREADY_COMMITTED"); self.assertFalse(again["write_performed"])
            self.assertEqual(result["snapshot"]["source_revision"], plan["target_revision"])
        finally: temp.cleanup()

    def test_02_committed_rollback_restores_exact_a_then_retry_commits_b(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            result = self.apply(s,t,c,comp,plan,snap); txid=result["transaction_id"]
            rolled = self.rollback(s,t,c,txid); self.assertEqual(rolled["status"], "ROLLED_BACK"); self.assert_a(s,c)
            retried = self.apply(s,t,c,comp,plan,snap); self.assertEqual(retried["status"], "COMMITTED"); self.assert_b(t,c)
        finally: temp.cleanup()

    def test_03_consumer_drift_after_plan_blocks_before_any_upgrade_write(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            marker = c / ".adwf/private.txt"; marker.write_text("foreign\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_REPLACE_AUTHORITY_INVALID"):
                self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(marker.read_text(encoding="utf-8"), "foreign\n")
            self.assertFalse((c / ".adwf-runtime/consumer-upgrade").exists())
        finally: temp.cleanup()

    def test_04_forged_plan_and_untrusted_snapshot_block_before_write(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            forged = copy.deepcopy(plan); forged["entries"][0]["action"] = "CREATE_PLANNED"
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_PLAN_DIGEST_MISMATCH"):
                self.apply(s,t,c,comp,forged,snap)
            untrusted = copy.deepcopy(snap); untrusted.pop("transaction_id"); untrusted.pop("plan_sha256"); untrusted.pop("consumer_root_sha256")
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_SOURCE_SNAPSHOT_ROOT_MISMATCH|UPGRADE_APPLY_TRUSTED_SOURCE_SNAPSHOT_REQUIRED"):
                self.apply(s,t,c,comp,plan,untrusted)
        finally: temp.cleanup()

    def test_05_unsupported_migration_never_executes(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            forged_comp = copy.deepcopy(comp); forged_plan = copy.deepcopy(plan)
            forged_comp["contracts"][0]["migration_id"] = "MIGRATION-X"
            import hashlib
            def seal(v, field):
                payload={k:x for k,x in v.items() if k!=field}; v[field]=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            seal(forged_comp,"compatibility_sha256")
            forged_plan["compatibility_sha256"] = forged_comp["compatibility_sha256"]
            seal(forged_plan,"plan_sha256")
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_UNSUPPORTED_MIGRATION"):
                self.apply(s,t,c,forged_comp,forged_plan,snap)
        finally: temp.cleanup()

    def test_06_crash_after_quarantine_recovers_a(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            rel = ".adwf/config.json"
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_backup:"+rel)
            txid = next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            result=self.recover(s,t,c,txid); self.assertEqual(result["status"],"ROLLED_BACK"); self.assert_a(s,c)
        finally: temp.cleanup()

    def test_07_crash_after_source_remove_recovers_a(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            rel = ".adwf/private.txt"
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_remove:"+rel)
            txid=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            self.assertEqual(self.recover(s,t,c,txid)["status"],"ROLLED_BACK"); self.assert_a(s,c)
        finally: temp.cleanup()

    def test_08_crash_after_install_uses_link_provenance_and_recovers(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            rel = ".adwf/private.txt"
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_install:"+rel)
            txid=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            self.assertEqual(self.recover(s,t,c,txid)["status"],"ROLLED_BACK"); self.assert_a(s,c)
        finally: temp.cleanup()

    def test_09_crash_during_profile_transition_recovers_exact_profile_a(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            source_profile=(c/PROFILE_REL).read_bytes()
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_profile_remove")
            txid=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            self.assertEqual(self.recover(s,t,c,txid)["status"],"ROLLED_BACK")
            self.assertEqual((c/PROFILE_REL).read_bytes(), source_profile); self.assert_a(s,c)
        finally: temp.cleanup()

    def test_10_tampered_journal_fails_closed(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_profile_backup")
            journal=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json"))
            value=json.loads(journal.read_text(encoding="utf-8")); value["status"]="COMMITTED"; journal.write_text(json.dumps(value)+"\n",encoding="utf-8")
            with self.assertRaisesRegex(ConsumerUpgradeError,"UPGRADE_TRANSACTION_JOURNAL_DIGEST_MISMATCH"):
                self.recover(s,t,c,journal.stem)
        finally: temp.cleanup()

    def test_11_foreign_target_during_recovery_is_preserved_and_blocks(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            rel=".adwf/private.txt"
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_remove:"+rel)
            (c/rel).write_text("foreign after crash\n",encoding="utf-8")
            txid=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            result=self.recover(s,t,c,txid); self.assertEqual(result["status"],"RECOVERY_BLOCKED")
            self.assertEqual((c/rel).read_text(encoding="utf-8"),"foreign after crash\n")
        finally: temp.cleanup()

    def test_12_quarantine_tamper_blocks_restore(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT)
        try:
            rel=".adwf/private.txt"
            with self.assertRaises(SimulatedUpgradeCrash): self.apply(s,t,c,comp,plan,snap,fault_at="after_remove:"+rel)
            txid=next((c/".adwf-runtime/consumer-upgrade/transactions").glob("*.json")).stem
            store=UpgradeTransactionStore(t,c,txid,create=False); store.quarantine_for(rel).write_text("tampered\n",encoding="utf-8")
            result=self.recover(s,t,c,txid); self.assertEqual(result["status"],"RECOVERY_BLOCKED")
        finally: temp.cleanup()

    def test_13_preserved_preexisting_flows_a_b_rollback_retry_without_byte_change(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT, preserve_shared=True)
        try:
            baseline = (c / "README.md").read_bytes()
            source_item = next(x for x in snap["entries"] if x["path"] == "README.md")
            planned = next(x for x in plan["entries"] if x["path"] == "README.md")
            self.assertFalse(source_item["managed_by_adwf"])
            self.assertNotEqual(source_item["installed_sha256"], source_item["preserved_sha256"])
            self.assertEqual(planned["action"], "PRESERVE_PREEXISTING")
            result = self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual((c / "README.md").read_bytes(), baseline)
            target_item = next(x for x in result["snapshot"]["entries"] if x["path"] == "README.md")
            self.assertFalse(target_item["managed_by_adwf"])
            self.assertEqual(target_item["preserved_sha256"], source_item["preserved_sha256"])
            txid = result["transaction_id"]
            rolled = self.rollback(s,t,c,txid)
            self.assertEqual(rolled["status"], "ROLLED_BACK")
            self.assertEqual((c / "README.md").read_bytes(), baseline)
            retried = self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(retried["status"], "COMMITTED")
            self.assertEqual((c / "README.md").read_bytes(), baseline)
        finally: temp.cleanup()

    def test_15_committed_rollback_with_unchanged_profile_is_verification_only(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT, change_profile=False)
        try:
            profile_path = c / PROFILE_REL
            baseline = profile_path.read_bytes()
            result = self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(result["status"], "COMMITTED")
            txid = result["transaction_id"]
            store = UpgradeTransactionStore(t,c,txid,create=False)
            journal = store.load(); self.assertIsNotNone(journal)
            self.assertEqual(journal["profile"]["state"], "UNCHANGED")
            self.assertIsNone(journal["profile"]["quarantine_path"])
            self.assertEqual(profile_path.read_bytes(), baseline)
            rolled = self.rollback(s,t,c,txid)
            self.assertEqual(rolled["status"], "ROLLED_BACK")
            self.assertEqual(profile_path.read_bytes(), baseline)
            self.assert_a(s,c)
            retried = self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(retried["status"], "COMMITTED")
            self.assertEqual(profile_path.read_bytes(), baseline)
            self.assert_b(t,c)
        finally: temp.cleanup()

    def test_16_drifted_unchanged_profile_blocks_rollback_without_rewrite(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT, change_profile=False)
        try:
            result = self.apply(s,t,c,comp,plan,snap)
            self.assertEqual(result["status"], "COMMITTED")
            profile_path = c / PROFILE_REL
            foreign = b'{"foreign":true}\n'
            profile_path.write_bytes(foreign)
            rolled = self.rollback(s,t,c,result["transaction_id"])
            self.assertEqual(rolled["status"], "RECOVERY_BLOCKED")
            self.assertIn("UPGRADE_RECOVERY_FOREIGN_UNCHANGED_PROFILE", rolled["blockers"])
            self.assertEqual(profile_path.read_bytes(), foreign)
        finally: temp.cleanup()

    def test_14_preserved_preexisting_drift_blocks_before_upgrade_write(self):
        temp, s, t, c, snap, comp, plan = prepared_transaction(ROOT, preserve_shared=True)
        try:
            (c / "README.md").write_text("consumer drift\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_APPLY_PREEXISTING_PRESERVE_INVALID"):
                self.apply(s,t,c,comp,plan,snap)
            self.assertFalse((c / ".adwf-runtime/consumer-upgrade").exists())
        finally: temp.cleanup()

if __name__ == "__main__": unittest.main()
