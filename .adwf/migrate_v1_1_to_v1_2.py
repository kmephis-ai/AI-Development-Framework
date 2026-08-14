#!/usr/bin/env python3
"""Транзакционная миграция v1.1 → v1.2 с проверяемым rollback."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile

ADWF_HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(ADWF_HOME))
from lib.contracts import validate  # noqa: E402

TARGET_VERSION = "1.2.1"


def validate_v1_2_migration_output(config: dict, state: dict) -> list[str]:
    """Historical invariant check.

    The live schemas belong to v1.3, so a v1.2 intermediate must not be
    validated against them. The original v1.2 package remains the normative
    schema source; these safety invariants keep the chained migration usable.
    """
    findings: list[str] = []
    if config.get("framework_version") != TARGET_VERSION or state.get("framework_version") != TARGET_VERSION:
        findings.append("VERSION")
    if config.get("policy", {}).get("fail_mode") != "CLOSED":
        findings.append("FAIL_MODE")
    if config.get("policy", {}).get("active_autonomy") not in {"A0", "A1"}:
        findings.append("ACTIVE_AUTONOMY")
    if config.get("cost", {}).get("monetary_budget") != 0:
        findings.append("MONETARY_BUDGET")
    if config.get("ci", {}).get("mandatory_ai_api") is not False:
        findings.append("MANDATORY_AI_API")
    if int(state.get("orchestration", {}).get("writers_active", -1)) != 0:
        findings.append("WRITERS_ACTIVE")
    if state.get("health", {}).get("product") != "NOT_VERIFIED":
        findings.append("PRODUCT_HEALTH")
    return findings


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def command_gate(old: dict, name: str, phases: list[str], warnings: list[str]) -> dict:
    gate = old.get("commands", {}).get(name, {})
    command = gate.get("command", [])
    if isinstance(command, str) and command.strip():
        warnings.append(f"STRING_COMMAND_DISABLED:{name}")
        command = []
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        command = []
    return {"required": gate.get("required") is True, "command": command, "phases": phases}


def migrated_config(old: dict, warnings: list[str]) -> dict:
    old_policy = old.get("policy", {})
    requested = old_policy.get("default_autonomy", old_policy.get("requested_autonomy", "A1"))
    if requested not in {"A0", "A1", "A2", "A3", "A4"}:
        requested = "A1"
    active = requested if requested in {"A0", "A1"} else "A1"
    old_ceiling = old_policy.get("max_autonomous_risk", "R1")
    safe_ceiling = old_ceiling if old_ceiling in {"R0", "R1"} else "R1"
    return {
        "$schema": "./schemas/config.schema.json",
        "framework_version": "1.2.1",
        "schema_version": 3,
        "profile": old.get("profile", "FREE_PRIVATE"),
        "language": {"human_facing": "ru", "machine_facing": "en"},
        "project": {
            "name": old.get("project", {}).get("name", "CHANGE_ME"),
            "default_branch": old.get("project", {}).get("default_branch", "main"),
            "type": old.get("project", {}).get("type", "generic"),
            "runtime_product": old.get("project", {}).get("runtime_product", old.get("project", {}).get("type") not in {"framework", "docs", "documentation", "library"}),
        },
        "provider": {"mode": "local", "secondary_read_only": None, "secondary_write_enabled": False},
        "policy": {
            "fail_mode": "CLOSED", "requested_autonomy": requested,
            "active_autonomy": active, "max_autonomous_risk": safe_ceiling,
            "a4_automatic": False, "main_policy": "PR_ONLY", "independent_review": True,
            "runtime_truth_required": True, "trust_changes_human_gated": True,
            "destructive_actions_human_gated": True,
        },
        "orchestration": {
            "enabled": True, "max_parallel_writers": 1, "one_roadmap_id_one_issue": True,
            "merge_integration": "SERIALIZED", "lease_ttl_minutes": min(int(old.get("orchestration", {}).get("lease_ttl_minutes", 120)), 240),
            "heartbeat_minutes": 30, "reconcile_ttl_minutes": 60, "conflict_domains_required": True,
            "continue_automatically": True, "stop_only_on_roadmap_end_or_hard_blocker": True,
        },
        "workspace": {"root": ".adwf-runtime/workspaces", "strategy": "git_worktree", "max_active": 1,
                      "stall_timeout_minutes": 45, "retry_base_seconds": 30, "retry_max_seconds": 900,
                      "max_retries": 3, "require_clean_cleanup": True},
        "reality": {
            "baseline_certification_required": True, "golden_paths_required_for_product_projects": True,
            "required_product_gates": ["smoke", "golden_paths"],
            "reality_check_every_significant_prs": int(old.get("reality", {}).get("reality_check_every_significant_prs", 5)),
            "reality_check_ttl_hours": int(old.get("reality", {}).get("reality_check_ttl_hours", 168)),
            "block_feature_work_on_broken_or_unknown_product": True,
        },
        "roadmap_quality": {
            "enabled": True,
            "verification_gap_warn": float(old.get("roadmap_quality", {}).get("verification_gap_warn", 0.15)),
            "verification_gap_block": float(old.get("roadmap_quality", {}).get("verification_gap_block", 0.30)),
            "false_progress_detection": True, "issue_sizing": True,
            "architecture_drift_detection": True, "technical_debt_budget": True,
        },
        "runtime": {"node_major": 24, "python_exact": "3.12.13", "enforce_node_for_node_projects": True},
        "commands": {
            "lint": command_gate(old, "lint", ["pr"], warnings),
            "unit": command_gate(old, "unit", ["pr", "main"], warnings),
            "integration": command_gate(old, "integration", ["main"], warnings),
            "build": command_gate(old, "build", ["pr", "main"], warnings),
            "smoke": command_gate(old, "smoke", ["runtime"], warnings),
            "golden_paths": command_gate(old, "golden_paths", ["runtime"], warnings),
            "e2e": command_gate(old, "e2e", ["runtime"], warnings),
        },
        "ci": {"default_executor": "SELF_HOSTED", "mandatory_ai_api": False, "timeout_minutes": 15,
               "cancel_superseded": True, "artifact_policy": "FAILURE_ONLY", "artifact_retention_days": 1,
               "cache_policy": "LOCKFILE_KEYED", "minimum_actions_runner": "2.329.0", "separate_trust_domains": True,
               "untrusted_runner_labels": ["self-hosted", "linux", "x64", "adwf-untrusted-ephemeral"],
               "main_runner_labels": ["self-hosted", "linux", "x64", "adwf-main-ephemeral"],
               "trusted_runner_labels": ["self-hosted", "linux", "x64", "adwf-trusted"]},
        "cost": {"mode": "FREE_ONLY", "monetary_budget": 0, "unknown_provider": "BLOCK",
                 "potentially_paid_provider": "BLOCK", "registry": ".adwf/providers.json",
                 "quota_evidence_ttl_minutes": 60, "default_ci_capability": "local_deterministic"},
        "github": {"project": {"enabled": False, "owner": None, "number": None, "dashboard_issue_number": None,
                               "token_secret": "ADWF_PROJECT_TOKEN"},
                   "notifications": {"email_required": False, "channels": ["checks", "control-center"]}},
        "gitlab": {"available": True, "untrusted_runner_tags": ["self-hosted", "adwf-untrusted-ephemeral"],
                   "main_runner_tags": ["self-hosted", "adwf-main-ephemeral"],
                   "trusted_runner_tags": ["self-hosted", "adwf-trusted"], "shared_runner_quota_allowed": False},
        "docs": {"human_language": "ru", "control_center": "CONTROL_CENTER.md", "freshness_registry": ".adwf/docs-registry.json"},
    }


def migrated_state(old: dict, config: dict) -> dict:
    progress = old.get("progress", {})
    return {
        "$schema": "./schemas/project-state.schema.json", "framework_version": "1.2.1", "schema_version": 3,
        "project": {"name": config["project"]["name"], "default_branch": config["project"]["default_branch"]},
        "provider": {"mode": config["provider"]["mode"], "observed_at": None},
        "profile": config["profile"], "status": "BOOTSTRAP", "autonomy_level": config["policy"]["active_autonomy"],
        "risk_ceiling": config["policy"]["max_autonomous_risk"],
        "health": {"product": "NOT_VERIFIED", "roadmap": "NOT_VERIFIED", "architecture": "NOT_VERIFIED", "security": "NOT_VERIFIED", "debt": "NOT_VERIFIED", "adwf": "NOT_VERIFIED"},
        "progress": {
            "implementation": float(progress.get("implementation", 0)), "verification": float(progress.get("verification", 0)),
            "product_readiness": float(progress.get("product_readiness", 0)), "verification_gap": float(progress.get("verification_gap", 0)),
        },
        "main": {"head": old.get("main", {}).get("head") if old.get("main", {}).get("head") not in {"UNSET", ""} else None, "health": "NOT_VERIFIED"},
        "runtime": {"canonical_revision": None, "smoke": "NOT_VERIFIED", "golden_paths": "NOT_VERIFIED", "last_reality_check": None},
        "orchestration": {"writers_active": 0, "reviewers_active": 0, "leases_active": 0, "conflicts": 0, "merge_train": "IDLE"},
        "queue": {"ready": 0, "in_progress": 0, "review": 0, "blocked": 0, "human_required": 0},
        "work_items": [],
        "active": {"roadmap_id": None, "issue": None, "pr": None, "branch": None, "writer": None, "lease_id": None, "state": None},
        "workspace": {"status": "NOT_CONFIGURED", "workspace_id": None, "heartbeat_at": None, "expires_at": None, "retry_count": 0, "next_retry_at": None},
        "snapshot": {"observed_at": None, "valid_until": None, "source_main_sha": None, "evidence_digest": None},
        "ci_metrics": {"status": "NOT_VERIFIED", "observed_at": None, "runs": 0, "p50_duration_seconds": None,
                       "p95_duration_seconds": None, "p95_time_to_first_failure_seconds": None,
                       "p95_queue_seconds": None, "flake_rate": None},
        "cost_usage": {"status": "NOT_VERIFIED", "capability": None, "observed_at": None,
                       "hosted_minutes_used": None, "hosted_minutes_internal_hard": None,
                       "artifact_mb": None, "cache_mb": None},
        "project_projection": {"status": "N/A", "observed_at": None, "project_id": None, "item_id": None},
        "incident_knowledge": {"status": "NOT_CONFIGURED", "incident_count": 0, "open_count": 0,
                               "repeated_count": 0, "latest_incident_id": None, "store_digest": None},
        "safe_healing": {"status": "NOT_CONFIGURED", "level": None, "last_decision": None,
                         "circuit_open": False, "active_recipe": None},
        "owner_experience": {
            "product_brief": {"status": "NOT_CONFIGURED", "brief_id": None, "goal_ru": None},
            "current_preview": {"status": "NOT_VERIFIED", "head_sha": None, "preview_digest": None, "url": None},
            "acceptance": {"status": "PENDING", "brief_id": None, "head_sha": None,
                           "preview_digest": None, "decided_at": None, "decided_by": None,
                           "note_ru": "", "stale_reason": None},
            "release_summary_ru": None,
        },
        "owner_decisions": [],
        "release": {"latest": None, "commit": None, "health": "NOT_VERIFIED"},
        "gates": {"ci": "NOT_VERIFIED", "review": "NOT_VERIFIED", "docs": "NOT_VERIFIED", "smoke": "NOT_VERIFIED", "reality": "NOT_VERIFIED", "roadmap_quality": "NOT_VERIFIED"},
        "blockers": ["После миграции требуется Baseline, reconciliation и свежее runtime evidence."],
        "last_reconciled_at": None, "last_verified_at": None,
    }


def patched_1_2_config(old: dict) -> dict:
    value = copy.deepcopy(old)
    value["framework_version"] = TARGET_VERSION
    value.setdefault("policy", {})["active_autonomy"] = "A1"
    if value["policy"].get("requested_autonomy") not in {"A1", "A2"}:
        value["policy"]["requested_autonomy"] = "A2"
    project = value.setdefault("project", {})
    project.setdefault("runtime_product", project.get("type") not in {"framework", "docs", "documentation", "library"})
    value.setdefault("commands", {}).setdefault("golden_paths", {"required": False, "command": [], "phases": ["runtime"]})
    return value


def patched_1_2_state(old: dict) -> dict:
    value = copy.deepcopy(old)
    value["framework_version"] = TARGET_VERSION
    value.setdefault("health", {}).setdefault("security", "NOT_VERIFIED")
    value.setdefault("work_items", [])
    value.setdefault("active", {}).setdefault("state", None)
    value.setdefault("workspace", {}).setdefault("expires_at", None)
    value.setdefault("project_projection", {"status": "N/A", "observed_at": None, "project_id": None, "item_id": None})
    value.setdefault("incident_knowledge", {"status": "NOT_CONFIGURED", "incident_count": 0, "open_count": 0,
                                             "repeated_count": 0, "latest_incident_id": None, "store_digest": None})
    value.setdefault("safe_healing", {"status": "NOT_CONFIGURED", "level": None, "last_decision": None,
                                      "circuit_open": False, "active_recipe": None})
    value.setdefault("owner_experience", {
        "product_brief": {"status": "NOT_CONFIGURED", "brief_id": None, "goal_ru": None},
        "current_preview": {"status": "NOT_VERIFIED", "head_sha": None, "preview_digest": None, "url": None},
        "acceptance": {"status": "PENDING", "brief_id": None, "head_sha": None, "preview_digest": None,
                       "decided_at": None, "decided_by": None, "note_ru": "", "stale_reason": None},
        "release_summary_ru": None,
    })
    return value


def backup(root: Path, files: list[Path], name: str) -> Path:
    directory = root / ".adwf/migrations" / name
    directory.mkdir(parents=True, exist_ok=False)
    entries = []
    for source in files:
        destination = directory / source.name
        shutil.copy2(source, destination)
        entries.append({"path": str(source.relative_to(root)), "backup": destination.name, "sha256": sha256(destination)})
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "entries": entries}
    atomic_json(directory / "manifest.json", manifest)
    return directory / "manifest.json"


def restore(root: Path, manifest_path: Path) -> None:
    migration_root = (root / ".adwf/migrations").resolve()
    if migration_root not in manifest_path.resolve().parents:
        raise ValueError("ROLLBACK_MANIFEST_OUTSIDE_MIGRATIONS")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets: list[Path] = []
    for entry in manifest.get("entries", []):
        if Path(str(entry.get("backup", ""))).name != entry.get("backup"):
            raise ValueError("BACKUP_NAME_INVALID")
        target = (root / str(entry.get("path", ""))).resolve()
        if root.resolve() not in target.parents:
            raise ValueError("ROLLBACK_TARGET_OUTSIDE_ROOT")
        targets.append(target)
        source = manifest_path.parent / entry["backup"]
        if not source.is_file() or sha256(source) != entry["sha256"]:
            raise ValueError(f"BACKUP_HASH_INVALID:{entry['path']}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup(root, targets, f"{stamp}-pre-rollback")
    for entry, target in zip(manifest["entries"], targets):
        raw = json.loads((manifest_path.parent / entry["backup"]).read_text(encoding="utf-8"))
        atomic_json(target, raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    config_path = root / ".adwf/config.json"
    state_path = root / ".adwf/project-state.json"
    if args.rollback:
        manifest = Path(args.rollback).resolve()
        if not args.apply:
            print(f"ROLLBACK DRY-RUN: {manifest}")
            return 0
        restore(root, manifest)
        print("ROLLBACK APPLIED. Повторно запустите doctor/self-test исходной версии.")
        return 0
    old_config = json.loads(config_path.read_text(encoding="utf-8"))
    old_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    current_version = str(old_config.get("framework_version", ""))
    if current_version == TARGET_VERSION:
        print("ALREADY_V1_2: повторная миграция не требуется.")
        return 0
    if int(old_state.get("orchestration", {}).get("writers_active", 0)) > 0:
        raise SystemExit("ACTIVE_WRITER_BLOCKS_MIGRATION: сначала reconcile/handoff")
    warnings: list[str] = []
    if current_version == "1.2.0":
        new_config = patched_1_2_config(old_config)
        new_state = patched_1_2_state(old_state)
        migration_name = "v1.2.0-to-v1.2.1"
        print("План patch: backup → v1.2.1 fields → schema verification → rollback point.")
    elif current_version.startswith("1.1"):
        new_config = migrated_config(old_config, warnings)
        new_state = migrated_state(old_state, new_config)
        migration_name = "v1.1-to-v1.2"
        print("План: backup → schema 3 → active autonomy A1 → Node 24 → self-hosted CI → все evidence NOT_VERIFIED.")
    else:
        raise SystemExit("Ожидалась конфигурация ADWF v1.1 или v1.2.0")
    for warning in warnings:
        print(f"Внимание: {warning}")
    if not args.apply:
        print("DRY-RUN. Для применения добавьте --apply.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = backup(root, [config_path, state_path], f"{stamp}-{migration_name}")
    try:
        atomic_json(config_path, new_config)
        atomic_json(state_path, new_state)
        findings = validate_v1_2_migration_output(
            json.loads(config_path.read_text(encoding="utf-8")),
            json.loads(state_path.read_text(encoding="utf-8")),
        )
        if findings:
            raise ValueError("POST_VERIFY_FAILED:" + ",".join(findings))
    except Exception:
        restore(root, manifest)
        raise
    print(f"APPLIED. Rollback manifest: {manifest}")
    print("До Baseline/reconciliation/autonomy certification feature progression остаётся заблокирован.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
