#!/usr/bin/env python3
"""Структурная проверка framework; не выдаёт Product Health."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.contracts import validate  # noqa: E402
from lib.strict_json import load as strict_json_load  # noqa: E402
from lib.roadmap_view import validate_roadmap_graph  # noqa: E402


def load(relative: str, errors: list[str]):
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"MISSING:{relative}")
        return {}
    try:
        return strict_json_load(path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"INVALID_JSON:{relative}:{type(exc).__name__}")
        return {}


def main() -> int:
    errors: list[str] = []
    required = [
        "VERSION", "SPECIFICATION.md", "README.md", "AGENTS.md", "ADWS.md", "CONTROL_CENTER.md",
        ".adwf/config.json", ".adwf/project-state.json", ".adwf/state-machine.json",
        ".adwf/providers.json", ".adwf/actions-lock.json", ".adwf/effective-policy.json",
        ".adwf/autonomy-matrix.json", ".adwf/project-layout.json", ".adwf/release-state-machine.json",
        ".adwf/healing-config.json", ".adwf/capabilities.json",
        ".adwf/knowledge/recipes/registry.json", ".adwf/reports/release-evidence.json",
        ".adwf/migrate_v1_2_to_v1_3.py", ".adwf/migrate_v1_3_to_v1_4.py", ".adwf/migrate_v1_4_to_v1_5.py", ".adwf/migrate_v1_5_to_v1_6.py", "CONTROL_CENTER.html",
        ".adwf/pipeline-ir.json", ".adwf/profiles/FREE_PUBLIC_GITHUB.json", ".adwf/scripts/validate_pipeline_ir.py",
        ".adwf/scripts/platform_smoke.py", ".adwf/scripts/github_runtime_sync.py", ".adwf/lib/runtime_supervisor.py",
        ".adwf/lib/work_memory.py", ".adwf/lib/preview_engine.py", ".adwf/lib/github_rulesets.py",
        ".adwf/lib/github_bootstrap.py", ".adwf/lib/github_agent_inbox.py", ".adwf/lib/github_auth.py",
        ".adwf/scripts/publish_agent_request.py", ".adwf/scripts/consume_agent_result.py", ".adwf/scripts/github_metrics_collector.py", ".adwf/scripts/generate_pipeline.py",
        ".adwf/scripts/publish_trusted_gate.py", ".adwf/scripts/run_preview.py", ".adwf/scripts/collect_preview_attestation.py", ".adwf/scripts/reference_delivery.py", ".adwf/scripts/release.py",
        ".adwf/lib/action_executors.py", ".adwf/lib/trust_boundary.py", ".adwf/lib/github_provider.py", ".adwf/lib/github_readback.py",
        ".adwf/lib/github_runtime_store.py", ".adwf/lib/owner_authority.py", ".adwf/lib/owner_intent_service.py", ".adwf/lib/durable_projection.py",
        ".adwf/lib/controller_wakeup.py", ".adwf/lib/pack_materializer.py", ".adwf/lib/performance_evidence.py", ".adwf/lib/delivery_adapters.py",
        ".adwf/lib/release_transaction.py", ".adwf/lib/preview_engine.py", ".adwf/capability-traceability.json", ".adwf/scripts/validate_capabilities.py", ".adwf/roadmap.json",
        ".adwf/lib/skill_layer.py", ".adwf/scripts/validate_skills.py", ".adwf/scripts/generate_skill_registry.py", ".adwf/scripts/eval_skills.py", ".adwf/scripts/vendor_skill.py",
        ".adwf/schemas/skill.schema.json", ".adwf/schemas/skill-eval.schema.json", ".adwf/schemas/skill-registry.schema.json", ".adwf/schemas/skill-legacy-allowlist.schema.json", ".adwf/skill-legacy-allowlist.json",
        ".github/workflows/adwf-pr.yml", ".github/workflows/adwf-main.yml",
        ".github/workflows/adwf-control.yml", ".github/workflows/adwf-platform-smoke.yml", ".gitlab-ci.yml",
    ]
    errors.extend(f"MISSING:{item}" for item in required if not (ROOT / item).is_file())
    pairs = [
        (".adwf/config.json", ".adwf/schemas/config.schema.json"),
        (".adwf/project-state.json", ".adwf/schemas/project-state.schema.json"),
        (".adwf/providers.json", ".adwf/schemas/providers.schema.json"),
        (".adwf/project-layout.json", ".adwf/schemas/project-layout.schema.json"),
        (".adwf/healing-config.json", ".adwf/schemas/healing-config.schema.json"),
        (".adwf/knowledge/recipes/registry.json", ".adwf/schemas/repair-recipe.schema.json"),
        (".adwf/reports/release-evidence.json", ".adwf/schemas/release-evidence.schema.json"),
        (".adwf/roadmap.json", ".adwf/schemas/roadmap.schema.json"),
        (".adwf/capability-traceability.json", ".adwf/schemas/capability-traceability.schema.json"),
        (".adwf/skill-legacy-allowlist.json", ".adwf/schemas/skill-legacy-allowlist.schema.json"),
    ]
    for data_name, schema_name in pairs:
        data = load(data_name, errors)
        schema = load(schema_name, errors)
        errors.extend(f"SCHEMA:{data_name}:{item.path}:{item.code}" for item in validate(data, schema))
    roadmap = load(".adwf/roadmap.json", errors)
    roadmap_tasks = [task for goal in (roadmap.get("goals") or []) for task in (goal.get("tasks") or [])]
    graph = validate_roadmap_graph(roadmap_tasks)
    errors.extend("ROADMAP_GRAPH:" + item for item in graph["errors"])
    version=(ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").is_file() else ""
    machine = load(".adwf/state-machine.json", errors)
    config = load(".adwf/config.json", errors)
    state = load(".adwf/project-state.json", errors)
    ir = load(".adwf/pipeline-ir.json", errors)
    preview_package = load(".adwf/preview/package.json", errors)
    if not version or any(x != version for x in (machine.get("version"),config.get("framework_version"),state.get("framework_version"),ir.get("framework_version"),preview_package.get("version"))):
        errors.append("FRAMEWORK_VERSION_CROSS_FILE_DRIFT")
    for policy_name in ("evidence","orchestration","reality","roadmap-quality","trust-boundary"):
        policy=load(f".adwf/policies/{policy_name}.json",errors)
        if policy.get("version") != version: errors.append(f"POLICY_VERSION_DRIFT:{policy_name}")
    process = subprocess.run([sys.executable, str(ROOT / ".adwf/scripts/validate_ci.py")], cwd=ROOT, capture_output=True, text=True, check=False)
    if process.returncode:
        errors.extend(line for line in process.stdout.splitlines() if line.startswith("- "))
    for script in ("compile_policy.py", "generate_labels.py", "docs_freshness.py", "validate_docs.py", "validate_pipeline_ir.py", "validate_capabilities.py", "validate_skills.py"):
        process = subprocess.run([sys.executable, str(ROOT / ".adwf/scripts" / script)], cwd=ROOT, capture_output=True, text=True, check=False)
        if process.returncode:
            errors.append(f"GENERATED_OR_FRESHNESS_CHECK_FAILED:{script}")
    skill_registry = subprocess.run([sys.executable, str(ROOT / ".adwf/scripts/generate_skill_registry.py"), "--check"], cwd=ROOT, capture_output=True, text=True, check=False)
    if skill_registry.returncode:
        errors.append("SKILL_REGISTRY_STALE_OR_INVALID")
    manifest = subprocess.run([sys.executable, str(ROOT / ".adwf/scripts/generate_manifest.py"), "--check"], cwd=ROOT, capture_output=True, text=True, check=False)
    if manifest.returncode:
        errors.append("MANIFEST_OR_SHA256SUMS_STALE")
    if errors:
        print("ADWF STRUCTURE: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("ADWF STRUCTURE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
