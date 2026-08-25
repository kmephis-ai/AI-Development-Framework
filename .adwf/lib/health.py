"""Truthful Health: package, config, control plane и реальный продукт раздельно."""
from __future__ import annotations
from .strict_json import loads as strict_loads

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from .contracts import validate
from .consumer_profile import ConsumerProfileError, load_effective_config
from .docs_freshness import check_docs
from .evidence import DEFAULT_PRODUCT_TTL_HOURS, parse_time, verify_product_evidence
from .policy_compiler import check_compiled_policy
from .project_gates import gate_configuration_findings
from .workspaces import OCCUPYING, read_registry

SAFE = {"VERIFIED", "HEALTHY"}


def _json(path: Path) -> Any:
    return strict_loads(path.read_text(encoding="utf-8"))


def active_state_path(root: str | Path) -> Path:
    base = Path(root).resolve()
    runtime = base / ".adwf-runtime/project-state.json"
    return runtime if runtime.is_file() else base / ".adwf/project-state.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(root: Path) -> list[str]:
    sums_path = root / "SHA256SUMS.txt"
    manifest_path = root / "MANIFEST.json"
    if not sums_path.is_file(): return ["SHA256SUMS_MISSING"]
    if not manifest_path.is_file(): return ["MANIFEST_MISSING"]
    errors: list[str] = []; seen: set[str] = set()
    try:
        manifest = _json(manifest_path)
        listed = set(str(x) for x in manifest.get("files", [])) | {"MANIFEST.json"}
    except (OSError, ValueError, json.JSONDecodeError):
        return ["MANIFEST_UNREADABLE"]
    for index, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if not match:
            errors.append(f"CHECKSUM_LINE_INVALID:{index}"); continue
        expected, rel = match.groups()
        if rel in seen or rel == "SHA256SUMS.txt": errors.append(f"CHECKSUM_ENTRY_INVALID:{rel}"); continue
        seen.add(rel); target=(root/rel).resolve()
        if root.resolve() not in target.parents: errors.append(f"CHECKSUM_PATH_ESCAPE:{rel}")
        elif not target.is_file(): errors.append(f"CHECKSUM_FILE_MISSING:{rel}")
        elif _sha(target) != expected: errors.append(f"CHECKSUM_MISMATCH:{rel}")
    for rel in sorted(listed-seen): errors.append(f"CHECKSUM_UNLISTED:{rel}")
    for rel in sorted(seen-listed): errors.append(f"CHECKSUM_OUTSIDE_MANIFEST:{rel}")
    return errors


def _category(status: str, findings: list[str]) -> dict[str, Any]:
    return {"status": status, "findings": findings}


def project_projection_health(cfg: dict[str, Any], state: dict[str, Any], now: datetime) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    not_verified: list[str] = []
    project = cfg.get("github", {}).get("project", {})
    if cfg.get("provider", {}).get("mode") != "github" or project.get("enabled") is not True or state.get("active", {}).get("issue") is None:
        return findings, not_verified
    projection = state.get("project_projection", {})
    if projection.get("status") == "FAIL":
        findings.append("PROJECT_PROJECTION_FAILED")
    elif projection.get("status") != "PASS" or not projection.get("project_id") or not projection.get("item_id"):
        not_verified.append("PROJECT_PROJECTION_NOT_VERIFIED")
    else:
        try:
            projected_at = parse_time(projection["observed_at"])
            snapshot_at = parse_time(state.get("snapshot", {}).get("observed_at"))
            if projected_at < snapshot_at or (now - projected_at).total_seconds() > int(cfg.get("orchestration", {}).get("reconcile_ttl_minutes", 60)) * 60:
                not_verified.append("PROJECT_PROJECTION_STALE")
        except (TypeError, ValueError):
            findings.append("PROJECT_PROJECTION_TIME_INVALID")
    return findings, not_verified


def package_integrity(root: Path) -> dict[str, Any]:
    required = [
        "VERSION", "README.md", "AGENTS.md", "ADWS.md", "SPECIFICATION.md",
        "MANIFEST.json", "SHA256SUMS.txt", ".adwf/adwf.py", ".adwf/config.json",
        ".adwf/state-machine.json", ".adwf/providers.json", ".adwf/actions-lock.json",
        ".adwf/effective-policy.json",
    ]
    findings = [f"MISSING:{rel}" for rel in required if not (root / rel).is_file()]
    if (root / "VERSION").is_file():
        version=(root / "VERSION").read_text(encoding="utf-8").strip()
        try: cfg=_json(root / ".adwf/config.json")
        except Exception: cfg={}
        if not version or cfg.get("framework_version") != version:
            findings.append("VERSION_CROSS_FILE_DRIFT")
    findings.extend(verify_manifest(root))
    return _category("VERIFIED" if not findings else "BROKEN", findings)


