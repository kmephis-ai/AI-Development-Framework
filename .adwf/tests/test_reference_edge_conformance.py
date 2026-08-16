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

from lib.capability_live_evidence import resolve_capability_live_evidence  # noqa: E402
from lib.project_packs import load_packs, validate_pack_definition  # noqa: E402
from lib.reference_conformance import (  # noqa: E402
    ReferenceConformanceError,
    initialize_reference_edge_controller_consumer,
    run_reference_edge_controller_conformance,
    seal_reference_conformance_report,
    validate_reference_conformance_report,
)


class ReferenceEdgeControllerConformanceTests(unittest.TestCase):
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
        shutil.copy2(ROOT / ".adwf/packs/edge-controller.json", source / ".adwf/packs/edge-controller.json")
        shutil.copy2(ROOT / ".adwf/packs/node.json", source / ".adwf/packs/node.json")
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = [".gitignore"]
        (source / ".adwf/managed-surface-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        files = sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file())
        manifest = {
            "framework": "AI Development Framework",
            "version": "edgeref-test",
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
        subprocess.run(["git", "config", "user.name", "ADWF EDGEREF Test"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "edgeref-test@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mini framework"], cwd=source, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, revision

    def test_reference_edge_controller_full_lifecycle_report_passes_without_device_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_edge_controller_conformance(
                source, template_root=ROOT / ".adwf/reference-consumers/edge-controller"
            )
            self.assertEqual(report["outcome"], "PASS")
            self.assertEqual(report["consumer_class"], "EDGE_CONTROLLER")
            self.assertEqual(report["pack"]["id"], "edge-controller")
            self.assertEqual(report["preview"]["status"], "NOT_APPLICABLE")
            self.assertEqual(report["preview"]["reason"], "EDGE_CONTROLLER_NO_BROWSER_PREVIEW")
            self.assertEqual(report["functional"]["gate_execution_id"], report["gate_execution"]["execution_id"])
            self.assertFalse(report["functional"]["external_network_required"])
            self.assertFalse(report["functional"]["device_runtime_executed"])
            self.assertFalse(report["functional"]["device_deployment_performed"])
            self.assertFalse(report["functional"]["ssh_required"])
            self.assertEqual(report["effective_config"]["project_type"], "edge-controller")
            self.assertEqual(report["preservation"]["before_sha256"], report["preservation"]["after_detach_sha256"])
            self.assertTrue(report["readoption"]["transaction_identity_changed"])
            self.assertEqual(validate_reference_conformance_report(report, source), [])
            self.assertIn("REAL_EDGE_DEVICE_RUNTIME_NOT_EXECUTED", report["limitations"])
            self.assertIn("SSH_OR_DEVICE_DEPLOYMENT_NOT_EXECUTED", report["limitations"])

    def test_report_pack_preview_functional_and_seal_substitution_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            report = run_reference_edge_controller_conformance(
                source, template_root=ROOT / ".adwf/reference-consumers/edge-controller"
            )
            wrong_pack = copy.deepcopy(report)
            wrong_pack["pack"]["id"] = "apps-script"
            wrong_pack = seal_reference_conformance_report(wrong_pack)
            self.assertIn("REFERENCE_CONFORMANCE_EDGE_PACK_MISMATCH", validate_reference_conformance_report(wrong_pack, source))

            false_preview = copy.deepcopy(report)
            false_preview["preview"]["status"] = "PASS"
            false_preview = seal_reference_conformance_report(false_preview)
            self.assertIn("REFERENCE_CONFORMANCE_EDGE_PREVIEW_TRUTH_MISMATCH", validate_reference_conformance_report(false_preview, source))

            false_device = copy.deepcopy(report)
            false_device["functional"]["device_runtime_executed"] = True
            false_device = seal_reference_conformance_report(false_device)
            self.assertIn("REFERENCE_CONFORMANCE_EDGE_DEVICE_RUNTIME_TRUTH_MISMATCH", validate_reference_conformance_report(false_device, source))

            missing_limit = copy.deepcopy(report)
            missing_limit["limitations"].remove("SSH_OR_DEVICE_DEPLOYMENT_NOT_EXECUTED")
            missing_limit = seal_reference_conformance_report(missing_limit)
            self.assertIn(
                "REFERENCE_CONFORMANCE_LIMITATION_MISSING:SSH_OR_DEVICE_DEPLOYMENT_NOT_EXECUTED",
                validate_reference_conformance_report(missing_limit, source),
            )

            raw_tamper = copy.deepcopy(report)
            raw_tamper["functional"]["fixture_sha256"] = "a" * 64
            self.assertIn("REFERENCE_CONFORMANCE_REPORT_DIGEST_MISMATCH", validate_reference_conformance_report(raw_tamper, source))

    def test_pack_authority_expansion_is_rejected(self) -> None:
        definition = copy.deepcopy(load_packs(ROOT)["edge-controller"]["definition"])
        definition["safety"]["network"] = "PACKAGE_REGISTRY"
        definition["commands"]["install"] = {"command": ["npm", "ci"], "phases": ["pr"]}
        definition["preview"] = {"default_url": "http://127.0.0.1:4173"}
        errors = validate_pack_definition(definition, ROOT, path=Path("edge-controller.json"))
        self.assertIn("EDGE_CONTROLLER_NETWORK_MUST_BE_NONE", errors)
        self.assertIn("EDGE_CONTROLLER_COMMAND_AUTHORITY_FORBIDDEN", errors)
        self.assertIn("EDGE_CONTROLLER_EXTERNAL_RUNTIME_FORBIDDEN", errors)

    def test_template_external_dependency_or_deployment_script_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template"
            shutil.copytree(ROOT / ".adwf/reference-consumers/edge-controller", template)
            package = json.loads((template / "package.json").read_text(encoding="utf-8"))
            package["scripts"]["deploy"] = "ssh controller install"
            (template / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                initialize_reference_edge_controller_consumer(Path(tmp) / "consumer", template)
            self.assertIn("REFERENCE_EDGE_CONTROLLER_LOCAL_SCRIPTS_REQUIRED", str(ctx.exception))

    def test_pack_command_tracked_mutation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, _ = self._mini_framework(base)
            template = base / "template"
            shutil.copytree(ROOT / ".adwf/reference-consumers/edge-controller", template)
            check = template / "scripts/check.mjs"
            text = check.read_text(encoding="utf-8")
            text = text.replace(
                "if (phase === 'test') {",
                "if (phase === 'test') { fs.appendFileSync('rules/controller.js', '\\n// runtime mutation\\n');",
            )
            check.write_text(text, encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_edge_controller_conformance(source, template_root=template)
            self.assertIn("REFERENCE_GATE_SAFETY_BLOCK", str(ctx.exception))

    def test_dirty_framework_source_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, _ = self._mini_framework(Path(tmp))
            (source / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaises(ReferenceConformanceError) as ctx:
                run_reference_edge_controller_conformance(
                    source, template_root=ROOT / ".adwf/reference-consumers/edge-controller"
                )
            self.assertIn("REFERENCE_FRAMEWORK_SOURCE_NOT_CLEAN", str(ctx.exception))

    def test_synthetic_reference_string_cannot_promote_live_verified(self) -> None:
        trace = json.loads((ROOT / ".adwf/capability-traceability.json").read_text(encoding="utf-8"))
        edge = next(item for item in trace["capabilities"] if item["id"] == "REFERENCE_EDGE_CONFORMANCE")
        edge["status"] = "LIVE_VERIFIED"
        edge["live_evidence"] = ["reference:EDGEREF-001-PASS"]
        registry = json.loads((ROOT / ".adwf/capability-live-evidence.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / ".adwf/schemas/capability-live-evidence-certification.schema.json").read_text(encoding="utf-8"))
        errors = resolve_capability_live_evidence(trace, registry, schema=schema)
        self.assertTrue(any(item.startswith("CAPABILITY_LIVE_CERTIFICATION_REF_INVALID:REFERENCE_EDGE_CONFORMANCE") for item in errors))


if __name__ == "__main__":
    unittest.main()
