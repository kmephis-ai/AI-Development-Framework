#!/usr/bin/env python3
"""Project gates without shell=True and with consumer runtime safety envelope."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_profile import ConsumerProfileError, load_effective_config  # noqa: E402
from lib.project_execution import ProjectExecutionError, ProjectExecutionSession, load_bound_project_pack  # noqa: E402
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


def _framework_self_host(config: dict) -> bool:
    project = config.get("project") or {}
    return project.get("type") == "framework" and project.get("runtime_product") is False


def _legacy_framework_gates(config: dict, phase: str, failed: list[str], results: dict[str, str]) -> None:
    """The ADWF repository itself has no consumer Project Pack and is not product runtime."""
    for name in ORDER:
        gate = config.get("commands", {}).get(name, {})
        if phase not in gate.get("phases", []):
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


def _consumer_gates(config: dict, phase: str, failed: list[str], results: dict[str, str]) -> None:
    binding = load_bound_project_pack(ROOT, ROOT)
    with ProjectExecutionSession(ROOT, ROOT, binding, purpose=f"project-gates:{phase}") as session:
        for name in ORDER:
            gate = config.get("commands", {}).get(name, {})
            if phase not in gate.get("phases", []):
                results[name] = "N/A"
                continue
            required = gate.get("required") is True
            command = gate.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
                results[name] = "NOT_VERIFIED" if required else "N/A"
                if required:
                    failed.append(f"REQUIRED_GATE_UNCONFIGURED:{name}")
                continue
            current = binding.get("commands", {}).get(name) or {}
            pack_bound = current.get("available") is True and current.get("command") == command
            observation = session.run(name, command, pack_bound=pack_bound, capture_output=True)
            results[name] = "PASS" if observation.process.returncode == 0 and observation.safety_status == "PASS" else "FAIL"
            if observation.safety_status != "PASS":
                failed.extend(observation.reason_codes)
            if required and observation.process.returncode:
                failed.append(f"REQUIRED_GATE_FAILED:{name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["pr", "main", "runtime"], default="pr")
    args = parser.parse_args()
    results: dict[str, str] = {}
    failed: list[str] = []
    try:
        config = load_effective_config(ROOT, ROOT)
        failed = runtime_checks(config) + gate_configuration_findings(config, ROOT)
        if _framework_self_host(config):
            _legacy_framework_gates(config, args.phase, failed, results)
        else:
            _consumer_gates(config, args.phase, failed, results)
    except (OSError, ConsumerProfileError, ProjectExecutionError, subprocess.SubprocessError) as exc:
        failed.append(str(exc).split(":", 1)[0])
        for name in ORDER:
            results.setdefault(name, "NOT_VERIFIED")
    for name in ORDER:
        print(f"{name:14} {results[name]}")
    for error in list(dict.fromkeys(failed)):
        print(f"BLOCK: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