def trust_boot_integrity(root: Path) -> dict[str, Any]:
    """Verify only trust-critical static prerequisites needed to evaluate a repair PR.

    This scope deliberately excludes candidate-repairable repository health such as
    documentation freshness. The trusted controller already verifies provider/cost,
    Pipeline IR and package integrity before invoking this check; compiled policy
    validation supplies the remaining fail-closed policy/config/trust invariants.
    """
    findings: list[str] = []
    try:
        findings.extend(check_compiled_policy(root))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings.append(f"TRUST_BOOT_UNREADABLE:{type(exc).__name__}")
    return _category("VERIFIED" if not findings else "BROKEN", findings)


def config_health(root: Path) -> dict[str, Any]:
    findings: list[str] = []
    try:
        cfg = load_effective_config(root, root)
        schema = _json(root / ".adwf/schemas/config.schema.json")
        findings.extend(f"SCHEMA:{item.path}:{item.code}" for item in validate(cfg, schema))
        if cfg.get("policy", {}).get("fail_mode") != "CLOSED":
            findings.append("FAIL_MODE_NOT_CLOSED")
        if cfg.get("project", {}).get("name") in {None, "", "CHANGE_ME"}:
            findings.append("PROJECT_NOT_CONFIGURED")
        if cfg.get("runtime", {}).get("node_major") != 24:
            findings.append("NODE_24_NOT_ENFORCED")
        findings.extend(gate_configuration_findings(cfg, root))
        project = cfg.get("github", {}).get("project", {})
        if project.get("enabled") is True and not all((project.get("owner"), project.get("number"), project.get("dashboard_issue_number"))):
            findings.append("GITHUB_CONTROL_CENTER_INCOMPLETE")
        # GitHub Project is an optional projection in v1.6; it is not part of the
        # public $0 trust boundary. A disabled Project must therefore not make a
        # clean package configuration BROKEN.
        #
        # The template also intentionally ships without an owner/reviewer login.
        # Identity and branch/ruleset state are provider readback facts and belong
        # to Control Plane NOT_VERIFIED until bootstrap/live reconciliation, not
        # to static Configuration BROKEN.
        github_trust = cfg.get("github", {}).get("trust", {})
        findings.extend(check_compiled_policy(root))
        findings.extend(check_docs(root))
    except (OSError, ConsumerProfileError, ValueError, json.JSONDecodeError) as exc:
        findings.append(f"CONFIG_UNREADABLE:{type(exc).__name__}")
    return _category("VERIFIED" if not findings else "BROKEN", findings)


