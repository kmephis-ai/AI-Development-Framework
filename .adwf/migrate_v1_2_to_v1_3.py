#!/usr/bin/env python3
"""Transactional ADWF v1.2.x -> v1.3.0 migration with verified rollback."""
from __future__ import annotations

from pathlib import Path
import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone

ADWF_HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(ADWF_HOME))
from lib.contracts import validate  # noqa: E402
from lib.policy_compiler import compile_policy  # noqa: E402
from migrate_v1_1_to_v1_2 import atomic_json, backup, restore  # noqa: E402

TARGET_VERSION = "1.3.0"
SOURCE_VERSIONS = {"1.2.0", "1.2.1"}
ACTIVE_RUN_STATUSES = {"RUNNING", "RETRY_WAIT", "RECOVERY", "HUMAN_REQUIRED"}
OCCUPYING_WORKSPACES = {"ACTIVE", "STALLED", "RETRY_WAIT", "RETRY_READY", "RECOVERY"}
ACTIVE_ITEM_STATES = {"CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY"}
SHA = re.compile(r"^[0-9a-f]{40}$")


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _safe_source(config: dict) -> None:
    if config.get("cost", {}).get("monetary_budget") != 0:
        raise ValueError("SOURCE_ZERO_BUDGET_NOT_VERIFIED")
    if config.get("policy", {}).get("fail_mode") != "CLOSED":
        raise ValueError("SOURCE_FAIL_CLOSED_NOT_VERIFIED")
    if config.get("ci", {}).get("mandatory_ai_api") is not False:
        raise ValueError("SOURCE_MANDATORY_AI_API_NOT_FALSE")


def migrated_config(old: dict) -> dict:
    _safe_source(old)
    value = copy.deepcopy(old)
    value["$schema"] = "./schemas/config.schema.json"
    value["framework_version"] = TARGET_VERSION
    value["schema_version"] = 3
    project = value.setdefault("project", {})
    project.setdefault("repository_visibility", "PRIVATE")
    policy = value.setdefault("policy", {})
    requested = policy.get("requested_autonomy", "A1")
    if requested not in {"A0", "A1", "A2", "A3", "A4"}:
        requested = "A1"
    policy["requested_autonomy"] = requested
    policy["active_autonomy"] = policy.get("active_autonomy") if policy.get("active_autonomy") in {"A0", "A1"} else "A1"
    policy["max_autonomous_risk"] = policy.get("max_autonomous_risk") if policy.get("max_autonomous_risk") in {"R0", "R1"} else "R1"
    policy.update({
        "fail_mode": "CLOSED", "a4_automatic": False, "main_policy": "PR_ONLY",
        "independent_review": True, "runtime_truth_required": True,
        "trust_changes_human_gated": True, "destructive_actions_human_gated": True,
    })
    ci = value.setdefault("ci", {})
    ci.update({
        "mandatory_ai_api": False, "larger_runners_allowed": False,
        "failure_artifacts_upload_only_on_failure": True,
        "failure_artifact_max_days": 1,
    })
    cost = value.setdefault("cost", {})
    cost.update({
        "mode": "FREE_ONLY", "monetary_budget": 0, "unknown_provider": "BLOCK",
        "potentially_paid_provider": "BLOCK",
        "allowed_capability_statuses": [
            "FREE_VERIFIED", "INCLUDED_QUOTA", "CONDITIONAL_FREE", "OWNER_PROVIDED",
        ],
        "stale_capability": "BLOCK", "owner_provided_requires_attestation": True,
    })
    github = value.setdefault("github", {})
    github.setdefault("project", {
        "enabled": False, "owner": None, "number": None,
        "dashboard_issue_number": None, "token_secret": "ADWF_PROJECT_TOKEN",
    })
    github.setdefault("notifications", {"email_required": False, "channels": ["checks", "control-center"]})
    github["trust"] = {
        "required_check_names": ["fast-feedback"],
        "trusted_check_app_slugs": ["github-actions"],
        "trusted_reviewer_logins": [],
        "check_ttl_hours": 24,
        "review_ttl_hours": 168,
    }
    return value


