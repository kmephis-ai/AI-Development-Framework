#!/usr/bin/env python3
"""Точная синхронизация одного Issue/PR с GitHub Project v2 через реальные gh IDs."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.dashboard import next_operator_action, snapshot_status  # noqa: E402
from lib.health import active_state_path, doctor  # noqa: E402

READBACK_QUERY = """
query($item: ID!) {
  node(id: $item) {
    ... on ProjectV2Item {
      project { id }
      fieldValues(first: 100) {
        nodes {
          __typename
          ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
        }
      }
    }
  }
}
"""


def run(command: list[str]) -> str:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        raise ValueError(f"GH_COMMAND_FAILED:{command[1]}:{(process.stderr or process.stdout).strip()[:300]}")
    return process.stdout


def project_context(owner: str, number: int) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    project = json.loads(run(["gh", "project", "view", str(number), "--owner", owner, "--format", "json"]))
    fields_raw = json.loads(run(["gh", "project", "field-list", str(number), "--owner", owner, "--format", "json"]))
    items_raw = json.loads(run(["gh", "project", "item-list", str(number), "--owner", owner, "--limit", "1000", "--format", "json"]))
    fields = fields_raw.get("fields", fields_raw if isinstance(fields_raw, list) else [])
    items = items_raw.get("items", items_raw if isinstance(items_raw, list) else [])
    if not project.get("id"):
        raise ValueError("PROJECT_NODE_ID_MISSING")
    return project["id"], fields, items


def item_id_for_url(items: list[dict[str, Any]], url: str) -> str | None:
    matches = [item.get("id") for item in items if (item.get("content") or {}).get("url") == url]
    if len(matches) > 1:
        raise ValueError("PROJECT_ITEM_DUPLICATE")
    return matches[0] if matches else None


def edit_command(project_id: str, item_id: str, field: dict[str, Any], value: str) -> list[str]:
    command = ["gh", "project", "item-edit", "--id", item_id, "--project-id", project_id, "--field-id", str(field["id"])]
    data_type = str(field.get("dataType", field.get("type", ""))).upper()
    if data_type == "SINGLE_SELECT":
        options = field.get("options") or []
        matches = [item for item in options if item.get("name") == value]
        if len(matches) != 1:
            raise ValueError(f"PROJECT_OPTION_NOT_FOUND:{field.get('name')}:{value}")
        return command + ["--single-select-option-id", str(matches[0]["id"])]
    if data_type == "DATE":
        return command + ["--date", value]
    if data_type == "NUMBER":
        return command + ["--number", value]
    return command + ["--text", value]


def parsed_readback(payload: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    node = payload.get("data", {}).get("node") or {}
    project_id = (node.get("project") or {}).get("id")
    values: dict[str, Any] = {}
    for item in (node.get("fieldValues") or {}).get("nodes", []):
        field_name = (item.get("field") or {}).get("name")
        if not field_name:
            continue
        value = next((item[key] for key in ("text", "name", "date", "number") if item.get(key) is not None), None)
        values[field_name] = value
    return project_id, values


def verify_values(project_id: str, item_id: str, expected: dict[str, Any]) -> list[str]:
    raw = json.loads(run(["gh", "api", "graphql", "-f", f"query={READBACK_QUERY}", "-F", f"item={item_id}"]))
    actual_project, actual = parsed_readback(raw)
    errors: list[str] = []
    if actual_project != project_id:
        errors.append("PROJECT_NODE_READBACK_MISMATCH")
    for name, value in expected.items():
        if value is None:
            continue
        observed = actual.get(name)
        if isinstance(value, (int, float)):
            try:
                if float(observed) != float(value):
                    errors.append(f"PROJECT_FIELD_READBACK_MISMATCH:{name}")
            except (TypeError, ValueError):
                errors.append(f"PROJECT_FIELD_READBACK_MISMATCH:{name}")
        elif str(observed) != str(value):
            errors.append(f"PROJECT_FIELD_READBACK_MISMATCH:{name}")
    return errors


def active_work_item(state: dict[str, Any]) -> dict[str, Any]:
    roadmap_id = state.get("active", {}).get("roadmap_id")
    matches = [item for item in state.get("work_items", []) if item.get("roadmap_id") == roadmap_id]
    if len(matches) != 1:
        raise ValueError(f"ACTIVE_WORK_ITEM_NOT_UNIQUE:{roadmap_id}:{len(matches)}")
    return matches[0]


def record_projection(path: Path, status: str, *, project_id: str | None = None, item_id: str | None = None,
                      now: datetime | None = None) -> None:
    state = json.loads(path.read_text(encoding="utf-8"))
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    state["project_projection"] = {"status": status, "observed_at": observed_at,
                                   "project_id": project_id, "item_id": item_id}
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def populate_from_state(args: argparse.Namespace) -> int | None:
    state = json.loads(active_state_path(ROOT).read_text(encoding="utf-8"))
    active = state.get("active", {})
    if active.get("issue") is None:
        print("PROJECT SYNC: активной Issue нет; изменять projection не требуется.")
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise ValueError("GITHUB_REPOSITORY_MISSING")
    health = doctor(ROOT)
    metrics, usage = state.get("ci_metrics", {}), state.get("cost_usage", {})
    item = active_work_item(state)
    args.url = f"https://github.com/{repo}/issues/{int(active['issue'])}"
    args.state = active.get("state")
    args.roadmap_id = active.get("roadmap_id")
    args.priority = item.get("priority")
    args.risk = item.get("risk")
    args.autonomy = state.get("autonomy_level")
    args.work_type = item.get("type")
    args.writer = active.get("writer")
    args.lease_id = active.get("lease_id")
    expires = item.get("expires_at")
    args.lease_until = expires[:10] if isinstance(expires, str) and len(expires) >= 10 else None
    args.conflict_domain = ", ".join(item.get("conflict_domains", []))
    args.provider = state.get("provider", {}).get("mode")
    args.cost_guard = usage.get("status")
    args.next_action = next_operator_action(state)
    args.human_required = "yes" if state.get("owner_decisions") else "no"
    args.product_health = health["categories"]["product_health"]["status"]
    args.control_plane = health["categories"]["control_plane_health"]["status"]
    args.verification = "IN_PROGRESS" if active.get("state") == "VERIFICATION" else state.get("gates", {}).get("reality", "NOT_VERIFIED")
    args.workspace = state.get("workspace", {}).get("status")
    args.snapshot_freshness = snapshot_status(state)
    valid_until = state.get("snapshot", {}).get("valid_until")
    args.snapshot_valid_until = valid_until[:10] if isinstance(valid_until, str) and len(valid_until) >= 10 else None
    args.verification_gap = float(state.get("progress", {}).get("verification_gap", 0)) * 100
    args.ci_p95 = metrics.get("p95_duration_seconds")
    args.ci_flake = None if metrics.get("flake_rate") is None else float(metrics["flake_rate"]) * 100
    args.hosted_minutes = usage.get("hosted_minutes_used")
    args.minutes_hard = usage.get("hosted_minutes_internal_hard")
    args.artifacts_mb = usage.get("artifact_mb")
    args.cache_mb = usage.get("cache_mb")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--state"); parser.add_argument("--roadmap-id"); parser.add_argument("--priority"); parser.add_argument("--risk")
    parser.add_argument("--autonomy"); parser.add_argument("--work-type"); parser.add_argument("--lease-until"); parser.add_argument("--conflict-domain")
    parser.add_argument("--writer"); parser.add_argument("--lease-id"); parser.add_argument("--provider"); parser.add_argument("--cost-guard")
    parser.add_argument("--product-health"); parser.add_argument("--control-plane"); parser.add_argument("--verification"); parser.add_argument("--workspace")
    parser.add_argument("--snapshot-freshness"); parser.add_argument("--snapshot-valid-until")
    parser.add_argument("--verification-gap", type=float); parser.add_argument("--ci-p95", type=float); parser.add_argument("--ci-flake", type=float)
    parser.add_argument("--hosted-minutes", type=float); parser.add_argument("--minutes-hard", type=float)
    parser.add_argument("--artifacts-mb", type=float); parser.add_argument("--cache-mb", type=float)
    parser.add_argument("--next-action"); parser.add_argument("--human-required", choices=["yes", "no"])
    parser.add_argument("--from-state", action="store_true"); parser.add_argument("--optional", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
    project = config.get("github", {}).get("project", {})
    if not project.get("enabled") or not project.get("owner") or not project.get("number"):
        if args.optional:
            print("PROJECT SYNC: Project отключён; локальная Control Center остаётся доступной.")
            return 0
        raise SystemExit("GitHub Project не настроен; feature progression остаётся BLOCKED.")
    try:
        if args.from_state:
            early = populate_from_state(args)
            if early is not None:
                return early
        if not args.url or not args.url.startswith("https://github.com/"):
            raise ValueError("PROJECT_ITEM_URL_NOT_GITHUB")
        if not shutil.which("gh"):
            raise ValueError("GH_CLI_REQUIRED")
        project_id, fields, items = project_context(project["owner"], int(project["number"]))
        item_id = item_id_for_url(items, args.url)
        if item_id is None:
            add = ["gh", "project", "item-add", str(project["number"]), "--owner", project["owner"], "--url", args.url, "--format", "json"]
            if not args.apply:
                print("DRY:", json.dumps(add, ensure_ascii=False))
                item_id = "DRY_RUN_ITEM_ID"
            else:
                item_id = json.loads(run(add)).get("id")
                if not item_id:
                    raise ValueError("PROJECT_ITEM_ID_MISSING_AFTER_ADD")
        values = {
            "Состояние ADWF": args.state, "Roadmap ID": args.roadmap_id, "Приоритет": args.priority,
            "Риск": args.risk, "Автономность": args.autonomy, "Тип работы": args.work_type,
            "Writer": args.writer, "Lease ID": args.lease_id, "Lease до": args.lease_until,
            "Контур конфликта": args.conflict_domain, "Provider": args.provider,
            "Cost Guard": args.cost_guard, "Следующее действие": args.next_action,
            "Product Health": args.product_health, "Control Plane": args.control_plane,
            "Verification": args.verification, "Workspace": args.workspace,
            "Свежесть snapshot": args.snapshot_freshness, "Snapshot до": args.snapshot_valid_until,
            "Verification Gap %": args.verification_gap, "CI p95 sec": args.ci_p95, "CI flake %": args.ci_flake,
            "Hosted minutes": args.hosted_minutes, "Minutes hard": args.minutes_hard,
            "Artifacts MB": args.artifacts_mb, "Cache MB": args.cache_mb,
            "Требует владельца": None if args.human_required is None else ("YES" if args.human_required == "yes" else "NO"),
        }
        by_name = {field.get("name"): field for field in fields}
        for name, value in values.items():
            if value is None:
                continue
            if name not in by_name:
                raise ValueError(f"PROJECT_FIELD_NOT_FOUND:{name}")
            command = edit_command(project_id, item_id, by_name[name], str(value))
            if args.apply:
                run(command)
                print(f"APPLIED {name}={value}")
            else:
                print("DRY:", json.dumps(command, ensure_ascii=False))
        if args.apply:
            _, _, verified_items = project_context(project["owner"], int(project["number"]))
            if item_id_for_url(verified_items, args.url) != item_id:
                raise ValueError("PROJECT_ITEM_POSTCONDITION_FAILED")
            readback_errors = verify_values(project_id, item_id, values)
            if readback_errors:
                raise ValueError(",".join(readback_errors))
            state_path = active_state_path(ROOT)
            record_projection(state_path, "PASS", project_id=project_id, item_id=item_id)
            final_control = doctor(ROOT)["categories"]["control_plane_health"]["status"]
            if values.get("Control Plane") != final_control:
                field = by_name.get("Control Plane")
                if field is None:
                    raise ValueError("PROJECT_FIELD_NOT_FOUND:Control Plane")
                run(edit_command(project_id, item_id, field, final_control))
                final_errors = verify_values(project_id, item_id, {"Control Plane": final_control})
                if final_errors:
                    raise ValueError(",".join(final_errors))
            record_projection(state_path, "PASS", project_id=project_id, item_id=item_id)
            print("PROJECT PROJECTION READBACK: PASS")
        return 0
    except ValueError as exc:
        if args.apply:
            try:
                record_projection(active_state_path(ROOT), "FAIL")
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        print(f"PROJECT SYNC: BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
