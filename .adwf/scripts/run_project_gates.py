#!/usr/bin/env python3
"""Project gates без shell=True и без плавающего runtime."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.project_gates import GATE_NAMES, gate_configuration_findings  # noqa: E402

ORDER = list(GATE_NAMES)


def runtime_checks(config: dict) -> list[str]:
    errors: list[str] = []
    expected_python = config["runtime"]["python_exact"]
    actual_python = ".".join(str(value) for value in sys.version_info[:3])
    if actual_python != expected_python:
        errors.append(f"PYTHON_VERSION:{actual_python}!={expected_python}")
    if (ROOT / "package.json").exists() and config["runtime"].get("enforce_node_for_node_projects"):
        process = subprocess.run(["node", "--version"], cwd=ROOT, capture_output=True, text=True, check=False)
        expected_node = f"v{config['runtime']['node_major']}."
        if process.returncode or not process.stdout.strip().startswith(expected_node):
            errors.append(f"NODE_VERSION:{process.stdout.strip() or 'MISSING'}!=24.x")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["pr", "main", "runtime"], default="pr")
    args = parser.parse_args()
    config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
    failed = runtime_checks(config) + gate_configuration_findings(config, ROOT)
    results: dict[str, str] = {}
    for name in ORDER:
        gate = config.get("commands", {}).get(name, {})
        if args.phase not in gate.get("phases", []):
            results[name] = "N/A"
            continue
        required = gate.get("required") is True
        command = gate.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
            results[name] = "NOT_VERIFIED" if required else "N/A"
            if required:
                failed.append(f"REQUIRED_GATE_UNCONFIGURED:{name}")
            continue
        process = subprocess.run(command, cwd=ROOT, check=False)
        results[name] = "PASS" if process.returncode == 0 else "FAIL"
        if required and process.returncode:
            failed.append(f"REQUIRED_GATE_FAILED:{name}")
    for name in ORDER:
        print(f"{name:14} {results[name]}")
    for error in failed:
        print(f"BLOCK: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
