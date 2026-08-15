"""Deterministic Skill Operating Layer contracts for ADWF.

The module deliberately does not execute Skill instructions or download remote
content. It validates package metadata, lifecycle, provenance, declared effects,
static security invariants, deterministic routing evals and generated registry
truth. Ambiguity is represented as a finding and therefore fails closed for
ACTIVE packages.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re
import shutil

from .contracts import validate
from .strict_json import load as strict_json_load, loads as strict_json_loads

SKILL_SCHEMA_VERSION = 1
CORE_ROUTERS = ("adwf-develop", "adwf-govern", "adwf-operate")
FIRST_PARTY_LIFECYCLE = (
    "DRAFT", "VALIDATED", "SECURITY_SCANNED", "EVAL_PASSED", "APPROVED", "ACTIVE", "DEPRECATED",
)
VENDOR_LIFECYCLE = (
    "UNTRUSTED", "QUARANTINED", "SCANNED", "VENDORED", "EVAL_PASSED", "APPROVED", "ACTIVE", "DEPRECATED",
)
EVAL_FILES = {
    "trigger_positive": "trigger-positive.json",
    "trigger_negative": "trigger-negative.json",
    "success_cases": "success-cases.json",
    "adversarial": "adversarial.json",
}
IGNORED_PACKAGE_PARTS = {"__pycache__", ".DS_Store"}
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
PROMPT_OVERRIDE_PATTERNS = (
    ("PROMPT_OVERRIDE_ATTEMPT", re.compile(r"\bignore\s+(?:all|any|the)?\s*(?:previous|prior|system|developer)\s+instructions\b", re.IGNORECASE)),
    ("SYSTEM_PROMPT_EXFIL_ATTEMPT", re.compile(r"\b(?:reveal|print|dump|show)\s+(?:the\s+)?system\s+prompt\b", re.IGNORECASE)),
    ("SAFETY_BYPASS_ATTEMPT", re.compile(r"\b(?:bypass|disable|override)\s+(?:the\s+)?(?:safety|policy|guardrails?)\b", re.IGNORECASE)),
)
FORBIDDEN_EXECUTABLE_PATTERNS = (
    ("PIPE_TO_SHELL", re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b", re.IGNORECASE)),
    ("POWERSHELL_IEX_DOWNLOAD", re.compile(r"\bIEX\s*\([^\n]*(?:DownloadString|Invoke-WebRequest)", re.IGNORECASE)),
    ("ENCODED_DYNAMIC_EXEC", re.compile(r"\b(?:eval|exec)\s*\([^\n]*(?:base64|b64decode)", re.IGNORECASE)),
)
URL_RE = re.compile(r"https?://([^/\s)`>\]]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SkillFinding:
    code: str
    path: str
    message: str
    severity: str = "ERROR"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _skill_files(package_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in package_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(package_dir)
        if any(part in IGNORED_PACKAGE_PARTS for part in rel.parts) or path.suffix == ".pyc":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(package_dir).as_posix())


def package_digest(package_dir: Path) -> str:
    """Content-address a Skill package including stable relative paths."""
    digest = hashlib.sha256()
    for path in _skill_files(package_dir):
        rel = path.relative_to(package_dir).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def descriptor_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_packages(root: Path) -> list[Path]:
    skills = root / "skills"
    if not skills.is_dir():
        return []
    result: list[Path] = []
    for child in sorted(skills.iterdir(), key=lambda item: item.name):
        if child.is_dir() and not child.is_symlink() and (child / "SKILL.md").is_file():
            result.append(child)
    return result


def _schema(root: Path, name: str) -> dict[str, Any]:
    return strict_json_load(root / ".adwf" / "schemas" / name)


def load_legacy_allowlist(root: Path) -> tuple[dict[str, Any], list[SkillFinding]]:
    path = root / ".adwf" / "skill-legacy-allowlist.json"
    if not path.is_file():
        return {"schema_version": 1, "entries": []}, [
            SkillFinding("LEGACY_ALLOWLIST_MISSING", str(path.relative_to(root)), "legacy allowlist is required")
        ]
    try:
        payload = strict_json_load(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, [SkillFinding("LEGACY_ALLOWLIST_INVALID_JSON", str(path.relative_to(root)), type(exc).__name__)]
    findings = [
        SkillFinding("LEGACY_ALLOWLIST_SCHEMA", f".adwf/skill-legacy-allowlist.json:{item.path}", item.code)
        for item in validate(payload, _schema(root, "skill-legacy-allowlist.schema.json"))
    ]
    return payload, findings


def _legacy_match(root: Path, package_dir: Path, allowlist: dict[str, Any], today: date | None = None) -> list[SkillFinding] | None:
    rel = package_dir.relative_to(root).as_posix()
    matches = [item for item in allowlist.get("entries", []) if item.get("path") == rel]
    if not matches:
        return None
    if len(matches) != 1:
        return [SkillFinding("LEGACY_ALLOWLIST_DUPLICATE", rel, "exactly one allowlist entry is required")]
    item = matches[0]
    findings: list[SkillFinding] = []
    expected_id = package_dir.name
    if item.get("id") != expected_id:
        findings.append(SkillFinding("LEGACY_ID_MISMATCH", rel, f"expected id {expected_id}"))
    actual = package_digest(package_dir)
    if item.get("package_sha256") != actual:
        findings.append(SkillFinding("LEGACY_DIGEST_MISMATCH", rel, f"expected exact package digest {item.get('package_sha256')}, got {actual}"))
    expiry = item.get("expires_on")
    if expiry:
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError:
            findings.append(SkillFinding("LEGACY_EXPIRY_INVALID", rel, "expires_on must be YYYY-MM-DD"))
        else:
            if (today or date.today()) > expiry_date:
                findings.append(SkillFinding("LEGACY_ALLOWLIST_EXPIRED", rel, f"expired on {expiry}"))
    return findings


def _read_descriptor(root: Path, package_dir: Path) -> tuple[dict[str, Any] | None, list[SkillFinding]]:
    path = package_dir / "skill.json"
    rel = path.relative_to(root).as_posix()
    if not path.is_file():
        return None, []
    try:
        payload = strict_json_load(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [SkillFinding("DESCRIPTOR_INVALID_JSON", rel, type(exc).__name__)]
    findings = [SkillFinding("DESCRIPTOR_SCHEMA", f"{rel}:{item.path}", item.code) for item in validate(payload, _schema(root, "skill.schema.json"))]
    return payload, findings


def lifecycle_transition_allowed(origin_kind: str, current: str, target: str) -> bool:
    lifecycle = FIRST_PARTY_LIFECYCLE if origin_kind == "first_party" else VENDOR_LIFECYCLE
    if current not in lifecycle or target not in lifecycle:
        return False
    if current == "DEPRECATED":
        return target == "DEPRECATED"
    return lifecycle.index(target) == lifecycle.index(current) + 1


def _extract_domains(text: str) -> set[str]:
    return {match.group(1).lower().rstrip(".,;") for match in URL_RE.finditer(text)}


def _has_secret_value(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS)


def _infer_script_effects(package_dir: Path) -> dict[str, Any]:
    effects = {"shell": False, "filesystem": "none", "network": "none", "secrets": "none"}
    rank = {"none": 0, "read": 1, "write": 2}
    network_rank = {"none": 0, "provider_connector": 1, "outbound": 2}
    for path in _skill_files(package_dir):
        if path.suffix not in {".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        if any(token in low for token in ("subprocess", "os.system", "shell=true", "child_process", "powershell", "cmd.exe")) or path.suffix in {".sh", ".ps1", ".bat", ".cmd"}:
            effects["shell"] = True
        fs_value = "none"
        if any(token in low for token in ("write_text(", "write_bytes(", "open(\"w", "open('w", "shutil.copy", "shutil.move", "unlink(", "mkdir(", "os.replace", "zipfile")):
            fs_value = "write"
        elif any(token in low for token in ("read_text(", "read_bytes(", "open(", "pathlib", "os.path")):
            fs_value = "read"
        if rank[fs_value] > rank[effects["filesystem"]]:
            effects["filesystem"] = fs_value
        net_value = "none"
        if any(token in low for token in ("requests.", "urllib", "httpx", "socket.", "curl ", "wget ", "ssh ", "scp ")):
            net_value = "outbound"
        if network_rank[net_value] > network_rank[effects["network"]]:
            effects["network"] = net_value
        if re.search(r"(?:os\.environ|getenv\()[^\n]{0,120}(?:TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY)", text, re.IGNORECASE):
            effects["secrets"] = "provider_managed"
    return effects


def security_scan(root: Path, package_dir: Path, descriptor: dict[str, Any]) -> list[SkillFinding]:
    rel = package_dir.relative_to(root).as_posix()
    findings: list[SkillFinding] = []
    declared = descriptor.get("effects", {})
    inferred = _infer_script_effects(package_dir)
    fs_rank = {"none": 0, "read": 1, "write": 2}
    net_rank = {"none": 0, "provider_connector": 1, "outbound": 2}
    if inferred["shell"] and not declared.get("shell"):
        findings.append(SkillFinding("UNDECLARED_SHELL_EFFECT", rel, "executable content uses shell/process capability"))
    if fs_rank.get(str(declared.get("filesystem")), -1) < fs_rank[inferred["filesystem"]]:
        findings.append(SkillFinding("UNDECLARED_FILESYSTEM_EFFECT", rel, f"inferred {inferred['filesystem']}"))
    if net_rank.get(str(declared.get("network")), -1) < net_rank[inferred["network"]]:
        findings.append(SkillFinding("UNDECLARED_NETWORK_EFFECT", rel, f"inferred {inferred['network']}"))
    if inferred["secrets"] != "none" and declared.get("secrets") == "none":
        findings.append(SkillFinding("UNDECLARED_SECRET_EFFECT", rel, "script references secret-like environment access"))

    declared_domains = {str(item).lower() for item in descriptor.get("external_domains", [])}
    observed_domains: set[str] = set()
    for path in _skill_files(package_dir):
        text = path.read_text(encoding="utf-8", errors="replace")
        if _has_secret_value(text):
            findings.append(SkillFinding("SECRET_VALUE_IN_PACKAGE", path.relative_to(root).as_posix(), "credential-like value detected"))
        for code, pattern in FORBIDDEN_EXECUTABLE_PATTERNS:
            if pattern.search(text):
                findings.append(SkillFinding(code, path.relative_to(root).as_posix(), "unsafe dynamic download/execute pattern"))
        observed_domains.update(_extract_domains(text))
        if "evals" not in path.relative_to(package_dir).parts:
            for code, pattern in PROMPT_OVERRIDE_PATTERNS:
                if pattern.search(text):
                    findings.append(SkillFinding(code, path.relative_to(root).as_posix(), "prompt-override or system-prompt exfiltration instruction detected"))
    unknown_domains = sorted(observed_domains - declared_domains)
    for domain in unknown_domains:
        findings.append(SkillFinding("UNDECLARED_EXTERNAL_DOMAIN", rel, domain))

    origin = descriptor.get("origin", {})
    if origin.get("kind") == "vendor":
        provenance = origin.get("provenance", {})
        required = ("source_url", "source_ref", "source_digest", "license", "attribution", "imported_at", "local_modifications")
        for field in required:
            if field not in provenance:
                findings.append(SkillFinding("VENDOR_PROVENANCE_INCOMPLETE", rel, field))
    for dependency in descriptor.get("dependencies", []):
        pin = str(dependency.get("pin", ""))
        if pin.casefold() in {"latest", "*", "main", "master", "head"} or any(token in pin for token in (">", "<", "^", "~")):
            findings.append(SkillFinding("UNPINNED_EXECUTABLE_DEPENDENCY", rel, f"{dependency.get('name')}:{pin}"))
    manifests = {"requirements.txt", "pyproject.toml", "package.json", "package-lock.json", "poetry.lock", "uv.lock"}
    observed_manifests = [path for path in _skill_files(package_dir) if path.name in manifests]
    if observed_manifests and not descriptor.get("dependencies"):
        findings.append(SkillFinding("UNDECLARED_EXECUTABLE_DEPENDENCIES", rel, ",".join(path.name for path in observed_manifests)))
    if origin.get("kind") == "vendor" and descriptor.get("lifecycle") == "ACTIVE" and findings:
        findings.append(SkillFinding("ACTIVE_VENDOR_NOT_TRUSTED", rel, "ACTIVE vendor requires complete current static evidence"))
    return findings


def _load_eval(root: Path, package_dir: Path, descriptor: dict[str, Any], key: str) -> tuple[dict[str, Any] | None, list[SkillFinding]]:
    declared_path = descriptor.get("evals", {}).get(key)
    expected = f"evals/{EVAL_FILES[key]}"
    rel_package = package_dir.relative_to(root).as_posix()
    if declared_path != expected:
        return None, [SkillFinding("EVAL_PATH_NONCANONICAL", rel_package, f"{key} must be {expected}")]
    path = package_dir / declared_path
    if not path.is_file():
        return None, [SkillFinding("EVAL_FILE_MISSING", path.relative_to(root).as_posix(), key)]
    try:
        payload = strict_json_load(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [SkillFinding("EVAL_INVALID_JSON", path.relative_to(root).as_posix(), type(exc).__name__)]
    findings = [SkillFinding("EVAL_SCHEMA", f"{path.relative_to(root).as_posix()}:{item.path}", item.code) for item in validate(payload, _schema(root, "skill-eval.schema.json"))]
    expected_kind = EVAL_FILES[key].removesuffix(".json")
    kind = payload.get("kind")
    for case in payload.get("cases", []):
        if kind in {"trigger-positive", "trigger-negative", "adversarial"}:
            if "input" not in case or "expected_trigger" not in case:
                findings.append(SkillFinding("EVAL_CASE_FIELDS_MISSING", path.relative_to(root).as_posix(), str(case.get("id"))))
        if kind == "trigger-positive" and case.get("expected_trigger") is not True:
            findings.append(SkillFinding("POSITIVE_EVAL_EXPECTATION_INVALID", path.relative_to(root).as_posix(), str(case.get("id"))))
        if kind == "trigger-negative" and case.get("expected_trigger") is not False:
            findings.append(SkillFinding("NEGATIVE_EVAL_EXPECTATION_INVALID", path.relative_to(root).as_posix(), str(case.get("id"))))
        if kind == "success-cases" and not case.get("required_phrases") and not case.get("forbidden_phrases"):
            findings.append(SkillFinding("SUCCESS_EVAL_ASSERTION_MISSING", path.relative_to(root).as_posix(), str(case.get("id"))))
    if payload.get("skill_id") != descriptor.get("id"):
        findings.append(SkillFinding("EVAL_SKILL_ID_MISMATCH", path.relative_to(root).as_posix(), key))
    if payload.get("kind") != expected_kind:
        findings.append(SkillFinding("EVAL_KIND_MISMATCH", path.relative_to(root).as_posix(), f"expected {expected_kind}"))
    raw = path.read_text(encoding="utf-8", errors="replace")
    if _has_secret_value(raw):
        findings.append(SkillFinding("SECRET_VALUE_IN_EVAL", path.relative_to(root).as_posix(), "credential-like fixture data detected"))
    return payload, findings


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def routing_match(descriptor: dict[str, Any], text: str) -> bool:
    normalized = _normalize(text)
    routing = descriptor.get("routing", {})
    negatives = [_normalize(str(item)) for item in routing.get("negative_terms", [])]
    if any(item and item in normalized for item in negatives):
        return False
    positives = [_normalize(str(item)) for item in routing.get("trigger_terms", [])]
    return any(item and item in normalized for item in positives)


def evaluate_package(root: Path, package_dir: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    findings: list[SkillFinding] = []
    fixtures: dict[str, dict[str, Any]] = {}
    for key in EVAL_FILES:
        payload, current = _load_eval(root, package_dir, descriptor, key)
        findings.extend(current)
        if payload is not None:
            fixtures[key] = payload

    results: list[dict[str, Any]] = []
    positive_total = positive_pass = 0
    negative_total = negative_pass = 0
    for key in ("trigger_positive", "trigger_negative"):
        payload = fixtures.get(key, {})
        expected = key == "trigger_positive"
        for case in payload.get("cases", []):
            actual = routing_match(descriptor, str(case.get("input", "")))
            passed = actual is expected
            results.append({"fixture": key, "id": case.get("id"), "passed": passed, "expected_trigger": expected, "actual_trigger": actual})
            if expected:
                positive_total += 1; positive_pass += int(passed)
            else:
                negative_total += 1; negative_pass += int(passed)

    skill_text = (package_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (package_dir / "SKILL.md").is_file() else ""
    for case in fixtures.get("success_cases", {}).get("cases", []):
        required_phrases = [str(item) for item in case.get("required_phrases", [])]
        forbidden_phrases = [str(item) for item in case.get("forbidden_phrases", [])]
        passed = all(item in skill_text for item in required_phrases) and all(item not in skill_text for item in forbidden_phrases)
        results.append({"fixture": "success_cases", "id": case.get("id"), "passed": passed})

    for case in fixtures.get("adversarial", {}).get("cases", []):
        actual = routing_match(descriptor, str(case.get("input", "")))
        expected = bool(case.get("expected_trigger"))
        passed = actual is expected
        results.append({"fixture": "adversarial", "id": case.get("id"), "passed": passed, "expected_trigger": expected, "actual_trigger": actual})

    if positive_total == 0:
        findings.append(SkillFinding("POSITIVE_EVAL_EMPTY", package_dir.relative_to(root).as_posix(), "at least one positive trigger case is required"))
    if negative_total == 0:
        findings.append(SkillFinding("NEGATIVE_EVAL_EMPTY", package_dir.relative_to(root).as_posix(), "at least one negative trigger case is required"))
    no_trigger_precision = (negative_pass / negative_total) if negative_total else 0.0
    trigger_recall = (positive_pass / positive_total) if positive_total else 0.0
    threshold = float(descriptor.get("quality", {}).get("min_no_trigger_precision", 1.0))
    if no_trigger_precision < threshold:
        findings.append(SkillFinding("NO_TRIGGER_PRECISION_BELOW_THRESHOLD", package_dir.relative_to(root).as_posix(), f"{no_trigger_precision:.3f} < {threshold:.3f}"))
    if any(not item["passed"] for item in results):
        findings.append(SkillFinding("EVAL_CASE_FAILED", package_dir.relative_to(root).as_posix(), "one or more deterministic eval cases failed"))
    budget = descriptor.get("context_budget", {})
    if len(descriptor.get("description", "")) > int(budget.get("max_description_chars", 320)):
        findings.append(SkillFinding("DESCRIPTION_CONTEXT_BUDGET_EXCEEDED", package_dir.relative_to(root).as_posix(), "description exceeds declared budget"))
    if len(skill_text) > int(budget.get("max_skill_md_chars", 16000)):
        findings.append(SkillFinding("SKILL_CONTEXT_BUDGET_EXCEEDED", package_dir.relative_to(root).as_posix(), "SKILL.md exceeds declared budget"))
    return {
        "status": "PASS" if not findings else "FAIL",
        "trigger_recall": round(trigger_recall, 6),
        "no_trigger_precision": round(no_trigger_precision, 6),
        "cases": results,
        "findings": [item.to_dict() for item in findings],
    }


def validate_package(root: Path, package_dir: Path, descriptor: dict[str, Any]) -> tuple[list[SkillFinding], dict[str, Any]]:
    rel = package_dir.relative_to(root).as_posix()
    findings: list[SkillFinding] = []
    if descriptor.get("id") != package_dir.name:
        findings.append(SkillFinding("PACKAGE_ID_PATH_MISMATCH", rel, f"descriptor id {descriptor.get('id')!r}"))
    entrypoint = package_dir / str(descriptor.get("entrypoint", ""))
    if not entrypoint.is_file():
        findings.append(SkillFinding("ENTRYPOINT_MISSING", rel, str(descriptor.get("entrypoint"))))
    if not (package_dir / "SPEC.md").is_file():
        findings.append(SkillFinding("SPEC_MISSING", rel, "SPEC.md is required for managed Skills"))
    origin_kind = descriptor.get("origin", {}).get("kind")
    lifecycle = descriptor.get("lifecycle")
    allowed = FIRST_PARTY_LIFECYCLE if origin_kind == "first_party" else VENDOR_LIFECYCLE
    if lifecycle not in allowed:
        findings.append(SkillFinding("LIFECYCLE_INVALID_FOR_ORIGIN", rel, f"{origin_kind}:{lifecycle}"))
    routing = descriptor.get("routing", {})
    if descriptor.get("kind") == "router":
        if not routing.get("startup_visible"):
            findings.append(SkillFinding("ROUTER_NOT_STARTUP_VISIBLE", rel, "router must be startup-visible"))
        if routing.get("routed_by"):
            findings.append(SkillFinding("ROUTER_ROUTED_BY_FORBIDDEN", rel, "routers cannot be routed by another Skill"))
    else:
        if routing.get("startup_visible"):
            findings.append(SkillFinding("LEAF_STARTUP_VISIBLE_FORBIDDEN", rel, "leaf Skills must use progressive disclosure"))
        if not routing.get("routed_by"):
            findings.append(SkillFinding("LEAF_ROUTER_REQUIRED", rel, "leaf Skill requires routed_by"))
    findings.extend(security_scan(root, package_dir, descriptor))
    evaluation = evaluate_package(root, package_dir, descriptor)
    findings.extend(SkillFinding(**item) for item in evaluation["findings"])
    if lifecycle in {"EVAL_PASSED", "APPROVED", "ACTIVE"} and evaluation["status"] != "PASS":
        findings.append(SkillFinding("LIFECYCLE_EVAL_EVIDENCE_MISSING", rel, lifecycle))
    if lifecycle in {"SECURITY_SCANNED", "EVAL_PASSED", "APPROVED", "ACTIVE"}:
        security_errors = [item for item in findings if item.code in {
            "UNDECLARED_SHELL_EFFECT", "UNDECLARED_FILESYSTEM_EFFECT", "UNDECLARED_NETWORK_EFFECT", "UNDECLARED_SECRET_EFFECT",
            "SECRET_VALUE_IN_PACKAGE", "PIPE_TO_SHELL", "POWERSHELL_IEX_DOWNLOAD", "ENCODED_DYNAMIC_EXEC", "UNDECLARED_EXTERNAL_DOMAIN",
            "VENDOR_PROVENANCE_INCOMPLETE", "ACTIVE_VENDOR_NOT_TRUSTED",
        }]
        if security_errors:
            findings.append(SkillFinding("LIFECYCLE_SECURITY_EVIDENCE_MISSING", rel, lifecycle))
    return findings, evaluation


def expected_registry(root: Path, descriptors: list[tuple[Path, dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    for package_dir, descriptor, evaluation in sorted(descriptors, key=lambda item: item[1]["id"]):
        skills.append({
            "id": descriptor["id"],
            "kind": descriptor["kind"],
            "lifecycle": descriptor["lifecycle"],
            "path": package_dir.relative_to(root).as_posix(),
            "entrypoint": descriptor["entrypoint"],
            "descriptor_sha256": descriptor_digest(package_dir / "skill.json"),
            "package_sha256": package_digest(package_dir),
            "startup_visible": bool(descriptor.get("routing", {}).get("startup_visible")),
            "routed_by": sorted(descriptor.get("routing", {}).get("routed_by", [])),
            "routes": sorted(descriptor.get("routing", {}).get("routes", [])),
            "security_status": "PASS" if not security_scan(root, package_dir, descriptor) else "FAIL",
            "eval_status": evaluation["status"],
            "no_trigger_precision": evaluation["no_trigger_precision"],
        })
    startup = sorted(item["id"] for item in skills if item["startup_visible"])
    return {"schema_version": 1, "generated": True, "startup_routers": startup, "skills": skills}


def validate_repository(root: Path, *, today: date | None = None) -> dict[str, Any]:
    findings: list[SkillFinding] = []
    allowlist, allowlist_findings = load_legacy_allowlist(root)
    findings.extend(allowlist_findings)
    managed: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    legacy: list[dict[str, str]] = []
    for package_dir in discover_packages(root):
        descriptor, current = _read_descriptor(root, package_dir)
        findings.extend(current)
        if descriptor is None:
            legacy_result = _legacy_match(root, package_dir, allowlist, today=today)
            if legacy_result is None:
                findings.append(SkillFinding("UNMANAGED_SKILL_PACKAGE", package_dir.relative_to(root).as_posix(), "skill.json missing and package is not exact-digest allowlisted"))
            else:
                findings.extend(legacy_result)
                if not legacy_result:
                    legacy.append({"id": package_dir.name, "path": package_dir.relative_to(root).as_posix(), "package_sha256": package_digest(package_dir)})
            continue
        package_findings, evaluation = validate_package(root, package_dir, descriptor)
        findings.extend(package_findings)
        managed.append((package_dir, descriptor, evaluation))

    ids = [item[1]["id"] for item in managed]
    if len(ids) != len(set(ids)):
        findings.append(SkillFinding("DUPLICATE_SKILL_ID", "skills", "managed Skill ids must be unique"))
    if managed:
        startup = sorted(item[1]["id"] for item in managed if item[1].get("routing", {}).get("startup_visible"))
        if len(startup) > 3:
            findings.append(SkillFinding("STARTUP_ROUTER_LIMIT_EXCEEDED", "skills", f"{len(startup)} > 3"))
        if startup != sorted(CORE_ROUTERS):
            findings.append(SkillFinding("CORE_ROUTER_SET_MISMATCH", "skills", f"expected {list(CORE_ROUTERS)}, got {startup}"))
        known_ids = set(ids)
        for _, descriptor, _ in managed:
            for router_id in descriptor.get("routing", {}).get("routed_by", []):
                if router_id not in known_ids:
                    findings.append(SkillFinding("UNKNOWN_ROUTER_REFERENCE", f"skills/{descriptor['id']}", router_id))
            for route_id in descriptor.get("routing", {}).get("routes", []):
                if route_id not in known_ids:
                    findings.append(SkillFinding("UNKNOWN_ROUTE_REFERENCE", f"skills/{descriptor['id']}", route_id))
        registry_path = root / "skills" / "registry.json"
        expected = expected_registry(root, managed)
        if not registry_path.is_file():
            findings.append(SkillFinding("SKILL_REGISTRY_MISSING", "skills/registry.json", "managed Skills require generated registry"))
        else:
            try:
                actual = strict_json_load(registry_path)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                findings.append(SkillFinding("SKILL_REGISTRY_INVALID_JSON", "skills/registry.json", type(exc).__name__))
            else:
                findings.extend(SkillFinding("SKILL_REGISTRY_SCHEMA", f"skills/registry.json:{item.path}", item.code) for item in validate(actual, _schema(root, "skill-registry.schema.json")))
                if actual != expected:
                    findings.append(SkillFinding("SKILL_REGISTRY_STALE", "skills/registry.json", "registry does not match managed package truth"))
    elif (root / "skills" / "registry.json").exists():
        findings.append(SkillFinding("SKILL_REGISTRY_WITHOUT_MANAGED_PACKAGES", "skills/registry.json", "remove empty/stale registry"))

    return {
        "status": "PASS" if not findings else "FAIL",
        "managed_count": len(managed),
        "legacy_count": len(legacy),
        "legacy": legacy,
        "findings": [item.to_dict() for item in findings],
    }


def write_registry(root: Path) -> dict[str, Any]:
    descriptors: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    findings: list[SkillFinding] = []
    for package_dir in discover_packages(root):
        descriptor, current = _read_descriptor(root, package_dir)
        findings.extend(current)
        if descriptor is None:
            continue
        package_findings, evaluation = validate_package(root, package_dir, descriptor)
        findings.extend(package_findings)
        descriptors.append((package_dir, descriptor, evaluation))
    if findings:
        return {"status": "FAIL", "findings": [item.to_dict() for item in findings]}
    if not descriptors:
        return {"status": "NO_MANAGED_SKILLS", "registry": None}
    payload = expected_registry(root, descriptors)
    path = root / "skills" / "registry.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return {"status": "PASS", "registry": str(path.relative_to(root)), "skill_count": len(descriptors)}


def vendor_intake_plan(source_dir: Path, skill_id: str, provenance: dict[str, Any], destination_root: Path) -> dict[str, Any]:
    """Build a fail-closed local-only quarantine plan; never downloads or activates."""
    findings: list[SkillFinding] = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", skill_id):
        findings.append(SkillFinding("VENDOR_ID_INVALID", str(source_dir), skill_id))
    if not source_dir.is_dir() or source_dir.is_symlink():
        findings.append(SkillFinding("VENDOR_SOURCE_INVALID", str(source_dir), "source must be a real local directory"))
    else:
        for path in source_dir.rglob("*"):
            if path.is_symlink():
                findings.append(SkillFinding("VENDOR_SYMLINK_FORBIDDEN", str(path), "symlinks are not accepted into quarantine"))
    for field in ("source_url", "source_ref", "source_digest", "license", "attribution", "imported_at", "local_modifications"):
        if field not in provenance:
            findings.append(SkillFinding("VENDOR_PROVENANCE_INCOMPLETE", skill_id, field))
    source_digest = provenance.get("source_digest")
    if source_digest is not None and re.fullmatch(r"[0-9a-f]{64}", str(source_digest)) is None:
        findings.append(SkillFinding("VENDOR_SOURCE_DIGEST_INVALID", skill_id, "source_digest must be sha256"))
    elif source_dir.is_dir() and not source_dir.is_symlink() and source_digest is not None:
        actual_source_digest = package_digest(source_dir)
        if source_digest != actual_source_digest:
            findings.append(SkillFinding("VENDOR_SOURCE_DIGEST_MISMATCH", skill_id, f"expected {source_digest}, got {actual_source_digest}"))
    target = destination_root / skill_id
    if target.exists():
        findings.append(SkillFinding("VENDOR_QUARANTINE_TARGET_EXISTS", str(target), "refusing overwrite"))
    return {
        "status": "PASS" if not findings else "FAIL",
        "source": str(source_dir),
        "target": str(target),
        "lifecycle": "QUARANTINED",
        "network_used": False,
        "auto_activate": False,
        "findings": [item.to_dict() for item in findings],
    }


def apply_vendor_intake(source_dir: Path, skill_id: str, provenance: dict[str, Any], destination_root: Path) -> dict[str, Any]:
    plan = vendor_intake_plan(source_dir, skill_id, provenance, destination_root)
    if plan["status"] != "PASS":
        return plan
    target = Path(plan["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target, symlinks=False)
    (target / "PROVENANCE.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan["applied"] = True
    return plan
