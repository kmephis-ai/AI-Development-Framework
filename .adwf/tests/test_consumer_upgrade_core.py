from __future__ import annotations
from pathlib import Path
import copy, hashlib, json, shutil, sys, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_fixture import A, B, prepared, seal_inventory  # noqa: E402
from lib.consumer_upgrade import ConsumerUpgradeError, build_upgrade_compatibility, plan_consumer_upgrade, validate_upgrade_compatibility, validate_upgrade_plan  # noqa: E402


class ConsumerUpgradeCoreTests(unittest.TestCase):
    def compat(self, source, target, consumer, snapshot, **kwargs):
        with patch("lib.consumer_upgrade._verify_revision", return_value=None):
            return build_upgrade_compatibility(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot, **kwargs)

    def plan(self, source, target, consumer, snapshot, **kwargs):
        with patch("lib.consumer_upgrade._verify_revision", return_value=None):
            return plan_consumer_upgrade(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot, **kwargs)

    def test_01_exact_state_is_deterministic_ready_and_read_only(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            first = self.plan(source, target, consumer, snapshot); second = self.plan(source, target, consumer, snapshot)
            self.assertEqual(first, second); self.assertEqual(first["status"], "READY"); self.assertFalse(first["write_performed"])
            self.assertTrue(all(item["action"] in {"KEEP_EXACT", "PRESERVE_SHARED"} for item in first["entries"]))
        finally: temp.cleanup()

    def test_02_private_change_has_explicit_rollback_prerequisite(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            (target / ".adwf/private.txt").write_text("private-v2\n", encoding="utf-8"); seal_inventory(target)
            result = self.compat(source, target, consumer, snapshot)
            entry = next(item for item in result["path_entries"] if item["path"] == ".adwf/private.txt")
            self.assertEqual(entry["classification"], "MODIFY_FRAMEWORK_PRIVATE"); self.assertEqual(entry["action"], "REPLACE_PLANNED")
            self.assertTrue(any(item["path"] == ".adwf/private.txt" for item in result["rollback_prerequisites"]["restore"]))
        finally: temp.cleanup()

    def test_03_shared_change_is_preserve_only_human_required(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            (target / "README.md").write_text("shared-v2\n", encoding="utf-8"); seal_inventory(target)
            result = self.compat(source, target, consumer, snapshot)
            entry = next(item for item in result["path_entries"] if item["path"] == "README.md")
            self.assertEqual(entry["action"], "PRESERVE_SHARED"); self.assertEqual(result["status"], "HUMAN_REQUIRED")
        finally: temp.cleanup()

    def test_04_pack_and_skill_substitution_require_explicit_records(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            pack_path = target / ".adwf/packs/apps-script.json"; pack = json.loads(pack_path.read_text(encoding="utf-8"))
            pack["commands"]["lint"]["phases"] = ["pr", "main"]; pack_path.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
            registry_path = target / "skills/registry.json"; registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["skills"][0]["package_sha256"] = "2" * 64; registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
            seal_inventory(target)
            bindings = {"schema_version": 1, "bindings": [{"id": "bound-skill", "package_sha256": "1" * 64}]}
            result = self.compat(source, target, consumer, snapshot, skill_bindings=bindings)
            self.assertEqual(result["project_pack"]["status"], "HUMAN_REQUIRED"); self.assertEqual(result["skills"][0]["status"], "HUMAN_REQUIRED")
        finally: temp.cleanup()

    def test_05_forged_seals_are_rejected(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            compatibility = self.compat(source, target, consumer, snapshot); forged = copy.deepcopy(compatibility); forged["status"] = "BLOCK"
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_COMPATIBILITY_DIGEST_MISMATCH"): validate_upgrade_compatibility(forged, target)
            plan = self.plan(source, target, consumer, snapshot); forged_plan = copy.deepcopy(plan); forged_plan["plan_sha256"] = "0" * 64
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_PLAN_DIGEST_MISMATCH"): validate_upgrade_plan(forged_plan, compatibility, target)
        finally: temp.cleanup()

    def test_06_legacy_preexisting_agents_router_change_is_ready_only_under_instruction_policy(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            consumer_bytes = b"# Legacy consumer AGENTS\nproduct invariant remains here\n"
            (consumer / "AGENTS.md").write_bytes(consumer_bytes)
            source_item = next(item for item in snapshot["entries"] if item["path"] == "AGENTS.md")
            source_item["managed_by_adwf"] = False
            source_item["preserved_sha256"] = hashlib.sha256(consumer_bytes).hexdigest()
            (target / "AGENTS.md").write_text("# Framework package router revision v2\n", encoding="utf-8")
            seal_inventory(target)
            result = self.plan(source, target, consumer, snapshot)
            entry = next(item for item in result["entries"] if item["path"] == "AGENTS.md")
            self.assertEqual(entry["action"], "PRESERVE_PREEXISTING")
            self.assertNotEqual(entry["source_sha256"], entry["target_sha256"])
            self.assertEqual(result["status"], "READY")
            self.assertEqual(result["findings"], [])
            self.assertEqual((consumer / "AGENTS.md").read_bytes(), consumer_bytes)
        finally: temp.cleanup()

    def test_07_non_instruction_preexisting_shared_change_remains_human_required(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            consumer_bytes = b"consumer-owned readme\n"
            (consumer / "README.md").write_bytes(consumer_bytes)
            source_item = next(item for item in snapshot["entries"] if item["path"] == "README.md")
            source_item["managed_by_adwf"] = False
            source_item["preserved_sha256"] = hashlib.sha256(consumer_bytes).hexdigest()
            (target / "README.md").write_text("shared-v2\n", encoding="utf-8")
            seal_inventory(target)
            result = self.compat(source, target, consumer, snapshot)
            self.assertEqual(result["status"], "HUMAN_REQUIRED")
            self.assertIn(
                {"severity": "HUMAN_REQUIRED", "code": "UPGRADE_PREEXISTING_PATH_CHANGE", "subject": "README.md"},
                result["findings"],
            )
        finally: temp.cleanup()

    def test_08_missing_target_instruction_policy_fails_closed(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            (target / ".adwf/consumer-instruction-policy.json").unlink()
            seal_inventory(target)
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_TARGET_INSTRUCTION_POLICY_INVALID"):
                self.compat(source, target, consumer, snapshot)
        finally: temp.cleanup()

    def test_09_consumer_invariant_type_ambiguity_blocks_planning(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            invariant = consumer / ".adwf-consumer/INVARIANTS.md"
            invariant.mkdir(parents=True)
            with self.assertRaisesRegex(ConsumerUpgradeError, "UPGRADE_TARGET_INSTRUCTION_POLICY_INVALID"):
                self.compat(source, target, consumer, snapshot)
        finally: temp.cleanup()

    def test_10_consumer_invariant_parent_ambiguity_blocks_planning(self):
        temp, source, target, consumer, snapshot = prepared(ROOT)
        try:
            parent = consumer / ".adwf-consumer"
            shutil.rmtree(parent)
            parent.write_text("not-a-directory\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerUpgradeError, "INVARIANTS_PARENT_DIRECTORY_REQUIRED"):
                self.compat(source, target, consumer, snapshot)
        finally: temp.cleanup()

if __name__ == "__main__": unittest.main()