def _incident_defaults() -> dict:
    return {
        "status": "NOT_CONFIGURED", "incident_count": 0, "open_count": 0,
        "repeated_count": 0, "latest_incident_id": None, "store_digest": None,
    }


def _healing_defaults() -> dict:
    return {
        "status": "NOT_CONFIGURED", "level": None, "last_decision": None,
        "circuit_open": False, "active_recipe": None,
    }


def _owner_defaults() -> dict:
    return {
        "product_brief": {"status": "NOT_CONFIGURED", "brief_id": None, "goal_ru": None},
        "current_preview": {"status": "NOT_VERIFIED", "head_sha": None, "preview_digest": None, "url": None},
        "acceptance": {
            "status": "PENDING", "brief_id": None, "head_sha": None,
            "preview_digest": None, "decided_at": None, "decided_by": None,
            "note_ru": "", "stale_reason": None,
        },
        "release_summary_ru": None,
    }


def migrated_state(old: dict, config: dict) -> dict:
    value = copy.deepcopy(old)
    value["$schema"] = "./schemas/project-state.schema.json"
    value["framework_version"] = TARGET_VERSION
    value["schema_version"] = 3
    value["profile"] = config["profile"]
    value["autonomy_level"] = config["policy"]["active_autonomy"]
    value["risk_ceiling"] = config["policy"]["max_autonomous_risk"]
    value["status"] = "BOOTSTRAP"
    health = value.setdefault("health", {})
    for name in ("product", "roadmap", "architecture", "security", "debt", "adwf"):
        health[name] = "NOT_VERIFIED"
    main = value.setdefault("main", {})
    if SHA.fullmatch(str(main.get("head") or "")) is None:
        main["head"] = None
    main["health"] = "NOT_VERIFIED"
    value["runtime"] = {
        "canonical_revision": None, "smoke": "NOT_VERIFIED",
        "golden_paths": "NOT_VERIFIED", "last_reality_check": None,
    }
    value["snapshot"] = {
        "observed_at": None, "valid_until": None,
        "source_main_sha": None, "evidence_digest": None,
    }
    value["incident_knowledge"] = _incident_defaults()
    value["safe_healing"] = _healing_defaults()
    value["owner_experience"] = _owner_defaults()
    value["owner_decisions"] = []
    for gate in value.setdefault("gates", {}):
        value["gates"][gate] = "NOT_VERIFIED"
    value["blockers"] = [
        "Миграция завершена; до нового Baseline, reconciliation и exact-SHA evidence продвижение заблокировано.",
        "Для merge через GitHub добавьте разрешённые логины владельцев в github.trust.trusted_reviewer_logins.",
    ]
    value["last_reconciled_at"] = None
    value["last_verified_at"] = None
    return value


def migration_blockers(root: Path, state: dict) -> list[str]:
    blockers: list[str] = []
    if int(state.get("orchestration", {}).get("writers_active", 0)) > 0:
        blockers.append("ACTIVE_WRITER")
    active_items = [
        str(item.get("id") or "UNKNOWN") for item in state.get("work_items", [])
        if isinstance(item, dict) and item.get("state") in ACTIVE_ITEM_STATES
    ]
    if active_items:
        blockers.append("ACTIVE_WORK_ITEMS:" + ",".join(active_items))
    orchestration_dir = root / ".adwf-runtime/orchestration"
    if orchestration_dir.is_dir():
        for path in orchestration_dir.glob("*.json"):
            try:
                if _json(path).get("status") in ACTIVE_RUN_STATUSES:
                    blockers.append("ACTIVE_DURABLE_RUN:" + path.stem)
            except (OSError, ValueError, json.JSONDecodeError):
                blockers.append("DURABLE_RUN_UNREADABLE:" + path.stem)
    workspace_path = root / ".adwf-runtime/workspaces.json"
    if workspace_path.is_file():
        try:
            workspaces = _json(workspace_path).get("workspaces", [])
            if any(isinstance(item, dict) and item.get("status") in OCCUPYING_WORKSPACES for item in workspaces):
                blockers.append("ACTIVE_OR_UNRECOVERED_WORKSPACE")
        except (OSError, ValueError, json.JSONDecodeError):
            blockers.append("WORKSPACE_REGISTRY_UNREADABLE")
    return blockers


