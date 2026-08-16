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
    initialize_reference_web_consumer,
    run_reference_web_conformance,
    seal_reference_conformance_report,
    validate_reference_conformance_report,
)


class ReferenceWebConformanceTests(unittest.TestCase):
    def _mini_framework(self, base: Path) -> tuple[Path, str]:
        source = base / "framework"
        (source / ".adwf/schemas").mkdir(parents=True)
        (source / ".adwf/packs").mkdir(parents=True)
        (source / ".adwf/preview/node_modules/playwright").mkdir(parents=True)
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
        shutil.copy2(ROOT / ".adwf/packs/react.json", source / ".adwf/packs/react.json")
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        # Keep the canonical shared-guarded semantics relevant to this mini
        # package without requiring every root shared path in the full release.
        policy["shared_guarded_paths"] = [".gitignore"]
        (source / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (source / ".adwf/preview/package.json").write_text(
            '{"private":true,"dependencies":{"playwright":"1.62.0"}}\n', encoding="utf-8"
        )
        (source / ".adwf/preview/node_modules/playwright/package.json").write_text(
            '{"name":"playwright","version":"1.62.0-reference-test"}\n', encoding="utf-8"
        )
        (source / ".adwf/preview/capture.mjs").write_text(
            "import fs from 'node:fs';\n"
            "import path from 'node:path';\n"
            "const spec=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
            "fs.mkdirSync(spec.output_dir,{recursive:true});\n"
            "const a=path.join(spec.output_dir,'desktop.png'); const b=path.join(spec.output_dir,'mobile.png');\n"
            "fs.writeFileSync(a,'reference-desktop'); fs.writeFileSync(b,'reference-mobile');\n"
            "fs.writeFileSync(path.join(spec.output_dir,'capture-result.json'),JSON.stringify({browser_version:'reference-test',node_version:process.version,platform:process.platform,arch:process.arch,screenshots:[{name:'desktop',path:a},{name:'mobile',path:b}],console_errors:[],failed_requests:[],accessibility:{status:'PASS'}}));\n",
            encoding="utf-8",
        )
        files = sorted(
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_file()
        )
        manifest = {
            "framework": "AI Development Framework",
            "version": "webref-test",
            "schema_version": 3,
            "scope": "FRAMEWORK_OWNED_TRUST_BOUNDARY",
            "file_count_excluding_manifests": len(files),
            "total_bytes_excluding_manifests": sum((source / rel).stat().st_size for rel in files),
            "files": files,
        }
        (source / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        sum_paths = files + ["MANIFEST.json"]
        (source / "SHA256SUMS.txt").write_text(
            "".join(
                f"{hashlib.sha256((source / rel).read_bytes()).hexdigest()}  {rel}\n"
                for rel in sorted(sum_paths)
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "ADWF WEBREF Test"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "webref-test@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mini framework"], cwd=source, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, revision

    def test_reference_web_full_lifecycle_report_passes_with_simulated_browser_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_web_conformance(
                source,
                template_root=ROOT / ".adwf/reference-consumers/web",
                install_playwright=False,
                capture_mode="SIMULATED_TEST",
            )
            self.assertEqual(report["outcome"], "PASS")
            self.assertEqual(report["pack"]["id"], "react")
            self.assertEqual(report["preview"]["capture_mode"], "SIMULATED_TEST")
            self.assertEqual(report["consumer"]["operational_head"], report["gate_execution"]["head_sha"])
            self.assertEqual(report["consumer"]["operational_head"], report["preview"]["head_sha"])
            self.assertTrue(report["effective_config"]["runtime_product"])
            self.assertEqual(report["effective_config"]["project_type"], "react")
            self.assertEqual(report["effective_config"]["pack_digest"], report["pack"]["digest"])
            self.assertEqual(report["preservation"]["before_sha256"], report["preservation"]["after_detach_sha256"])
            self.assertEqual(report["readoption"]["status"], "COMMITTED")
            self.assertTrue(report["readoption"]["transaction_identity_changed"])
            self.assertNotEqual(report["readoption"]["transaction_id"], report["adoption"]["transaction_id"])
            self.assertEqual(report["readoption"]["profile_status"], "ALREADY_MATERIALIZED")
            self.assertEqual(validate_reference_conformance_report(report, source), [])
            self.assertIn("REFERENCE_NOT_LIVE_PROVIDER_EVIDENCE", report["limitations"])
            self.assertIn("READOPTION_REQUIRES_DISTINCT_PLAN_IDENTITY", report["limitations"])

    def test_report_digest_and_cross_evidence_binding_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_web_conformance(
                source,
                template_root=ROOT / ".adwf/reference-consumers/web",
                capture_mode="SIMULATED_TEST",
            )
            forged = copy.deepcopy(report)
            forged["preview"]["head_sha"] = "a" * 40
            forged = seal_reference_conformance_report(forged)
            self.assertIn("REFERENCE_CONFORMANCE_PREVIEW_HEAD_MISMATCH", validate_reference_conformance_report(forged, source))
            substituted = copy.deepcopy(report)
            substituted["preview"]["pack_digest"] = "b" * 64
            substituted = seal_reference_conformance_report(substituted)
            self.assertIn("REFERENCE_CONFORMANCE_PREVIEW_PACK_MISMATCH", validate_reference_conformance_report(substituted, source))
            effective_substitution = copy.deepcopy(report)
            effective_substitution["effective_config"]["pack_digest"] = "d" * 64
            effective_substitution = seal_reference_conformance_report(effective_substitution)
            self.assertIn("REFERENCE_CONFORMANCE_EFFECTIVE_PACK_MISMATCH", validate_reference_conformance_report(effective_substitution, source))
            raw_tamper = copy.deepcopy(report)
            raw_tamper["limitations"].append("FORGED")
            self.assertIn("REFERENCE_CONFORMANCE_REPORT_DIGEST_MISMATCH", validate_reference_conformance_report(raw_tamper, source))
            missing_truth = copy.deepcopy(report)
            missing_truth["limitations"].remove("NETWORK_DECLARATION_ONLY_NOT_ENFORCED")
            missing_truth = seal_reference_conformance_report(missing_truth)
            self.assertIn(
                "REFERENCE_CONFORMANCE_LIMITATION_MISSING:NETWORK_DECLARATION_ONLY_NOT_ENFORCED",
                validate_reference_conformance_report(missing_truth, source),
            )

    def test_pack_command_tracked_mutation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _ = self._mini_framework(base)
            template = base / "template"
            shutil.copytree(ROOT / ".adwf/reference-consumers/web", template)
            package = json.loads((template / "package.json").read_text(encoding="utf-8"))
            package["scripts"]["test"] = "node scripts/mutate.mjs"
            (template / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
            (template / "scripts/mutate.mjs").write_text(
                "import fs from 'node:fs'; fs.appendFileSync('index.html','<!-- mutation -->');\n", encoding="utf-8"
            )
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_web_conformance(source, template_root=template, capture_mode="SIMULATED_TEST")
            self.assertIn("REFERENCE_GATE_SAFETY_BLOCK", str(ctx.exception))

    def test_dirty_framework_source_is_not_accepted_as_exact_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            (source / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_web_conformance(
                    source, template_root=ROOT / ".adwf/reference-consumers/web", capture_mode="SIMULATED_TEST"
                )
            self.assertIn("REFERENCE_FRAMEWORK_SOURCE_NOT_CLEAN", str(ctx.exception))

    def test_profile_pack_substitution_and_staleness_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _ = self._mini_framework(base)
            consumer = base / "profile-consumer"
            initialize_reference_web_consumer(consumer, ROOT / ".adwf/reference-consumers/web")
            applied = materialize_project_pack(
                consumer,
                source,
                apply=True,
                product_name="Reference",
                default_branch="main",
                repository_visibility="PRIVATE",
            )
            self.assertEqual(applied["status"], "APPLIED")
            path = consumer / ".adwf-consumer/profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["project_pack_digest"] = "c" * 64
            path.write_text(json.dumps(seal_profile(profile), indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ConsumerProfileError) as ctx:
                load_consumer_profile(consumer, source, required=True)
            self.assertIn("CONSUMER_PROFILE_PACK_DIGEST_MISMATCH", str(ctx.exception))

            path.unlink()
            applied = materialize_project_pack(
                consumer,
                source,
                apply=True,
                product_name="Reference",
                default_branch="main",
                repository_visibility="PRIVATE",
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
            initialize_reference_web_consumer(consumer, ROOT / ".adwf/reference-consumers/web")
            (consumer / ".adwf").mkdir()
            (consumer / "foreign-config").write_text("foreign\n", encoding="utf-8")
            (consumer / ".adwf/config.json").symlink_to(consumer / "foreign-config")
            plan = plan_adoption(source, consumer, source_revision=revision)
            self.assertEqual(plan["status"], "BLOCK")
            self.assertTrue(any(item.startswith("TARGET_SYMLINK_FORBIDDEN:.adwf/config.json") for item in plan["blockers"]))


if __name__ == "__main__":
    unittest.main()
