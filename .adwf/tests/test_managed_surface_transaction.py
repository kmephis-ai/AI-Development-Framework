from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.managed_surface import ManagedSurfaceError, plan_adoption  # noqa: E402
from lib.managed_surface_transaction import apply_adoption, recover_adoption  # noqa: E402
from lib.strict_json import load as strict_load  # noqa: E402


class ManagedSurfaceTransactionTests(unittest.TestCase):
    def source_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".adwf/schemas").mkdir(parents=True)
        for name in (
            "managed-surface-policy.schema.json",
            "managed-surface-snapshot.schema.json",
            "managed-surface-plan.schema.json",
            "managed-surface-detach-transaction.schema.json",
            "managed-surface-transaction.schema.json",
        ):
            (root / ".adwf/schemas" / name).write_text(
                (ROOT / ".adwf/schemas" / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = ["README.md"]
        (root / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (root / ".adwf/private.txt").write_text("framework private\n", encoding="utf-8")
        (root / ".adwf/second.txt").write_text("framework second\n", encoding="utf-8")
        (root / "README.md").write_text("framework readme\n", encoding="utf-8")
        files = sorted(
            [
                ".adwf/managed-surface-policy.json",
                ".adwf/private.txt",
                ".adwf/second.txt",
                ".adwf/schemas/managed-surface-plan.schema.json",
                ".adwf/schemas/managed-surface-detach-transaction.schema.json",
                ".adwf/schemas/managed-surface-policy.schema.json",
                ".adwf/schemas/managed-surface-snapshot.schema.json",
                ".adwf/schemas/managed-surface-transaction.schema.json",
                "README.md",
            ]
        )
        manifest = {
            "framework": "AI Development Framework",
            "version": "test",
            "schema_version": 3,
            "scope": "FRAMEWORK_OWNED_TRUST_BOUNDARY",
            "file_count_excluding_manifests": len(files),
            "total_bytes_excluding_manifests": sum((root / rel).stat().st_size for rel in files),
            "files": files,
        }
        (root / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sum_paths = files + ["MANIFEST.json"]
        (root / "SHA256SUMS.txt").write_text(
            "".join(
                f"{hashlib.sha256((root / rel).read_bytes()).hexdigest()}  {rel}\n" for rel in sorted(sum_paths)
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "ADWF Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "adwf-test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "source"], cwd=root, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        return temp, root, revision

    def plan(self, source: Path, target: Path, revision: str) -> dict:
        return plan_adoption(source, target, source_revision=revision)

    def journal_path(self, target: Path, transaction_id: str) -> Path:
        return target / ".adwf-runtime/managed-surface/transactions" / f"{transaction_id}.json"

    def test_01_successful_apply_creates_only_absent_and_binds_snapshot(self) -> None:
        temp, source, revision = self.source_repo()
        consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name)
            product = target / "product-data.json"
            product.write_text('{"keep": true}\n', encoding="utf-8")
            exact = target / "README.md"
            exact.write_bytes((source / "README.md").read_bytes())
            plan = self.plan(source, target, revision)
            result = apply_adoption(source, target, plan)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual(product.read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertEqual(exact.read_bytes(), (source / "README.md").read_bytes())
            snapshot = result["snapshot"]
            self.assertEqual(snapshot["transaction_id"], result["transaction_id"])
            self.assertEqual(snapshot["plan_sha256"], strict_load(self.journal_path(target, result["transaction_id"]))["plan_sha256"])
            self.assertTrue(len(snapshot["consumer_root_sha256"]) == 64)
            readme = next(x for x in snapshot["entries"] if x["path"] == "README.md")
            private = next(x for x in snapshot["entries"] if x["path"] == ".adwf/private.txt")
            self.assertFalse(readme["managed_by_adwf"])
            self.assertTrue(private["managed_by_adwf"])
            self.assertTrue(Path(target, result["snapshot_path"]).is_file())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_02_collision_between_plan_and_apply_never_overwrites(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            collision = target / ".adwf/private.txt"
            collision.parent.mkdir(parents=True)
            collision.write_text("consumer wins\n", encoding="utf-8")
            result = apply_adoption(source, target, plan)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertEqual(collision.read_text(encoding="utf-8"), "consumer wins\n")
            self.assertFalse((target / "README.md").exists())
        finally:
            temp.cleanup(); consumer.cleanup()

    @unittest.skipIf(not hasattr(Path, "symlink_to"), "symlink unsupported")
    def test_03_symlink_parent_blocks_without_escape_write(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory(); outside = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            (target / ".adwf").symlink_to(Path(outside.name), target_is_directory=True)
            result = apply_adoption(source, target, plan)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertIn("TARGET_PARENT_SYMLINK_FORBIDDEN", result["error"])
            self.assertEqual(list(Path(outside.name).iterdir()), [])
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        finally:
            temp.cleanup(); consumer.cleanup(); outside.cleanup()

    def test_04_fault_after_writes_rolls_back_only_transaction_files(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); product = target / "product.txt"; product.write_text("keep\n", encoding="utf-8")
            plan = self.plan(source, target, revision)
            result = apply_adoption(source, target, plan, fault_after_writes=2)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertEqual(product.read_text(encoding="utf-8"), "keep\n")
            for item in plan["entries"]:
                if item["action"] == "CREATE_PLANNED":
                    self.assertFalse((target / item["path"]).exists(), item["path"])
            self.assertFalse(any((target / ".adwf-runtime/managed-surface/snapshots").glob("*.snapshot.json")))
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_05_drift_after_transaction_write_is_preserved_and_blocks_recovery(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            from lib import managed_surface_transaction as module
            original = module._link_stage_no_replace
            mutated = {"done": False, "path": None}
            def drift(stage: Path, destination: Path, expected: str) -> None:
                original(stage, destination, expected)
                if not mutated["done"]:
                    destination.write_text("consumer drift after create\n", encoding="utf-8")
                    mutated["done"] = True; mutated["path"] = destination
            with patch.object(module, "_link_stage_no_replace", side_effect=drift):
                result = apply_adoption(source, target, plan)
            self.assertEqual(result["status"], "RECOVERY_BLOCKED")
            self.assertTrue(mutated["path"].exists())
            self.assertEqual(mutated["path"].read_text(encoding="utf-8"), "consumer drift after create\n")
            self.assertTrue(any("RECOVERY_TARGET_DRIFT" in x for x in result["blockers"]))
            rel = mutated["path"].relative_to(target).as_posix()
            mutated["path"].write_bytes((source / rel).read_bytes())
            recovered = recover_adoption(source, target, result["transaction_id"])
            self.assertEqual(recovered["status"], "ROLLED_BACK")
            self.assertFalse(mutated["path"].exists())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_06_forged_ready_plan_is_rejected_before_write(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision); forged = copy.deepcopy(plan)
            forged["entries"][0]["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(ManagedSurfaceError, "PLAN_SOURCE_DIGEST_MISMATCH"):
                apply_adoption(source, target, forged)
            self.assertFalse((target / ".adwf-runtime").exists())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_07_forged_journal_identity_is_rejected(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            first = apply_adoption(source, target, plan, fault_after_writes=1)
            self.assertEqual(first["status"], "ROLLED_BACK")
            path = self.journal_path(target, first["transaction_id"])
            journal = strict_load(path); journal["plan_sha256"] = "f" * 64
            path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "TRANSACTION_JOURNAL_DIGEST_MISMATCH"):
                apply_adoption(source, target, plan)
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_08_repeated_apply_is_idempotent(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            first = apply_adoption(source, target, plan)
            before = {x["path"]: hashlib.sha256((target / x["path"]).read_bytes()).hexdigest() for x in plan["entries"]}
            second = apply_adoption(source, target, plan)
            after = {x["path"]: hashlib.sha256((target / x["path"]).read_bytes()).hexdigest() for x in plan["entries"]}
            self.assertEqual(second["status"], "ALREADY_COMMITTED")
            self.assertFalse(second["write_performed"])
            self.assertEqual(before, after)
            self.assertEqual(first["transaction_id"], second["transaction_id"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_09_interrupted_applying_journal_resumes_without_reclaiming_foreign_files(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            committed = apply_adoption(source, target, plan)
            path = self.journal_path(target, committed["transaction_id"])
            journal = strict_load(path)
            journal["status"] = "APPLYING"; journal["snapshot_path"] = None; journal["snapshot_sha256"] = None
            from lib.managed_surface_transaction import _seal_transaction
            journal = _seal_transaction(journal)
            path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
            snapshot_path = Path(target, committed["snapshot_path"]); snapshot_path.unlink()
            resumed = apply_adoption(source, target, plan)
            self.assertEqual(resumed["status"], "COMMITTED")
            self.assertEqual(resumed["transaction_id"], committed["transaction_id"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_10_source_revision_move_blocks_even_when_package_bytes_are_same(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "move head"], cwd=source, check=True)
            with self.assertRaisesRegex(ManagedSurfaceError, "SOURCE_REVISION_MISMATCH"):
                apply_adoption(source, target, plan)
            self.assertFalse((target / ".adwf-runtime").exists())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_11_recover_is_idempotent_after_rollback(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            failed = apply_adoption(source, target, plan, fault_after_writes=1)
            self.assertEqual(failed["status"], "ROLLED_BACK")
            recovered = recover_adoption(source, target, failed["transaction_id"])
            self.assertEqual(recovered["status"], "ROLLED_BACK")
            self.assertEqual(recovered["blockers"], [])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_12_preexisting_exact_private_is_not_reclassified_as_managed(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); dst = target / ".adwf/private.txt"; dst.parent.mkdir(parents=True)
            dst.write_bytes((source / ".adwf/private.txt").read_bytes())
            plan = self.plan(source, target, revision)
            result = apply_adoption(source, target, plan)
            item = next(x for x in result["snapshot"]["entries"] if x["path"] == ".adwf/private.txt")
            self.assertFalse(item["managed_by_adwf"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_13_dirty_source_checkout_cannot_impersonate_exact_revision(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            (source / "untracked.tmp").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "SOURCE_WORKTREE_NOT_CLEAN"):
                apply_adoption(source, target, plan)
            self.assertFalse((target / ".adwf-runtime").exists())
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_15_retry_after_clean_rollback_commits_and_then_is_idempotent(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); product = target / "product.txt"; product.write_text("keep\n", encoding="utf-8")
            plan = self.plan(source, target, revision)
            failed = apply_adoption(source, target, plan, fault_after_writes=1)
            self.assertEqual(failed["status"], "ROLLED_BACK")
            self.assertEqual(product.read_text(encoding="utf-8"), "keep\n")
            retried = apply_adoption(source, target, plan)
            self.assertEqual(retried["status"], "COMMITTED")
            self.assertEqual(retried["transaction_id"], failed["transaction_id"])
            self.assertEqual(product.read_text(encoding="utf-8"), "keep\n")
            repeated = apply_adoption(source, target, plan)
            self.assertEqual(repeated["status"], "ALREADY_COMMITTED")
            self.assertFalse(repeated["write_performed"])
            self.assertEqual(product.read_text(encoding="utf-8"), "keep\n")
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_16_adoption_recovery_required_resumes_with_adoption_semantics(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            committed = apply_adoption(source, target, plan)
            path = self.journal_path(target, committed["transaction_id"])
            journal = strict_load(path)
            journal["status"] = "RECOVERY_REQUIRED"
            journal["snapshot_path"] = None
            journal["snapshot_sha256"] = None
            from lib.managed_surface_transaction import _seal_transaction
            journal = _seal_transaction(journal)
            path.write_text(json.dumps(journal, indent=2) + "\n", encoding="utf-8")
            Path(target, committed["snapshot_path"]).unlink()
            resumed = apply_adoption(source, target, plan)
            self.assertEqual(resumed["status"], "COMMITTED")
            self.assertEqual(resumed["transaction_id"], committed["transaction_id"])
        finally:
            temp.cleanup(); consumer.cleanup()

    def test_14_no_replace_link_failure_never_falls_back_to_overwrite(self) -> None:
        temp, source, revision = self.source_repo(); consumer = tempfile.TemporaryDirectory()
        try:
            target = Path(consumer.name); plan = self.plan(source, target, revision)
            from lib import managed_surface_transaction as module
            with patch.object(module.os, "link", side_effect=OSError("unsupported")):
                result = apply_adoption(source, target, plan)
            self.assertEqual(result["status"], "ROLLED_BACK")
            self.assertIn("ATOMIC_NO_REPLACE_CREATE_FAILED", result["error"])
            for item in plan["entries"]:
                if item["action"] == "CREATE_PLANNED":
                    self.assertFalse((target / item["path"]).exists(), item["path"])
        finally:
            temp.cleanup(); consumer.cleanup()


if __name__ == "__main__":
    unittest.main()
