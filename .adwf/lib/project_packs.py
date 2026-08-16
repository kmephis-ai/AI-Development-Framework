"""Built-in Project Pack SDK: strict definitions, deterministic detection and commands."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
import hashlib
import json
import re

from .contracts import validate
from .strict_json import loads as strict_loads

PACK_ORDER = ("apps-script", "edge-controller", "react", "vue", "angular", "fastapi", "node", "python", "go")
COMMAND_NAMES = {"lint", "unit", "integration", "build", "smoke", "golden_paths", "e2e", "install", "start"}
SHELL_CONTROL = re.compile(r"(?:\r|\n|\x00|`|\$\(|&&|\|\||[;|<>])")
EXECUTABLE = re.compile(r"^[A-Za-z0-9_.+-]+$")


class ProjectPackError(ValueError):
    """Deterministic fail-closed Project Pack contract error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def pack_digest(definition: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(definition)).hexdigest()


def _safe_rel(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and value == pure.as_posix() and all(part not in {"", ".", ".."} for part in pure.parts)


def _semantic_findings(value: dict[str, Any], path: Path) -> list[str]:
    errors: list[str] = []
    detect = value.get("detect") or {}
    primary = [name for name in ("dependency", "files", "contains") if detect.get(name)]
    if len(primary) != 1:
        errors.append("DETECT_STRATEGY_COUNT")
    if detect.get("contains") and not detect.get("scan_files"):
        errors.append("DETECT_SCAN_FILES_REQUIRED")
    if detect.get("scan_files") and not detect.get("contains"):
        errors.append("DETECT_SCAN_FILES_WITHOUT_CONTAINS")
    for rel in list(detect.get("files") or []) + list(detect.get("scan_files") or []):
        if not _safe_rel(str(rel)):
            errors.append("DETECT_PATH_INVALID:" + str(rel))

    commands = value.get("commands") or {}
    if set(commands) - COMMAND_NAMES:
        errors.append("COMMAND_CAPABILITY_UNKNOWN")
    for name, entry in commands.items():
        command = entry.get("command") or []
        if command:
            if not EXECUTABLE.fullmatch(str(command[0])):
                errors.append(f"COMMAND_EXECUTABLE_INVALID:{name}")
            for token in command:
                if SHELL_CONTROL.search(str(token)):
                    errors.append(f"COMMAND_SHELL_CONTROL_FORBIDDEN:{name}")
                    break
        rel = entry.get("requires_file")
        if rel is not None and not _safe_rel(str(rel)):
            errors.append(f"COMMAND_REQUIRES_FILE_INVALID:{name}")

    preview = value.get("preview") or {}
    default_url = preview.get("default_url")
    if default_url:
        parsed = urlsplit(str(default_url))
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            errors.append("PREVIEW_URL_NOT_LOOPBACK_HTTP")
    for key in ("golden_paths",):
        for route in preview.get(key) or []:
            if not str(route).startswith("/") or ".." in str(route) or "\\" in str(route):
                errors.append("PREVIEW_PATH_INVALID:" + str(route))
    health = preview.get("health_path")
    if health and (not str(health).startswith("/") or ".." in str(health) or "\\" in str(health)):
        errors.append("PREVIEW_HEALTH_PATH_INVALID")

    safety = value.get("safety") or {}
    network = safety.get("network")
    has_install = bool((commands.get("install") or {}).get("command"))
    has_preview = bool(default_url)
    if has_install and network not in {"PACKAGE_REGISTRY", "PACKAGE_REGISTRY_AND_LOOPBACK"}:
        errors.append("SAFETY_PACKAGE_NETWORK_REQUIRED")
    if has_preview and network not in {"LOOPBACK", "PACKAGE_REGISTRY_AND_LOOPBACK"}:
        errors.append("SAFETY_LOOPBACK_NETWORK_REQUIRED")
    if value.get("id") == "apps-script":
        if detect.get("files") != ["appsscript.json"]:
            errors.append("APPS_SCRIPT_DETECTION_MARKER_REQUIRED")
        if network != "NONE":
            errors.append("APPS_SCRIPT_NETWORK_MUST_BE_NONE")
        if commands.get("install"):
            errors.append("APPS_SCRIPT_INSTALL_COMMAND_FORBIDDEN")
        if commands.get("start") or preview:
            errors.append("APPS_SCRIPT_PREVIEW_RUNTIME_FORBIDDEN")
    if value.get("id") == "edge-controller":
        if detect.get("files") != ["edge-controller.json"]:
            errors.append("EDGE_CONTROLLER_DETECTION_MARKER_REQUIRED")
        if network != "NONE":
            errors.append("EDGE_CONTROLLER_NETWORK_MUST_BE_NONE")
        if set(commands) - {"lint", "unit", "build"}:
            errors.append("EDGE_CONTROLLER_COMMAND_AUTHORITY_FORBIDDEN")
        if commands.get("install") or commands.get("start") or preview:
            errors.append("EDGE_CONTROLLER_EXTERNAL_RUNTIME_FORBIDDEN")
        expected = {
            "lint": (["npm", "run", "lint"], "lint"),
            "unit": (["npm", "test"], "test"),
            "build": (["npm", "run", "build"], "build"),
        }
        for command_name, (tokens, script) in expected.items():
            entry = commands.get(command_name) or {}
            if entry.get("command") != tokens or entry.get("requires_script") != script:
                errors.append("EDGE_CONTROLLER_LOCAL_COMMAND_REQUIRED:" + command_name)
    if path.stem != value.get("id"):
        errors.append("PROJECT_PACK_ID_MISMATCH")
    return list(dict.fromkeys(errors))


def validate_pack_definition(value: dict[str, Any], framework_root: str | Path, *, path: Path | None = None) -> list[str]:
    root = Path(framework_root).resolve()
    schema_path = root / ".adwf/schemas/project-pack.schema.json"
    try:
        schema = strict_loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ["PROJECT_PACK_SCHEMA_INVALID:" + type(exc).__name__]
    findings = validate(value, schema)
    errors = [f"SCHEMA:{item.path}:{item.code}" for item in findings]
    errors.extend(_semantic_findings(value, path or Path(str(value.get("id") or "invalid") + ".json")))
    return list(dict.fromkeys(errors))


def load_packs(root: str | Path) -> dict[str, dict[str, Any]]:
    framework = Path(root).resolve()
    base = framework / ".adwf/packs"
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("*.json")):
        try:
            value = strict_loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectPackError(f"PROJECT_PACK_JSON_INVALID:{path.name}:{type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise ProjectPackError(f"PROJECT_PACK_OBJECT_REQUIRED:{path.name}")
        errors = validate_pack_definition(value, framework, path=path)
        if errors:
            raise ProjectPackError(f"PROJECT_PACK_INVALID:{path.name}:" + ",".join(errors))
        pack_id = str(value["id"])
        if pack_id in out:
            raise ProjectPackError("PROJECT_PACK_DUPLICATE_ID:" + pack_id)
        out[pack_id] = {"definition": value, "digest": pack_digest(value)}
    unknown_order = sorted(set(out) - set(PACK_ORDER))
    if unknown_order:
        raise ProjectPackError("PROJECT_PACK_ORDER_UNREGISTERED:" + ",".join(unknown_order))
    return out


def _package(root: Path) -> dict[str, Any]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        value = strict_loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _matches_detect(project: Path, package: dict[str, Any], definition: dict[str, Any]) -> bool:
    detect = definition["detect"]
    deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    if detect.get("dependency"):
        return str(detect["dependency"]) in deps
    if detect.get("files"):
        return any((project / str(rel)).is_file() for rel in detect["files"])
    tokens = [str(item).lower() for item in detect.get("contains") or []]
    text = ""
    for rel in detect.get("scan_files") or []:
        path = project / str(rel)
        if path.is_file() and not path.is_symlink():
            text += path.read_text(encoding="utf-8", errors="ignore").lower() + "\n"
    return bool(tokens) and all(token in text for token in tokens)


def detect_pack(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    project = Path(project_root).resolve()
    packs = load_packs(framework_root)
    package = _package(project)
    candidates = [name for name in PACK_ORDER if name in packs and _matches_detect(project, package, packs[name]["definition"])]
    chosen = candidates[0] if candidates else None
    loaded = packs.get(chosen) if chosen else None
    return {
        "pack": chosen,
        "candidates": candidates,
        "confidence": "HIGH" if chosen else "LOW",
        "definition": loaded["definition"] if loaded else None,
        "pack_digest": loaded["digest"] if loaded else None,
    }


def commands_for_pack(project_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    detected = detect_pack(project_root, framework_root)
    definition = detected.get("definition") or {}
    commands = json.loads(json.dumps(definition.get("commands") or {}))
    project = Path(project_root).resolve()
    package = _package(project)
    scripts = package.get("scripts") or {}
    for entry in commands.values():
        script = entry.get("requires_script")
        required_file = entry.get("requires_file")
        if script and script not in scripts:
            entry["available"] = False
        elif required_file and not (project / str(required_file)).is_file():
            entry["available"] = False
        else:
            entry["available"] = True
    return {
        **detected,
        "commands": commands,
        "preview": definition.get("preview") or {},
        "safety": definition.get("safety") or {},
    }
