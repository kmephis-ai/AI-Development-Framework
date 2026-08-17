from __future__ import annotations
from pathlib import Path
import sys, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_transaction_fixture import prepared_transaction  # noqa: E402
from lib.consumer_upgrade_transaction import apply_upgrade, rollback_upgrade  # noqa: E402


class ConsumerInstructionUpgradeTransactionTests(unittest.TestCase):
    def apply(self, source, target, consumer, compatibility, plan, snapshot):
        with patch("lib.consumer_upgrade_transaction._verify_revision", return_value=None):
            return apply_upgrade(source, target, consumer, compatibility, plan, snapshot)

    def rollback(self, source, target, consumer, transaction_id):
        with patch("lib.consumer_upgrade_transaction._verify_revision", return_value=None):
            return rollback_upgrade(source, target, consumer, transaction_id)

    def test_changed_legacy_agents_router_is_verification_only_across_apply_rollback_retry(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(
            ROOT, preserve_agents_router=True
        )
        try:
            baseline = (consumer / "AGENTS.md").read_bytes()
            source_item = next(item for item in snapshot["entries"] if item["path"] == "AGENTS.md")
            planned = next(item for item in plan["entries"] if item["path"] == "AGENTS.md")
            self.assertFalse(source_item["managed_by_adwf"])
            self.assertEqual(planned["action"], "PRESERVE_PREEXISTING")
            self.assertNotEqual(planned["source_sha256"], planned["target_sha256"])

            result = self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual((consumer / "AGENTS.md").read_bytes(), baseline)
            target_item = next(item for item in result["snapshot"]["entries"] if item["path"] == "AGENTS.md")
            self.assertFalse(target_item["managed_by_adwf"])
            self.assertEqual(target_item["preserved_sha256"], source_item["preserved_sha256"])

            rolled = self.rollback(source, target, consumer, result["transaction_id"])
            self.assertEqual(rolled["status"], "ROLLED_BACK")
            self.assertEqual((consumer / "AGENTS.md").read_bytes(), baseline)

            retried = self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertEqual(retried["status"], "COMMITTED")
            self.assertEqual((consumer / "AGENTS.md").read_bytes(), baseline)
        finally:
            temp.cleanup()

    def test_legacy_source_without_instruction_contract_reaches_layered_b_without_router_write(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(
            ROOT, preserve_agents_router=True, legacy_source_without_instruction_policy=True
        )
        try:
            self.assertFalse((source / ".adwf/consumer-instruction-policy.json").exists())
            self.assertTrue((target / ".adwf/consumer-instruction-policy.json").is_file())
            baseline = (consumer / "AGENTS.md").read_bytes()
            result = self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual((consumer / "AGENTS.md").read_bytes(), baseline)
            self.assertTrue((consumer / ".adwf/consumer-instruction-policy.json").is_file())
            self.assertTrue((consumer / ".adwf/instructions/CORE.md").is_file())
        finally:
            temp.cleanup()

    def test_consumer_invariant_type_ambiguity_blocks_before_upgrade_write(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(
            ROOT, preserve_agents_router=True
        )
        try:
            invariant = consumer / ".adwf-consumer/INVARIANTS.md"
            invariant.mkdir(parents=True)
            with self.assertRaisesRegex(Exception, "UPGRADE_APPLY_TARGET_INSTRUCTION_POLICY_INVALID"):
                self.apply(source, target, consumer, compatibility, plan, snapshot)
            self.assertFalse((consumer / ".adwf-runtime/consumer-upgrade").exists())
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
