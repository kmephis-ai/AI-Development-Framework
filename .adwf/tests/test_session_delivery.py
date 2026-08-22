from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.project_packs import commands_for_pack


def _project(tmp_path: Path, *, package: dict | None = None, files: dict[str, str] | None = None) -> Path:
    if package is not None:
        (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for name, content in (files or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


class SessionDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp_path = Path(self._temp.name)

    def test_unknown_project_gets_conservative_generic_continuity(self) -> None:
        result = commands_for_pack(self.tmp_path, ROOT)
        binding = result["session_continuity"]
        self.assertIsNone(result["pack"])
        self.assertIs(binding["inherits_framework_core"], True)
        self.assertIs(binding["provider_authority"], False)
        self.assertEqual(binding["runtime_evidence_mode"], "PROVIDER_FACTS_ONLY")
        self.assertEqual(binding["resumable_commands"], [])
        self.assertIn("FRESH_RECONCILE_BEFORE_WRITE", binding["safety_boundaries"])

    def test_apps_script_binding_requires_external_runtime_readback(self) -> None:
        project = _project(self.tmp_path, files={"appsscript.json": "{}"})
        result = commands_for_pack(project, ROOT)
        binding = result["session_continuity"]
        self.assertEqual(result["pack"], "apps-script")
        self.assertEqual(binding["runtime_evidence_mode"], "CONSUMER_NATIVE_EXTERNAL_RUNTIME_READBACK")
        self.assertEqual(binding["resumable_commands"], [])
        self.assertIn("NO_LOCAL_RUNTIME_INFERENCE", binding["safety_boundaries"])
        self.assertIn("NO_NETWORK", binding["safety_boundaries"])

    def test_edge_binding_never_expands_to_external_or_physical_runtime(self) -> None:
        package = {"scripts": {"lint": "eslint .", "test": "pytest", "build": "npm run compile"}}
        project = _project(self.tmp_path, package=package, files={"edge-controller.json": "{}"})
        result = commands_for_pack(project, ROOT)
        binding = result["session_continuity"]
        self.assertEqual(result["pack"], "edge-controller")
        self.assertEqual(binding["runtime_evidence_mode"], "REPOSITORY_TEST_EVIDENCE_ONLY")
        self.assertLessEqual(set(binding["resumable_commands"]), {"lint", "unit", "build"})
        self.assertIn("NO_EXTERNAL_RUNTIME", binding["safety_boundaries"])
        self.assertIn("NO_PHYSICAL_ACTIONS", binding["safety_boundaries"])
        self.assertIn("NO_NETWORK", binding["safety_boundaries"])

    def test_web_binding_inherits_core_and_only_resumes_safe_commands(self) -> None:
        package = {
            "dependencies": {"react": "1.0.0"},
            "scripts": {"lint": "eslint .", "test": "vitest", "build": "vite build", "start": "vite"},
        }
        project = _project(self.tmp_path, package=package)
        result = commands_for_pack(project, ROOT)
        binding = result["session_continuity"]
        self.assertEqual(result["pack"], "react")
        self.assertIs(binding["inherits_framework_core"], True)
        self.assertIs(binding["provider_authority"], False)
        self.assertNotIn("install", binding["resumable_commands"])
        self.assertNotIn("start", binding["resumable_commands"])
        if result["preview"].get("default_url"):
            self.assertEqual(binding["runtime_evidence_mode"], "LOOPBACK_PREVIEW_PLUS_PROVIDER_FACTS")
            self.assertIn("LOOPBACK_ONLY", binding["safety_boundaries"])


if __name__ == "__main__":
    unittest.main()
