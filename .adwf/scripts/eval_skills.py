#!/usr/bin/env python3
"""Run deterministic routing/outcome/adversarial evals for managed Skills."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.skill_layer import discover_packages, evaluate_package, _read_descriptor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--skill")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    output = []
    failed = False
    for package in discover_packages(root):
        descriptor, findings = _read_descriptor(root, package)
        if descriptor is None:
            continue
        if args.skill and descriptor.get("id") != args.skill:
            continue
        if findings:
            result = {"skill_id": package.name, "status": "FAIL", "findings": [item.to_dict() for item in findings]}
        else:
            result = {"skill_id": descriptor["id"], **evaluate_package(root, package, descriptor)}
        output.append(result)
        failed = failed or result["status"] != "PASS"
    if args.skill and not output:
        output.append({"skill_id": args.skill, "status": "FAIL", "findings": [{"code": "SKILL_NOT_FOUND", "path": "skills", "message": args.skill, "severity": "ERROR"}]})
        failed = True
    print(json.dumps({"status": "FAIL" if failed else "PASS", "skills": output}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
