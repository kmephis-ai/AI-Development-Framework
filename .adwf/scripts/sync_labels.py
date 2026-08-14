#!/usr/bin/env python3
"""Идемпотентно создать/исправить только canonical ADWF labels; чужие labels сохраняются."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> str:
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if process.returncode:
        raise ValueError((process.stderr or process.stdout).strip()[:500])
    return process.stdout


def commands_for(desired: list[dict], existing: list[dict]) -> list[list[str]]:
    by_name = {item.get("name"): item for item in existing}
    commands: list[list[str]] = []
    for item in desired:
        current = by_name.get(item["name"])
        if current is None:
            commands.append(["gh", "label", "create", item["name"], "--color", item["color"], "--description", item["description"]])
        elif str(current.get("color", "")).lower() != item["color"].lower() or current.get("description", "") != item["description"]:
            commands.append(["gh", "label", "edit", item["name"], "--color", item["color"], "--description", item["description"]])
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not shutil.which("gh"):
        raise SystemExit("GH_CLI_REQUIRED")
    desired = json.loads((ROOT / ".adwf/labels.json").read_text(encoding="utf-8"))
    existing = json.loads(run(["gh", "label", "list", "--limit", "1000", "--json", "name,color,description"]))
    commands = commands_for(desired, existing)
    for command in commands:
        if args.apply:
            run(command)
            print("APPLIED", command[2], command[3])
        else:
            print("DRY:", json.dumps(command, ensure_ascii=False))
    if args.apply:
        verified = json.loads(run(["gh", "label", "list", "--limit", "1000", "--json", "name,color,description"]))
        remaining = commands_for(desired, verified)
        if remaining:
            print("LABEL READBACK: FAIL")
            return 1
        print("LABEL READBACK: PASS")
    elif not commands:
        print("LABELS: ALREADY_CURRENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
