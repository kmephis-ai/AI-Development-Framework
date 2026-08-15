#!/usr/bin/env python3
"""Generate deterministic skills/registry.json from managed Skill package truth."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.skill_layer import expected_registry, discover_packages, validate_package, _read_descriptor  # noqa: E402
from lib.strict_json import load as strict_json_load  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    managed = []
    findings = []
    for package in discover_packages(root):
        descriptor, current = _read_descriptor(root, package)
        findings.extend(current)
        if descriptor is None:
            continue
        current, evaluation = validate_package(root, package, descriptor)
        findings.extend(current)
        managed.append((package, descriptor, evaluation))
    if findings:
        print("SKILL REGISTRY: FAIL")
        for item in findings:
            print(f"- {item.code}:{item.path}:{item.message}")
        return 1
    path = root / "skills" / "registry.json"
    if not managed:
        if args.check and path.exists():
            print("SKILL REGISTRY: FAIL STALE_WITHOUT_MANAGED_SKILLS")
            return 1
        print("SKILL REGISTRY: PASS NO_MANAGED_SKILLS")
        return 0
    payload = expected_registry(root, managed)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            print("SKILL REGISTRY: FAIL STALE")
            return 1
        print("SKILL REGISTRY: PASS")
        return 0
    path.write_text(text, encoding="utf-8")
    print(f"SKILL REGISTRY updated: {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
