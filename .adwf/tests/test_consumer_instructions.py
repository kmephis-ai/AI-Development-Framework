from __future__ import annotations
from pathlib import Path
import json, sys, tempfile, unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_fixture import build_framework  # noqa: E402
from lib.consumer_instructions import (  # noqa: E402
    ConsumerInstructionError, load_consumer_instruction_policy, validate_consumer_instruction_state, validate_consumer_router,
)
from lib.managed_surface import load_source_inventory  # noqa: E402


class ConsumerInstructionContractTests(unittest.TestCase):
    def test_01_policy_binds_framework_core_router_and_consumer_invariants_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / "framework"; framework.mkdir(); build_framework(framework, ROOT)
            inventory = load_source_inventory(framework)
            policy = load_consumer_instruction_policy(framework, inventory)
            self.assertEqual(policy["framework_core"]["ownership"], "FRAMEWORK_PRIVATE")
            self.assertEqual(policy["router"]["ownership"], "SHARED_GUARDED")
            self.assertEqual(policy["consumer_invariants"]["ownership"], "CONSUMER_OWNED")
            self.assertNotIn(policy["consumer_invariants"]["path"], inventory["files"])

    def test_02_compact_router_template_passes_and_volatile_state_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); framework = root / "framework"; consumer = root / "consumer"
            framework.mkdir(); consumer.mkdir(); build_framework(framework, ROOT)
            policy = load_consumer_instruction_policy(framework, load_source_inventory(framework))
            (consumer / "AGENTS.md").write_bytes((framework / ".adwf/instructions/AGENTS_ROUTER.template.md").read_bytes())
            validate_consumer_router(consumer, policy)
            (consumer / "AGENTS.md").write_text(
                (consumer / "AGENTS.md").read_text(encoding="utf-8") + "\nCURRENT_SHA: deadbeef\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ConsumerInstructionError, "ROUTER_VOLATILE_STATE_FORBIDDEN"):
                validate_consumer_router(consumer, policy)

    def test_03_framework_cannot_claim_consumer_invariants_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / "framework"; framework.mkdir(); build_framework(framework, ROOT)
            invariants = framework / ".adwf-consumer/INVARIANTS.md"
            invariants.parent.mkdir(parents=True); invariants.write_text("consumer only\n", encoding="utf-8")
            # Re-seal through fixture helper after adding an unauthorized framework-owned path.
            from consumer_upgrade_fixture import seal_inventory
            seal_inventory(framework)
            with self.assertRaisesRegex(ConsumerInstructionError, "INVARIANTS_OWNERSHIP_INVALID"):
                load_consumer_instruction_policy(framework, load_source_inventory(framework))

    def test_04_router_must_remain_shared_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / "framework"; framework.mkdir(); build_framework(framework, ROOT)
            managed = framework / ".adwf/managed-surface-policy.json"
            value = json.loads(managed.read_text(encoding="utf-8"))
            value["shared_guarded_paths"].remove("AGENTS.md")
            managed.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            from consumer_upgrade_fixture import seal_inventory
            seal_inventory(framework)
            with self.assertRaisesRegex(ConsumerInstructionError, "ROUTER_OWNERSHIP_INVALID"):
                load_consumer_instruction_policy(framework, load_source_inventory(framework))

    def test_05_consumer_invariant_object_must_be_regular_file_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); framework = root / "framework"; consumer = root / "consumer"
            framework.mkdir(); consumer.mkdir(); build_framework(framework, ROOT)
            policy = load_consumer_instruction_policy(framework, load_source_inventory(framework))
            invariant = consumer / ".adwf-consumer/INVARIANTS.md"
            invariant.mkdir(parents=True)
            with self.assertRaisesRegex(ConsumerInstructionError, "INVARIANTS_REGULAR_FILE_REQUIRED"):
                validate_consumer_instruction_state(consumer, policy)


if __name__ == "__main__": unittest.main()
