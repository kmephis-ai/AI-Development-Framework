"""Fail-closed qualification boundary for replaceable Creative Agent adapters.

Qualification constrains invocation authority and package/result binding. It does
not make creative output trusted: every adapter result remains LOW_TRUST until
the existing trusted/provider verification pipeline accepts the exact revision.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

from .ai_work_contracts import (
    CREATIVE_PHASES,
    build_work_result,
    compile_work_package,
    validate_work_package,
    validate_work_result,
)
from .contracts import validate
from .strict_json import loads as strict_loads

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
PROFILE_ID = "CREATIVE_AGENT_COMMAND_V2"
PROFILE_VERSION = 2
SAFE_ENV_NAMES = {
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
    "SYSTEMROOT", "WINDIR", "PATHEXT", "PYTHONIOENCODING",
}
SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without(value: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in names}


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def qualification_profile_digest(profile_id: str = PROFILE_ID, version: int = PROFILE_VERSION) -> str:
    return _digest({"contract": "ADWF_CREATIVE_AGENT_QUALIFICATION", "id": profile_id, "version": version})


def adapter_digest(adapter: dict[str, Any]) -> str:
    return _digest(adapter)


def seal_registry(registry: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(registry)
    value["registry_sha256"] = _digest(_without(value, "registry_sha256"))
    return value


def load_registry(root: str | Path) -> dict[str, Any]:
    path = Path(root).resolve() / ".adwf/creative-agent-adapters.json"
    value = strict_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("AGENT_ADAPTER_REGISTRY_NOT_OBJECT")
    return value


def reference_qualification_report(adapter: dict[str, Any]) -> dict[str, Any]:
    report = {
        "$schema": ".adwf/schemas/creative-agent-qualification-report.schema.json",
        "schema_version": 1,
        "adapter_id": adapter["id"],
        "adapter_version": adapter["version"],
        "adapter_kind": adapter["kind"],
        "invocation_mode": adapter["invocation_mode"],
        "qualification_profile_id": adapter["qualification_profile_id"],
        "qualification_profile_version": adapter["qualification_profile_version"],
        "qualification_profile_digest": adapter["qualification_profile_digest"],
        "adapter_digest": adapter_digest(adapter),
        "status": "PASS",
        "supported_phases": list(adapter["supported_phases"]),
        "monetary_budget_usd": adapter["monetary_budget_usd"],
        "executor_auth": adapter["executor_auth"],
        "network": adapter["authority"]["network"],
        "secrets": adapter["authority"]["secrets"],
        "filesystem": adapter["authority"]["filesystem"],
        "exact_package_binding": True,
        "allowed_write_surface_enforced": True,
        "forbidden_write_surface_rejected": True,
        "changed_paths_verified": True,
        "result_contract_verified": True,
        "secret_environment_filtered": True,
        "stale_package_rejected": True,
        "timeout_fail_closed": True,
        "nonzero_exit_fail_closed": True,
        "missing_result_fail_closed": True,
        "low_trust_result": True,
        "real_external_agent_verified": False,
    }
    report["report_sha256"] = _digest(report)
    return report


def validate_qualification_report(report: dict[str, Any], adapter: dict[str, Any], root: str | Path) -> list[str]:
    base = Path(root).resolve()
    errors: list[str] = []
    try:
        schema = strict_loads((base / ".adwf/schemas/creative-agent-qualification-report.schema.json").read_text(encoding="utf-8"))
        errors.extend(f"REPORT_SCHEMA:{item.path}:{item.code}" for item in validate(report, schema))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ["REPORT_SCHEMA_UNREADABLE:" + type(exc).__name__]
    expected = reference_qualification_report(adapter)
    if report != expected:
        errors.append("QUALIFICATION_REPORT_BINDING_MISMATCH")
    if report.get("report_sha256") != _digest(_without(report, "report_sha256")):
        errors.append("QUALIFICATION_REPORT_DIGEST_MISMATCH")
    if report.get("real_external_agent_verified") is not False:
        errors.append("REFERENCE_AGENT_CANNOT_VERIFY_EXTERNAL_AGENT")
    if report.get("low_trust_result") is not True:
        errors.append("QUALIFICATION_RESULT_MUST_REMAIN_LOW_TRUST")
    return list(dict.fromkeys(errors))


def validate_registry(registry: dict[str, Any], root: str | Path) -> list[str]:
    base = Path(root).resolve()
    errors: list[str] = []
    try:
        schema = strict_loads((base / ".adwf/schemas/creative-agent-adapters.schema.json").read_text(encoding="utf-8"))
        errors.extend(f"REGISTRY_SCHEMA:{item.path}:{item.code}" for item in validate(registry, schema))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ["REGISTRY_SCHEMA_UNREADABLE:" + type(exc).__name__]
    if registry.get("registry_sha256") != _digest(_without(registry, "registry_sha256")):
        errors.append("REGISTRY_DIGEST_MISMATCH")
    profile = registry.get("qualification_profile") if isinstance(registry.get("qualification_profile"), dict) else {}
    expected_profile = qualification_profile_digest(str(profile.get("id") or ""), int(profile.get("version") or 0))
    if profile.get("digest") != expected_profile:
        errors.append("QUALIFICATION_PROFILE_DIGEST_MISMATCH")
    seen: set[str] = set()
    for adapter in registry.get("adapters") or []:
        aid = str(adapter.get("id") or "")
        if aid in seen:
            errors.append("ADAPTER_ID_DUPLICATE")
        seen.add(aid)
        if SAFE_ID_RE.fullmatch(aid) is None:
            errors.append("ADAPTER_ID_INVALID:" + aid)
        phases = adapter.get("supported_phases") or []
        if not phases or any(phase not in CREATIVE_PHASES for phase in phases):
            errors.append("ADAPTER_PHASE_INVALID:" + aid)
        if adapter.get("qualification_profile_id") != profile.get("id") or adapter.get("qualification_profile_version") != profile.get("version") or adapter.get("qualification_profile_digest") != profile.get("digest"):
            errors.append("ADAPTER_QUALIFICATION_PROFILE_MISMATCH:" + aid)
        command = adapter.get("command") if isinstance(adapter.get("command"), dict) else {}
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
        qpath = adapter.get("qualification_report")
        if not _safe_relative(qpath):
            errors.append("ADAPTER_QUALIFICATION_PATH_INVALID:" + aid)
        authority = adapter.get("authority") if isinstance(adapter.get("authority"), dict) else {}
        executor_auth = adapter.get("executor_auth")
        if executor_auth == "PROVIDER_MANAGED_SESSION":
            if adapter.get("kind") != "EXTERNAL_COMMAND":
                errors.append("PROVIDER_MANAGED_SESSION_KIND_FORBIDDEN:" + aid)
            if authority.get("secrets") != "FORBIDDEN":
                errors.append("PROVIDER_MANAGED_SESSION_SECRET_DELIVERY_FORBIDDEN:" + aid)
        elif executor_auth != "NONE":
            errors.append("EXECUTOR_AUTH_MODE_INVALID:" + aid)
        if adapter.get("monetary_budget_usd") != 0:
            errors.append("COMMAND_ADAPTER_COST_FORBIDDEN:" + aid)
        if adapter.get("kind") == "REFERENCE_DETERMINISTIC":
            if executor_auth != "NONE":
                errors.append("REFERENCE_ADAPTER_EXECUTOR_AUTH_FORBIDDEN:" + aid)
            if authority != {"network": "NONE", "secrets": "FORBIDDEN", "filesystem": "PACKAGE_SCOPED"}:
                errors.append("COMMAND_ADAPTER_AUTHORITY_FORBIDDEN:" + aid)
            if adapter.get("invocation_mode") != "COMMAND":
                errors.append("REFERENCE_ADAPTER_MODE_FORBIDDEN:" + aid)
        if adapter.get("package_schema") != ".adwf/schemas/ai-work-package.schema.json" or adapter.get("result_schema") != ".adwf/schemas/ai-work-result.schema.json":
            errors.append("ADAPTER_WORK_CONTRACT_SCHEMA_MISMATCH:" + aid)
        if _safe_relative(qpath):
            path = base / str(qpath)
            if not path.is_file():
                errors.append("QUALIFICATION_REPORT_MISSING:" + aid)
            else:
                try:
                    report = strict_loads(path.read_text(encoding="utf-8"))
                    if not isinstance(report, dict):
                        errors.append("QUALIFICATION_REPORT_NOT_OBJECT:" + aid)
                    else:
                        errors.extend(validate_qualification_report(report, adapter, base))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append("QUALIFICATION_REPORT_UNREADABLE:" + aid + ":" + type(exc).__name__)
    return list(dict.fromkeys(errors))


def adapter_by_id(root: str | Path, adapter_id: str) -> dict[str, Any]:
    registry = load_registry(root)
    errors = validate_registry(registry, root)
    if errors:
        raise ValueError("AGENT_ADAPTER_REGISTRY_INVALID:" + ",".join(errors))
    matches = [item for item in registry.get("adapters") or [] if item.get("id") == adapter_id]
    if len(matches) != 1:
        raise ValueError("AGENT_ADAPTER_NOT_FOUND:" + adapter_id)
    return matches[0]


def load_qualified_command_adapter(root: str | Path, adapter_id: str, phase: str) -> dict[str, Any]:
    adapter = adapter_by_id(root, adapter_id)
    if adapter.get("invocation_mode") != "COMMAND":
        raise ValueError("AGENT_ADAPTER_NOT_COMMAND:" + adapter_id)
    if phase not in (adapter.get("supported_phases") or []):
        raise ValueError("AGENT_ADAPTER_PHASE_NOT_QUALIFIED:" + phase)
    return adapter


def command_argv(root: str | Path, adapter: dict[str, Any]) -> list[str]:
    base = Path(root).resolve()
    command = adapter.get("command") or {}
    rel = str(command.get("path") or "")
    if not _safe_relative(rel):
        raise ValueError("AGENT_COMMAND_PATH_INVALID")
    path = (base / rel).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise ValueError("AGENT_COMMAND_PATH_ESCAPES_ROOT")
    if not path.is_file():
        raise ValueError("AGENT_COMMAND_MISSING")
    if command.get("sha256") != _sha256_file(path):
        raise ValueError("AGENT_COMMAND_DIGEST_MISMATCH")
    runner = command.get("runner")
    if runner == "PYTHON":
        return [sys.executable, str(path)]
    if runner == "EXECUTABLE":
        return [str(path)]
    raise ValueError("AGENT_COMMAND_RUNNER_INVALID")


def sanitized_agent_environment(
    source_env: dict[str, str],
    *,
    request: str | Path,
    result: str | Path,
    state: dict[str, Any],
    adapter: dict[str, Any],
) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in SAFE_ENV_NAMES:
        value = source_env.get(name)
        if isinstance(value, str) and value and not any(marker in name.upper() for marker in SECRET_MARKERS):
            env[name] = value
    env.update(
        {
            "ADWF_ACTION_REQUEST": str(Path(request).resolve()),
            "ADWF_ACTION_RESULT": str(Path(result).resolve()),
            "ADWF_RUN_ID": str(state.get("run_id") or ""),
            "ADWF_PHASE": str(state.get("phase") or ""),
            "ADWF_AGENT_ADAPTER_ID": str(adapter.get("id") or ""),
            "ADWF_AGENT_ADAPTER_VERSION": str(adapter.get("version") or ""),
            "ADWF_AGENT_EXECUTOR_AUTH": str(adapter.get("executor_auth") or ""),
            "ADWF_AGENT_NETWORK_AUTHORITY": str((adapter.get("authority") or {}).get("network") or ""),
            "ADWF_AGENT_SECRETS_AUTHORITY": str((adapter.get("authority") or {}).get("secrets") or ""),
        }
    )
    return env


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def verify_local_command_result(root: str | Path, package: dict[str, Any], work_result: dict[str, Any]) -> list[str]:
    base = Path(root).resolve()
    errors = list(validate_work_result(work_result, package=package))
    head = str(work_result.get("head_sha") or "")
    package_base = str(package.get("base_sha") or "")
    actual = _git(base, "rev-parse", "HEAD")
    if actual.returncode or actual.stdout.strip() != head:
        errors.append("LOCAL_AGENT_HEAD_MISMATCH")
    exists = _git(base, "cat-file", "-e", package_base + "^{commit}")
    if exists.returncode:
        errors.append("LOCAL_AGENT_BASE_MISSING")
    ancestor = _git(base, "merge-base", "--is-ancestor", package_base, head)
    if ancestor.returncode:
        errors.append("LOCAL_AGENT_BASE_NOT_ANCESTOR")
    diff = _git(base, "diff", "--name-only", package_base + ".." + head)
    if diff.returncode:
        errors.append("LOCAL_AGENT_DIFF_UNREADABLE")
    else:
        changed = sorted(line.strip() for line in diff.stdout.splitlines() if line.strip())
        if changed != sorted(work_result.get("changed_paths") or []):
            errors.append("LOCAL_AGENT_CHANGED_PATHS_MISMATCH")
    status = _git(base, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        errors.append("LOCAL_AGENT_WORKTREE_NOT_CLEAN")
    return list(dict.fromkeys(errors))


def _write_request(path: Path, package: dict[str, Any], state: dict[str, Any]) -> None:
    value = {
        "schema_version": 3,
        "idempotency_key": "q" * 64,
        "run_id": state["run_id"],
        "revision": state["revision"],
        "brief_id": state["roadmap_id"],
        "phase": state["phase"],
        "capability": "edit",
        "subject_sha": state["subject_sha"],
        "risk": state["risk"],
        "work_type": state["work_type"],
        "work_package": package,
        "work_package_digest": package["package_digest"],
    }
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _init_fixture(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/input.txt").write_text("owner-intent\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=ADWF Qualification", "-c", "user.email=adwf@invalid", "commit", "-q", "-m", "base"], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _run_reference_case(framework_root: Path, workspace: Path, runtime: Path, package: dict[str, Any], state: dict[str, Any]) -> tuple[int, Path]:
    adapter = load_qualified_command_adapter(framework_root, "reference-local", state["phase"])
    request = runtime / "request.json"
    result = runtime / "result.json"
    runtime.mkdir(parents=True, exist_ok=True)
    _write_request(request, package, state)
    source = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "GITHUB_TOKEN": "must-not-leak",
        "OPENAI_API_KEY": "must-not-leak",
        "PASSWORD": "must-not-leak",
    }
    env = sanitized_agent_environment(source, request=request, result=result, state=state, adapter=adapter)
    proc = subprocess.run(command_argv(framework_root, adapter), cwd=workspace, env=env, text=True, capture_output=True, check=False, timeout=int(adapter["timeout_seconds"]))
    return proc.returncode, result


def run_reference_qualification(root: str | Path) -> dict[str, Any]:
    framework_root = Path(root).resolve()
    adapter = load_qualified_command_adapter(framework_root, "reference-local", "EXECUTE")
    with tempfile.TemporaryDirectory(prefix="adwf-agentqual-") as tmp:
        parent = Path(tmp)
        workspace = parent / "workspace"
        runtime = parent / "runtime"
        workspace.mkdir()
        base_sha = _init_fixture(workspace)
        state = {
            "run_id": "run-agentqual-reference",
            "roadmap_id": "AGENTQUAL-001",
            "issue_id": "99",
            "revision": 1,
            "phase": "EXECUTE",
            "work_type": "verification",
            "risk": "R1",
            "subject_sha": base_sha,
            "allowed_write_surfaces": ["src/**"],
            "forbidden_write_surfaces": ["src/private/**"],
            "required_evidence": ["changed_paths", "verification_claims"],
        }
        package = compile_work_package(state, {"task_ru": "Квалифицировать локальный reference Creative Agent adapter"}, created_at="2026-08-16T00:00:00Z")
        code, result_path = _run_reference_case(framework_root, workspace, runtime, package, state)
        if code != 0 or not result_path.is_file():
            raise ValueError("REFERENCE_QUALIFICATION_POSITIVE_FAILED")
        result = strict_loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("REFERENCE_QUALIFICATION_RESULT_INVALID")
        errors = verify_local_command_result(workspace, package, result)
        if errors:
            raise ValueError("REFERENCE_QUALIFICATION_LOCAL_BINDING_FAILED:" + ",".join(errors))

        forbidden_workspace = parent / "forbidden-workspace"
        forbidden_runtime = parent / "forbidden-runtime"
        forbidden_workspace.mkdir()
        forbidden_base = _init_fixture(forbidden_workspace)
        forbidden_state = dict(state)
        forbidden_state["subject_sha"] = forbidden_base
        forbidden_state["allowed_write_surfaces"] = ["docs/**"]
        forbidden_state["forbidden_write_surfaces"] = ["src/**"]
        forbidden_pkg = compile_work_package(forbidden_state, {"task_ru": "Проверить блокировку forbidden surface"}, created_at="2026-08-16T00:00:01Z")
        forbidden_code, _ = _run_reference_case(framework_root, forbidden_workspace, forbidden_runtime, forbidden_pkg, forbidden_state)
        if forbidden_code == 0:
            raise ValueError("REFERENCE_QUALIFICATION_FORBIDDEN_WRITE_NOT_REJECTED")

        stale_workspace = parent / "stale-workspace"
        stale_runtime = parent / "stale-runtime"
        stale_workspace.mkdir()
        stale_base = _init_fixture(stale_workspace)
        stale_state = dict(state)
        stale_state["subject_sha"] = stale_base
        stale_pkg = compile_work_package(stale_state, {"task_ru": "Проверить stale package блокировку"}, created_at="2026-08-16T00:00:02Z")
        (stale_workspace / "src/input.txt").write_text("changed-before-agent\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=stale_workspace, check=True)
        subprocess.run(["git", "-c", "user.name=ADWF Qualification", "-c", "user.email=adwf@invalid", "commit", "-q", "-m", "stale"], cwd=stale_workspace, check=True)
        stale_code, _ = _run_reference_case(framework_root, stale_workspace, stale_runtime, stale_pkg, stale_state)
        if stale_code == 0:
            raise ValueError("REFERENCE_QUALIFICATION_STALE_PACKAGE_NOT_REJECTED")

    report = strict_loads((framework_root / str(adapter["qualification_report"])).read_text(encoding="utf-8"))
    report_errors = validate_qualification_report(report, adapter, framework_root)
    if report_errors:
        raise ValueError("REFERENCE_QUALIFICATION_REPORT_INVALID:" + ",".join(report_errors))
    return {
        "status": "PASS",
        "adapter_id": adapter["id"],
        "adapter_version": adapter["version"],
        "adapter_digest": adapter_digest(adapter),
        "qualification_profile_digest": adapter["qualification_profile_digest"],
        "report_sha256": report["report_sha256"],
        "executor_auth": adapter["executor_auth"],
        "network": adapter["authority"]["network"],
        "secrets": adapter["authority"]["secrets"],
        "filesystem": adapter["authority"]["filesystem"],
        "monetary_budget_usd": adapter["monetary_budget_usd"],
        "low_trust_result": True,
        "real_external_agent_verified": False,
    }
