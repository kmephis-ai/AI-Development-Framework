"""Reference consumer conformance built from existing ADWF lifecycle/runtime evidence.

WEBREF-001 deliberately proves a synthetic/reference consumer only.  It does not
promote downstream capabilities to LIVE_VERIFIED and it does not claim network
sandboxing: ProjectExecution evidence continues to report declaration-only
network enforcement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid

from .consumer_profile import PROFILE_REL, load_consumer_profile, load_effective_config
from .contracts import validate
from .managed_surface import ManagedSurfaceError, plan_adoption, plan_detach
from .managed_surface_transaction import apply_adoption, apply_detach
from .pack_materializer import materialize_project_pack
from .preview_engine import capture_preview
from .project_execution import (
    NETWORK_ENFORCEMENT,
    ProjectExecutionError,
    ProjectExecutionSession,
    load_bound_project_pack,
    validate_execution_evidence,
)
from .strict_json import loads as strict_loads

REPORT_SCHEMA = ".adwf/schemas/reference-conformance-report.schema.json"
REFERENCE_TEMPLATE = ".adwf/reference-consumers/web"
LIMITATIONS = [
    "SHARED_GUARDED_MERGE_NOT_IMPLEMENTED",
    "NETWORK_DECLARATION_ONLY_NOT_ENFORCED",
    "REFERENCE_NOT_LIVE_PROVIDER_EVIDENCE",
    "READOPTION_REQUIRES_DISTINCT_PLAN_IDENTITY",
]


class ReferenceConformanceError(ValueError):
    """Deterministic fail-closed reference conformance blocker."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _object_sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False, timeout=120)
    if proc.returncode:
        raise ReferenceConformanceError("REFERENCE_GIT_FAILED:" + str(args[0]))
    return proc.stdout.strip()


def _git_identity(root: Path) -> tuple[str, str]:
    return _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")


def _git_clean(root: Path) -> bool:
    return not _git(root, "status", "--porcelain=v1", "--untracked-files=all")


def _source_identity(root: Path) -> tuple[str, str]:
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ReferenceConformanceError("REFERENCE_FRAMEWORK_NOT_GIT_TOPLEVEL")
    if not _git_clean(root):
        raise ReferenceConformanceError("REFERENCE_FRAMEWORK_SOURCE_NOT_CLEAN")
    return _git_identity(root)


def _copy_reference_template(template: Path, target: Path) -> list[str]:
    if not template.is_dir():
        raise ReferenceConformanceError("REFERENCE_WEB_TEMPLATE_MISSING")
    paths: list[str] = []
    for source in sorted(template.rglob("*")):
        if source.is_symlink():
            raise ReferenceConformanceError("REFERENCE_WEB_TEMPLATE_SYMLINK_FORBIDDEN")
        if not source.is_file():
            continue
        rel = source.relative_to(template)
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        paths.append(rel.as_posix())
    if "package.json" not in paths or "index.html" not in paths:
        raise ReferenceConformanceError("REFERENCE_WEB_TEMPLATE_INCOMPLETE")
    return paths


def initialize_reference_web_consumer(target: str | Path, template_root: str | Path) -> dict[str, Any]:
    consumer = Path(target).resolve()
    template = Path(template_root).resolve()
    if consumer.exists() and any(consumer.iterdir()):
        raise ReferenceConformanceError("REFERENCE_CONSUMER_ROOT_NOT_EMPTY")
    consumer.mkdir(parents=True, exist_ok=True)
    paths = _copy_reference_template(template, consumer)
    _git(consumer, "init", "-q", "-b", "main")
    _git(consumer, "config", "user.name", "ADWF Reference Conformance")
    _git(consumer, "config", "user.email", "adwf-reference@example.invalid")
    _git(consumer, "config", "core.autocrlf", "false")
    _git(consumer, "add", "--", *paths)
    _git(consumer, "commit", "-q", "-m", "reference web consumer seed")
    head, tree = _git_identity(consumer)
    preservation = {rel: _file_sha(consumer / rel) for rel in paths}
    return {"head": head, "tree": tree, "paths": paths, "preservation": preservation}


