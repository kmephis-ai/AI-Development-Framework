#!/usr/bin/env python3
"""Validate governed Skill packages, legacy bridge, security, evals and registry."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.skill_layer import validate_repository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate_repository(Path(args.root).resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SKILL LAYER: {result['status']} managed={result['managed_count']} legacy={result['legacy_count']}")
        for finding in result["findings"]:
            print(f"- {finding['code']}:{finding['path']}:{finding['message']}")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
