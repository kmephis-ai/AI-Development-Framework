from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.consumer_profile import (  # noqa: E402
    PROFILE_REL,
    ConsumerProfileError,
    apply_consumer_profile,
    load_consumer_profile,
    load_effective_config,
    plan_consumer_profile,
    seal_profile,
)
from lib.managed_surface import ownership_for, load_source_inventory, plan_adoption, plan_detach  # noqa: E402
from lib.managed_surface_transaction import apply_adoption, apply_detach  # noqa: E402
from lib.pack_materializer import materialize_project_pack  # noqa: E402


class ConsumerProfileTests(unittest.TestCase):
    def _framework(self, base: Path) -> Path:
        framework = base / "framework"
        shutil.copytree(
            ROOT / ".adwf",
            framework / ".adwf",
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
        return framework

    def _node_project(self, base: Path) -> Path:
        project = base / "consumer"
        project.mkdir(parents=True)
        (project / "package.json").write_text(
            json.dumps({"name": "consumer", "scripts": {"test": "echo ok"}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (project / "package-lock.json").write_text("{}\n", encoding="utf-8")
        return project

    def test_profile_materialization_preserves_framework_config_and_overlays_only_consumer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            framework = self._framework(base)
            project = self._node_project(base)
            before = (framework / ".adwf/config.json").read_bytes()
            result = materialize_project_pack(
                project,
                framework,
                apply=True,
                product_name="Family Consumer",
                default_branch="main",
                repository_visibility="PRIVATE",
            )
            self.assertEqual(result["status"], "APPLIED")
            self.assertEqual((framework / ".adwf/config.json").read_bytes(), before)
            self.assertTrue((project / PROFILE_REL).is_file())
            effective = load_effective_config(project, framework)
            self.assertEqual(effective["project"]["name"], "Family Consumer")
            self.assertEqual(effective["project"]["type"], "node")
            self.assertTrue(effective["project"]["runtime_product"])
            self.assertEqual(effective["project"]["repository_visibility"], "PRIVATE")
            self.assertEqual(effective["project_packs"]["selected"], "node")
            canonical = json.loads((framework / ".adwf/config.json").read_text(encoding="utf-8"))
            self.assertEqual(canonical["project"]["type"], "framework")

    def test_profile_cannot_add_governance_authority_and_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            framework = self._framework(base)
            project = self._node_project(base)
            applied = apply_consumer_profile(
                project,
                framework,
                product_name="Consumer",
                default_branch="main",
                repository_visibility="PUBLIC",
            )
            self.assertEqual(applied["status"], "APPLIED")
            path = project / PROFILE_REL
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["policy"] = {"fail_mode": "OPEN"}
            path.write_text(json.dumps(seal_profile(profile), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ConsumerProfileError) as ctx:
                load_consumer_profile(project, framework, required=True)
            self.assertIn("CONSUMER_PROFILE_SCHEMA_MISMATCH", str(ctx.exception))

    def test_stale_framework_or_foreign_existing_profile_blocks_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            framework = self._framework(base)
            project = self._node_project(base)
            applied = apply_consumer_profile(
                project,
                framework,
                product_name="Consumer",
                default_branch="main",
                repository_visibility="PUBLIC",
            )
            self.assertEqual(applied["status"], "APPLIED")
            original = (project / PROFILE_REL).read_bytes()
            config_path = framework / ".adwf/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["project"]["name"] = "Framework Changed"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ConsumerProfileError) as ctx:
                load_effective_config(project, framework)
            self.assertIn("CONSUMER_PROFILE_FRAMEWORK_CONFIG_STALE", str(ctx.exception))
            config_path.write_bytes(ROOT.joinpath(".adwf/config.json").read_bytes())
            (project / PROFILE_REL).write_text('{"foreign":true}\n', encoding="utf-8")
            plan = plan_consumer_profile(
                project,
                framework,
                product_name="Consumer",
                default_branch="main",
                repository_visibility="PUBLIC",
            )
            self.assertEqual(plan["status"], "HUMAN_REQUIRED")
            self.assertEqual((project / PROFILE_REL).read_text(encoding="utf-8"), '{"foreign":true}\n')
            self.assertNotEqual((project / PROFILE_REL).read_bytes(), original)

    def _exact_source_repo(self, base: Path) -> tuple[Path, str]:
        source = base / "source"
        (source / ".adwf/schemas").mkdir(parents=True)
        (source / ".adwf/packs").mkdir(parents=True)
        schema_names = (
            "managed-surface-policy.schema.json",
            "managed-surface-snapshot.schema.json",
            "managed-surface-plan.schema.json",
            "managed-surface-transaction.schema.json",
            "managed-surface-detach-transaction.schema.json",
            "config.schema.json",
            "project-pack.schema.json",
            "consumer-profile.schema.json",
        )
        for name in schema_names:
            shutil.copy2(ROOT / ".adwf/schemas" / name, source / ".adwf/schemas" / name)
        shutil.copy2(ROOT / ".adwf/config.json", source / ".adwf/config.json")
        shutil.copy2(ROOT / ".adwf/packs/node.json", source / ".adwf/packs/node.json")
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = []
        (source / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        files = sorted(
            str(path.relative_to(source)).replace("\\", "/")
            for path in source.rglob("*") if path.is_file()
        )
        manifest = {
            "framework": "AI Development Framework",
            "version": "test",
            "schema_version": 3,
            "scope": "FRAMEWORK_OWNED_TRUST_BOUNDARY",
            "file_count_excluding_manifests": len(files),
            "total_bytes_excluding_manifests": sum((source / rel).stat().st_size for rel in files),
            "files": files,
        }
        (source / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sum_paths = files + ["MANIFEST.json"]
        (source / "SHA256SUMS.txt").write_text(
            "".join(
                f"{hashlib.sha256((source / rel).read_bytes()).hexdigest()}  {rel}\n"
                for rel in sorted(sum_paths)
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "ADWF Consumer Profile Test"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "adwf-test@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "exact source"], cwd=source, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, revision

    def test_adoption_profile_and_guarded_detach_preserve_consumer_overlay_and_original_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, revision = self._exact_source_repo(base)
            consumer = base / "consumer"
            consumer.mkdir()
            (consumer / "package.json").write_text(
                json.dumps({"name": "consumer", "scripts": {"test": "echo ok"}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (consumer / "package-lock.json").write_text("{}\n", encoding="utf-8")
            plan = plan_adoption(source, consumer, source_revision=revision)
            self.assertEqual(plan["status"], "READY")
            adoption = apply_adoption(source, consumer, plan)
            self.assertEqual(adoption["status"], "COMMITTED")
            snapshot = copy.deepcopy(adoption["snapshot"])
            protected_bytes = {
                rel: (consumer / rel).read_bytes()
                for rel in (".adwf/config.json", ".adwf/managed-surface-policy.json")
            }
            profile_result = materialize_project_pack(
                consumer,
                consumer,
                apply=True,
                product_name="Adopted Consumer",
                default_branch="main",
                repository_visibility="PRIVATE",
            )
            self.assertEqual(profile_result["status"], "APPLIED")
            for rel, before in protected_bytes.items():
                self.assertEqual((consumer / rel).read_bytes(), before, rel)
            self.assertTrue((consumer / PROFILE_REL).is_file())
            inventory = load_source_inventory(source)
            self.assertEqual(ownership_for(PROFILE_REL, inventory), "CONSUMER_OWNED")
            detach_plan = plan_detach(consumer, snapshot, framework_root=source)
            self.assertEqual(detach_plan["status"], "READY")
            self.assertNotIn(PROFILE_REL, {item["path"] for item in detach_plan["entries"]})
            detached = apply_detach(source, consumer, snapshot, detach_plan)
            self.assertEqual(detached["status"], "COMMITTED")
            self.assertTrue((consumer / PROFILE_REL).is_file())
            self.assertTrue((consumer / "package.json").is_file())
            self.assertEqual(json.loads((consumer / "package.json").read_text(encoding="utf-8"))["name"], "consumer")


if __name__ == "__main__":
    unittest.main()
