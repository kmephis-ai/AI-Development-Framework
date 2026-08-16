from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.managed_surface import (  # noqa: E402
    ManagedSurfaceError,
    load_source_inventory,
    ownership_for,
    plan_adoption,
    plan_detach,
    snapshot_from_adoption_plan,
    validate_canonical_contract,
)


class ManagedSurfaceTests(unittest.TestCase):
    def minimal_source(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".adwf/schemas").mkdir(parents=True)
        for name in (
            "managed-surface-policy.schema.json",
            "managed-surface-snapshot.schema.json",
            "managed-surface-plan.schema.json",
            "managed-surface-detach-transaction.schema.json",
        ):
            (root / ".adwf/schemas" / name).write_text(
                (ROOT / ".adwf/schemas" / name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = ["README.md"]
        (root / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (root / ".adwf/private.txt").write_text("framework private\n", encoding="utf-8")
        (root / "README.md").write_text("framework readme\n", encoding="utf-8")
        files = sorted([
            ".adwf/managed-surface-policy.json",
            ".adwf/private.txt",
            ".adwf/schemas/managed-surface-plan.schema.json",
            ".adwf/schemas/managed-surface-detach-transaction.schema.json",
            ".adwf/schemas/managed-surface-policy.schema.json",
            ".adwf/schemas/managed-surface-snapshot.schema.json",
            "README.md",
        ])
        manifest = {
            "framework": "AI Development Framework",
            "version": "test",
            "schema_version": 3,
            "scope": "FRAMEWORK_OWNED_TRUST_BOUNDARY",
            "file_count_excluding_manifests": len(files),
            "total_bytes_excluding_manifests": sum((root / rel).stat().st_size for rel in files),
            "files": files,
        }
        manifest_path = root / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths = files + ["MANIFEST.json"]
        sums = "".join(
            f"{hashlib.sha256((root / rel).read_bytes()).hexdigest()}  {rel}\n"
            for rel in sorted(paths)
        )
        (root / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
        return temp, root

    def test_01_canonical_contract_reuses_package_inventory(self) -> None:
        result = validate_canonical_contract(ROOT)
        self.assertEqual(result["status"], "PASS")
        inventory = load_source_inventory(ROOT)
        self.assertEqual(set(inventory["files"]), set(json.loads((ROOT / "MANIFEST.json").read_text())["files"]))
        self.assertEqual(ownership_for("package.json", inventory), "CONSUMER_OWNED")
        self.assertEqual(ownership_for("README.md", inventory), "SHARED_GUARDED")

    def test_02_invalid_source_revision_fails_closed(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            with self.assertRaisesRegex(ManagedSurfaceError, "SOURCE_REVISION_INVALID"):
                plan_adoption(source, target.name, source_revision="short")
        finally:
            temp.cleanup()
            target.cleanup()

    def test_03_shared_collision_blocks_adoption(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            Path(target.name, "README.md").write_text("consumer readme\n", encoding="utf-8")
            plan = plan_adoption(source, target.name, source_revision="1" * 40)
            self.assertEqual(plan["status"], "BLOCK")
            self.assertIn("TARGET_CONTENT_COLLISION:README.md", plan["blockers"])
            item = next(x for x in plan["entries"] if x["path"] == "README.md")
            self.assertEqual(item["ownership"], "SHARED_GUARDED")
            self.assertEqual(item["action"], "BLOCK")
        finally:
            temp.cleanup()
            target.cleanup()

    def test_04_unlisted_product_file_is_never_in_adoption_or_detach_plan(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            product = Path(target.name, "product-data.json")
            product.write_text('{"keep": true}\n', encoding="utf-8")
            plan = plan_adoption(source, target.name, source_revision="2" * 40)
            self.assertEqual(plan["status"], "READY")
            self.assertNotIn("product-data.json", {x["path"] for x in plan["entries"]})
            snapshot = snapshot_from_adoption_plan(plan, source)
            for item in plan["entries"]:
                if item["target_state"] == "ABSENT":
                    dst = Path(target.name, item["path"])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(Path(source, item["path"]).read_bytes())
            detach = plan_detach(target.name, snapshot, framework_root=source)
            self.assertNotIn("product-data.json", {x["path"] for x in detach["entries"]})
            self.assertEqual(product.read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertFalse(detach["write_performed"])
        finally:
            temp.cleanup()
            target.cleanup()

    def test_05_tampered_source_checksum_fails_closed(self) -> None:
        temp, source = self.minimal_source()
        try:
            (source / ".adwf/private.txt").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "SOURCE_FILE_DIGEST_MISMATCH"):
                load_source_inventory(source)
        finally:
            temp.cleanup()

    def test_06_target_symlink_state_is_blocked_without_mutation(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            from lib import managed_surface as module
            original = module._target_state

            def fake_state(path: Path, expected: str):
                if path.as_posix().endswith("/README.md"):
                    return "SYMLINK", None
                return original(path, expected)

            with patch.object(module, "_target_state", side_effect=fake_state):
                plan = plan_adoption(source, target.name, source_revision="3" * 40)
            self.assertEqual(plan["status"], "BLOCK")
            self.assertIn("TARGET_SYMLINK_FORBIDDEN:README.md", plan["blockers"])
            self.assertFalse(plan["write_performed"])
        finally:
            temp.cleanup()
            target.cleanup()

    def test_07_snapshot_owns_only_paths_absent_before_adoption(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            existing = Path(target.name, ".adwf/private.txt")
            existing.parent.mkdir(parents=True)
            existing.write_bytes((source / ".adwf/private.txt").read_bytes())
            plan = plan_adoption(source, target.name, source_revision="4" * 40)
            self.assertEqual(plan["status"], "READY")
            snapshot = snapshot_from_adoption_plan(plan, source)
            private = next(x for x in snapshot["entries"] if x["path"] == ".adwf/private.txt")
            readme = next(x for x in snapshot["entries"] if x["path"] == "README.md")
            self.assertFalse(private["managed_by_adwf"])
            self.assertTrue(readme["managed_by_adwf"])
        finally:
            temp.cleanup()
            target.cleanup()

    def test_08_detach_removes_only_unchanged_private_and_preserves_shared(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            plan = plan_adoption(source, target.name, source_revision="5" * 40)
            snapshot = snapshot_from_adoption_plan(plan, source)
            for item in plan["entries"]:
                dst = Path(target.name, item["path"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(Path(source, item["path"]).read_bytes())
            detach = plan_detach(target.name, snapshot, framework_root=source)
            self.assertEqual(detach["status"], "READY")
            private = next(x for x in detach["entries"] if x["path"] == ".adwf/private.txt")
            shared = next(x for x in detach["entries"] if x["path"] == "README.md")
            self.assertEqual(private["action"], "REMOVE_ELIGIBLE")
            self.assertEqual(shared["action"], "PRESERVE_SHARED")
            self.assertTrue(Path(target.name, ".adwf/private.txt").exists())
            self.assertTrue(Path(target.name, "README.md").exists())
        finally:
            temp.cleanup()
            target.cleanup()

    def test_09_drifted_managed_private_blocks_destructive_detach(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            plan = plan_adoption(source, target.name, source_revision="6" * 40)
            snapshot = snapshot_from_adoption_plan(plan, source)
            for item in plan["entries"]:
                dst = Path(target.name, item["path"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(Path(source, item["path"]).read_bytes())
            Path(target.name, ".adwf/private.txt").write_text("consumer modification\n", encoding="utf-8")
            detach = plan_detach(target.name, snapshot, framework_root=source)
            self.assertEqual(detach["status"], "BLOCK")
            self.assertIn("DETACH_CONTENT_DRIFT:.adwf/private.txt", detach["blockers"])
            private = next(x for x in detach["entries"] if x["path"] == ".adwf/private.txt")
            self.assertEqual(private["action"], "PRESERVE_BLOCK")
        finally:
            temp.cleanup()
            target.cleanup()

    def test_10_preexisting_exact_private_is_preserved(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            dst = Path(target.name, ".adwf/private.txt")
            dst.parent.mkdir(parents=True)
            dst.write_bytes(Path(source, ".adwf/private.txt").read_bytes())
            plan = plan_adoption(source, target.name, source_revision="7" * 40)
            snapshot = snapshot_from_adoption_plan(plan, source)
            detach = plan_detach(target.name, snapshot, framework_root=source)
            private = next(x for x in detach["entries"] if x["path"] == ".adwf/private.txt")
            self.assertEqual(private["action"], "PRESERVE_PREEXISTING")
        finally:
            temp.cleanup()
            target.cleanup()

    def test_11_manifest_traversal_fails_closed(self) -> None:
        temp, source = self.minimal_source()
        try:
            manifest = json.loads((source / "MANIFEST.json").read_text(encoding="utf-8"))
            manifest["files"].append("../escape")
            manifest["files"].sort()
            (source / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ManagedSurfaceError, "SURFACE_PATH_INVALID"):
                load_source_inventory(source)
        finally:
            temp.cleanup()

    def test_12_blocked_adoption_cannot_create_snapshot(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            Path(target.name, "README.md").write_text("collision\n", encoding="utf-8")
            plan = plan_adoption(source, target.name, source_revision="8" * 40)
            with self.assertRaisesRegex(ManagedSurfaceError, "SNAPSHOT_REQUIRES_READY_ADOPTION_PLAN"):
                snapshot_from_adoption_plan(plan, source)
        finally:
            temp.cleanup()
            target.cleanup()

    def test_13_forged_snapshot_cannot_claim_consumer_file_for_detach(self) -> None:
        temp, source = self.minimal_source()
        target = tempfile.TemporaryDirectory()
        try:
            plan = plan_adoption(source, target.name, source_revision="9" * 40)
            snapshot = snapshot_from_adoption_plan(plan, source)
            forged = copy.deepcopy(snapshot)
            forged["entries"][0]["path"] = "product-data.json"
            forged["entries"][0]["ownership"] = "FRAMEWORK_PRIVATE"
            forged["entries"][0]["managed_by_adwf"] = True
            with self.assertRaisesRegex(ManagedSurfaceError, "SNAPSHOT_INVENTORY_SET_MISMATCH"):
                plan_detach(target.name, forged, framework_root=source)
        finally:
            temp.cleanup()
            target.cleanup()


if __name__ == "__main__":
    unittest.main()
