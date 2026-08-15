#!/usr/bin/env python3
"""Local-only external Skill quarantine intake. No network and no auto-activation."""
from __future__ import annotations
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.skill_layer import apply_vendor_intake, vendor_intake_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--skill-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-digest", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--attribution", required=True)
    parser.add_argument("--imported-at", required=True)
    parser.add_argument("--local-modification", action="append", default=[])
    parser.add_argument("--quarantine-root", default=str(ROOT / ".adwf-runtime" / "skill-quarantine"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    provenance = {
        "source_url": args.source_url,
        "source_ref": args.source_ref,
        "source_digest": args.source_digest,
        "license": args.license,
        "attribution": args.attribution,
        "imported_at": args.imported_at,
        "local_modifications": args.local_modification,
    }
    fn = apply_vendor_intake if args.apply else vendor_intake_plan
    result = fn(Path(args.source).resolve(), args.skill_id, provenance, Path(args.quarantine_root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
