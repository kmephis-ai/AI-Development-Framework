from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.consumer_profile import ConsumerProfileError, load_consumer_profile, seal_profile  # noqa: E402
from lib.managed_surface import plan_adoption  # noqa: E402
from lib.pack_materializer import materialize_project_pack  # noqa: E402
from lib.reference_conformance import (  # noqa: E402
    ReferenceConformanceError,
    initialize_reference_apps_script_consumer,
    run_reference_apps_script_conformance,
    seal_reference_conformance_report,
    validate_reference_conformance_report,
)


class ReferenceAppsScriptConformanceTests(unittest.TestCase):
    def _mini_framework(self, base: Path) -> tuple[Path, str]:
        source = base / "framework"
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
            "project-execution-evidence.schema.json",
            "reference-conformance-report.schema.json",
        )
        for name in schema_names:
            shutil.copy2(ROOT / ".adwf/schemas" / name, source / ".adwf/schemas" / name)
        shutil.copy2(ROOT / ".adwf/config.json", source / ".adwf/config.json")
        shutil.copy2(ROOT / ".gitignore", source / ".gitignore")
        shutil.copy2(ROOT / ".adwf/packs/apps-script.json", source / ".adwf/packs/apps-script.json")
        shutil.copy2(ROOT / ".adwf/packs/node.json", source / ".adwf/packs/node.json")
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = [".gitignore"]
        (source / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        files = sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file())
        manifest = {
            "framework": "AI Development Framework",
            "version": "asref-test",
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
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "ADWF ASREF Test"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "asref-test@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mini framework"], cwd=source, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, revision

    def test_reference_apps_script_full_lifecycle_report_passes_without_google_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_apps_script_conformance(
                source, template_root=ROOT / ".adwf/reference-consumers/apps-script"
            )
            self.assertEqual(report["outcome"], "PASS")
            self.assertEqual(report["consumer_class"], "APPS_SCRIPT_DATA_CENTRIC")
            self.assertEqual(report["pack"]["id"], "apps-script")
            self.assertEqual(report["preview"]["status"], "NOT_APPLICABLE")
            self.assertEqual(report["preview"]["capture_mode"], "NOT_APPLICABLE")
            self.assertEqual(report["preview"]["reason"], "DATA_CENTRIC_NO_BROWSER_PREVIEW")
            self.assertEqual(report["preview"]["screenshot_digests"], [])
            self.assertEqual(report["consumer"]["operational_head"], report["gate_execution"]["head_sha"])
            self.assertEqual(report["functional"]["gate_execution_id"], report["gate_execution"]["execution_id"])
            self.assertFalse(report["functional"]["google_credentials_required"])
            self.assertFalse(report["functional"]["external_network_required"])
            self.assertEqual(report["effective_config"]["project_type"], "apps-script")
            self.assertEqual(report["effective_config"]["pack_digest"], report["pack"]["digest"])
            self.assertEqual(report["preservation"]["before_sha256"], report["preservation"]["after_detach_sha256"])
            self.assertTrue(report["readoption"]["transaction_identity_changed"])
            self.assertEqual(validate_reference_conformance_report(report, source), [])
            self.assertIn("GOOGLE_APPS_SCRIPT_RUNTIME_NOT_EXECUTED", report["limitations"])
            self.assertIn("GOOGLE_PROVIDER_NOT_VERIFIED", report["limitations"])

    def test_report_digest_class_pack_and_functional_binding_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_apps_script_conformance(source, template_root=ROOT / ".adwf/reference-consumers/apps-script")
            forged = copy.deepcopy(report)
            forged["functional"]["gate_execution_id"] = "PEX-" + "a" * 32
            forged = seal_reference_conformance_report(forged)
            self.assertIn("REFERENCE_CONFORMANCE_FUNCTIONAL_GATE_MISMATCH", validate_reference_conformance_report(forged, source))
            substituted = copy.deepcopy(report)
            substituted["pack"]["id"] = "react"
            substituted = seal_reference_conformance_report(substituted)
            self.assertIn("REFERENCE_CONFORMANCE_APPS_SCRIPT_PACK_MISMATCH", validate_reference_conformance_report(substituted, source))
            missing_truth = copy.deepcopy(report)
            missing_truth["limitations"].remove("GOOGLE_PROVIDER_NOT_VERIFIED")
            missing_truth = seal_reference_conformance_report(missing_truth)
            self.assertIn(
                "REFERENCE_CONFORMANCE_LIMITATION_MISSING:GOOGLE_PROVIDER_NOT_VERIFIED",
                validate_reference_conformance_report(missing_truth, source),
            )
            raw_tamper = copy.deepcopy(report)
            raw_tamper["limitations"].append("FORGED")
            self.assertIn("REFERENCE_CONFORMANCE_REPORT_DIGEST_MISMATCH", validate_reference_conformance_report(raw_tamper, source))

    def test_pack_command_tracked_mutation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _ = self._mini_framework(base)
            template = base / "template"
            shutil.copytree(ROOT / ".adwf/reference-consumers/apps-script", template)
            check = template / "scripts/check.mjs"
            text = check.read_text(encoding="utf-8")
            text = text.replace(
                "if (phase === 'test') {",
                "if (phase === 'test') { fs.appendFileSync('Code.gs', '\\n// runtime mutation\\n');",
            )
            check.write_text(text, encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_apps_script_conformance(source, template_root=template)
            self.assertIn("REFERENCE_GATE_SAFETY_BLOCK", str(ctx.exception))

    def test_template_external_dependency_or_deployment_script_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template"
            shutil.copytree(ROOT / ".adwf/reference-consumers/apps-script", template)
            package = json.loads((template / "package.json").read_text(encoding="utf-8"))
            package["dependencies"] = {"googleapis": "1.0.0"}
            (template / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                initialize_reference_apps_script_consumer(Path(tmp) / "consumer", template)
            self.assertIn("REFERENCE_APPS_SCRIPT_EXTERNAL_PACKAGE_DEPENDENCY_FORBIDDEN", str(ctx.exception))

    def test_dirty_framework_source_is_not_accepted_as_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            (source / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_apps_script_conformance(source, template_root=ROOT / ".adwf/reference-consumers/apps-script")
            self.assertIn("REFERENCE_FRAMEWORK_SOURCE_NOT_CLEAN", str(ctx.exception))

    def test_profile_pack_substitution_and_staleness_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _ = self._mini_framework(base)
            consumer = base / "profile-consumer"
            initialize_reference_apps_script_consumer(consumer, ROOT / ".adwf/reference-consumers/apps-script")
            applied = materialize_project_pack(
                consumer, source, apply=True, product_name="Reference", default_branch="main", repository_visibility="PRIVATE"
            )
            self.assertEqual(applied["pack"], "apps-script")
            path = consumer / ".adwf-consumer/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["project_pack_digest"] = "c" * 64
            path.write_text(json.dumps(seal_profile(profile), indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ConsumerProfileError) as ctx:
                load_consumer_profile(consumer, source, required=True)
            self.assertIn("CONSUMER_PROFILE_PACK_DIGEST_MISMATCH", str(ctx.exception))

            path.unlink()
            applied = materialize_project_pack(
                consumer, source, apply=True, product_name="Reference", default_branch="main", repository_visibility="PRIVATE"
            )
            self.assertEqual(applied["status"], "APPLIED")
            config = source / ".adwf/config.json"
            config.write_text(config.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(ConsumerProfileError) as stale:
                load_consumer_profile(consumer, source, required=True)
            self.assertIn("CONSUMER_PROFILE_FRAMEWORK_CONFIG_STALE", str(stale.exception))

    @unittest.skipIf(os.name == "nt", "symlink creation is privilege-dependent on Windows")
    def test_managed_surface_symlink_collision_blocks_reference_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, revision = self._mini_framework(base)
            consumer = base / "consumer"
            initialize_reference_apps_script_consumer(consumer, ROOT / ".adwf/reference-consumers/apps-script")
            (consumer / ".adwf").mkdir()
            (consumer / "foreign-config").write_text("foreign\n", encoding="utf-8")
            (consumer / ".adwf/config.json").symlink_to(consumer / "foreign-config")
            plan = plan_adoption(source, consumer, source_revision=revision)
            self.assertEqual(plan["status"], "BLOCK")
            self.assertTrue(any(item.startswith("TARGET_SYMLINK_FORBIDDEN:.adwf/config.json") for item in plan["blockers"]))


if __name__ == "__main__":
    unittest.main()
