"""Детерминированный помощник настройки CI/CD без AI/API и write-действий."""
from __future__ import annotations
from .strict_json import loads as strict_loads

from pathlib import Path
from typing import Any
import json

from .cost_guard import evaluate_provider
from .project_packs import commands_for_pack


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return strict_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def detect_project(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    stacks: list[str] = []
    facts: list[str] = []
    commands: dict[str, dict[str, Any]] = {}

    package = _load_json(base / "package.json")
    if package:
        stacks.append("node")
        facts.append("package.json")
        scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
        mapping = {
            "lint": ("lint", ["pr"]),
            "unit": ("test", ["pr", "main"]),
            "integration": ("test:integration", ["main"]),
            "build": ("build", ["pr", "main"]),
            "smoke": ("test:smoke", ["runtime"]),
            "e2e": ("test:e2e", ["runtime"]),
        }
        for gate, (script, phases) in mapping.items():
            if isinstance(scripts.get(script), str) and scripts[script].strip():
                commands[gate] = {"required": True, "command": ["npm", "run", script], "phases": phases}

    pyproject = base / "pyproject.toml"
    requirements = base / "requirements.txt"
    python_tests = (base / "tests").is_dir() or (base / ".adwf/tests").is_dir()
    if pyproject.is_file() or requirements.is_file() or python_tests:
        stacks.append("python")
        facts.extend(name for name, exists in (
            ("pyproject.toml", pyproject.is_file()),
            ("requirements.txt", requirements.is_file()),
            ("tests", python_tests),
        ) if exists)
        if python_tests and "unit" not in commands:
            test_dir = ".adwf/tests" if (base / ".adwf/tests").is_dir() else "tests"
            commands["unit"] = {
                "required": True,
                "command": ["python3", "-m", "unittest", "discover", "-s", test_dir, "-p", "test_*.py"],
                "phases": ["pr", "main"],
            }

    for filename, stack in (("go.mod", "go"), ("pom.xml", "java-maven"), ("build.gradle", "java-gradle"), ("Cargo.toml", "rust")):
        if (base / filename).is_file():
            stacks.append(stack)
            facts.append(filename)

    return {
        "stacks": sorted(set(stacks)),
        "facts": sorted(set(facts)),
        "commands": commands,
        "confidence": "HIGH" if commands else ("MEDIUM" if stacks else "LOW"),
    }


def build_setup_plan(
    root: str | Path,
    registry: dict[str, Any],
    *,
    capability: str,
    canonical_provider: str,
) -> dict[str, Any]:
    detected = detect_project(root)
    pack = commands_for_pack(root, Path(__file__).resolve().parents[2])
    # Fill only missing commands from a built-in pack, and only when the pack says
    # the command is actually available in this repository.
    for name, entry in (pack.get("commands") or {}).items():
        if name not in detected["commands"] and entry.get("available") is True and entry.get("command"):
            detected["commands"][name] = {"required": True, "command": entry["command"], "phases": entry.get("phases") or ["pr"]}
    provider = evaluate_provider(
        registry,
        {
            "provider": capability,
            "mandatory_ci": True,
            "automated": True,
            "projected_cost": 0,
        },
        canonical_provider=canonical_provider,
    )
    blockers: list[str] = []
    questions: list[dict[str, Any]] = []
    if provider.get("result") != "ALLOW":
        blockers.append("PROVIDER_NOT_CONFIRMED_ZERO_COST")
    if not detected["commands"]:
        blockers.append("NO_VERIFIABLE_PROJECT_COMMANDS")
        questions.append({
            "question_ru": "Какую готовую команду проект уже использует для проверки работоспособности?",
            "why_ru": "Помощник не будет придумывать или исполнять неизвестную команду.",
        })
    commands = {}
    for name in ("lint", "unit", "integration", "build", "smoke", "golden_paths", "e2e"):
        commands[name] = detected["commands"].get(name, {
            "required": False,
            "command": [],
            "phases": ["runtime"] if name in {"smoke", "golden_paths", "e2e"} else ["pr"],
        })
    return {
        "schema_version": 2,
        "mode": "BOOTSTRAP_PLAN",
        "detected": detected,
        "project_pack": {"id": pack.get("pack"), "confidence": pack.get("confidence"), "preview": pack.get("preview") or {}},
        "provider": provider,
        "proposed_commands": commands,
        "blockers": blockers,
        "questions": questions[:1],
        "owner_summary_ru": (
            f"Определены технологии: {', '.join(detected['stacks']) or 'не определены'}. "
            f"Найдено проверяемых команд: {len(detected['commands'])}. "
            + ("Настройку можно оформить отдельным PR." if not blockers else "Автоматическое включение остановлено до устранения блокеров.")
        ),
        "write_performed": False,
        "next_action": "github-bootstrap" if not blockers else "resolve-blockers",
        "ai_api_required": False,
        "monetary_budget_usd": 0,
    }
