#!/usr/bin/env python3
"""Проверить/создать Project fields; Views остаются описанными и проверяемыми в layout."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> str:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode:
        raise ValueError((process.stderr or process.stdout).strip()[:500])
    return process.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True); parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not shutil.which("gh"):
        raise SystemExit("gh CLI required")
    layout = json.loads((ROOT / ".adwf/project-layout.json").read_text(encoding="utf-8"))
    try:
        json.loads(run(["gh", "project", "view", str(args.number), "--owner", args.owner, "--format", "json"]))
        raw = json.loads(run(["gh", "project", "field-list", str(args.number), "--owner", args.owner, "--format", "json"]))
        existing = {field.get("name"): field for field in raw.get("fields", raw if isinstance(raw, list) else [])}
        errors = []
        for field in layout["fields"]:
            current = existing.get(field["name"])
            if current:
                expected = {"single_select": "SINGLE_SELECT", "text": "TEXT", "date": "DATE", "number": "NUMBER"}[field["type"]]
                actual = str(current.get("dataType", current.get("type", ""))).upper()
                if actual and actual != expected:
                    errors.append(f"FIELD_TYPE_MISMATCH:{field['name']}:{actual}!={expected}")
                if expected == "SINGLE_SELECT":
                    actual_options = [item.get("name") for item in current.get("options", [])]
                    if actual_options and actual_options != field.get("options", []):
                        errors.append(f"FIELD_OPTIONS_MISMATCH:{field['name']}")
                continue
            data_type = {"single_select": "SINGLE_SELECT", "text": "TEXT", "date": "DATE", "number": "NUMBER"}[field["type"]]
            command = ["gh", "project", "field-create", str(args.number), "--owner", args.owner, "--name", field["name"], "--data-type", data_type]
            if data_type == "SINGLE_SELECT":
                command += ["--single-select-options", ",".join(field["options"])]
            if args.apply:
                run(command); print("CREATED", field["name"])
            else:
                print("DRY:", json.dumps(command, ensure_ascii=False))
        if errors:
            for error in errors: print("BLOCK:", error)
            return 1
        print("PROJECT FIELDS: PASS")
        print("PROJECT VIEWS:", ", ".join(view["name"] for view in layout["views"]))
        print("Views API недоступен как надёжный write-contract; полный owner dashboard обеспечивается pinned Dashboard Issue + CONTROL_CENTER.md.")
        return 0
    except ValueError as exc:
        print(f"PROJECT BOOTSTRAP: BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