def _schema_findings(config: dict, state: dict) -> list[str]:
    """Frozen v1.3 invariants; deliberately independent from current v1.4 schemas."""
    findings=[]
    if config.get("framework_version") != TARGET_VERSION or config.get("schema_version") != 3:
        findings.append("CONFIG:VERSION")
    if state.get("framework_version") != TARGET_VERSION or state.get("schema_version") != 3:
        findings.append("STATE:VERSION")
    if config.get("cost",{}).get("monetary_budget") != 0: findings.append("CONFIG:BUDGET")
    if config.get("policy",{}).get("fail_mode") != "CLOSED": findings.append("CONFIG:FAIL_MODE")
    if config.get("ci",{}).get("mandatory_ai_api") is not False: findings.append("CONFIG:AI_API")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.rollback:
        manifest = Path(args.rollback).resolve()
        if not args.apply:
            print(f"ROLLBACK DRY-RUN: {manifest}")
            return 0
        restore(root, manifest)
        print("ROLLBACK APPLIED. Используйте команды проверки исходной версии.")
        return 0

    config_path = root / ".adwf/config.json"
    state_path = root / ".adwf/project-state.json"
    policy_path = root / ".adwf/effective-policy.json"
    if not all(path.is_file() for path in (config_path, state_path, policy_path)):
        raise SystemExit("MIGRATION_SOURCE_FILES_MISSING")
    old_config, old_state = _json(config_path), _json(state_path)
    version = str(old_config.get("framework_version") or "")
    if version == TARGET_VERSION:
        print("ALREADY_V1_3: повторная миграция не требуется.")
        return 0
    if version not in SOURCE_VERSIONS or str(old_state.get("framework_version") or "") not in SOURCE_VERSIONS:
        raise SystemExit("EXPECTED_ADWF_V1_2_SOURCE")
    blockers = migration_blockers(root, old_state)
    if blockers:
        raise SystemExit("MIGRATION_BLOCKED:" + ",".join(blockers))
    try:
        new_config = migrated_config(old_config)
        new_state = migrated_state(old_state, new_config)
    except ValueError as exc:
        raise SystemExit(f"MIGRATION_SOURCE_UNSAFE:{exc}") from exc
    findings = _schema_findings(new_config, new_state)
    if findings:
        raise SystemExit("MIGRATION_PLAN_INVALID:" + ",".join(findings))
    print("План: backup → v1.3 contracts → autonomy не выше A1 → evidence/acceptance NOT_VERIFIED → Effective Policy rebuild.")
    if not args.apply:
        print("DRY-RUN. Для применения добавьте --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = backup(root, [config_path, state_path, policy_path], f"{stamp}-v1.2-to-v1.3")
    try:
        atomic_json(config_path, new_config)
        atomic_json(state_path, new_state)
        legacy_policy = _json(policy_path)
        legacy_policy["framework_version"] = TARGET_VERSION
        legacy_policy["profile"] = new_config.get("profile")
        legacy_policy["canonical_provider"] = new_config.get("provider", {}).get("mode")
        legacy_policy["active_autonomy"] = new_config.get("policy", {}).get("active_autonomy")
        legacy_policy["max_autonomous_risk"] = new_config.get("policy", {}).get("max_autonomous_risk")
        atomic_json(policy_path, legacy_policy)
        findings = _schema_findings(_json(config_path), _json(state_path))
        if findings:
            raise ValueError("POST_VERIFY_FAILED:" + ",".join(findings))
    except Exception:
        restore(root, manifest)
        raise
    print(f"APPLIED. Rollback manifest: {manifest}")
    print("Следующий шаг: настройте trusted reviewers, затем запустите compile_policy, manifest и doctor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
