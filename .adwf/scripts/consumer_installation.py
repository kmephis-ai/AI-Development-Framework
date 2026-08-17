#!/usr/bin/env python3
"""Validate a committed provider-durable Consumer Installation Record from a fresh checkout."""
from __future__ import annotations
from pathlib import Path
import argparse, json, sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_installation import ConsumerInstallationError, validate_fresh_session  # noqa: E402

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ADWF Consumer Installation Record v1")
    parser.add_argument("--consumer-root", required=True)
    parser.add_argument("--framework-root", required=True)
    parser.add_argument("--repository")
    args = parser.parse_args()
    try:
        result = validate_fresh_session(args.consumer_root, args.framework_root, expected_repository=args.repository)
    except (ConsumerInstallationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"BLOCK","reason":str(exc).split("\n",1)[0],"mutation_authority":"NONE_RECORD_IS_PROOF_ONLY"}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
