from __future__ import annotations

import json
from pathlib import Path

from adwf.lib.project_packs import commands_for_pack


ROOT = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path, *, package: dict | None = None, files: dict[str, str] | None = None) -> Path:
    if package is not None:
        (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for name, content in (files or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def test_unknown_project_gets_conservative_generic_continuity(tmp_path: Path) -> None:
    result = commands_for_pack(tmp_path, ROOT)
    binding = result["session_continuity"]
    assert result["pack"] is None
    assert binding["inherits_framework_core"] is True
    assert binding["provider_authority"] is False
    assert binding["runtime_evidence_mode"] == "PROVIDER_FACTS_ONLY"
    assert binding["resumable_commands"] == []
    assert "FRESH_RECONCILE_BEFORE_WRITE" in binding["safety_boundaries"]


def test_apps_script_binding_requires_external_runtime_readback(tmp_path: Path) -> None:
    project = _project(tmp_path, files={"appsscript.json": "{}"})
    result = commands_for_pack(project, ROOT)
    binding = result["session_continuity"]
    assert result["pack"] == "apps-script"
    assert binding["runtime_evidence_mode"] == "CONSUMER_NATIVE_EXTERNAL_RUNTIME_READBACK"
    assert binding["resumable_commands"] == []
    assert "NO_LOCAL_RUNTIME_INFERENCE" in binding["safety_boundaries"]
    assert "NO_NETWORK" in binding["safety_boundaries"]


def test_edge_binding_never_expands_to_external_or_physical_runtime(tmp_path: Path) -> None:
    package = {"scripts": {"lint": "eslint .", "test": "pytest", "build": "npm run compile"}}
    project = _project(tmp_path, package=package, files={"edge-controller.json": "{}"})
    result = commands_for_pack(project, ROOT)
    binding = result["session_continuity"]
    assert result["pack"] == "edge-controller"
    assert binding["runtime_evidence_mode"] == "REPOSITORY_TEST_EVIDENCE_ONLY"
    assert set(binding["resumable_commands"]) <= {"lint", "unit", "build"}
    assert "NO_EXTERNAL_RUNTIME" in binding["safety_boundaries"]
    assert "NO_PHYSICAL_ACTIONS" in binding["safety_boundaries"]
    assert "NO_NETWORK" in binding["safety_boundaries"]


def test_web_binding_inherits_core_and_only_resumes_safe_commands(tmp_path: Path) -> None:
    package = {
        "dependencies": {"react": "1.0.0"},
        "scripts": {"lint": "eslint .", "test": "vitest", "build": "vite build", "start": "vite"},
    }
    project = _project(tmp_path, package=package)
    result = commands_for_pack(project, ROOT)
    binding = result["session_continuity"]
    assert result["pack"] == "react"
    assert binding["inherits_framework_core"] is True
    assert binding["provider_authority"] is False
    assert "install" not in binding["resumable_commands"]
    assert "start" not in binding["resumable_commands"]
    if result["preview"].get("default_url"):
        assert binding["runtime_evidence_mode"] == "LOOPBACK_PREVIEW_PLUS_PROVIDER_FACTS"
        assert "LOOPBACK_ONLY" in binding["safety_boundaries"]