def _commit_operational_consumer(consumer: Path) -> tuple[str, str]:
    _git(consumer, "add", "-A")
    status = _git(consumer, "status", "--porcelain=v1", "--untracked-files=all")
    if not status:
        raise ReferenceConformanceError("REFERENCE_OPERATIONAL_COMMIT_EMPTY")
    _git(consumer, "commit", "-q", "-m", "adopt ADWF reference runtime")
    if not _git_clean(consumer):
        raise ReferenceConformanceError("REFERENCE_OPERATIONAL_SOURCE_NOT_CLEAN")
    return _git_identity(consumer)


def _wait_loopback(url: str, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1.5) as response:
                if response.status < 500:
                    body = response.read(4096)
                    if b"ADWF Web Reference Consumer" not in body:
                        raise ReferenceConformanceError("REFERENCE_PREVIEW_CONTENT_MISMATCH")
                    return
        except ReferenceConformanceError:
            raise
        except Exception:
            time.sleep(0.15)
    raise ReferenceConformanceError("REFERENCE_PREVIEW_SERVER_NOT_READY")


def _stop_process(process: subprocess.Popen[str]) -> bool:
    controlled = process.poll() is None
    if not controlled:
        return False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    return True


def _run_gate_execution(consumer: Path, binding: dict[str, Any]) -> dict[str, Any]:
    with ProjectExecutionSession(consumer, consumer, binding, purpose="reference-web-gates") as session:
        for name in ("lint", "unit", "build"):
            entry = binding.get("commands", {}).get(name) or {}
            if entry.get("available") is not True or not entry.get("command"):
                raise ReferenceConformanceError("REFERENCE_GATE_UNAVAILABLE:" + name)
            observation = session.run(name, entry["command"], pack_bound=True, capture_output=True)
            if observation.process.returncode != 0:
                raise ReferenceConformanceError("REFERENCE_GATE_NONZERO:" + name)
            if observation.safety_status != "PASS":
                raise ReferenceConformanceError("REFERENCE_GATE_SAFETY_BLOCK:" + name)
    evidence = session.evidence
    if not isinstance(evidence, dict) or evidence.get("outcome") != "PASS":
        raise ReferenceConformanceError("REFERENCE_GATE_EVIDENCE_NOT_PASS")
    errors = validate_execution_evidence(
        evidence, consumer, expected_head=session.head_sha, expected_pack_digest=binding["pack_digest"]
    )
    if errors:
        raise ReferenceConformanceError("REFERENCE_GATE_EVIDENCE_INVALID:" + errors[0])
    return evidence


