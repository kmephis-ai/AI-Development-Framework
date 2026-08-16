#!/usr/bin/env python3
"""Run ASREF-001 reference Apps Script/data-centric conformance."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.reference_conformance import ReferenceConformanceError, run_reference_apps_script_conformance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer-root")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        report = run_reference_apps_script_conformance(ROOT, consumer_root=args.consumer_root)
    except ReferenceConformanceError as exc:
        print(json.dumps({"status": "BLOCK", "reason": str(exc).split(":", 1)[0]}, ensure_ascii=False))
        return 5
    path = Path(args.output) if args.output else ROOT / ".adwf-runtime/reference-conformance/apps-script/latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
