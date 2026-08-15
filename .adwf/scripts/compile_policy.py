#!/usr/bin/env python3
"""Собрать или проверить единый Effective Policy."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.policy_compiler import check_compiled_policy, compile_policy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    compiled, errors = compile_policy(ROOT)
    if errors:
        print("EFFECTIVE POLICY: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    target = ROOT / ".adwf/effective-policy.json"
    if args.write:
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n")
        print(f"EFFECTIVE POLICY: WRITTEN {compiled['policy_hash']}")
        return 0
    errors = check_compiled_policy(ROOT)
    if errors:
        print("EFFECTIVE POLICY: STALE")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"EFFECTIVE POLICY: PASS {compiled['policy_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
