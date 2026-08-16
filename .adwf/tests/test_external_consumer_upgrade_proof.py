from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from consumer_upgrade_fixture import build_framework, seal_inventory
from lib.external_consumer_upgrade_proof import (
    ExternalConsumerUpgradeProofError,
    run_external_consumer_upgrade_proof,
    validate_external_consumer_upgrade_proof,
)
import lib.external_consumer_upgrade_proof as proof_mod


class ExternalConsumerUpgradeProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = Path(__file__).resolve().parents[2]

    def _git(self, root: Path, *args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
        return proc.stdout.strip()

    def _commit(self, root: Path, message: str) -> tuple[str, str]:
        self._git(root, "init", "-q", "-b", "main")
        self._git(root, "config", "user.name", "ADWF Proof Test")
        self._git(root, "config", "user.email", "adwf-proof@example.invalid")
        self._git(root, "config", "core.autocrlf", "false")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-q", "-m", message)
        return self._git(root, "rev-parse", "HEAD"), self._git(root, "rev-parse", "HEAD^{tree}")

    def _fixture(self):
        temp = tempfile.TemporaryDirectory()
        base = Path(temp.name)
        source, target, consumer = base / "source", base / "target", base / "consumer"
        source.mkdir(); consumer.mkdir()
        build_framework(source, self.canonical)
        source_sha, source_tree = self._commit(source, "framework A")
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))
        proof_schema = target / ".adwf/schemas/external-consumer-upgrade-proof.schema.json"
        proof_schema.parent.mkdir(parents=True, exist_ok=True)
        proof_schema.write_bytes((self.canonical / ".adwf/schemas/external-consumer-upgrade-proof.schema.json").read_bytes())
        (target / ".adwf/private.txt").write_text("private-v2\n", encoding="utf-8")
        seal_inventory(target)
        target_sha, target_tree = self._commit(target, "framework B")
        (consumer / "appsscript.json").write_text('{"timeZone":"Etc/UTC"}\n', encoding="utf-8")
        (consumer / "Code.gs").write_text("function total(){return 1;}\n", encoding="utf-8")
        (consumer / "README.md").write_text("consumer-owned shared readme\n", encoding="utf-8")
        consumer_sha, consumer_tree = self._commit(consumer, "external consumer")
        return temp, source, target, consumer, source_sha, source_tree, target_sha, target_tree, consumer_sha, consumer_tree

    def _run(self, fixture, **overrides):
        temp, source, target, consumer, source_sha, source_tree, target_sha, target_tree, consumer_sha, consumer_tree = fixture
        kwargs = {
            "consumer_repository": "owner/real-product",
            "consumer_sha": consumer_sha,
            "consumer_tree": consumer_tree,
            "source_sha": source_sha,
            "source_tree": source_tree,
            "target_sha": target_sha,
            "target_tree": target_tree,
            "product_name": "Real Product",
            "default_branch": "main",
            "repository_visibility": "PUBLIC",
            "provider_run_id": "TEST-RUN-1",
        }
        kwargs.update(overrides)
        return run_external_consumer_upgrade_proof(consumer, source, target, **kwargs)

    def test_full_cycle_isolated_and_byte_preserved(self):
        fixture = self._fixture()
        try:
            consumer = fixture[3]
            before_status = self._git(consumer, "status", "--porcelain=v1", "--untracked-files=all")
            report = self._run(fixture)
            self.assertEqual(report["transitions"], {
                "adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"
            })
            self.assertEqual(report["pack"]["id"], "apps-script")
            self.assertEqual(report["upgrade"]["transaction_id"], report["upgrade"]["retry_transaction_id"])
            self.assertTrue(report["external_source_unchanged"])
            self.assertFalse(report["write_back_performed"])
            self.assertTrue(all(item["preservation_sha256"] == report["preservation_set_sha256"] for item in report["preservation_checkpoints"]))
            self.assertEqual(validate_external_consumer_upgrade_proof(report, fixture[2]), [])
            self.assertEqual(self._git(consumer, "status", "--porcelain=v1", "--untracked-files=all"), before_status)
            self.assertFalse((consumer / ".adwf").exists())
            self.assertFalse((consumer / ".adwf-consumer").exists())
        finally:
            fixture[0].cleanup()

    def test_substituted_or_dirty_consumer_identity_blocks(self):
        fixture = self._fixture()
        try:
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "CONSUMER_SHA_MISMATCH"):
                self._run(fixture, consumer_sha="0" * 40)
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "CONSUMER_TREE_MISMATCH"):
                self._run(fixture, consumer_tree="1" * 40)
            (fixture[3] / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "CONSUMER_DIRTY"):
                self._run(fixture)
        finally:
            fixture[0].cleanup()

    def test_wrong_framework_identity_blocks(self):
        fixture = self._fixture()
        try:
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "SOURCE_FRAMEWORK_SHA_MISMATCH"):
                self._run(fixture, source_sha="0" * 40)
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "TARGET_FRAMEWORK_TREE_MISMATCH"):
                self._run(fixture, target_tree="1" * 40)
        finally:
            fixture[0].cleanup()

    def test_preservation_mismatch_blocks_without_source_write(self):
        fixture = self._fixture()
        real_apply = proof_mod.apply_adoption
        seen_disposable: list[Path] = []
        def corrupt_after_adoption(framework_root, consumer_root, plan, **kwargs):
            result = real_apply(framework_root, consumer_root, plan, **kwargs)
            root = Path(consumer_root).resolve(); seen_disposable.append(root)
            (root / "README.md").write_text("tampered disposable bytes\n", encoding="utf-8")
            return result
        try:
            with patch.object(proof_mod, "apply_adoption", side_effect=corrupt_after_adoption):
                with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "PRESERVATION_MISMATCH:ADOPTION_A:README.md"):
                    self._run(fixture)
            self.assertTrue(seen_disposable)
            self.assertNotEqual(seen_disposable[0], fixture[3].resolve())
            self.assertEqual((fixture[3] / "README.md").read_text(encoding="utf-8"), "consumer-owned shared readme\n")
            self.assertFalse((fixture[3] / ".adwf").exists())
        finally:
            fixture[0].cleanup()


    def test_disposable_root_cannot_overlap_source_or_framework_checkout(self):
        fixture = self._fixture()
        try:
            external = fixture[3]
            class UnsafeTempDir:
                def __init__(self, *args, **kwargs):
                    self.path = external
                def __enter__(self):
                    return str(self.path)
                def __exit__(self, exc_type, exc, tb):
                    return False
            with patch.object(proof_mod.tempfile, "TemporaryDirectory", UnsafeTempDir):
                with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "DISPOSABLE_NOT_ISOLATED"):
                    self._run(fixture)
            self.assertEqual(self._git(external, "status", "--porcelain=v1", "--untracked-files=all"), "")
            self.assertFalse((external / ".adwf").exists())
        finally:
            fixture[0].cleanup()

    def test_tampered_report_fails_self_verification(self):
        fixture = self._fixture()
        try:
            report = self._run(fixture)
            tampered = json.loads(json.dumps(report))
            tampered["transitions"]["retry_b"] = "ROLLED_BACK"
            errors = validate_external_consumer_upgrade_proof(tampered, fixture[2])
            self.assertIn("EXTERNAL_PROOF_REPORT_DIGEST_MISMATCH", errors)
            self.assertIn("EXTERNAL_PROOF_TRANSITION_MISMATCH", errors)
        finally:
            fixture[0].cleanup()

    def test_tracked_symlink_is_rejected(self):
        if not hasattr(Path, "symlink_to"):
            self.skipTest("symlink unavailable")
        fixture = self._fixture()
        try:
            consumer = fixture[3]
            link = consumer / "linked.txt"
            try:
                link.symlink_to("README.md")
            except OSError:
                self.skipTest("symlink unavailable on this platform")
            self._git(consumer, "add", "linked.txt")
            self._git(consumer, "commit", "-q", "-m", "tracked symlink")
            sha = self._git(consumer, "rev-parse", "HEAD"); tree = self._git(consumer, "rev-parse", "HEAD^{tree}")
            with self.assertRaisesRegex(ExternalConsumerUpgradeProofError, "TRACKED_SYMLINK_FORBIDDEN"):
                self._run(fixture, consumer_sha=sha, consumer_tree=tree)
        finally:
            fixture[0].cleanup()


if __name__ == "__main__":
    unittest.main()