def _run_preview_execution(
    consumer: Path,
    binding: dict[str, Any],
    *,
    install_playwright: bool,
    capture_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if capture_mode not in {"LIVE_BROWSER", "SIMULATED_TEST"}:
        raise ReferenceConformanceError("REFERENCE_PREVIEW_CAPTURE_MODE_INVALID")
    preview = binding.get("preview") or {}
    start = (binding.get("commands") or {}).get("start") or {}
    url = preview.get("default_url")
    if start.get("available") is not True or not start.get("command") or not url:
        raise ReferenceConformanceError("REFERENCE_PREVIEW_BINDING_UNAVAILABLE")
    manifest: dict[str, Any] | None = None
    with ProjectExecutionSession(consumer, consumer, binding, purpose="reference-web-preview") as session:
        server = session.popen("start", start["command"], pack_bound=True)
        process_observation = None
        try:
            _wait_loopback(str(url))

            def safe_tool_runner(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
                observation = session.run(
                    "preview-tool", command, pack_bound=False, cwd=cwd, timeout=timeout, capture_output=True
                )
                if observation.safety_status != "PASS":
                    raise ProjectExecutionError("REFERENCE_PREVIEW_TOOL_SAFETY_BLOCK")
                return observation.process

            output = consumer / ".adwf-runtime/reference-conformance/preview" / session.head_sha[:12]
            manifest = capture_preview(
                session.workspace,
                url=str(url),
                head_sha=session.head_sha,
                output_dir=output,
                install=install_playwright,
                command_runner=safe_tool_runner,
                runtime_root=consumer,
            )
        finally:
            controlled = _stop_process(server)
            process_observation = session.record_process(
                "start", start["command"], server, pack_bound=True, expected_termination=controlled
            )
        if process_observation.safety_status != "PASS":
            raise ReferenceConformanceError("REFERENCE_PREVIEW_START_SAFETY_BLOCK")
    evidence = session.evidence
    if not isinstance(manifest, dict):
        raise ReferenceConformanceError("REFERENCE_PREVIEW_MANIFEST_MISSING")
    if not isinstance(evidence, dict) or evidence.get("outcome") != "PASS":
        raise ReferenceConformanceError("REFERENCE_PREVIEW_EXECUTION_EVIDENCE_NOT_PASS")
    errors = validate_execution_evidence(
        evidence, consumer, expected_head=manifest.get("head_sha"), expected_pack_digest=binding["pack_digest"]
    )
    if errors:
        raise ReferenceConformanceError("REFERENCE_PREVIEW_EXECUTION_EVIDENCE_INVALID:" + errors[0])
    source = manifest.get("source_attestation") or {}
    if source.get("verified") is not True or source.get("head_sha") != manifest.get("head_sha"):
        raise ReferenceConformanceError("REFERENCE_PREVIEW_SOURCE_ATTESTATION_INVALID")
    shots = [str(item.get("sha256") or "") for item in manifest.get("screenshots") or []]
    if not shots or any(len(value) != 64 for value in shots):
        raise ReferenceConformanceError("REFERENCE_PREVIEW_SCREENSHOT_DIGEST_MISSING")
    return manifest, evidence


def seal_reference_conformance_report(report: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(report)
    value["report_sha256"] = _object_sha({k: v for k, v in value.items() if k != "report_sha256"})
    return value


def validate_reference_conformance_report(value: dict[str, Any], framework_root: str | Path) -> list[str]:
    root = Path(framework_root).resolve()
    errors: list[str] = []
    try:
        schema = strict_loads((root / REPORT_SCHEMA).read_text(encoding="utf-8"))
    except Exception as exc:
        return ["REFERENCE_CONFORMANCE_SCHEMA_INVALID:" + type(exc).__name__]
    for item in validate(value, schema):
        errors.append(f"SCHEMA:{item.path}:{item.code}")
    expected = _object_sha({k: v for k, v in value.items() if k != "report_sha256"})
    if value.get("report_sha256") != expected:
        errors.append("REFERENCE_CONFORMANCE_REPORT_DIGEST_MISMATCH")
    operational_head = (value.get("consumer") or {}).get("operational_head")
    pack_digest = (value.get("pack") or {}).get("digest")
    gate = value.get("gate_execution") or {}
    preview = value.get("preview") or {}
    if gate.get("head_sha") != operational_head:
        errors.append("REFERENCE_CONFORMANCE_GATE_HEAD_MISMATCH")
    if gate.get("pack_digest") != pack_digest:
        errors.append("REFERENCE_CONFORMANCE_GATE_PACK_MISMATCH")
    if preview.get("head_sha") != operational_head:
        errors.append("REFERENCE_CONFORMANCE_PREVIEW_HEAD_MISMATCH")
    if preview.get("pack_digest") != pack_digest:
        errors.append("REFERENCE_CONFORMANCE_PREVIEW_PACK_MISMATCH")
    effective = value.get("effective_config") or {}
    if effective.get("pack_digest") != pack_digest:
        errors.append("REFERENCE_CONFORMANCE_EFFECTIVE_PACK_MISMATCH")
    if effective.get("runtime_product") is not True or effective.get("project_type") == "framework":
        errors.append("REFERENCE_CONFORMANCE_EFFECTIVE_SELF_HOST_MISMATCH")
    preservation = value.get("preservation") or {}
    if preservation.get("before_sha256") != preservation.get("after_detach_sha256"):
        errors.append("REFERENCE_CONFORMANCE_CONSUMER_PRESERVATION_MISMATCH")
    if (value.get("pack") or {}).get("network_enforcement") != NETWORK_ENFORCEMENT:
        errors.append("REFERENCE_CONFORMANCE_NETWORK_TRUTH_MISMATCH")
    limitations = set(value.get("limitations") or [])
    for required in LIMITATIONS:
        if required not in limitations:
            errors.append("REFERENCE_CONFORMANCE_LIMITATION_MISSING:" + required)
    return list(dict.fromkeys(errors))


def _preservation_digest(root: Path, paths: list[str]) -> str:
    values: dict[str, str] = {}
    for rel in paths:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise ReferenceConformanceError("REFERENCE_CONSUMER_FILE_NOT_PRESERVED:" + rel)
        values[rel] = _file_sha(path)
    return _object_sha(values)


def run_reference_web_conformance(
    framework_root: str | Path,
    *,
    consumer_root: str | Path | None = None,
    template_root: str | Path | None = None,
    install_playwright: bool = False,
    capture_mode: str = "LIVE_BROWSER",
) -> dict[str, Any]:
    framework = Path(framework_root).resolve()
    source_sha, source_tree = _source_identity(framework)
    manifest_path = framework / "MANIFEST.json"
    if not manifest_path.is_file():
        raise ReferenceConformanceError("REFERENCE_FRAMEWORK_MANIFEST_MISSING")
    manifest_sha = _file_sha(manifest_path)
    template = Path(template_root).resolve() if template_root else framework / REFERENCE_TEMPLATE

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if consumer_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="adwf-webref-")
        consumer = Path(temporary.name).resolve()
    else:
        consumer = Path(consumer_root).resolve()
        try:
            consumer.relative_to(framework)
            raise ReferenceConformanceError("REFERENCE_CONSUMER_INSIDE_FRAMEWORK_FORBIDDEN")
        except ValueError:
            pass

    try:
        seed = initialize_reference_web_consumer(consumer, template)
        preservation_before = _object_sha(seed["preservation"])

        adoption_plan = plan_adoption(framework, consumer, source_revision=source_sha)
        if adoption_plan.get("status") != "READY":
            code = (adoption_plan.get("blockers") or ["REFERENCE_ADOPTION_NOT_READY"])[0]
            raise ReferenceConformanceError("REFERENCE_ADOPTION_BLOCK:" + str(code))
        adoption = apply_adoption(framework, consumer, adoption_plan)
        if adoption.get("status") != "COMMITTED":
            raise ReferenceConformanceError("REFERENCE_ADOPTION_NOT_COMMITTED")
        snapshot = copy.deepcopy(adoption["snapshot"])
        snapshot_sha = _object_sha(snapshot)

        profile = materialize_project_pack(
            consumer,
            consumer,
            apply=True,
            product_name="ADWF Reference Web Consumer",
            default_branch="main",
            repository_visibility="PRIVATE",
        )
        if profile.get("status") != "APPLIED" or profile.get("pack") != "react":
            raise ReferenceConformanceError("REFERENCE_PROFILE_OR_PACK_NOT_APPLIED")
        profile_path = consumer / PROFILE_REL
        profile_value = load_consumer_profile(consumer, consumer, required=True)
        if profile_value is None:
            raise ReferenceConformanceError("REFERENCE_PROFILE_MISSING")
        profile_sha = _file_sha(profile_path)
        effective = load_effective_config(consumer, consumer)
        project_identity = effective.get("project") or {}
        pack_projection = effective.get("project_packs") or {}
        if (
            project_identity.get("name") != "ADWF Reference Web Consumer"
            or project_identity.get("type") != "react"
            or project_identity.get("runtime_product") is not True
            or project_identity.get("default_branch") != "main"
            or project_identity.get("repository_visibility") != "PRIVATE"
            or pack_projection.get("selected") != "react"
            or pack_projection.get("selected_digest") != profile.get("pack_digest")
            or pack_projection.get("materialized") is not True
        ):
            raise ReferenceConformanceError("REFERENCE_EFFECTIVE_CONFIG_NOT_CONSUMER_RUNTIME")

        operational_head, operational_tree = _commit_operational_consumer(consumer)
        binding = load_bound_project_pack(consumer, consumer)
        if binding.get("pack") != "react" or binding.get("safety", {}).get("monetary_budget_usd") != 0:
            raise ReferenceConformanceError("REFERENCE_PACK_BINDING_INVALID")
        if binding.get("safety", {}).get("network") != "PACKAGE_REGISTRY_AND_LOOPBACK":
            raise ReferenceConformanceError("REFERENCE_PACK_NETWORK_DECLARATION_INVALID")

        gate_evidence = _run_gate_execution(consumer, binding)
        if gate_evidence.get("head_sha") != operational_head or gate_evidence.get("tree_sha") != operational_tree:
            raise ReferenceConformanceError("REFERENCE_GATE_OPERATIONAL_REVISION_MISMATCH")

        preview_manifest, preview_evidence = _run_preview_execution(
            consumer,
            binding,
            install_playwright=install_playwright,
            capture_mode=capture_mode,
        )
        if preview_manifest.get("head_sha") != operational_head:
            raise ReferenceConformanceError("REFERENCE_PREVIEW_OPERATIONAL_REVISION_MISMATCH")
        if not _git_clean(consumer):
            raise ReferenceConformanceError("REFERENCE_CANONICAL_SOURCE_MUTATED_BY_RUNTIME")

        detach_plan = plan_detach(consumer, snapshot, framework_root=framework)
        if detach_plan.get("status") != "READY":
            code = (detach_plan.get("blockers") or ["REFERENCE_DETACH_NOT_READY"])[0]
            raise ReferenceConformanceError("REFERENCE_DETACH_BLOCK:" + str(code))
        detached = apply_detach(framework, consumer, snapshot, detach_plan)
        if detached.get("status") != "COMMITTED":
            raise ReferenceConformanceError("REFERENCE_DETACH_NOT_COMMITTED")
        if not profile_path.is_file():
            raise ReferenceConformanceError("REFERENCE_PROFILE_REMOVED_BY_DETACH")
        preservation_after = _preservation_digest(consumer, seed["paths"])
        if preservation_after != preservation_before:
            raise ReferenceConformanceError("REFERENCE_CONSUMER_CONTENT_CHANGED_BY_DETACH")

        readoption_plan = plan_adoption(framework, consumer, source_revision=source_sha)
        if readoption_plan.get("status") != "READY":
            code = (readoption_plan.get("blockers") or ["REFERENCE_READOPTION_NOT_READY"])[0]
            raise ReferenceConformanceError("REFERENCE_READOPTION_BLOCK:" + str(code))
        readoption = apply_adoption(framework, consumer, readoption_plan)
        if readoption.get("status") != "COMMITTED":
            raise ReferenceConformanceError("REFERENCE_READOPTION_NOT_COMMITTED")
        if readoption.get("transaction_id") == adoption.get("transaction_id"):
            raise ReferenceConformanceError("REFERENCE_READOPTION_TRANSACTION_IDENTITY_NOT_CHANGED")
        profile_again = materialize_project_pack(
            consumer,
            consumer,
            apply=True,
            product_name="ADWF Reference Web Consumer",
            default_branch="main",
            repository_visibility="PRIVATE",
        )
        if profile_again.get("status") != "ALREADY_MATERIALIZED":
            raise ReferenceConformanceError("REFERENCE_PROFILE_NOT_IDEMPOTENT_AFTER_READOPTION")


        report = {
            "$schema": REPORT_SCHEMA,
            "schema_version": 1,
            "role": "REFERENCE_CONFORMANCE_REPORT",
            "conformance_id": "RCF-" + uuid.uuid4().hex,
            "consumer_class": "STANDARD_WEB",
            "reference_kind": "SYNTHETIC_REFERENCE_CONSUMER",
            "framework": {
                "source_sha": source_sha,
                "source_tree": source_tree,
                "manifest_sha256": manifest_sha,
            },
            "consumer": {
                "seed_head": seed["head"],
                "seed_tree": seed["tree"],
                "operational_head": operational_head,
                "operational_tree": operational_tree,
            },
            "pack": {
                "id": binding["pack"],
                "digest": binding["pack_digest"],
                "network_enforcement": NETWORK_ENFORCEMENT,
            },
            "adoption": {
                "status": adoption["status"],
                "transaction_id": adoption["transaction_id"],
                "snapshot_sha256": snapshot_sha,
            },
            "profile": {"status": profile["status"], "path": PROFILE_REL, "sha256": profile_sha},
            "effective_config": {
                "project_name": project_identity["name"],
                "project_type": project_identity["type"],
                "runtime_product": project_identity["runtime_product"],
                "default_branch": project_identity["default_branch"],
                "repository_visibility": project_identity["repository_visibility"],
                "pack_selected": pack_projection["selected"],
                "pack_digest": pack_projection["selected_digest"],
                "pack_materialized": pack_projection["materialized"],
            },
            "gate_execution": {
                "status": "PASS",
                "head_sha": gate_evidence["head_sha"],
                "pack_digest": gate_evidence["pack_digest"],
                "execution_id": gate_evidence["execution_id"],
                "evidence_sha256": gate_evidence["evidence_sha256"],
            },
            "preview": {
                "status": "PASS",
                "capture_mode": capture_mode,
                "head_sha": preview_manifest["head_sha"],
                "pack_digest": binding["pack_digest"],
                "execution_id": preview_evidence["execution_id"],
                "execution_evidence_sha256": preview_evidence["evidence_sha256"],
                "preview_digest": preview_manifest["preview_digest"],
                "attestation_id": preview_manifest["attestation_id"],
                "screenshot_digests": [item["sha256"] for item in preview_manifest["screenshots"]],
            },
            "detach": {
                "status": detached["status"],
                "transaction_id": detached["transaction_id"],
                "removed_files": int(detached["removed_files"]),
            },
            "readoption": {
                "status": readoption["status"],
                "transaction_id": readoption["transaction_id"],
                "profile_status": profile_again["status"],
                "transaction_identity_changed": readoption["transaction_id"] != adoption["transaction_id"],
            },
            "preservation": {
                "paths": seed["paths"],
                "before_sha256": preservation_before,
                "after_detach_sha256": preservation_after,
                "profile_survived": True,
            },
            "limitations": list(LIMITATIONS),
            "outcome": "PASS",
            "reason_codes": [],
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        sealed = seal_reference_conformance_report(report)
        errors = validate_reference_conformance_report(sealed, framework)
        if errors:
            raise ReferenceConformanceError("REFERENCE_CONFORMANCE_REPORT_INVALID:" + errors[0])
        return sealed
    except (ManagedSurfaceError, ProjectExecutionError, OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, ReferenceConformanceError):
            raise
        raise ReferenceConformanceError(str(exc).split(":", 1)[0]) from exc
    finally:
        if temporary is not None:
            temporary.cleanup()
