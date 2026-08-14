"""Fail-closed проверка, что обязательные project gates реально настроены."""
from __future__ import annotations

from pathlib import Path
from typing import Any

GATE_NAMES = ("lint", "unit", "integration", "build", "smoke", "golden_paths", "e2e")
PHASES = {"pr", "main", "runtime"}


def command_configured(gate: dict[str, Any]) -> bool:
    command = gate.get("command")
    return isinstance(command, list) and bool(command) and all(isinstance(item, str) and item for item in command)


def gate_configuration_findings(config: dict[str, Any], root: str | Path | None = None) -> list[str]:
    """Вернуть причины незавершённого bootstrap; framework-template имеет явное исключение."""
    findings: list[str] = []
    commands = config.get("commands", {})
    for name in GATE_NAMES:
        gate = commands.get(name, {})
        phases = gate.get("phases")
        if not isinstance(phases, list) or not phases or any(phase not in PHASES for phase in phases):
            findings.append(f"GATE_PHASES_INVALID:{name}")
        if gate.get("required") is True and not command_configured(gate):
            findings.append(f"REQUIRED_GATE_UNCONFIGURED:{name}")

    project = config.get("project", {})
    if project.get("type") == "framework":
        return list(dict.fromkeys(findings))

    required = {
        name for name, gate in commands.items()
        if gate.get("required") is True and command_configured(gate)
    }
    if not any("pr" in commands[name].get("phases", []) for name in required):
        findings.append("BOOTSTRAP_PR_GATE_MISSING")
    if not any("main" in commands[name].get("phases", []) for name in required):
        findings.append("BOOTSTRAP_MAIN_GATE_MISSING")

    project_type = str(project.get("type", "")).lower()
    if project_type not in {"docs", "documentation"} and "unit" not in required:
        findings.append("BOOTSTRAP_UNIT_GATE_MISSING")
    if root is not None and (Path(root) / "package.json").is_file() and "build" not in required:
        findings.append("NODE_BUILD_GATE_MISSING")
    if project.get("runtime_product") is True:
        for name in ("smoke", "golden_paths"):
            if name not in required or "runtime" not in commands[name].get("phases", []):
                findings.append(f"PRODUCT_RUNTIME_GATE_MISSING:{name}")
    return list(dict.fromkeys(findings))
