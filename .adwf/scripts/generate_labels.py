#!/usr/bin/env python3
"""`.adwf/labels.json` — единственный источник; root labels.json — projection."""
from __future__ import annotations

from pathlib import Path
import argparse
import json

ROOT = Path(__file__).resolve().parents[2]


def rendered() -> str:
    labels = json.loads((ROOT / ".adwf/labels.json").read_text(encoding="utf-8"))
    names = [item["name"] for item in labels]
    if len(names) != len(set(names)):
        raise ValueError("DUPLICATE_CANONICAL_LABEL")
    return json.dumps(names, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = rendered()
    target = ROOT / "labels.json"
    if args.write:
        target.write_text(expected, encoding="utf-8")
        print("LABEL PROJECTION: WRITTEN")
        return 0
    if not target.is_file() or target.read_text(encoding="utf-8") != expected:
        print("LABEL PROJECTION: STALE")
        return 1
    print("LABEL PROJECTION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
