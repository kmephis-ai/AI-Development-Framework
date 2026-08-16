#!/usr/bin/env python3
"""Run deterministic Creative Agent qualification without network/provider dependencies."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.creative_agent_qualification import run_reference_qualification  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = run_reference_qualification(Path(args.root).resolve())
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)[:500]}, ensure_ascii=False, sort_keys=True))
        return 1
    text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
