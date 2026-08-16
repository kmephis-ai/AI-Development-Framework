#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: harden.py <candidate-root>")
root = Path(sys.argv[1]).resolve()

# Bind qualification to exact command bytes, not merely the command path.
module_path = root / ".adwf/lib/creative_agent_qualification.py"
text = module_path.read_text(encoding="utf-8")
needle = '''def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _without'''
replacement = '''def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without'''
if text.count(needle) != 1:
    raise SystemExit("QUAL_MODULE_DIGEST_PATCH_MISMATCH")
text = text.replace(needle, replacement, 1)
needle = '''        command = adapter.get("command") if isinstance(adapter.get("command"), dict) else {}
        if not _safe_relative(command.get("path")):
            errors.append("ADAPTER_COMMAND_PATH_INVALID:" + aid)
        qpath = adapter.get("qualification_report")'''
replacement = '''        command = adapter.get("command") if isinstance(adapter.get("command"), dict) else {}
        if not _safe_relative(command.get("path")):
            errors.append("ADAPTER_COMMAND_PATH_INVALID:" + aid)
        else:
            command_path = (base / str(command.get("path"))).resolve()
            try:
                command_path.relative_to(base)
            except ValueError:
                errors.append("ADAPTER_COMMAND_PATH_ESCAPES_ROOT:" + aid)
            else:
                if not command_path.is_file():
                    errors.append("ADAPTER_COMMAND_MISSING:" + aid)
                elif command.get("sha256") != _sha256_file(command_path):
                    errors.append("ADAPTER_COMMAND_DIGEST_MISMATCH:" + aid)
        qpath = adapter.get("qualification_report")'''
if text.count(needle) != 1:
    raise SystemExit("QUAL_MODULE_REGISTRY_PATCH_MISMATCH")
text = text.replace(needle, replacement, 1)
needle = '''    runner = command.get("runner")
    if runner == "PYTHON":
        return [sys.executable, str(path)]'''
replacement = '''    if not path.is_file():
        raise ValueError("AGENT_COMMAND_MISSING")
    if command.get("sha256") != _sha256_file(path):
        raise ValueError("AGENT_COMMAND_DIGEST_MISMATCH")
    runner = command.get("runner")
    if runner == "PYTHON":
        return [sys.executable, str(path)]'''
if text.count(needle) != 1:
    raise SystemExit("QUAL_MODULE_ARGV_PATCH_MISMATCH")
text = text.replace(needle, replacement, 1)
module_path.write_text(text, encoding="utf-8", newline="\n")

# Extend strict registry schema with exact command digest.
schema_path = root / ".adwf/schemas/creative-agent-adapters.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))
command_schema = schema["properties"]["adapters"]["items"]["properties"]["command"]
if command_schema["required"] != ["runner", "path"]:
    raise SystemExit("ADAPTER_SCHEMA_COMMAND_BASE_DRIFT")
command_schema["required"] = ["runner", "path", "sha256"]
command_schema["properties"]["sha256"] = {"type": "string", "format": "sha256"}
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Tests must materialize the exact command bytes when validating a copied registry.
test_path = root / ".adwf/tests/test_creative_agent_qualification.py"
test_text = test_path.read_text(encoding="utf-8")
needle = '''            ".adwf/schemas/creative-agent-qualification-report.schema.json",
        ):
            target = root / rel'''
replacement = '''            ".adwf/schemas/creative-agent-qualification-report.schema.json",
            ".adwf/scripts/reference_agent_adapter.py",
        ):
            target = root / rel'''
if test_text.count(needle) != 1:
    raise SystemExit("QUAL_TEST_COPY_PATCH_MISMATCH")
test_text = test_text.replace(needle, replacement, 1)
needle = '''    def test_command_argv_is_framework_bound(self):
        adapter = load_qualified_command_adapter(ROOT, "reference-local", "RECOVERY")'''
replacement = '''    def test_tampered_command_bytes_invalidate_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._minimal_qualified_root(tmp)
            script = root / ".adwf/scripts/reference_agent_adapter.py"
            script.write_text(script.read_text(encoding="utf-8") + "\\n# tampered\\n", encoding="utf-8")
            self.assertIn("ADAPTER_COMMAND_DIGEST_MISMATCH:reference-local", validate_registry(load_registry(root), root))

    def test_command_argv_is_framework_bound(self):
        adapter = load_qualified_command_adapter(ROOT, "reference-local", "RECOVERY")'''
if test_text.count(needle) != 1:
    raise SystemExit("QUAL_TEST_TAMPER_PATCH_MISMATCH")
test_text = test_text.replace(needle, replacement, 1)
test_path.write_text(test_text, encoding="utf-8", newline="\n")

# Re-seal canonical registry/report with exact command SHA-256.
sys.path.insert(0, str(root / ".adwf"))
from lib.creative_agent_qualification import reference_qualification_report, seal_registry
registry_path = root / ".adwf/creative-agent-adapters.json"
registry = json.loads(registry_path.read_text(encoding="utf-8"))
raw = {key: value for key, value in registry.items() if key != "registry_sha256"}
command_path = root / raw["adapters"][0]["command"]["path"]
raw["adapters"][0]["command"]["sha256"] = hashlib.sha256(command_path.read_bytes()).hexdigest()
registry = seal_registry(raw)
registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
report = reference_qualification_report(registry["adapters"][0])
(root / ".adwf/creative-agent-qualification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("AGENTQUAL_HARDEN_V2: PASS")