def control_plane_health(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    findings: list[str] = []
    not_verified: list[str] = []
    try:
        cfg = _json(root / ".adwf/config.json")
        state = _json(active_state_path(root))
        schema = _json(root / ".adwf/schemas/project-state.schema.json")
        findings.extend(f"SCHEMA:{item.path}:{item.code}" for item in validate(state, schema))
        if state.get("profile") != cfg.get("profile"):
            findings.append("PROFILE_SPLIT_BRAIN")
        if state.get("provider", {}).get("mode") != cfg.get("provider", {}).get("mode"):
            findings.append("CANONICAL_PROVIDER_SPLIT_BRAIN")
        orchestration = state.get("orchestration", {})
        if int(orchestration.get("writers_active", -1)) > 1:
            findings.append("MULTIPLE_ACTIVE_WRITERS")
        if int(orchestration.get("conflicts", -1)) > 0:
            findings.append("WRITER_CONFLICT")
        if orchestration.get("writers_active") is None or orchestration.get("conflicts") is None:
            not_verified.append("ORCHESTRATION_UNKNOWN")
        reconciled = state.get("last_reconciled_at")
        if not reconciled:
            not_verified.append("RECONCILIATION_NOT_VERIFIED")
        else:
            try:
                age = now - parse_time(reconciled)
                if age.total_seconds() > int(cfg.get("orchestration", {}).get("reconcile_ttl_minutes", 60)) * 60:
                    not_verified.append("RECONCILIATION_STALE")
            except ValueError:
                findings.append("RECONCILIATION_TIME_INVALID")
        if state.get("main", {}).get("head") in {None, "", "UNSET"}:
            not_verified.append("MAIN_HEAD_NOT_VERIFIED")
        snapshot = state.get("snapshot", {})
        if not snapshot.get("observed_at") or not snapshot.get("valid_until"):
            not_verified.append("SNAPSHOT_FRESHNESS_NOT_VERIFIED")
        else:
            try:
                observed_at = parse_time(snapshot["observed_at"])
                valid_until = parse_time(snapshot["valid_until"])
                if observed_at > now or valid_until <= now or observed_at >= valid_until:
                    not_verified.append("SNAPSHOT_STALE")
            except ValueError:
                findings.append("SNAPSHOT_TIME_INVALID")
        main_head = state.get("main", {}).get("head")
        if main_head and snapshot.get("source_main_sha") != main_head:
            not_verified.append("SNAPSHOT_MAIN_SHA_STALE")
        workspace_status = state.get("workspace", {}).get("status")
        if workspace_status in {"STALLED", "RECOVERY"}:
            findings.append(f"WORKSPACE_{workspace_status}")
        if state.get("cost_usage", {}).get("status") not in {"ALLOW_ZERO_COST"}:
            not_verified.append("COST_USAGE_NOT_VERIFIED")
        projection_findings, projection_not_verified = project_projection_health(cfg, state, now)
        findings.extend(projection_findings)
        not_verified.extend(projection_not_verified)
        runtime_workspaces = [item for item in read_registry(root).get("workspaces", []) if item.get("status") in OCCUPYING]
        if len(runtime_workspaces) > 1:
            findings.append("MULTIPLE_RUNTIME_WORKSPACES")
        elif len(runtime_workspaces) == 1:
            runtime_workspace = runtime_workspaces[0]
            if state.get("active", {}).get("lease_id") != runtime_workspace.get("lease_id"):
                findings.append("WORKSPACE_STATE_SPLIT_BRAIN")
        if state.get("health", {}).get("adwf") not in SAFE:
            not_verified.append("ADWF_RUNTIME_HEALTH_NOT_VERIFIED")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings.append(f"STATE_UNREADABLE:{type(exc).__name__}")
    if findings:
        return _category("BROKEN", findings + not_verified)
    if not_verified:
        return _category("NOT_VERIFIED", not_verified)
    return _category("VERIFIED", [])


def product_health(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    findings: list[str] = []
    status = "NOT_VERIFIED"
    try:
        cfg = load_effective_config(root, root)
        state = _json(active_state_path(root))
        head = state.get("main", {}).get("head")
        if re.fullmatch(r"[0-9a-f]{40}", str(head or "")) is None:
            findings.append("PRODUCT_MAIN_SHA_NOT_VERIFIED")
        else:
            mapping = {"smoke": "SMOKE", "golden_paths": "GOLDEN_PATHS", "e2e": "E2E"}
            configured = cfg.get("reality", {}).get("required_product_gates", ["smoke", "golden_paths"])
            required_kinds = {"REALITY"}
            ttl_by_kind = dict(DEFAULT_PRODUCT_TTL_HOURS)
            reality_ttl = cfg.get("reality", {}).get("reality_check_ttl_hours")
            if isinstance(reality_ttl, bool) or not isinstance(reality_ttl, int) or reality_ttl <= 0:
                findings.append("PRODUCT_EVIDENCE_TTL_POLICY_INVALID")
                status = "BROKEN"
            else:
                ttl_by_kind["REALITY"] = reality_ttl
            for gate in configured:
                if gate not in mapping:
                    findings.append(f"PRODUCT_GATE_EVIDENCE_KIND_UNKNOWN:{gate}")
                    status = "BROKEN"
                else:
                    required_kinds.add(mapping[gate])
            evidence = verify_product_evidence(
                root,
                expected_sha=head,
                required_kinds=required_kinds,
                now=now,
                max_ttl_hours_by_kind=ttl_by_kind,
            )
            findings.extend(evidence["errors"])
            if status != "BROKEN":
                status = evidence["status"]

        # State fields remain a projection. They may block safely, but can never
        # establish PASS without the append-only evidence graph above.
        runtime = state.get("runtime", {})
        if runtime.get("canonical_revision") not in {None, head}:
            findings.append("RUNTIME_PROJECTION_SHA_MISMATCH")
            status = "BROKEN"
        for gate in ("smoke", "golden_paths"):
            if runtime.get(gate) == "FAIL":
                findings.append(f"RUNTIME_PROJECTION_FAIL:{gate}")
                status = "BROKEN"
        if state.get("health", {}).get("product") == "BROKEN":
            findings.append("PRODUCT_PROJECTION_DECLARED_BROKEN")
            status = "BROKEN"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings.append(f"PRODUCT_STATE_UNREADABLE:{type(exc).__name__}")
        status = "BROKEN"
    return _category("VERIFIED" if not findings and status == "VERIFIED" else status, findings)


def doctor(root: str | Path, *, scope: str = "all") -> dict[str, Any]:
    base = Path(root).resolve()
    categories = {
        "package_integrity": package_integrity(base),
        "trust_boot_integrity": trust_boot_integrity(base),
        "config_health": config_health(base),
        "control_plane_health": control_plane_health(base),
        "product_health": product_health(base),
    }
    requested = list(categories) if scope == "all" else [scope]
    if any(name not in categories for name in requested):
        return {"overall": "BROKEN", "categories": categories, "findings": ["UNKNOWN_SCOPE"]}
    statuses = [categories[name]["status"] for name in requested]
    if "BROKEN" in statuses:
        overall = "BROKEN"
    elif any(status not in SAFE for status in statuses):
        overall = "NOT_VERIFIED"
    else:
        overall = "VERIFIED"
    return {"overall": overall, "scope": scope, "categories": categories}
